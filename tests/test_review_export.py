# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``sbomminbench.review_export`` (``review-export``)."""

import argparse
from pathlib import Path

import pytest
from review_support import (
    EXPECTED,
    make_data,
    read_rows,
    with_expected,
)

from sbomminbench import review
from sbomminbench.jsonio import read_entry
from sbomminbench.review_common import load_entries
from sbomminbench.review_export import COLUMNS, csv_text, queue_rows, sample_rows
from sbomminbench.schema import Entry


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    review.add_review_export_arguments(parser)
    return parser.parse_args(argv)


def test_queue_holds_only_non_scored_statuses(tmp_path: Path) -> None:
    rows = read_rows(
        csv_text(
            queue_rows(
                load_entries(make_data(tmp_path)), ["auto_conflict", "auto_unresolved"]
            )
        )
    )
    keys = [(r["id"], r["element"], r["auto_status"]) for r in rows]
    assert keys == [
        ("pkg:cargo/ring@0.16.20", "license", "auto_unresolved"),
        ("pkg:pypi/flask@3.0.3", "license", "auto_conflict"),
    ]
    assert rows[1]["candidates"] == "Apache-2.0 || MIT"
    assert rows[1]["auto_value"] == ""  # a conflict has no value
    assert all(r["decision"] == r["value"] == r["note"] == "" for r in rows)


def test_status_choice_adds_absent_in_source(tmp_path: Path) -> None:
    entries = load_entries(make_data(tmp_path))
    rows = read_rows(csv_text(queue_rows(entries, ["absent_in_source"])))
    assert [(r["id"], r["element"]) for r in rows] == [
        ("pkg:cargo/clap@4.5.20", "provider"),
        ("pkg:pypi/flask@3.0.3", "provider"),
    ]


def test_csv_bytes_bom_lf_and_header(tmp_path: Path) -> None:
    data = make_data(tmp_path)
    out = tmp_path / "q.csv"
    assert (
        review.COMMANDS["review-export"][1](
            _args("--data-dir", str(data), "--out", str(out))
        )
        == 0
    )
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf" + ",".join(COLUMNS).encode() + b"\n")
    assert b"\r" not in raw


def test_evidence_escapes_awkward_values(tmp_path: Path) -> None:
    entry = read_entry(EXPECTED / "cargo/_/ring@0.16.20.json")
    data = entry.model_dump(mode="json")
    obs = next(o for o in data["platform"]["observations"] if o["element"] == "license")
    obs.update(raw='Zoë, "Q"\nline2 ' + "x" * 300, native_type="string")
    obs["semantic_format"] = "prose"
    obs["format_validity"] = "not_applicable"
    row = read_rows(
        csv_text(queue_rows([Entry.model_validate(data)], ["auto_unresolved"]))
    )[0]
    line = next(x for x in row["evidence"].split("\n") if x.startswith(obs["source"]))
    raw = 'Zo\u00eb, "Q"\\nline2 ' + "x" * 185
    assert line == f"{obs['source']}:{obs['field_path']} = {raw}"
    assert len(row["evidence"].split("\n")) <= 6


def test_blind_hides_value_and_candidates(tmp_path: Path) -> None:
    entries = load_entries(make_data(tmp_path))
    audit = [("pkg:pypi/requests@2.32.3", "root", "license")]
    shown = read_rows(csv_text(queue_rows(entries, ["auto_conflict"], audit=audit)))
    blind = read_rows(
        csv_text(queue_rows(entries, ["auto_conflict"], blind=True, audit=audit))
    )
    assert {r["auto_value"] for r in shown} == {"", "Apache-2.0"}
    assert shown[0]["candidates"] != ""
    assert all(r["auto_value"] == r["candidates"] == "" for r in blind)
    assert [r["id"] for r in blind] == [r["id"] for r in shown]


def test_audit_rows_are_added_and_ordered(tmp_path: Path) -> None:
    entries = load_entries(make_data(tmp_path))
    audit = [("pkg:pypi/requests@2.32.3", "root", "name")]
    rows = read_rows(csv_text(queue_rows(entries, ["auto_conflict"], audit=audit)))
    assert [(r["id"], r["auto_status"]) for r in rows] == [
        ("pkg:pypi/flask@3.0.3", "auto_conflict"),
        ("pkg:pypi/requests@2.32.3", "auto_unanimous"),
    ]


def test_reversed_input_gives_identical_output(tmp_path: Path) -> None:
    entries = load_entries(make_data(tmp_path))
    statuses = ["auto_conflict", "auto_unresolved", "absent_in_source", "not_assessed"]
    assert len(entries) > 1
    forward = csv_text(queue_rows(entries, statuses))
    assert forward == csv_text(queue_rows(list(reversed(entries)), statuses))
    assert forward == csv_text(queue_rows(entries, reversed(statuses)))


def test_model_subjects_follow_root_numerically(tmp_path: Path) -> None:
    entry = read_entry(EXPECTED / "pypi/_/magika@0.6.1.json")
    assert len(entry.subjects) > 1
    entry = with_expected(
        entry, "license", status="auto_unresolved", value=None, licenseid=[]
    )
    data = entry.model_dump(mode="json")
    for s in data["subjects"][1:]:
        s["expected"]["license"].update(
            status="auto_unresolved", value=None, licenseid=[]
        )
    rows = queue_rows([Entry.model_validate(data)], ["auto_unresolved"])
    assert [r[1] for r in rows] == [s["id"] for s in data["subjects"]]


def test_sample_rows_is_seeded_and_keeps_order(tmp_path: Path) -> None:
    rows = [[str(i)] for i in range(30)]
    one = sample_rows(rows, 7, 5)
    assert one == sample_rows(rows, 7, 5)
    assert one != sample_rows(rows, 7, 6)
    assert one == sorted(one, key=lambda r: int(r[0])) and len(one) == 7
    assert sample_rows(rows, 99, 5) == rows


def test_export_twice_is_byte_identical(tmp_path: Path) -> None:
    data = make_data(tmp_path)
    outs = [tmp_path / "a.csv", tmp_path / "b.csv"]
    for out in outs:
        argv = [
            "--data-dir",
            str(data),
            "--out",
            str(out),
            "--audit",
            "2",
            "--sample",
            "5",
        ]
        assert review.COMMANDS["review-export"][1](_args(*argv)) == 0
    assert outs[0].read_bytes() == outs[1].read_bytes()


@pytest.mark.parametrize("bad", ["auto_unanimous", "", "auto_conflict,nope"])
def test_status_option_rejects_scored_or_unknown(bad: str) -> None:
    with pytest.raises(SystemExit):
        _args("--status", bad)
