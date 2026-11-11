# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Shared pieces of the review tooling.

See also: ``review_export``, ``review_import``, ``review_audit``, ``review_store``.
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final, get_args

from sbomminbench import console
from sbomminbench.jsonio import iter_entries
from sbomminbench.schema import (
    Element,
    Entry,
    ExpectedLicense,
    ExpectedName,
    ExpectedProvider,
    LicenseValue,
    NameValue,
    Observation,
    ProviderValue,
    Subject,
)

ELEMENTS: Final[tuple[Element, ...]] = get_args(Element)
NON_SCORED: Final = (
    "absent_in_source",
    "auto_conflict",
    "auto_unresolved",
    "not_assessed",
)
NAME_SEP: Final = " | "
CANDIDATE_SEP: Final = " || "
MAX_EVIDENCE: Final = 6
EVIDENCE_CHARS: Final = 200

type AnyExpected = ExpectedName | ExpectedProvider | ExpectedLicense
type AnyValue = LicenseValue | NameValue | ProviderValue
type RowKey = tuple[str, str, str]  # (entry id, subject id, element)


class ReviewError(ValueError):
    """Review input that cannot be accepted; ``errors`` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"{len(errors)} problem(s): " + "; ".join(errors[:3]))
        self.errors = errors


def expected_of(subject: Subject, element: Element) -> AnyExpected:
    """Return the expected answer of *element* on *subject*."""
    match element:
        case "name":
            return subject.expected.name
        case "provider":
            return subject.expected.provider
        case "license":
            return subject.expected.license


def value_text(value: AnyValue | None) -> str:
    """Return the answer as the reviewer reads it (``""`` for no value)."""
    match value:
        case None:
            return ""
        case LicenseValue():
            return value.spdx_expression
        case NameValue():
            return value.name
        case ProviderValue():
            return NAME_SEP.join(e.name for e in value.entities)


def value_json(value: AnyValue | None) -> object:
    """Return the answer as JSON data (``None`` for no value)."""
    return None if value is None else value.model_dump(mode="json")


def as_element(text: str) -> Element:
    """Return *text* as an element name, or raise :class:`ValueError`."""
    for element in ELEMENTS:
        if element == text:
            return element
    raise ValueError(f"unknown element {text!r}")


def subject_order(subject_id: str) -> tuple[int, int]:
    """Sort key: ``root`` first, then ``model:1..n`` numerically."""
    if subject_id == "root":
        return (0, 0)
    return (1, int(subject_id.partition(":")[2]))


def row_sort_key(key: RowKey) -> tuple[str, tuple[int, int], int]:
    """Sort key of an ``(entry id, subject id, element)`` triple."""
    return key[0], subject_order(key[1]), ELEMENTS.index(key[2])


def subject_observations(entry: Entry, subject: Subject) -> list[Observation]:
    """Return the observations that can vote for *subject*, by ``(source, path)``."""
    pool = list(subject.manifest_observations)
    if subject.id == "root":
        pool += entry.platform.observations
    return sorted(pool, key=lambda o: (o.source, o.field_path))


def _raw_text(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_lines(entry: Entry, subject: Subject, element: Element) -> list[str]:
    """Return up to six ``source:field_path = raw[:200]`` lines, one line each."""
    lines: list[str] = []
    for obs in subject_observations(entry, subject):
        if obs.element != element or not obs.present:
            continue
        text = console.escape(_raw_text(obs.raw)[:EVIDENCE_CHARS])
        lines.append(f"{obs.key} = {text}")
    return lines[:MAX_EVIDENCE]


def load_entries(data_dir: Path) -> list[Entry]:
    """Read every entry under ``<data-dir>/entries``, sorted by path."""
    return [entry for _, entry in iter_entries(data_dir / "entries")]


def element_cells(entries: Iterable[Entry]) -> Iterator[tuple[Entry, Subject, Element]]:
    """Yield every ``(entry, subject, element)`` in a deterministic order."""
    for entry in sorted(entries, key=lambda e: e.id):
        for subject in sorted(entry.subjects, key=lambda s: subject_order(s.id)):
            for element in ELEMENTS:
                yield entry, subject, element
