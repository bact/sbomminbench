# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``validate`` V21: ``benchmark.csv`` and ``benchmark-unscored.csv``.

Both files must be what :func:`sbomminbench.benchmark_csv.benchmark_rows`
makes of the final entries in ``release/entries.jsonl``: the same header, the
same ``(id, subject)`` rows and the same cells. The rows come from the one
function ``export`` uses, so the files cannot drift from the entries unnoticed.

See also: ``validate.py`` (driver), ``validate_tree.py`` (V17, ids and counts).
"""

import os
from pathlib import Path

from pydantic import ValidationError

from sbomminbench import console
from sbomminbench.benchmark_csv import (
    COLUMNS,
    FILES,
    UNSCORED_COLUMNS,
    benchmark_rows,
)
from sbomminbench.evaluate.csvio import read_table
from sbomminbench.schema import Entry
from sbomminbench.validate_entry import clip
from sbomminbench.validate_findings import Finding, error

type Row = list[str]


def _release_entries(path: Path) -> list[Entry] | None:
    """The entries of ``entries.jsonl``; ``None`` when unreadable (V17 reports it)."""
    try:
        return [
            Entry.model_validate_json(raw)
            for raw in path.read_bytes().split(b"\n")
            if raw.strip()
        ]
    except (ValidationError, ValueError, OSError):
        return None


def _differences(have: list[Row], want: list[Row], header: list[str]) -> str | None:
    """Describe how the table *have* differs from *want*; ``None`` when equal."""
    keys_have = {(r[0], r[1]) for r in have if len(r) > 1}
    keys_want = {(r[0], r[1]) for r in want}
    if keys_have != keys_want or len(have) != len(want):
        extra = sorted(keys_have - keys_want)[:3]
        missing = sorted(keys_want - keys_have)[:3]
        return (
            f"{len(have)} rows for {len(want)} subjects; not in entries: "
            f"{[console.quote(f'{i} {s}') for i, s in extra]}; missing from "
            f"release: {[console.quote(f'{i} {s}') for i, s in missing]}"
        )
    for got, exp in zip(sorted(have), sorted(want), strict=True):
        if got != exp:
            cols = [h for h, a, b in zip(header, got, exp, strict=False) if a != b]
            return f"{console.quote(got[0])} {got[1]} differs in {', '.join(cols)}"
    return None


def _check_file(
    release: Path, name: str, header: list[str], want: list[Row]
) -> Finding | None:
    path = release / name
    target = f"release/{name}"
    if not os.path.isfile(path):
        return error("V21", target, "missing")
    try:
        got_header, rows = read_table(path)
    except (ValueError, OSError):
        return error("V21", target, "is not a UTF-8 CSV file")
    if got_header != header:
        return error("V21", target, clip(f"header is not {','.join(header)}"))
    if any(len(r) != len(header) for r in rows):
        return error("V21", target, "has a row with the wrong number of cells")
    problem = _differences(rows, want, header)
    return error("V21", target, clip(problem)) if problem else None


def check_benchmark(data_dir: Path) -> list[Finding]:
    """V21: the two benchmark CSV files equal the rows of the release entries."""
    release = data_dir / "release"
    jsonl = release / "entries.jsonl"
    if not os.path.isfile(jsonl):
        return []
    entries = _release_entries(jsonl)
    if entries is None:
        return []
    scored, unscored = benchmark_rows(entries)
    checks = [
        _check_file(release, FILES[0], COLUMNS, scored),
        _check_file(release, FILES[1], UNSCORED_COLUMNS, unscored),
    ]
    return [c for c in checks if c is not None]
