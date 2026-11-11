# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""CSV input of the evaluator: ``benchmark.csv`` as gold, a CSV as predictions.

Gold: the rows of ``benchmark.csv`` (or ``benchmark-unscored.csv``) become
light entries that carry only what scoring reads. The CSV has no stratum,
challenge tags, independent origins or pre-review status, so those slices are
left out of the result (``stratum`` is ``""``, ``independent_origins`` is
``-1``, a reviewed answer has ``auto_status`` ``"unknown"``), and a name has no
accepted alternative spellings beyond the ecosystem's identity key.

Predictions: a header row with ``id`` and any of ``subject`` (default
``root``), ``name``, ``provider`` (names joined by ``"; "``) and ``license``.
The values mean what the same JSONL line would mean. A column that is absent
is a key that is absent (a miss). In a column that is present, an empty cell is
the empty string, or the empty list for ``provider``: an explicit abstention,
exactly as ``"name": ""`` or ``"provider": []`` is in JSONL. A row that is
missing is a miss. A row with the wrong number of cells, or an empty ``id``, is
malformed and skipped.

See also: ``predictions.py`` (dispatches here by the ``.csv`` extension),
``sbomminbench.benchmark_csv`` (the columns).
"""

import csv
import io
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

from sbomminbench.benchmark_csv import COLUMNS, split_providers
from sbomminbench.evaluate.matching import canonical_key
from sbomminbench.schema import (
    SCORED_STATUSES,
    Ecosystem,
    Entry,
    ExpectedLicense,
    ExpectedName,
    ExpectedProvider,
    ExpectedSet,
    LicenseValue,
    NameValue,
    ProviderEntity,
    ProviderValue,
    Subject,
)

_UTF8_BOM = "﻿"
_AUTO_STATUSES = frozenset(
    {
        "auto_unanimous",
        "auto_conflict",
        "auto_unresolved",
        "absent_in_source",
        "not_assessed",
    }
)
type Record = dict[str, object]


def read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    """Return the header and the data rows of a UTF-8 CSV file."""
    text = path.read_bytes().decode("utf-8").removeprefix(_UTF8_BOM)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    return (rows[0], rows[1:]) if rows else ([], [])


# -- gold ----------------------------------------------------------------------------


def _construct[T](cls: type[T], **fields: object) -> T:
    """Build a model without validation: a light gold holds only what scoring reads."""
    built: T = cast(Any, cls).model_construct(**fields)
    return built


def _expected[T: ExpectedName | ExpectedProvider | ExpectedLicense](
    cls: type[T], status: str, value: object
) -> T:
    reviewed = status not in _AUTO_STATUSES
    return _construct(
        cls,
        status=status,
        value=value,
        independent_origins=-1,
        auto_status="unknown" if reviewed else None,
    )


def _values(eco: Ecosystem, row: dict[str, str]) -> tuple[object, object, object]:
    """The name, provider and licence values of a row (``None`` when blank)."""
    name = row["expected_name"]
    licence = row["expected_license"]
    status = row["provider_status"]
    names = split_providers(row["expected_provider"])
    provider = (
        ProviderValue(
            entities=[ProviderEntity(name=n) for n in names],
            has_unnamed_group=row["provider_has_unnamed_group"] == "true",
        )
        if names or status in SCORED_STATUSES
        else None
    )
    return (
        NameValue(name=name, canonical_key=canonical_key(eco, name), accepted=[name])
        if name
        else None,
        provider,
        LicenseValue(spdx_expression=licence, has_license_ref="LicenseRef-" in licence)
        if licence
        else None,
    )


def _subject(eco: Ecosystem, row: dict[str, str]) -> Subject:
    name, provider, licence = _values(eco, row)
    return _construct(
        Subject,
        id=row["subject"],
        kind=row["kind"],
        challenge_tags=[],
        expected=_construct(
            ExpectedSet,
            name=_expected(ExpectedName, row["name_status"], name),
            provider=_expected(ExpectedProvider, row["provider_status"], provider),
            license=_expected(ExpectedLicense, row["license_status"], licence),
        ),
    )


def _subject_order(sid: str) -> int:
    return 0 if sid == "root" else int(sid.removeprefix("model:") or 0)


def load_gold_csv(path: Path) -> list[Entry]:
    """Read ``benchmark.csv`` as gold entries sorted by id.

    Raises:
        ValueError: a required column is missing, a ``reason`` column is present
            (``benchmark-unscored.csv``), a row has the wrong number of cells, or a
            ``(id, subject)`` pair repeats.
    """
    header, rows = read_table(path)
    if "reason" in header:
        raise ValueError("a benchmark-unscored.csv has no gold; use benchmark.csv")
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise ValueError(f"gold CSV lacks columns: {', '.join(missing)}")
    by_entry: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for n, cells in enumerate(rows, 2):
        if len(cells) != len(header):
            raise ValueError(
                f"gold CSV row {n} has {len(cells)} cells, not {len(header)}"
            )
        row = dict(zip(header, cells, strict=True))
        key = (row["id"], row["subject"])
        if key in seen:
            raise ValueError(f"gold CSV row {n} repeats an id and subject")
        seen.add(key)
        by_entry[row["id"]].append(row)
    entries: list[Entry] = []
    for purl in sorted(by_entry):
        group = sorted(by_entry[purl], key=lambda r: _subject_order(r["subject"]))
        eco = cast(Ecosystem, group[0]["ecosystem"])
        entries.append(
            _construct(
                Entry,
                id=purl,
                ecosystem=eco,
                stratum="",
                subjects=[_subject(eco, r) for r in group],
            )
        )
    return entries


# -- predictions ---------------------------------------------------------------------


def parse_prediction_rows(
    header: list[str], rows: list[list[str]]
) -> tuple[dict[tuple[str, str], Record], int, int, int]:
    """Return ``(records, rows, malformed rows, duplicates)`` of a prediction table.

    Raises:
        ValueError: the header has no ``id`` column.
    """
    if "id" not in header:
        raise ValueError("prediction CSV has no id column")
    records: dict[tuple[str, str], Record] = {}
    bad = dup = 0
    for cells in rows:
        row = dict(zip(header, cells, strict=False))
        if len(cells) != len(header) or not row["id"]:
            bad += 1
            continue
        key = (row["id"], row.get("subject", "root"))
        dup += key in records
        record: Record = {}
        if "name" in row:
            record["name"] = row["name"]
        if "provider" in row:
            record["provider"] = split_providers(row["provider"])
        if "license" in row:
            record["license"] = row["license"]
        records[key] = record
    return records, len(rows), bad, dup
