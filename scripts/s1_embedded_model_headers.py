# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Experimental S1 probe: what Pitloom's model readers state about embedded models.

Reads the model files already cached inside package archives (no network) and
writes ``experimental/s1-embedded-model-headers.jsonl``. The output is
non-voting: ``build``, ``validate`` and ``export`` never read it, so it cannot
change a status or the review queue.

Usage: ``uv run python scripts/s1_embedded_model_headers.py`` (needs the
``pitloom[ai]`` extra, which the project pins; without a reader's library the
row says ``library_missing``).
"""

import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from pitloom.extract.ai_model.limits import ModelLimitExceeded
from pitloom.extract.ai_model.reader import read_ai_model

from sbomminbench.archive import MemberTooLarge, read_member
from sbomminbench.jsonio import iter_entries
from sbomminbench.redact import redact_json

ROOT = Path(__file__).resolve().parent.parent
MAX_MODEL_BYTES = 16 * 1024 * 1024
OUT = ROOT / "experimental" / "s1-embedded-model-headers.jsonl"


def _archive_for(cache: Path, filename: str) -> Path | None:
    found = sorted((cache / "artifacts").glob(f"*/{filename}"))
    return found[0] if found else None


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_jsonable(v) for v in value]
        return (
            sorted(items, key=json.dumps)
            if isinstance(value, (set, frozenset))
            else items
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _probe(archive: Path, locator: str) -> dict[str, Any]:
    try:
        data = read_member(archive, locator, max_bytes=MAX_MODEL_BYTES)
    except MemberTooLarge:
        return {"status": "over_cap", "cap_bytes": MAX_MODEL_BYTES}
    if data is None:
        return {"status": "member_absent"}
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / Path(locator).name
        target.write_bytes(data)
        try:
            meta = read_ai_model(target)
        except ImportError as exc:
            return {"status": "library_missing", "error": str(exc)}
        except (ValueError, ModelLimitExceeded) as exc:
            return {"status": "reader_error", "error": type(exc).__name__}
    fields = {k: v for k, v in _jsonable(meta).items() if v not in (None, "", [], {})}
    return {"status": "read", "pitloom_fields": fields}


def _squash(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())


def _name_evidence(row: dict[str, Any], package_name: str | None) -> dict[str, Any]:
    """Compare the header's model name with the tool that wrote it and other names."""
    fields = row.get("pitloom_fields", {})
    header = fields.get("name")
    producer = (fields.get("format_info") or {}).get("framework")
    stem = Path(row["locator"]).stem
    return {
        "header_name": header,
        "producer_name": producer,
        "header_name_contains_producer": bool(
            header and producer and _squash(producer) in _squash(header)
        ),
        "file_stem": stem,
        "header_name_matches_file_stem": bool(
            header and _squash(header) == _squash(stem)
        ),
        "package_name": package_name,
        "file_stem_contains_package_name": bool(
            package_name and _squash(package_name) in _squash(stem)
        ),
    }


def main() -> int:
    cache = ROOT / ".cache"
    rows: list[dict[str, Any]] = []
    for _path, entry in iter_entries(ROOT / "data" / "entries"):
        artifacts = {a.filename: a for a in entry.platform.artifacts if a.fetched}
        for subject in entry.subjects:
            if subject.kind != "model" or not subject.locator:
                continue
            archive = next(
                (p for n in artifacts if (p := _archive_for(cache, n))), None
            )
            row: dict[str, Any] = {
                "entry": entry.id,
                "subject": subject.id,
                "locator": subject.locator,
                "model_format": subject.model_format,
                "size_bytes": subject.size_bytes,
            }
            row.update(
                _probe(archive, subject.locator)
                if archive
                else {"status": "archive_not_cached"}
            )
            if row["status"] == "read":
                root = next(sub for sub in entry.subjects if sub.id == "root")
                name = root.expected.name.value
                row["name_evidence"] = _name_evidence(row, name.name if name else None)
            rows.append(row)
    rows.sort(key=lambda r: (r["entry"], r["subject"]))
    cleaned, _ = redact_json(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "".join(
            json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in cleaned
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"FILE={OUT.relative_to(ROOT)} ROWS={len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
