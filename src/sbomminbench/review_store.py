# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Review files under ``<data-dir>/reviews`` and the ``stale_review`` check.

A reviewer's decisions live in ``<reviewer>.jsonl`` (one compact
:class:`sbomminbench.schema.ReviewLine` per line, in import order). Each line
carries the auto answer it was made against (``auto_status`` and
``auto_value_at_review``); :func:`is_stale` compares them with the current
answer. ``build`` never touches these files.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from sbomminbench import console
from sbomminbench.ids import entry_relpath
from sbomminbench.jsonio import read_entry, write_if_changed
from sbomminbench.review_common import (
    AnyExpected,
    AnyValue,
    ReviewError,
    RowKey,
    as_element,
    expected_of,
    row_sort_key,
)
from sbomminbench.schema import ReviewLine

type AutoRecord = dict[str, object]


@dataclass(frozen=True)
class StaleReview:
    """A review made against an auto answer that has since changed."""

    key: RowKey
    reviewer: str
    was: AutoRecord
    now: AutoRecord


def reviews_dir(data_dir: Path) -> Path:
    return data_dir / "reviews"


def line_text(line: ReviewLine) -> str:
    """Return the compact JSON line (no LF) of one review."""
    data = line.model_dump(mode="json")
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_lines(path: Path) -> list[ReviewLine]:
    """Read the review lines of one file; a bad line raises :class:`ReviewError`."""
    if not os.path.isfile(path):
        return []
    text = path.read_bytes().decode("utf-8")
    lines: list[ReviewLine] = []
    for number, raw in enumerate(text.split("\n"), 1):
        if not raw:
            continue
        try:
            lines.append(ReviewLine.model_validate_json(raw))
        except ValueError as exc:
            where = console.quote(path)
            raise ReviewError([f"{where}:{number}: not a review line ({exc})"]) from exc
    return lines


def write_lines(path: Path, lines: list[ReviewLine]) -> bool:
    """Write *lines* (LF, final newline) unless the bytes are unchanged."""
    return write_if_changed(path, "".join(line_text(x) + "\n" for x in lines))


def auto_record(status: str, value: AnyValue | None) -> AutoRecord:
    """Return an auto answer (status and value) as JSON data."""
    return {
        "auto_status": status,
        "auto_value_at_review": None
        if value is None
        else value.model_dump(mode="json"),
    }


def is_stale(line: ReviewLine, expected: AnyExpected | None) -> bool:
    """Return whether the auto answer changed since *line* was made.

    *expected* is the answer now under the line's cell (``None`` when the
    entry, subject or element is gone, which counts as changed). Only the
    status and the value are compared.
    """
    return (
        expected is None
        or expected.status != line.auto_status
        or expected.value != line.auto_value_at_review
    )


def current_expected(data_dir: Path, key: RowKey) -> AnyExpected | None:
    """Return the answer now under *key* (``None`` when the cell is gone)."""
    path = data_dir / "entries" / entry_relpath(key[0])
    if not os.path.isfile(path):
        return None
    for subject in read_entry(path).subjects:
        if subject.id == key[1]:
            return expected_of(subject, as_element(key[2]))
    return None


def stale_reviews(data_dir: Path) -> list[StaleReview]:
    """Return the latest review per cell whose auto answer changed since."""
    root = reviews_dir(data_dir)
    found: list[StaleReview] = []
    for path in sorted(root.glob("*.jsonl")) if root.is_dir() else []:
        latest = {(x.id, x.subject, x.element): x for x in read_lines(path)}
        for key in sorted(latest, key=row_sort_key):
            line = latest[key]
            expected = current_expected(data_dir, key)
            if is_stale(line, expected):
                was = auto_record(line.auto_status, line.auto_value_at_review)
                now = (
                    {}
                    if expected is None
                    else auto_record(expected.status, expected.value)
                )
                found.append(StaleReview(key, line.reviewer, was, now))
    return found
