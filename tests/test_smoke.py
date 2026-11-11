# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys

import pytest

from sbomminbench.__main__ import SUBCOMMANDS, main


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "SBOM-MinBench" in capsys.readouterr().out


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_stub_subcommand_exits_two(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([command]) == 2
    assert "not implemented" in capsys.readouterr().err


def test_import_does_not_load_pitloom_or_licenseid() -> None:
    code = (
        "import sys, sbomminbench; "
        "print(sorted(m for m in ('pitloom', 'licenseid') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.stdout.strip() == "[]"
