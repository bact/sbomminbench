# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Overlay of human reviews on auto answers.

Reviews live in ``<data-dir>/reviews/<reviewer>.jsonl``, one ``ReviewLine`` per
line. The latest line per ``(id, subject, element)`` wins: sorted by ``at``,
ties by file name and line order. ``confirm`` gives ``human_confirmed`` (the
auto value stays), ``correct`` gives ``human_corrected`` and ``exclude`` gives
``excluded``. The auto status moves to ``auto_status``. A stale review (the
auto answer differs from the line's ``auto_status``/``auto_value_at_review``)
is skipped with a ``WARNING:``.

See also: ``export.py`` (calls this), ``export_rows.py``.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from sbomminbench import console, jsonio
from sbomminbench.export_rows import ELEMENTS, expected_of
from sbomminbench.review_store import is_stale
from sbomminbench.schema import (
    Entry,
    ExpectedLicense,
    ExpectedName,
    ExpectedProvider,
    ReviewLine,
)

type ReviewKey = tuple[str, str, str]
type Expected = ExpectedName | ExpectedProvider | ExpectedLicense
_UTF8_BOM = b"\xef\xbb\xbf"
_NO_CONFIRM = frozenset({"auto_conflict", "auto_unresolved", "not_assessed"})
_AUTO = frozenset(
    {
        "auto_unanimous",
        "auto_conflict",
        "auto_unresolved",
        "absent_in_source",
        "not_assessed",
    }
)


@dataclass
class Overlay:
    """The outcome of applying reviews to a set of entries."""

    applied: int = 0
    skipped: int = 0
    used: set[ReviewKey] = field(default_factory=set)


def read_review_lines(reviews_dir: Path) -> list[ReviewLine]:
    """Return every review line, in file-name then line order.

    Raises:
        ValueError: a line is not a valid ``ReviewLine`` (names file and line).
    """
    if not os.path.isdir(reviews_dir):
        return []
    lines: list[ReviewLine] = []
    for path in sorted(reviews_dir.glob("*.jsonl"), key=lambda p: p.name):
        data = path.read_bytes().removeprefix(_UTF8_BOM)
        for number, raw in enumerate(data.splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                lines.append(ReviewLine.model_validate_json(raw))
            except ValidationError as exc:
                raise ValueError(
                    f"{console.quote(path.name)} line {number}: invalid review line"
                ) from exc
    return lines


def latest_reviews(lines: list[ReviewLine]) -> dict[ReviewKey, ReviewLine]:
    """Return the winning line per ``(id, subject, element)``."""
    winners: dict[ReviewKey, ReviewLine] = {}
    for line in sorted(lines, key=lambda r: r.at):  # stable: file order on ties
        winners[(line.id, line.subject, line.element)] = line
    return winners


def _reviewed(exp: Expected, line: ReviewLine) -> Expected | None:
    """Return ``exp`` after ``line``, or ``None`` when the review cannot apply."""
    status = exp.status
    if status not in _AUTO or (line.decision == "confirm" and status in _NO_CONFIRM):
        return None
    data = exp.model_dump()
    data.update(auto_status=status, reviewer=line.reviewer, reviewed_at=line.at)
    if line.note is not None:
        data["note"] = line.note
    match line.decision:
        case "confirm":
            data["status"] = "human_confirmed"
        case "correct":
            data["status"] = "human_corrected"
            data["value"] = line.value.model_dump() if line.value else None
        case "exclude":
            data["status"] = "excluded"
            data["value"] = None
    return type(exp).model_validate(data)


def overlay_entry(
    entry: Entry, reviews: dict[ReviewKey, ReviewLine], result: Overlay
) -> Entry:
    """Return a copy of ``entry`` with the matching reviews applied."""
    out = entry.model_copy(deep=True)
    for subject in out.subjects:
        for element in ELEMENTS:
            key = (out.id, subject.id, element)
            line = reviews.get(key)
            if line is None:
                continue
            result.used.add(key)
            old = expected_of(subject, element)
            if is_stale(line, old):
                result.skipped += 1
                console.warning(
                    f"review ignored for {console.quote(out.id)} {subject.id} "
                    f"{element}: stale_review, the auto answer changed since"
                )
                continue
            new = _reviewed(old, line)
            if new is None:
                result.skipped += 1
                console.warning(
                    f"review ignored for {console.quote(out.id)} {subject.id} "
                    f"{element}: {line.decision} does not apply to {old.status}"
                )
                continue
            setattr(subject.expected, element, new)
            result.applied += 1
    return out


def load_final(data_dir: Path) -> tuple[list[Entry], Overlay]:
    """Return the entries of ``data_dir`` sorted by id, reviews applied.

    Raises:
        ValueError: a review file holds an invalid line.
    """
    reviews = latest_reviews(read_review_lines(data_dir / "reviews"))
    result = Overlay()
    entries = [
        overlay_entry(entry, reviews, result)
        for _, entry in jsonio.iter_entries(data_dir / "entries")
    ]
    entries.sort(key=lambda e: e.id)
    missing = len(reviews) - len(result.used)
    if missing:
        console.warning(f"{missing} review lines match no entry subject; ignored")
    return entries, result
