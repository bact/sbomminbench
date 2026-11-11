# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.validate_benchmark (V21)."""

from pathlib import Path

import pytest
from validate_support import hits, make_tree, run

from sbomminbench import export


def exported(tmp_path: Path) -> Path:
    data = make_tree(tmp_path)
    export.export(data)
    return data


def rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text  # the input changes
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def test_v21_clean_after_export(tmp_path: Path) -> None:
    assert not hits(run(exported(tmp_path)), "V21")


def test_v21_not_checked_without_a_release(tmp_path: Path) -> None:
    assert not hits(run(make_tree(tmp_path)), "V21")


@pytest.mark.parametrize("name", ["benchmark.csv", "benchmark-unscored.csv"])
def test_v21_missing_file(tmp_path: Path, name: str) -> None:
    data = exported(tmp_path)
    (data / "release" / name).unlink()
    (found,) = hits(run(data), "V21", "ERROR")
    assert (found.target, found.detail) == (f"release/{name}", "missing")


@pytest.mark.parametrize(
    ("old", "new", "detail"),
    [
        ("Apache-2.0", "MIT", "differs in expected_license"),
        ("auto_unanimous", "human_confirmed", "differs in name_status"),
        ("id,subject", "subject,id", "header is not"),
        ("false", "true", "differs in provider_has_unnamed_group"),
    ],
    ids=["expected-value", "status", "header", "group-flag"],
)
def test_v21_cell_differs(tmp_path: Path, old: str, new: str, detail: str) -> None:
    data = exported(tmp_path)
    rewrite(data / "release" / "benchmark.csv", old, new)
    (found,) = hits(run(data), "V21", "ERROR")
    assert detail in found.detail


def test_v21_extra_and_missing_rows(tmp_path: Path) -> None:
    data = exported(tmp_path)
    path = data / "release" / "benchmark.csv"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_bytes(
        "".join([*lines[:-1], lines[1].replace("root", "model:9")]).encode()
    )
    (found,) = hits(run(data), "V21", "ERROR")
    assert "not in entries" in found.detail and "missing from release" in found.detail


def test_v21_follows_the_release_entries_not_data_entries(tmp_path: Path) -> None:
    data = exported(tmp_path)
    rewrite(
        data / "release" / "entries.jsonl",
        '"spdx_expression":"',
        '"spdx_expression":"MIT AND ',
    )
    assert any("expected_license" in f.detail for f in hits(run(data), "V21", "ERROR"))
