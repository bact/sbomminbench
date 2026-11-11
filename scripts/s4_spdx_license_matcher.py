# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Experimental S4: SPDX License Matcher on the texts of S2.

Input:    the licence files and cached licence pages that
          ``s2_license_text_crosscheck.py`` reads.
Method:   run ``spdx-license-matcher`` (package ``license-matcher``) on each
          text; compare its match with the reviewer's value.
Output:   ``experimental/s4-spdx-license-matcher.jsonl``.
Network:  none; a page that S2 did not cache gives ``not_cached``.
Voting:   none. ``build``, ``validate`` and ``export`` never read the output.
Needs:    Java 11 or later, and a Redis server on ``localhost:6379`` that
          holds the matcher's licence list (``spdx-license-matcher --build``).
          This script never builds or changes that database.
Usage:    ``uv run --with license-matcher==2.11.1 python
          scripts/s4_spdx_license_matcher.py``
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import s2_license_text_crosscheck as s2

from sbomminbench.archive import ArchiveError, list_members, read_member

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "experimental" / "s4-spdx-license-matcher.jsonl"
THRESHOLD = 0.9
MATCH = "The following license ID(s) match: "


def _licence_data() -> dict[bytes, bytes]:
    import redis

    host = os.environ.get("SPDX_REDIS_HOST", "localhost")
    client = redis.StrictRedis(host=host, port=6379, db=0)
    keys = client.keys()
    if not keys:
        raise SystemExit("ERROR: the matcher database is empty; build it first")
    return dict(zip(keys, client.mget(keys), strict=True))


def _match(text: str, data: dict[bytes, bytes]) -> dict[str, Any]:
    """The matcher's answer for *text*, as its command line would decide it."""
    from spdx_license_matcher.computation import (
        get_close_matches,
        get_matching_string,
    )

    if not text.strip():
        return {"matched": None, "close": None}
    try:
        close = get_close_matches(text, data, THRESHOLD)
        answer = get_matching_string(close, text)
    except Exception as exc:  # noqa: BLE001 - a probe records, never aborts
        return {"matched": None, "close": None, "match_error": type(exc).__name__}
    best = max(close, key=close.get) if close else None
    row: dict[str, Any] = {
        "matched": None,
        "close": best,
        "dice": round(close[best], 4) if best else None,
    }
    if answer.startswith(MATCH):
        row["matched"] = sorted(answer[len(MATCH) :].split(", "))
    return row


def _relation(row: dict[str, Any], human: str | None) -> str:
    matched = row.get("matched")
    if not matched:
        return "close_only" if row.get("close") else "no_match"
    if not human:
        return "no_human_value"
    want = s2._keys(human)
    got = {m.casefold() for m in matched}
    if got == want or (len(want) == 1 and want <= got):
        return "same_licences"
    if got & want:
        return "part_of_human_value" if got < want else "overlap"
    return "different"


def _file_rows(archive: Path, data: dict[bytes, bytes]) -> list[dict[str, Any]]:
    rows = []
    try:
        members = list_members(archive)
    except ArchiveError:
        return []
    for member in sorted(members, key=lambda m: m.name):
        parts = member.name.split("/")
        if not member.is_file or len(parts) > 2:
            continue
        if not s2.LICENSE_FILE.match(parts[-1]):
            continue
        try:
            raw = read_member(archive, member.name, max_bytes=s2.MAX_BYTES)
        except ArchiveError:
            continue
        text = (raw or b"").decode("utf-8", errors="replace")
        rows.append({"kind": "file", "file": member.name, **_match(text, data)})
    return rows


def _url_rows(entry: dict[str, Any], data: dict[bytes, bytes]) -> list[dict[str, Any]]:
    declared = s2._licence_urls(entry)
    urls = declared + [
        v for u in declared if (v := s2._raw_variant(u)) and v not in declared
    ]
    rows = []
    for url in urls:
        meta = s2._fetch(url, offline=True)
        body = meta.pop("body", None)
        kind = "url" if url in declared else "url_raw_variant"
        row: dict[str, Any] = {"kind": kind, "url": url, "fetch": meta["fetch"]}
        if body is not None:
            text = s2._visible_text(body, str(meta.get("content_type", "")))
            row.update(_match(text, data))
        rows.append(row)
    return rows


def main() -> int:
    data = _licence_data()
    path = ROOT / "data" / "release" / "entries.jsonl"
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        expected = entry["subjects"][0]["expected"]["license"]
        if "auto_unresolved" not in (expected["status"], expected.get("auto_status")):
            continue
        human = (expected.get("value") or {}).get("spdx_expression")
        sources: list[dict[str, Any]] = []
        archive = s2._archive(ROOT / ".cache", entry)
        if archive is not None:
            sources += _file_rows(archive, data)
        sources += _url_rows(entry, data)
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
        print(f"INFO: {entry['id']} {len(sources)} sources", file=sys.stderr)
    out.sort(key=lambda r: r["id"])
    OUT.write_text(
        "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in out),
        encoding="utf-8",
        newline="\n",
    )
    print(f"FILE={OUT.relative_to(ROOT)} ROWS={len(out)} LICENCES={len(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
