# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``sbomminbench.review_audit`` (``audit-sample``, Wilson interval)."""

import argparse
from collections import Counter
from pathlib import Path

import pytest
from review_support import EXPECTED, clone_entry, with_expected

from sbomminbench import review
from sbomminbench.jsonio import read_entry, write_entry
from sbomminbench.review_audit import allocate, audit_frame, audit_sample
from sbomminbench.schema import Entry


def _pypi(count: int, origins: int) -> list[Entry]:
    base = read_entry(EXPECTED / "pypi/_/requests@2.32.3.json")
    out = []
    for i in range(count):
        entry = clone_entry(base, f"pkg-{origins}-{i:02d}")
        out.append(with_expected(entry, "name", independent_origins=origins))
    return out


def _frame() -> list[Entry]:
    cargo = read_entry(EXPECTED / "cargo/_/tokio@1.41.0.json")
    return [*_pypi(12, 1), *_pypi(8, 2), cargo]


def test_allocate_is_proportional_and_exact() -> None:
    sizes = {("a", "1"): 30, ("a", "2+"): 10, ("b", "1"): 10}
    assert allocate(sizes, 10) == {("a", "1"): 6, ("a", "2+"): 2, ("b", "1"): 2}
    got = allocate({("x", "1"): 1, ("y", "1"): 1, ("z", "1"): 1}, 2)
    assert sum(got.values()) == 2
    assert got == {("x", "1"): 1, ("y", "1"): 1, ("z", "1"): 0}  # ties by key
    assert allocate(sizes, 99) == sizes


def test_frame_holds_only_unanimous_root_answers(tmp_path: Path) -> None:
    data = read_entry(EXPECTED / "pypi/_/magika@0.6.1.json").model_dump(mode="json")
    name = dict(data["subjects"][0]["expected"]["name"], basis=[])
    data["subjects"][1]["expected"]["name"] = name  # a unanimous model answer
    magika = Entry.model_validate(data)
    assert magika.subjects[1].expected.name.status == "auto_unanimous"
    ring = read_entry(EXPECTED / "cargo/_/ring@0.16.20.json")
    frame = audit_frame([magika, ring])
    assert frame and all(i.key[1] == "root" for i in frame)
    assert all(i.entry_id == magika.id or i.element != "license" for i in frame)
    root = magika.subjects[0].expected
    unanimous = {
        el
        for el in ("name", "provider", "license")
        if getattr(root, el).status == "auto_unanimous"
    }
    assert {i.element for i in frame if i.entry_id == magika.id} == unanimous
    assert {i.element for i in audit_frame([magika], ["name"])} <= {"name"}


def test_sample_is_stratified_and_proportional() -> None:
    entries = _frame()
    sample = audit_sample(entries, 10, 1, ["name"])
    counts = Counter((i.ecosystem, i.origins) for i in sample)
    # 12 : 8 : 1 of 21 -> 5.7 : 3.8 : 0.5 -> 6, 4, 0 by largest remainder
    assert counts == {("pypi", "1"): 6, ("pypi", "2+"): 4}
    assert len(sample) == 10 and len(set(sample)) == 10


def test_sample_is_seeded_and_order_independent() -> None:
    entries = _frame()
    one = audit_sample(entries, 9, 1, ["name"])
    assert one == audit_sample(entries, 9, 1, ["name"])
    assert one == audit_sample(list(reversed(entries)), 9, 1, ["name"])
    assert one != audit_sample(entries, 9, 2, ["name"])


def test_sample_larger_than_frame_returns_the_frame() -> None:
    entries = _frame()
    frame = audit_frame(entries, ["name"])
    assert audit_sample(entries, 500, 1, ["name"]) == frame


def test_cli_prints_one_line_per_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for entry in _frame():
        write_entry(entry, tmp_path / "entries")
    parser = argparse.ArgumentParser()
    review.add_arguments(parser.add_subparsers(dest="command"))
    argv = ["audit-sample", "--data-dir", str(tmp_path), "--n", "4", "--seed", "3"]
    argv += ["--element", "name"]
    assert review.run_cli(parser.parse_args(argv)) == 0
    first = capsys.readouterr()
    lines = first.out.splitlines()
    assert len(lines) == 4 and all(
        ln.startswith("ID=pkg:") and " SUBJECT=root ELEMENT=name ECO=" in ln
        for ln in lines
    )
    assert first.err.startswith("INFO: audit-sample: 4 of 21 answers, seed 3")
    review.run_cli(parser.parse_args(argv))
    assert capsys.readouterr().out == first.out
    argv[argv.index("4")] = "99"
    assert review.run_cli(parser.parse_args(argv)) == 0
    assert "WARNING: audit-sample: the frame holds 21" in capsys.readouterr().err
