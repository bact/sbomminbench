# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.evaluate.cli (the lead wires the subcommand)."""

import argparse
import json
import re
from pathlib import Path

import pytest

from sbomminbench.evaluate import cli

FIXTURES = Path(__file__).parent / "fixtures"
KV = re.compile(r"^[A-Z_]+=\S*( [A-Z_]+=\S*)*( (TITLE|CAVEAT|LICENSE)=.*)?$")
STDERR_LINE = re.compile(r"^(ERROR|WARNING|INFO): \S")


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    cli.add_arguments(parser)
    return parser.parse_args(["--n-boot", "10", *argv])


@pytest.fixture
def gold(tmp_path: Path) -> Path:
    lines = []
    for name in ("magika", "qwen"):
        data = json.loads((FIXTURES / "examples" / f"{name}.json").read_text("utf-8"))
        lines.append(json.dumps(data, sort_keys=True))
    path = tmp_path / "entries.jsonl"
    path.write_bytes(("\n".join(lines) + "\n").encode())
    return path


def test_self_test_passes_and_prints_kv(
    gold: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.run_cli(parse("--gold", str(gold), "--self-test", "--by-standard")) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines
    assert all(KV.match(line) for line in lines), [x for x in lines if not KV.match(x)]
    assert (
        "GROUP=all SLICE=all ELEMENT=name METRIC=strict_accuracy N=2 K=2 RATE=1.000000"
        in out
    )
    assert any(
        line.startswith("GROUP=standard SLICE=cisa2026:component-name ")
        for line in lines
    )
    assert any(
        "METRIC=no_element TITLE=NTIA 2021: no element" in line for line in lines
    )
    assert all(STDERR_LINE.match(x) for x in err.splitlines())
    assert "self-test passed" in err


def test_self_test_fails_when_a_rate_is_below_one(
    gold: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sbomminbench.evaluate import predictions

    real = predictions.predictions_from_gold

    def broken(entries: object) -> list[dict[str, object]]:
        recs = real(entries)  # type: ignore[arg-type]
        recs[0]["name"] = "changed"
        return recs

    monkeypatch.setattr(predictions, "predictions_from_gold", broken)
    assert cli.run_cli(parse("--gold", str(gold), "--self-test")) == 1
    assert "ERROR: evaluate: self-test below 1.0" in capsys.readouterr().err


def test_sample_predictions_json_output(
    gold: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pred = FIXTURES / "predictions" / "sample.jsonl"
    assert (
        cli.run_cli(parse("--gold", str(gold), "--pred", str(pred), "--format", "json"))
        == 0
    )
    out, err = capsys.readouterr()
    result = json.loads(out)
    assert result["summary"]["prediction_malformed_lines"] == 1
    assert "WARNING: evaluate: 1 malformed prediction lines" in err
    root = next(
        g for g in result["groups"] if (g["group"], g["value"]) == ("all", "all")
    )
    assert root["metrics"]["name"]["rates"]["strict_accuracy"]["rate"] == 0.5
    assert root["metrics"]["license"]["counts"]["equivalent"] == 1
    assert root["metrics"]["provider"]["rates"]["strict_precision"]["k"] == 2  # tp


def test_output_is_deterministic(
    gold: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pred = FIXTURES / "predictions" / "sample.jsonl"
    runs = []
    for _ in range(2):
        cli.run_cli(
            parse(
                "--gold",
                str(gold),
                "--pred",
                str(pred),
                "--format",
                "json",
                "--by-standard",
            )
        )
        runs.append(capsys.readouterr().out)
    assert runs[0] == runs[1]


def test_markdown_format(gold: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.run_cli(parse("--gold", str(gold), "--self-test", "--format", "markdown"))
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("| group |")
    table = [x for x in lines if x]
    assert all(line.startswith("|") for line in table)


def test_pred_required_without_self_test(
    gold: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.run_cli(parse("--gold", str(gold))) == 2
    assert capsys.readouterr().err.startswith("ERROR: evaluate: --pred is required")


def test_gold_directory_and_empty_gold_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.run_cli(parse("--gold", str(empty), "--self-test")) == 0
    assert "WARNING: evaluate: no scored rows" in capsys.readouterr().err
    entries = tmp_path / "entries"
    entries.mkdir()
    (entries / "m.json").write_bytes(
        (FIXTURES / "examples" / "magika.json").read_bytes()
    )
    assert cli.run_cli(parse("--gold", str(entries), "--self-test")) == 0


def test_real_pilot_data_self_test_when_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pilot = Path(__file__).parent.parent / ".pilot" / "data" / "entries"
    if not pilot.is_dir():
        pytest.skip("no .pilot data")
    assert cli.run_cli(parse("--gold", str(pilot), "--self-test", "--by-standard")) == 0
    capsys.readouterr()


def run_out(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.run_cli(parse(*argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_markdown_by_standard_keeps_standard_rows_with_fit_and_caveat(
    gold: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ("--gold", str(gold), "--self-test", "--by-standard")
    _, kv, _ = run_out(capsys, *args)
    _, md, err = run_out(capsys, *args, "--format", "markdown")
    kv_rows = [x for x in kv.splitlines() if x.startswith("GROUP=standard ")]
    kv_metric_rows = [x for x in kv_rows if "METRIC=no_element" not in x]
    standard = [x for x in md.splitlines() if x.startswith("| standard |")]
    assert standard
    assert len(standard) == len([x for x in kv_metric_rows if "METRIC=caveat" not in x])
    header = md.splitlines()[0]
    assert header.endswith("| fit | caveat |")
    assert any("no_element" in x for x in kv_rows)
    assert not any("no_element" in x for x in md.splitlines())
    row = next(x for x in standard if "fsct2024:concluded-license" in x)
    assert "| close |" in row
    assert "declared licence" in row
    assert "relabelled element scores, not compliance" in md
    assert "relabelled element scores, not compliance" in err


def test_kv_has_coverage_caveat_and_majority_lines(
    gold: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = run_out(capsys, "--gold", str(gold), "--self-test", "--by-standard")
    lines = out.splitlines()
    assert (
        "GROUP=all SLICE=all ELEMENT=license METRIC=coverage GOLD_ROWS=3 SCORED=1 "
        "ABSTENTION=0 UNSCORED=2 NOT_COVERED=0 UNSCORED_BY=auto_unresolved:1,not_assessed:1"
    ) in lines
    assert any(
        x.startswith("GROUP=standard SLICE=g7ai2026:model-producer ELEMENT=provider")
        and "METRIC=caveat CAVEAT=" in x
        and "hub owner" in x
        for x in lines
    )
    assert any("METRIC=majority_license_share" in x and " LICENSE=" in x for x in lines)


def test_empty_predictions_warn_and_score_zero(
    gold: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    code, out, err = run_out(capsys, "--gold", str(gold), "--pred", str(empty))
    assert code == 0
    assert "WARNING: evaluate: 3 gold subjects have no prediction line" in err
    assert "METRIC=strict_accuracy N=2 K=0 RATE=0.000000" in out


def test_self_test_fails_when_a_respelling_scores_below_one(
    gold: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sbomminbench.evaluate import predictions

    def broken(
        records: list[dict[str, object]], entries: object
    ) -> list[dict[str, object]]:
        return [{**r, "provider": ["Somebody Else"]} for r in records]

    monkeypatch.setattr(predictions, "respell_equivalent", broken)
    code, _, err = run_out(capsys, "--gold", str(gold), "--self-test")
    assert code == 1
    assert "respelled predictions below 1.0" in err
