# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``benchmark.csv``: a flat, JSON-free view of the gold for tool developers.

A derived view of the entries, not a schema: ``entries.jsonl`` stays the
source of truth. One row per subject (entry root or ``model:n``). Columns
(``COLUMNS``): ``id`` (purl, the join key), ``subject``, ``ecosystem``,
``kind``, ``name_input`` (the name segment of the purl: what a tool is given),
``expected_name``, ``expected_provider`` (names joined by ``"; "`` in stored
order), ``provider_has_unnamed_group`` (``true``/``false``: the gold list is
incomplete, so a predicted name outside it is not a false positive),
``expected_license`` (SPDX expression, may hold ``LicenseRef-``) and the
``name_status``, ``provider_status`` and ``license_status`` strings of the
entry, unchanged.

Rule, taken from :func:`sbomminbench.evaluate.gold_state.gold_class` so the
file and the evaluator never disagree: a subject is in ``benchmark.csv`` only
when every element is *scored* (status ``auto_unanimous``, ``human_confirmed``
or ``human_corrected`` with a value) or an explicit *abstention* (status
``absent_in_source``, or ``human_confirmed`` without a value). A blank
``expected_*`` cell therefore means the source states nothing and the status
says so; a scored element is never blank, except a scored provider with no
entity. Every other subject goes to ``benchmark-unscored.csv`` with the same
columns plus ``reason`` (``element=status`` pairs, joined by ``"; "``), and an
unscored element's ``expected_*`` cell is blank. A provider name that holds
``"; "`` cannot be joined without ambiguity: the provider counts as unscored
(``provider=name_contains_separator``).

Rows are sorted by id, then by subject order (``root``, ``model:1``, ...).

See also: ``export.py`` (writes the files), ``validate_benchmark.py`` (V21),
``evaluate/csvio.py`` (reads them back).
"""

from collections.abc import Sequence
from typing import Final

from packageurl import PackageURL

from sbomminbench.evaluate.gold_state import gold_class
from sbomminbench.export_rows import ELEMENTS, csv_text, expected_of
from sbomminbench.schema import (
    Entry,
    LicenseValue,
    NameValue,
    ProviderValue,
    Subject,
)

SEPARATOR: Final = "; "
SEPARATOR_REASON: Final = "provider=name_contains_separator"
COLUMNS: Final = [
    "id",
    "subject",
    "ecosystem",
    "kind",
    "name_input",
    "expected_name",
    "expected_provider",
    "provider_has_unnamed_group",
    "expected_license",
    "name_status",
    "provider_status",
    "license_status",
]
UNSCORED_COLUMNS: Final = [*COLUMNS, "reason"]
FILES: Final = ("benchmark.csv", "benchmark-unscored.csv")


def name_input(purl: str) -> str:
    """The name segment of *purl* (without namespace or version)."""
    return PackageURL.from_string(purl).name


def join_providers(names: Sequence[str]) -> str:
    """Join provider names for a cell."""
    return SEPARATOR.join(names)


def split_providers(cell: str) -> list[str]:
    """Split a provider cell; an empty cell is no provider."""
    return cell.split(SEPARATOR) if cell else []


def _value_text(value: NameValue | ProviderValue | LicenseValue) -> str | None:
    """The cell text of a gold value; ``None`` when a provider cannot be joined."""
    if isinstance(value, NameValue):
        return value.name
    if isinstance(value, LicenseValue):
        return value.spdx_expression
    names = [e.name for e in value.entities]
    return None if any(SEPARATOR in n for n in names) else join_providers(names)


def _expected_cells(subject: Subject) -> tuple[dict[str, str], list[str]]:
    """Return the cells of the expected values and the reasons they are blocked."""
    cells = {element: "" for element in ELEMENTS}
    blocked: list[str] = []
    for element in ELEMENTS:
        exp = expected_of(subject, element)
        if gold_class(exp.status, exp.value) == "unscored":
            blocked.append(f"{element}={exp.status}")
        elif exp.value is not None:
            text = _value_text(exp.value)
            if text is None:
                blocked.append(SEPARATOR_REASON)
            else:
                cells[element] = text
    return cells, blocked


def subject_row(entry: Entry, subject: Subject) -> tuple[list[str], str]:
    """Return the ``COLUMNS`` cells of one subject and its reason ("" = scored).

    The one function behind ``benchmark.csv`` and ``validate`` (V21).
    """
    cells, blocked = _expected_cells(subject)
    provider = subject.expected.provider.value
    ex = subject.expected
    row = [
        entry.id,
        subject.id,
        entry.ecosystem,
        subject.kind,
        name_input(entry.id),
        cells["name"],
        cells["provider"],
        "true" if provider is not None and provider.has_unnamed_group else "false",
        cells["license"],
        ex.name.status,
        ex.provider.status,
        ex.license.status,
    ]
    return row, SEPARATOR.join(blocked)


def benchmark_rows(
    entries: Sequence[Entry],
) -> tuple[list[list[str]], list[list[str]]]:
    """Return the rows of ``benchmark.csv`` and of ``benchmark-unscored.csv``."""
    scored: list[list[str]] = []
    unscored: list[list[str]] = []
    for entry in sorted(entries, key=lambda e: e.id):
        for subject in entry.subjects:
            row, reason = subject_row(entry, subject)
            if reason:
                unscored.append([*row, reason])
            else:
                scored.append(row)
    return scored, unscored


def benchmark_texts(entries: Sequence[Entry]) -> dict[str, str]:
    """Return the text of both files, by file name."""
    scored, unscored = benchmark_rows(entries)
    return {
        FILES[0]: csv_text(COLUMNS, scored),
        FILES[1]: csv_text(UNSCORED_COLUMNS, unscored),
    }
