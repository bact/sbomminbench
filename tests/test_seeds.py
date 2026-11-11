# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.seeds."""

from pathlib import Path

import pytest

from sbomminbench.schema import Stratum
from sbomminbench.seeds import (
    Seed,
    append_seeds,
    read_seeds,
    record_failure,
)

HEADER = "name\tversion\tstratum\trank\tnote\n"


def seed(name: str, version: str = "1.0", rank: int | None = 1) -> Seed:
    stratum: Stratum = "top" if rank is not None else "edge"
    return Seed("pypi", name, version, stratum, rank)


def test_read_seeds_skips_comments_and_parses_fields(tmp_path: Path) -> None:
    path = tmp_path / "pypi.tsv"
    path.write_bytes(
        b"\xef\xbb\xbf# comment\r\n"
        + HEADER.encode()
        + b"# another comment\n"
        + b"numpy\t2.1.0\ttop\t1\tfirst\n"
        + b"requests\t2.32.3\tedge\t\tnote with\ttab\n"
        + b"\n"
    )
    assert read_seeds(path, "pypi") == [
        Seed("pypi", "numpy", "2.1.0", "top", 1, "first"),
        Seed("pypi", "requests", "2.32.3", "edge", None, "note with\ttab"),
    ]


def test_read_seeds_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_seeds(tmp_path / "absent.tsv", "pypi") == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("name\tversion\tstratum\trank\n", "header"),
        (HEADER + "numpy\t2.1.0\tmid\n", "expected 5 fields"),
        (HEADER + "numpy\t2.1.0\tbogus\t1\t\n", "unknown stratum"),
        (HEADER + "numpy\t2.1.0\ttop\tx\t\n", "invalid literal"),
    ],
    ids=["bad-header", "short-row", "bad-stratum", "bad-rank"],
)
def test_read_seeds_rejects_malformed(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "pypi.tsv"
    path.write_text(body, encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match=message):
        read_seeds(path, "pypi")


def test_read_seeds_rejects_non_utf8(tmp_path: Path) -> None:
    path = tmp_path / "pypi.tsv"
    path.write_bytes(HEADER.encode() + b"caf\xe9\t1\ttop\t1\t\n")
    with pytest.raises(ValueError, match="not UTF-8"):
        read_seeds(path, "pypi")


def test_append_seeds_skips_known_and_repeated(tmp_path: Path) -> None:
    path = tmp_path / "pypi.tsv"
    assert append_seeds(path, [seed("numpy"), seed("requests", rank=None)]) == 2
    added = append_seeds(
        path, [seed("numpy"), seed("flask"), seed("flask"), seed("numpy", "2.0")]
    )
    assert added == 2
    names = [(s.name, s.version) for s in read_seeds(path, "pypi")]
    assert names == [
        ("numpy", "1.0"),
        ("requests", "1.0"),
        ("flask", "1.0"),
        ("numpy", "2.0"),
    ]
    assert path.read_bytes().startswith(HEADER.encode())
    assert append_seeds(path, [seed("numpy")]) == 0


def test_append_seeds_rejects_tab_in_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tab or newline"):
        append_seeds(tmp_path / "pypi.tsv", [seed("bad\tname")])


def test_record_failure_appends_rows(tmp_path: Path) -> None:
    path = tmp_path / "failed.tsv"
    record_failure(path, "npm", "left-pad", "1.3.0", "fetch", "404\nnot found")
    record_failure(path, "npm", "x", "2.0", "fetch", "timeout")
    lines = path.read_bytes().decode("utf-8").split("\n")
    assert lines[0] == "eco\tname\tversion\tstage\treason\tat"
    assert lines[1] == "npm\tleft-pad\t1.3.0\tfetch\t404 not found\t"
    assert lines[2] == "npm\tx\t2.0\tfetch\ttimeout\t"
    assert lines[3] == ""
    assert b"\r" not in path.read_bytes()


def test_record_failure_rejects_unknown_ecosystem(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown ecosystem"):
        record_failure(tmp_path / "failed.tsv", "crates", "x", "1", "fetch", "r")
