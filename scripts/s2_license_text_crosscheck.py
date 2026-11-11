# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Experimental S2 cross-check: licence text detection against the review.

For each root subject whose licence the automatic derivation could not settle,
runs LicenseID's text detector on (a) the licence files of the cached package
archive and (b) the text behind each licence URL of the metadata, and compares
the top detection with the reviewer's value. Writes
``experimental/s2-license-text-crosscheck.jsonl``.

The output is non-voting: ``build``, ``validate`` and ``export`` never read
it. A detection is a computed value, not a declared one.

Usage: ``uv run python scripts/s2_license_text_crosscheck.py --db DB
[--limit-urls N] [--no-network]``. ``DB`` is a LicenseID database
(``licenseid --db DB update --source packaged``). Fetched pages are cached
under ``.cache/s2/``; one request per second per run.
"""

import argparse
import hashlib
import html.parser
import ipaddress
import json
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import requests
from license_expression import get_spdx_licensing

from sbomminbench.archive import ArchiveError, archive_kind, list_members, read_member
from sbomminbench.http import USER_AGENT
from sbomminbench.redact import redact_json

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "s2"
OUT = ROOT / "experimental" / "s2-license-text-crosscheck.jsonl"
MAX_BYTES = 2 * 1024 * 1024
INTERVAL = 1.0
# A top detection below this score is not counted as a detection. The detector
# always names its closest text, also for a text that is no listed licence.
MIN_SCORE = 0.9
LICENSE_FILE = re.compile(
    r"^(licen[cs]e|copying|unlicense|eula)([._-].*)?$|^.*licen[cs]e.*\.(md|txt)$",
    re.IGNORECASE,
)
_last = [0.0]


class _Text(html.parser.HTMLParser):
    """Collect the visible text of an HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _visible_text(body: bytes, content_type: str) -> str:
    text = body.decode("utf-8", errors="replace")
    if "html" not in content_type and "<html" not in text[:2000].lower():
        return text
    parser = _Text()
    parser.feed(text)
    return re.sub(
        r"[ \t]+", " ", "\n".join(p.strip() for p in parser.parts if p.strip())
    )


def _public_host(url: str) -> bool:
    """True when *url* is http(s) and its host resolves to public addresses only."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except OSError:
        return False
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def _fetch(url: str, offline: bool) -> dict[str, Any]:
    """Return the cached or fetched record of *url* (no body kept on failure)."""
    key = hashlib.sha256(url.encode()).hexdigest()
    meta_path, body_path = CACHE / f"{key}.json", CACHE / f"{key}.body"
    if meta_path.exists():
        meta = cast(dict[str, Any], json.loads(meta_path.read_text(encoding="utf-8")))
        if body_path.exists():
            meta["body"] = body_path.read_bytes()
        return meta
    if offline:
        return {"fetch": "not_cached"}
    if not _public_host(url):
        return {"fetch": "refused_url"}
    wait = INTERVAL - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    started = time.monotonic()
    meta = {"fetch": "ok"}
    body = b""
    try:
        with requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=30, stream=True
        ) as resp:
            meta["http_status"] = resp.status_code
            meta["final_host"] = urlsplit(resp.url).hostname
            meta["content_type"] = resp.headers.get("Content-Type", "").split(";")[0]
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    meta["fetch"] = "over_cap"
                    break
            if resp.status_code != 200:
                meta["fetch"] = "http_error"
    except requests.RequestException as exc:
        meta = {"fetch": "network_error", "error": type(exc).__name__}
    _last[0] = time.monotonic()
    meta["seconds"] = round(time.monotonic() - started, 2)
    CACHE.mkdir(parents=True, exist_ok=True)
    if meta["fetch"] == "ok":
        body_path.write_bytes(body)
        meta["bytes"] = len(body)
    if meta["fetch"] != "network_error":
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    if meta["fetch"] == "ok":
        meta["body"] = body
    return meta


def _detect(detector: Any, text: str) -> dict[str, Any]:
    if not text.strip():
        return {"detected": None}
    try:
        found = detector.detect(text=text)
    except Exception as exc:  # noqa: BLE001 - a probe records, never aborts
        return {"detected": None, "detect_error": type(exc).__name__}
    if not found:
        return {"detected": None}
    top = found[0]
    return {
        "detected": top.get("expression"),
        "exact": top.get("exact"),
        "score": top.get("score"),
        "coverage": top.get("coverage"),
    }


def _keys(expression: str | None) -> set[str]:
    if not expression:
        return set()
    try:
        parsed = get_spdx_licensing().parse(expression)
    except Exception:  # noqa: BLE001 - odd input is "no keys"
        return set()
    return {str(k).casefold() for k in get_spdx_licensing().license_keys(parsed)}


def _relation(row: dict[str, Any], human: str | None) -> str:
    """How the top detection of *row* relates to the reviewer's value."""
    detected = row.get("detected")
    if not detected:
        return "no_detection"
    if not row.get("exact") and (row.get("score") or 0.0) < MIN_SCORE:
        return "low_score"
    if not human:
        return "no_human_value"
    got, want = _keys(detected), _keys(human)
    if got == want:
        return "same_licences"
    if got and got < want:
        return "part_of_human_value"
    if got & want:
        return "overlap"
    return "different"


def _archive(cache: Path, entry: dict[str, Any]) -> Path | None:
    for artifact in entry["platform"]["artifacts"]:
        name = artifact.get("filename")
        if artifact.get("fetched") and name and archive_kind(name):
            found = sorted((cache / "artifacts").glob(f"*/{name}"))
            if found:
                return found[0]
    return None


def _file_rows(archive: Path, detector: Any) -> list[dict[str, Any]]:
    rows = []
    try:
        members = list_members(archive)
    except ArchiveError:
        return [{"kind": "file", "file": None, "read": "archive_error"}]
    for member in sorted(members, key=lambda m: m.name):
        parts = member.name.split("/")
        if not member.is_file or len(parts) > 2 or not LICENSE_FILE.match(parts[-1]):
            continue
        try:
            data = read_member(archive, member.name, max_bytes=MAX_BYTES)
        except ArchiveError:
            rows.append({"kind": "file", "file": member.name, "read": "over_cap"})
            continue
        text = (data or b"").decode("utf-8", errors="replace")
        rows.append(
            {
                "kind": "file",
                "file": member.name,
                "read": "ok",
                **_detect(detector, text),
            }
        )
    return rows


def _licence_urls(entry: dict[str, Any]) -> list[str]:
    subject = entry["subjects"][0]
    seen: list[str] = []
    for obs in subject["manifest_observations"] + entry["platform"]["observations"]:
        raw = obs.get("raw")
        if (
            obs["element"] == "license"
            and isinstance(raw, str)
            and raw.startswith(("http://", "https://"))
            and raw not in seen
        ):
            seen.append(raw)
    return seen


def _raw_variant(url: str) -> str | None:
    """The plain-text address of a file page on Hugging Face or GitHub."""
    host = urlsplit(url).hostname or ""
    if host in ("huggingface.co", "github.com") and "/blob/" in url:
        return url.replace("/blob/", "/raw/", 1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--db", required=True, help="LicenseID database file")
    parser.add_argument("--limit-urls", type=int, default=-1, help="fetch at most N")
    parser.add_argument("--no-network", action="store_true")
    args = parser.parse_args()
    from licenseid import LicenseDetector

    detector = LicenseDetector(args.db)
    lines = (
        (ROOT / "data" / "release" / "entries.jsonl").read_text("utf-8").splitlines()
    )
    out: list[dict[str, Any]] = []
    fetched = 0
    for entry in map(json.loads, lines):
        expected = entry["subjects"][0]["expected"]["license"]
        if "auto_unresolved" not in (expected["status"], expected.get("auto_status")):
            continue
        value = expected.get("value") or {}
        human = value.get("spdx_expression")
        sources: list[dict[str, Any]] = []
        archive = _archive(ROOT / ".cache", entry)
        if archive is not None:
            sources += _file_rows(archive, detector)
        urls = _licence_urls(entry)
        urls += [v for u in urls if (v := _raw_variant(u)) and v not in urls]
        for url in urls:
            cached = (
                CACHE / (hashlib.sha256(url.encode()).hexdigest() + ".json")
            ).exists()
            limited = 0 <= args.limit_urls <= fetched and not cached
            meta = _fetch(url, args.no_network or limited)
            if not cached and meta["fetch"] != "not_cached":
                fetched += 1
                print(
                    f"INFO: {url} {meta['fetch']} {meta.get('seconds', '')}",
                    file=sys.stderr,
                )
            body = meta.pop("body", None)
            declared = url in _licence_urls(entry)
            row = {"kind": "url" if declared else "url_raw_variant", "url": url, **meta}
            if body is not None:
                text = _visible_text(body, str(meta.get("content_type", "")))
                row.update(_detect(detector, text))
            sources.append(row)
        for row in sources:
            row["relation"] = _relation(row, human)
        out.append(
            {
                "id": entry["id"],
                "ecosystem": entry["ecosystem"],
                "status": expected["status"],
                "human_value": human,
                "sources": sources,
            }
        )
    out.sort(key=lambda r: r["id"])
    cleaned, _ = redact_json(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "".join(
            json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
            for r in cast(list[Any], cleaned)
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"FILE={OUT.relative_to(ROOT)} ROWS={len(out)} URLS_FETCHED={fetched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
