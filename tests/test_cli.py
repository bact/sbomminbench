# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI wiring (sbomminbench.__main__) and sbomminbench.console."""

import json
import logging
import re
import signal
from pathlib import Path

import pytest

from sbomminbench import __main__ as cli
from sbomminbench import console, jsonio
from sbomminbench.__main__ import IMPLEMENTED, SUBCOMMANDS, main

ROOT = Path(__file__).parent.parent
CACHE = Path(__file__).parent / "fixtures" / "cache"
STDERR_LINE = re.compile(r"^(ERROR|WARNING|INFO): \S")
KV = re.compile(r"^[A-Z_]+=")


def dirs(tmp_path: Path) -> list[str]:
    return ["--cache-dir", str(CACHE), "--data-dir", str(tmp_path)]


# -- parser -----------------------------------------------------------------------------


@pytest.mark.parametrize("command", [*IMPLEMENTED, *SUBCOMMANDS])
def test_every_subcommand_parses(command: str) -> None:
    argv = (
        [command, "queue.csv", "--reviewer", "r"]
        if command == "review-import"
        else [command]
    )
    args = cli._build_parser().parse_args(argv)  # pylint: disable=protected-access
    assert args.command == command


def test_implemented_and_stub_names_do_not_overlap() -> None:
    assert not set(IMPLEMENTED) & set(SUBCOMMANDS)
    assert {
        "select",
        "fetch",
        "build",
        "export-schema",
        "rederive-diff",
        "export",
        "stats",
        "validate",
        "evaluate",
        "review-export",
        "review-import",
        "audit-sample",
    } == set(IMPLEMENTED)


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_stub_prints_one_error_line_and_exits_two(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([command]) == 2
    out, err = capsys.readouterr()
    assert (out, err) == ("", f"ERROR: {command}: not implemented\n")


def test_select_is_wired_to_select_run_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Offline: select cannot fetch its frame, so it fails inside select.run_cli.
    argv = ["select", "--offline", "--eco", "pypi", *dirs(tmp_path)]
    assert main(argv) == 1
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and err[0].startswith("ERROR: select")


def test_missing_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


# -- build, output rules ----------------------------------------------------------------


def test_build_stdout_is_data_and_stderr_lines_are_tagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", "--eco", "cargo", *dirs(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() and all(KV.match(ln) for ln in out.splitlines())
    assert err.splitlines() and all(STDERR_LINE.match(ln) for ln in err.splitlines())
    assert err.splitlines()[-1].startswith("INFO: build: 5 written, 0 unchanged")


def test_library_warnings_get_one_tag_and_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["stats", *dirs(tmp_path)])  # configures logging
    capsys.readouterr()
    logging.getLogger("sbomminbench").warning("odd value %s", "a\nb\x1b[0m")
    logging.getLogger("sbomminbench.eco").info("fine")
    logging.getLogger("sbomminbench").debug("hidden")
    err = capsys.readouterr().err
    assert err == "WARNING: odd value a\\nb\\x1b[0m\nINFO: fine\n"
    assert logging.getLogger("pitloom").level == logging.ERROR


def test_internal_error_is_one_error_line_with_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(args: object) -> int:
        raise ValueError("bad\nvalue " + "x" * 600)

    monkeypatch.setitem(IMPLEMENTED, "build", (lambda p: None, boom))
    assert main(["build"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and err[0].startswith("ERROR: build: ValueError: bad\\nvalue")
    assert len(err[0]) < 600


def test_ctrl_c_ends_the_process_by_sigint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sent: list[tuple[int, int]] = []
    handlers: list[object] = []

    def interrupted(argv: object = None) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "main", interrupted)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.append((sig, h)))
    with pytest.raises(SystemExit) as exc:
        cli.console_main()
    assert exc.value.code == 130
    assert [s for _, s in sent] == [signal.SIGINT]
    assert handlers == [(signal.SIGINT, signal.SIG_DFL)]
    assert capsys.readouterr().err == "INFO: interrupted\n"


# -- export-schema ------------------------------------------------------------------------


def test_export_schema_equals_the_committed_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "out" / "entry.schema.json"
    target.parent.mkdir()
    assert main(["export-schema", "-o", str(target)]) == 0
    assert target.read_bytes() == (ROOT / "schema" / "entry.schema.json").read_bytes()
    assert capsys.readouterr().out == f"RESULT=written FILE={target}\n"
    assert main(["export-schema", "-o", str(target)]) == 0
    assert capsys.readouterr().out.startswith("RESULT=unchanged")


# -- rederive-diff ------------------------------------------------------------------------


def parse_kv(line: str) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in line.split(" "))


def test_rederive_diff_is_quiet_on_an_unchanged_tree_and_finds_a_tamper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", *dirs(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["rederive-diff", *dirs(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out == "" and err == "INFO: rederive-diff: 36 entries compared, 0 differ\n"

    path = tmp_path / "entries" / "cargo" / "_" / "serde@1.0.210.json"
    data = json.loads(path.read_bytes())
    expected = data["subjects"][0]["expected"]["license"]["value"]
    assert expected["spdx_expression"] == "Apache-2.0 OR MIT"
    expected["spdx_expression"] = "MIT"
    path.write_bytes(jsonio.dumps(data).encode())
    assert main(["rederive-diff", "--eco", "cargo", *dirs(tmp_path)]) == 0
    out, err = capsys.readouterr()
    (line,) = out.splitlines()
    got = parse_kv(line)
    assert got["ID"] == "pkg:cargo/serde@1.0.210"
    assert (got["CHANGE"], got["SUBJECT"], got["ELEMENT"]) == (
        "changed",
        "root",
        "license",
    )
    assert (got["OLD_STATUS"], got["NEW_STATUS"]) == ("auto_unanimous",) * 2
    assert json.loads(got["OLD_VALUE"])["spdx_expression"] == "MIT"
    assert json.loads(got["NEW_VALUE"])["spdx_expression"] == "Apache-2.0 OR MIT"
    assert "5 entries compared, 1 differ" in err


def test_rederive_diff_reports_a_status_change_and_a_new_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", "--eco", "npm", *dirs(tmp_path)]) == 0
    root = tmp_path / "entries" / "npm"
    path = root / "_" / "express@4.21.1.json"
    data = json.loads(path.read_bytes())
    prov = data["subjects"][0]["expected"]["provider"]
    prov.update(status="absent_in_source", value=None, basis=[])
    path.write_bytes(jsonio.dumps(data).encode())
    (root / "_" / "sha.js@2.4.11.json").unlink()
    capsys.readouterr()
    assert main(["rederive-diff", "--eco", "npm", *dirs(tmp_path)]) == 0
    lines = [parse_kv(ln) for ln in capsys.readouterr().out.splitlines()]
    assert [(x["ID"], x["CHANGE"]) for x in lines] == [
        ("pkg:npm/express@4.21.1", "changed"),
        ("pkg:npm/sha.js@2.4.11", "added"),
    ]
    assert (lines[0]["ELEMENT"], lines[0]["OLD_STATUS"], lines[0]["NEW_STATUS"]) == (
        "provider",
        "absent_in_source",
        "auto_unanimous",
    )
    assert " " not in lines[0]["NEW_VALUE"] and "TJ" in lines[0]["NEW_VALUE"]


# -- console helpers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "quoted"),
    [
        ("plain", '"plain"'),
        ("a\nb", '"a\\nb"'),
        ('say "x"', '"say \\"x\\""'),
        ("z\u200bw\x00\x1b", '"z\\u200bw\\x00\\x1b"'),
        (Path("C:\\dir\\f.json"), '"C:\\dir\\f.json"'),
    ],
    ids=["plain", "newline", "quote", "invisible", "windows-path"],
)
def test_quote_keeps_one_line_and_escapes_controls(value: object, quoted: str) -> None:
    assert console.quote(value) == quoted


def test_kv_line_escapes_values_and_keeps_pair_order() -> None:
    assert console.kv_line([("A", "x\ny"), ("B", 3)]) == "A=x\\ny B=3"


@pytest.mark.parametrize(
    ("eco", "expected"),
    [("all", 6), ("pypi", 1)],
)
def test_resolve_ecos(eco: str, expected: int) -> None:
    assert len(console.resolve_ecos(eco)) == expected
