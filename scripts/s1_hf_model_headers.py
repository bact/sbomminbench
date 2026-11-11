# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Experimental S1 probe: model-file headers of the Hugging Face entries.

For each Hugging Face entry, reads the header of a few model files (GGUF,
Safetensors, ONNX) at the pinned revision with ranged requests, then runs
Pitloom's reader on it. Writes ``experimental/s1-hf-model-headers.jsonl``.
The output is non-voting: ``build``, ``validate`` and ``export`` never read it.

Only the header bytes are fetched for GGUF and Safetensors. The reader gets a
sparse file: the real header followed by a hole up to the declared size, so
nothing that depends on tensor data is meaningful. An ONNX file is fetched
whole, when it is within :data:`ONNX_MAX_BYTES`. No token is sent: a gated
repository is recorded as ``gated``.

With ``--repos N`` the probe goes beyond the dataset: the next repositories of
the ecosyste.ms downloads ranking are added until N repositories are covered
(``in_dataset`` is false for them; they are not entries of the dataset).

Usage: ``uv run python scripts/s1_hf_model_headers.py [--repos 500]``
(network; one request per ``--interval`` seconds, default 1; a 429 answer is
waited out, and after repeated 429 the run stops and a rerun continues, since
header bytes and results are cached under ``.cache/s1/``).
"""

import argparse
import dataclasses
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import requests
from pitloom.extract.ai_model.limits import ModelLimitExceeded
from pitloom.extract.ai_model.reader import read_ai_model

from sbomminbench.eco.huggingface import API_URL
from sbomminbench.http import USER_AGENT, HttpClient
from sbomminbench.jsonio import iter_entries
from sbomminbench.redact import redact_json
from sbomminbench.select import _read_page

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "s1"
OUT = ROOT / "experimental" / "s1-hf-model-headers.jsonl"
PER_FORMAT = {"safetensors": 2, "gguf": 1, "onnx": 1}
GGUF_FIRST = 4 * 1024 * 1024
GGUF_MAX = 64 * 1024 * 1024
SAFETENSORS_MAX = 64 * 1024 * 1024
ONNX_MAX_BYTES = 100 * 1024 * 1024
MAX_RANKING_PAGES = 20
INTERVAL = [1.0]
MAX_TRIES = 4
MAX_WAIT = 300.0
_last = [0.0]


class Skip(Exception):
    """A file that is not read; ``args[0]`` is the status, ``args[1]`` detail."""


class RateLimited(Exception):
    """The host keeps answering 429; the run stops and keeps what it has."""


def _wait_turn() -> None:
    wait = INTERVAL[0] - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)


def _request(url: str, headers: dict[str, str], timeout: float) -> requests.Response:
    """GET *url*, waiting between requests and backing off on 429 or 5xx."""
    for attempt in range(MAX_TRIES):
        _wait_turn()
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT, **headers}, timeout=timeout
        )
        _last[0] = time.monotonic()
        if resp.status_code not in (429, 500, 502, 503, 504):
            return resp
        after = resp.headers.get("Retry-After", "")
        delay = float(after) if after.isdecimal() else 30.0 * (attempt + 1)
        print(
            f"WARNING: HTTP {resp.status_code}; waiting {delay:.0f} s",
            file=sys.stderr,
        )
        if delay > MAX_WAIT:
            break
        time.sleep(delay)
    raise RateLimited(url)


def _get(url: str, start: int, end: int) -> tuple[bytes, int]:
    """Return bytes ``start..end`` (inclusive) of *url* and its declared size."""
    key = hashlib.sha256(f"{url}|{start}-{end}".encode()).hexdigest()
    body, meta = CACHE / f"{key}.bin", CACHE / f"{key}.json"
    if body.exists() and meta.exists():
        return body.read_bytes(), json.loads(meta.read_text())["size"]
    resp = _request(url, {"Range": f"bytes={start}-{end}"}, 120)
    if resp.status_code in (401, 403):
        raise Skip("gated", resp.status_code)
    if resp.status_code == 404:
        raise Skip("not_found", 404)
    if resp.status_code != 206:
        raise Skip("http_error", resp.status_code)
    size = int(resp.headers["Content-Range"].rsplit("/", 1)[1])
    CACHE.mkdir(parents=True, exist_ok=True)
    body.write_bytes(resp.content)
    meta.write_text(json.dumps({"url": url, "range": [start, end], "size": size}))
    return resp.content, size


def _head_safetensors(url: str) -> tuple[bytes, int]:
    prefix, size = _get(url, 0, 7)
    (length,) = struct.unpack("<Q", prefix)
    if length > SAFETENSORS_MAX:
        raise Skip("over_cap", length)
    rest, _ = _get(url, 8, 8 + length - 1)
    return prefix + rest, size


def _gguf_header_complete(path: Path, have: int) -> bool:
    from gguf import GGUFReader

    try:
        return int(GGUFReader(str(path), mode="r").data_offset) <= have
    except Exception:  # noqa: BLE001 - a cut header fails in many ways
        return False


def _sparse(path: Path, head: bytes, size: int) -> None:
    path.write_bytes(head)
    os.truncate(path, max(size, len(head)))


def _head_gguf(url: str, tmp: Path) -> tuple[bytes, int]:
    want = GGUF_FIRST
    while True:
        head, size = _get(url, 0, want - 1)
        probe = tmp / "probe.gguf"
        _sparse(probe, head, size)
        if len(head) >= size or _gguf_header_complete(probe, len(head)):
            return head, size
        if want >= GGUF_MAX:
            raise Skip("over_cap", want)
        want *= 4


def _whole_onnx(url: str) -> tuple[bytes, int]:
    _, size = _get(url, 0, 0)
    if size > ONNX_MAX_BYTES:
        raise Skip("over_cap", size)
    resp = _request(url, {}, 600)
    if resp.status_code != 200:
        raise Skip("http_error", resp.status_code)
    return resp.content, size


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=str)}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(v) for v in value), key=json.dumps)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _onnx_raw(path: Path) -> dict[str, Any]:
    """Return the name fields as stored, before any reader drops a default."""
    import onnx

    try:
        model = onnx.load(str(path), load_external_data=False)
    except Exception as exc:  # noqa: BLE001 - protobuf raises its own types
        return {"error": type(exc).__name__}
    return {
        "graph_name": model.graph.name,
        "producer_name": model.producer_name,
        "metadata_props": len(model.metadata_props),
    }


def _probe(url: str, name: str, fmt: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        try:
            if fmt == "safetensors":
                head, size = _head_safetensors(url)
            elif fmt == "gguf":
                head, size = _head_gguf(url, tmp)
            else:
                head, size = _whole_onnx(url)
        except Skip as skip:
            return {"status": skip.args[0], "detail": skip.args[1]}
        except requests.RequestException as exc:
            return {"status": "network_error", "detail": type(exc).__name__}
        target = tmp / Path(name).name
        _sparse(target, head, size)
        out: dict[str, Any] = {"declared_size_bytes": size, "bytes_read": len(head)}
        if fmt == "onnx":
            out["onnx_raw"] = _onnx_raw(target)
        try:
            meta = read_ai_model(target)
        except ImportError as exc:
            return {**out, "status": "library_missing", "detail": str(exc)}
        except (ValueError, ModelLimitExceeded, OSError) as exc:
            return {**out, "status": "reader_error", "detail": type(exc).__name__}
    fields = {k: v for k, v in _jsonable(meta).items() if v not in (None, "", [], {})}
    return {**out, "status": "read", "pitloom_fields": fields}


def _pick(files: list[str]) -> list[tuple[str, str]]:
    picked: list[tuple[str, str]] = []
    for fmt, limit in PER_FORMAT.items():
        names = sorted(f for f in files if f.lower().endswith("." + fmt))
        picked += [(n, fmt) for n in names[:limit]]
    return picked


def _cached_probe(url: str, name: str, fmt: str) -> dict[str, Any]:
    """Probe once per URL; a network failure is not kept, so a rerun retries it."""
    path = CACHE / "results" / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if path.exists():
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    result = _probe(url, name, fmt)
    if result["status"] != "network_error":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _dataset_repos() -> list[dict[str, Any]]:
    repos = []
    for _p, entry in iter_entries(ROOT / "data" / "entries"):
        if entry.ecosystem != "huggingface":
            continue
        files = [f for subject in entry.subjects for f in subject.files]
        repos.append(
            {
                "repo": entry.platform.name,
                "sha": entry.platform.version,
                "files": files,
                "in_dataset": True,
                "rank": entry.rank,
            }
        )
    return repos


def _ranked_repos(http: HttpClient, want: int, skip: set[str]) -> list[dict[str, Any]]:
    """The next *want* repositories of the downloads ranking, not in *skip*."""
    repos: list[dict[str, Any]] = []
    for page in range(1, MAX_RANKING_PAGES + 1):
        rows, _url, date = _read_page("huggingface", http, page)
        for row in rows:
            if len(repos) >= want:
                return repos
            if row.name.lower() in skip:
                continue
            skip.add(row.name.lower())
            doc, resp = http.get_json(API_URL.format(id=row.name, sha=row.version))
            base = {
                "repo": row.name,
                "sha": row.version,
                "in_dataset": False,
                "rank": row.rank,
                "ranking_date": date,
            }
            if not isinstance(doc, dict):
                repos.append({**base, "files": [], "api_status": resp.status})
                continue
            names = [
                s.get("rfilename")
                for s in doc.get("siblings") or []
                if isinstance(s, dict)
            ]
            tags = _strings(doc.get("tags"))
            repos.append(
                {
                    **base,
                    "files": _strings(names),
                    "api_status": resp.status,
                    "api_author": doc.get("author"),
                    "api_license_tags": sorted(
                        t for t in tags if t.startswith("license:")
                    ),
                    "api_gated": doc.get("gated"),
                }
            )
    return repos


def _write(rows: list[dict[str, Any]]) -> None:
    rows = sorted(rows, key=lambda r: (r["repo"].lower(), r.get("file") or ""))
    cleaned, _ = redact_json(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "".join(
            json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
            for r in cast(list[Any], cleaned)
        ),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repos", type=int, default=0, help="total repositories")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds")
    args = parser.parse_args()
    INTERVAL[0] = args.interval
    repos = _dataset_repos()
    http = HttpClient(ROOT / ".cache" / "s1-http")
    extra = args.repos - len(repos)
    if extra > 0:
        repos += _ranked_repos(http, extra, {r["repo"].lower() for r in repos})
    rows: list[dict[str, Any]] = []
    stopped = False
    for i, repo in enumerate(repos, 1):
        base = {k: v for k, v in repo.items() if k != "files"}
        picked = _pick(repo["files"])
        if not picked:
            rows.append({**base, "file": None, "status": "no_model_file_picked"})
        for name, fmt in picked:
            url = (
                f"https://huggingface.co/{repo['repo']}/resolve/{repo['sha']}/"
                f"{quote(name)}"
            )
            try:
                result = _cached_probe(url, name, fmt)
            except RateLimited:
                stopped = True
                break
            rows.append({**base, "file": name, "model_format": fmt, **result})
            print(
                f"INFO: {i}/{len(repos)} {repo['repo']} {name} {result['status']}",
                file=sys.stderr,
            )
        if stopped:
            print("WARNING: rate limited; stopped early, rerun later", file=sys.stderr)
            break
        if i % 25 == 0:
            _write(rows)
    _write(rows)
    done = len({r["repo"] for r in rows})
    print(f"FILE={OUT.relative_to(ROOT)} ROWS={len(rows)} REPOS={done}")
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
