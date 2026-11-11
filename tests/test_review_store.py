# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``sbomminbench.review_store``: review files and ``stale_review``."""

import json
from pathlib import Path

import pytest
from review_support import make_data, read_rows, with_expected, write_rows

from sbomminbench.jsonio import write_entry
from sbomminbench.review_common import ReviewError, load_entries
from sbomminbench.review_export import csv_text, queue_rows
from sbomminbench.review_import import import_reviews
from sbomminbench.review_store import read_lines, stale_reviews

AT = "2026-11-09T10:00:00Z"
RING = "pkg:cargo/ring@0.16.20"
REQUESTS = "pkg:pypi/requests@2.32.3"


def _review(tmp_path: Path, purl: str, element: str, **fields: str) -> Path:
    data = make_data(tmp_path)
    rows = read_rows(
        csv_text(
            queue_rows(
                load_entries(data),
                ["auto_unresolved"],
                audit=[(REQUESTS, "root", "license")],
            )
        )
    )
    row = next(r for r in rows if r["id"] == purl and r["element"] == element)
    row.update(fields)
    import_reviews(write_rows(tmp_path / "in.csv", rows), data, "alice", AT)
    return data


def _change(data: Path, purl: str, element: str, **fields: object) -> None:
    entry = next(e for e in load_entries(data) if e.id == purl)
    write_entry(with_expected(entry, element, **fields), data / "entries")


def test_no_reviews_means_nothing_stale(tmp_path: Path) -> None:
    assert stale_reviews(make_data(tmp_path)) == []


def test_unchanged_answer_is_not_stale(tmp_path: Path) -> None:
    data = _review(tmp_path, RING, "license", decision="exclude")
    assert stale_reviews(data) == []


def test_changed_status_is_stale(tmp_path: Path) -> None:
    data = _review(tmp_path, RING, "license", decision="correct", value="MIT")
    _change(
        data,
        RING,
        "license",
        status="auto_unanimous",
        basis=[],
        value={"spdx_expression": "MIT", "has_license_ref": False},
    )
    (stale,) = stale_reviews(data)
    assert stale.key == (RING, "root", "license") and stale.reviewer == "alice"
    assert stale.was["auto_status"] == "auto_unresolved"
    assert stale.now["auto_status"] == "auto_unanimous"


@pytest.mark.parametrize(
    ("new", "stale"),
    [("Apache-2.0", False), ("MIT", True)],
    ids=["same-value", "other-value"],
)
def test_confirmed_value_change_is_stale_only_when_value_differs(
    tmp_path: Path, new: str, stale: bool
) -> None:
    data = _review(tmp_path, REQUESTS, "license", decision="confirm")
    value = {"spdx_expression": new, "has_license_ref": False}
    _change(data, REQUESTS, "license", value=value, note="rebuilt")
    assert bool(stale_reviews(data)) is stale


def test_removed_entry_is_stale(tmp_path: Path) -> None:
    data = _review(tmp_path, RING, "license", decision="exclude")
    next((data / "entries").rglob("ring@*.json")).unlink()
    (stale,) = stale_reviews(data)
    assert stale.now == {}


def _rewrite_first_line(data: Path, **fields: object) -> None:
    path = data / "reviews" / "alice.jsonl"
    row = json.loads(path.read_bytes().splitlines()[0])
    path.write_bytes((json.dumps(row | fields, sort_keys=True) + "\n").encode())


def test_line_carries_the_answer_and_no_sidecar_exists(tmp_path: Path) -> None:
    data = _review(tmp_path, REQUESTS, "license", decision="confirm")
    (line,) = read_lines(data / "reviews" / "alice.jsonl")
    assert line.auto_status == "auto_unanimous"
    assert line.auto_value_at_review is not None
    assert sorted(p.name for p in (data / "reviews").iterdir()) == ["alice.jsonl"]


@pytest.mark.parametrize(
    ("fields", "stale"),
    [
        ({"auto_status": "auto_conflict"}, True),
        ({"auto_value_at_review": None}, True),
        ({}, False),
    ],
    ids=["other-status", "other-value", "unchanged"],
)
def test_stale_compares_the_lines_own_fields(
    tmp_path: Path, fields: dict[str, object], stale: bool
) -> None:
    data = _review(tmp_path, REQUESTS, "license", decision="confirm")
    _rewrite_first_line(data, **fields)
    assert bool(stale_reviews(data)) is stale


def test_line_without_the_auto_answer_is_rejected(tmp_path: Path) -> None:
    data = _review(tmp_path, RING, "license", decision="exclude")
    path = data / "reviews" / "alice.jsonl"
    row = json.loads(path.read_bytes())
    del row["auto_status"]
    path.write_bytes((json.dumps(row) + "\n").encode())
    with pytest.raises(ReviewError, match="needs auto_status"):
        read_lines(path)


def test_a_change_outside_the_answer_is_not_stale(tmp_path: Path) -> None:
    data = _review(tmp_path, RING, "license", decision="exclude")
    _change(data, RING, "license", candidates=[], note="rebuilt")
    assert stale_reviews(data) == []  # the note is not the answer


def test_bad_review_line_is_reported_with_its_position(tmp_path: Path) -> None:
    data = _review(tmp_path, RING, "license", decision="exclude")
    path = data / "reviews" / "alice.jsonl"
    path.write_bytes(path.read_bytes() + b'{"id": 1}\n')
    with pytest.raises(ReviewError, match=r"alice\.jsonl.*:2"):
        read_lines(path)
