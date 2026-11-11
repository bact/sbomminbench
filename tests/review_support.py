# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Helpers for the review tests: a small ``data/`` tree built from the fixtures."""

import csv
import io
from pathlib import Path
from typing import Any

from sbomminbench.jsonio import read_entry, write_entry
from sbomminbench.review_export import COLUMNS
from sbomminbench.schema import Entry

EXPECTED = Path(__file__).parent / "fixtures" / "expected"
COPIED = (
    "cargo/_/ring@0.16.20.json",  # licence auto_unresolved
    "cargo/_/clap@4.5.20.json",  # provider absent_in_source
    "pypi/_/requests@2.32.3.json",
    "pypi/_/flask@3.0.3.json",
)


def with_expected(entry: Entry, element: str, **fields: Any) -> Entry:
    """Return *entry* with the root *element* answer replaced by ``fields``."""
    data = entry.model_dump(mode="json")
    data["subjects"][0]["expected"][element].update(fields)
    return Entry.model_validate(data)


def conflict_entry(entry: Entry) -> Entry:
    """Return *entry* with an ``auto_conflict`` root licence."""
    return with_expected(
        entry,
        "license",
        status="auto_conflict",
        value=None,
        licenseid=[],
        candidates=["Apache-2.0", "MIT"],
    )


def make_data(tmp_path: Path) -> Path:
    """Copy the fixtures into ``tmp_path/data/entries``; flask gets a conflict."""
    data = tmp_path / "data"
    for rel in COPIED:
        entry = read_entry(EXPECTED / rel)
        if entry.id.startswith("pkg:pypi/flask"):
            entry = conflict_entry(entry)
        write_entry(entry, data / "entries")
    return data


def read_rows(text: str) -> list[dict[str, str]]:
    """Parse queue text (BOM allowed) into rows keyed by column."""
    reader = csv.reader(io.StringIO(text.removeprefix("﻿"), newline=""))
    assert next(reader) == list(COLUMNS)
    return [dict(zip(COLUMNS, r, strict=True)) for r in reader]


def write_rows(path: Path, rows: list[dict[str, str]]) -> Path:
    """Write *rows* as a queue file the way ``review-export`` does."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows([r[c] for c in COLUMNS] for r in rows)
    path.write_bytes(("﻿" + buffer.getvalue()).encode("utf-8"))
    return path


def clone_entry(entry: Entry, name: str) -> Entry:
    """Return a copy of a ``pypi`` *entry* under another name (version 1.0)."""
    data = entry.model_dump(mode="json")
    data["id"] = f"pkg:pypi/{name}@1.0"
    data["platform"].update(name=name, version="1.0")
    return Entry.model_validate(data)
