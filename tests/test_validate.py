# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.validate: the driver, the output and the clean tree."""

import argparse
from pathlib import Path

import pytest
from validate_support import (
    BOM,
    EXPECTED,
    PACKAGING,
    SCHEMA,
    edit,
    hits,
    make_tree,
    run,
)

from sbomminbench import validate


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    validate.add_arguments(parser)
    return parser.parse_args(list(argv))


def test_every_fixture_entry_is_clean(tmp_path: Path) -> None:
    names = tuple(
        p.relative_to(EXPECTED).as_posix() for p in sorted(EXPECTED.rglob("*.json"))
    )
    report = run(make_tree(tmp_path, names))
    assert len(report.entries) == len(names)
    assert [f for f in report.findings if f.severity == "ERROR"] == []
    # The only finding is the missing cache (V15), a WARN.
    assert {f.check for f in report.findings} == {"V15"}


def test_cli_clean_exit_zero_and_summary_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = make_tree(tmp_path)
    args = parse("--data-dir", str(data), "--cache-dir", str(tmp_path / "c"))
    args.schema, args.bom_dir = SCHEMA, BOM
    assert validate.run_cli(args) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "CHECK=V15 SEVERITY=WARN ID=- DETAIL=no cache directory: determinism not checked"
    ]
    assert "INFO: validate: 4 entries, 0 errors, 1 warnings" in err


def test_cli_error_exit_one_with_one_line_per_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = make_tree(tmp_path)
    edit(data, PACKAGING, lambda d: d.update(extra="line one\nline two"))
    args = parse("--data-dir", str(data), "--cache-dir", str(tmp_path / "c"))
    args.schema, args.bom_dir = SCHEMA, BOM
    assert validate.run_cli(args) == 1
    out, err = capsys.readouterr()
    lines = [ln for ln in out.splitlines() if "SEVERITY=ERROR" in ln]
    assert len(lines) == 1
    assert lines[0].startswith(
        "CHECK=V01 SEVERITY=ERROR ID=pkg:pypi/packaging@26.3 DETAIL="
    )
    assert "ERROR: validate: 1 errors" in err
    assert all(
        ln.startswith(("INFO: ", "ERROR: ", "WARNING: ")) for ln in err.splitlines()
    )


def test_missing_entries_directory_is_an_error(tmp_path: Path) -> None:
    report = run(tmp_path / "empty")
    assert [f.check for f in report.findings] == ["V01"]
    assert report.errors == 1


def test_findings_are_sorted_and_deterministic(tmp_path: Path) -> None:
    data = make_tree(tmp_path)
    edit(data, PACKAGING, lambda d: d.update(extra=1))
    first, second = run(data).findings, run(data).findings
    assert first == second
    assert first == sorted(
        first, key=lambda f: (f.check, f.target, f.severity, f.detail)
    )
    assert hits(run(data), "V01")
