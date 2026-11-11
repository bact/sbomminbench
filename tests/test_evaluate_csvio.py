# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.evaluate.csvio: benchmark.csv gold and CSV predictions."""

import json
from pathlib import Path
from typing import Any

import pytest
from helpers_evaluate import BOOT, entries, load, perfect, set_expected, variants

from sbomminbench import __main__ as cli
from sbomminbench import benchmark_csv
from sbomminbench.evaluate import metrics, predictions
from sbomminbench.evaluate.predictions import parse_predictions, respell_equivalent
from sbomminbench.schema import Entry


def gold_entries() -> list[Entry]:
    """Scored variants, plus a root with an absent provider and an absent licence."""
    d = load("magika")
    d["id"] = "pkg:pypi/absent@0.6.1"
    set_expected(d, "root", "provider", "absent_in_source")
    set_expected(d, "root", "license", "human_confirmed", None)
    d["subjects"][0]["expected"]["name"]["value"] = {
        "name": "absent",
        "canonical_key": "absent",
        "accepted": ["absent"],
    }
    return [*variants(), Entry.model_validate(d), *entries()[:1]]


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    import csv

    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path


def gold_csv(tmp_path: Path, gold: list[Entry]) -> Path:
    text = benchmark_csv.benchmark_texts(gold)["benchmark.csv"]
    path = tmp_path / "benchmark.csv"
    path.write_bytes(text.encode("utf-8"))
    return path


def pred_csv(
    tmp_path: Path, recs: list[dict[str, Any]], columns: tuple[str, ...] = ()
) -> Path:
    cols = columns or ("id", "subject", "name", "provider", "license")
    rows = [
        [
            "; ".join(r[c]) if c == "provider" and r[c] is not None else r[c] or ""
            for c in cols
        ]
        for r in recs
    ]
    return write_csv(tmp_path / "pred.csv", list(cols), rows)


def groups(result: dict[str, Any], dims: set[str]) -> dict[str, Any]:
    return {
        f"{g['group']}={g['value']}": g["metrics"]
        for g in result["groups"]
        if g["group"] in dims or g["group"] == "all"
    }


def same(csv_result: dict[str, Any], jsonl: dict[str, Any]) -> bool:
    """The CSV result's slices equal the JSONL ones (JSONL also has model rows)."""
    mine, theirs = groups(csv_result, DIMS), groups(jsonl, DIMS)
    return len(mine) >= 4 and mine == {k: theirs[k] for k in mine}


def both(tmp_path: Path, recs: list[dict[str, Any]]) -> tuple[Any, Any]:
    """Evaluate JSONL gold + JSONL preds and CSV gold + CSV preds."""
    gold = gold_entries()
    jsonl = metrics.evaluate_entries(
        gold, parse_predictions(json.dumps(r) for r in recs), n_boot=BOOT
    )
    csv_gold = predictions.load_gold(gold_csv(tmp_path, gold))
    csv_pred = predictions.load_predictions(pred_csv(tmp_path, recs))
    return jsonl, metrics.evaluate_entries(csv_gold, csv_pred, n_boot=BOOT)


def scored_only(gold: list[Entry]) -> list[dict[str, Any]]:
    keep = {(r[0], r[1]) for r in benchmark_csv.benchmark_rows(gold)[0]}
    return [r for r in perfect(gold) if (r["id"], r["subject"]) in keep]


DIMS = {"ecosystem", "kind", "status", "gold"}


def test_perfect_csv_predictions_score_one_and_equal_jsonl(tmp_path: Path) -> None:
    jsonl, csv_result = both(tmp_path, scored_only(gold_entries()))
    assert csv_result["summary"]["rows_scored"] >= 10
    assert csv_result["summary"]["rows_abstain"] == 2
    for block in ("name", "provider", "license"):
        assert metrics_rate(csv_result, block) == 1.0
    assert same(csv_result, jsonl)
    assert csv_result["summary"]["rows_scored"] == jsonl["summary"]["rows_scored"]
    assert csv_result["summary"]["rows_abstain"] == jsonl["summary"]["rows_abstain"]


def metrics_rate(result: dict[str, Any], block: str) -> Any:
    g = next(g for g in result["groups"] if g["group"] == "all")["metrics"][block]
    return g["rates"][
        "strict_accuracy"
        if block == "name"
        else ("strict_f1" if block == "provider" else "accuracy")
    ]["rate"]


def test_unnamed_group_flag_survives_the_csv(tmp_path: Path) -> None:
    gold = gold_entries()
    d = load("magika")
    d["id"] = "pkg:pypi/grp@0.6.1"
    set_expected(
        d,
        "root",
        "provider",
        "auto_unanimous",
        {"entities": [{"name": "Ann", "role": "author"}], "has_unnamed_group": True},
    )
    gold.append(Entry.model_validate(d))
    recs = [r for r in scored_only(gold)]
    next(r for r in recs if r["id"].endswith("grp@0.6.1"))["provider"] = [
        "Ann",
        "Extra",
    ]
    jsonl = metrics.evaluate_entries(
        gold, parse_predictions(json.dumps(r) for r in recs), n_boot=BOOT
    )
    csv_gold = predictions.load_gold(gold_csv(tmp_path, gold))
    csv_pred = predictions.load_predictions(pred_csv(tmp_path, recs))
    result = metrics.evaluate_entries(csv_gold, csv_pred, n_boot=BOOT)
    counts = next(g for g in result["groups"] if g["group"] == "all")["metrics"][
        "provider"
    ]["counts"]
    assert counts["strict_unmatched_under_group"] == 1 and counts["strict_fp"] == 0
    assert same(result, jsonl)


def test_respelled_csv_predictions_equal_jsonl(tmp_path: Path) -> None:
    gold = gold_entries()
    recs = respell_equivalent(scored_only(gold), gold)
    assert recs != scored_only(gold)  # the input changed
    jsonl, csv_result = both(tmp_path, recs)
    assert same(csv_result, jsonl)
    assert metrics_rate(csv_result, "license") == 1.0


def test_misses_and_abstentions_match_jsonl(tmp_path: Path) -> None:
    recs = scored_only(gold_entries())
    victim = next(r for r in recs if r["id"].endswith("absent@0.6.1"))
    victim["name"] = "wrong"  # a wrong value
    victim["provider"] = []  # an empty cell: abstention on an absent provider
    victim["license"] = None  # NOASSERTION-like: abstention on an absent licence
    recs = [r for r in recs if not r["id"].endswith("aa@0.6.1")]  # a missing row
    jsonl, csv_result = both(tmp_path, recs)
    assert same(csv_result, jsonl)
    s = csv_result["summary"]
    assert s["subjects_without_prediction"] == 1
    assert s["rows_not_covered"]["license"] == 1
    block = next(g for g in csv_result["groups"] if g["group"] == "all")["metrics"]
    assert block["abstain_provider"]["rates"]["accuracy"]["rate"] == 1.0
    assert block["abstain_license"]["rates"]["accuracy"]["rate"] == 1.0


def test_absent_column_is_a_miss_but_an_empty_cell_is_an_abstention(
    tmp_path: Path,
) -> None:
    gold = gold_entries()
    recs = scored_only(gold)
    without = metrics.evaluate_entries(
        predictions.load_gold(gold_csv(tmp_path, gold)),
        predictions.load_predictions(
            pred_csv(tmp_path, recs, ("id", "subject", "name"))
        ),
        n_boot=BOOT,
    )
    block = next(g for g in without["groups"] if g["group"] == "all")["metrics"]
    assert block["abstain_provider"]["counts"]["not_covered"] == 1
    assert block["abstain_provider"]["rates"]["accuracy"]["rate"] == 0.0
    assert without["summary"]["rows_not_covered"]["license"] > 0


def test_subject_column_defaults_to_root(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "p.csv", ["id", "name"], [["pkg:pypi/aa@0.6.1", "aa"]])
    preds = predictions.load_predictions(path)
    assert preds.records == {("pkg:pypi/aa@0.6.1", "root"): {"name": "aa"}}


def test_prediction_rows_malformed_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_bytes(
        b"\xef\xbb\xbfid,subject,name\n,root,x\nA,root,1\nA,root,2\nB,root\nC,root,c,d\n"
    )
    preds = predictions.load_predictions(path)
    assert (preds.lines, preds.malformed_lines, preds.duplicates) == (5, 3, 1)
    assert preds.records == {("A", "root"): {"name": "2"}}


def test_provider_cell_splits_on_separator_only(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "p.csv",
        ["id", "provider"],
        [["a", "Ann; Bob;Co"], ["b", ""]],
    )
    recs = predictions.load_predictions(path).records
    assert recs[("a", "root")]["provider"] == ["Ann", "Bob;Co"]
    assert recs[("b", "root")]["provider"] == []


def test_errors(tmp_path: Path) -> None:
    no_id = write_csv(tmp_path / "n.csv", ["name"], [["x"]])
    with pytest.raises(ValueError, match="no id column"):
        predictions.load_predictions(no_id)
    bad = write_csv(tmp_path / "g.csv", ["id", "subject"], [["a", "root"]])
    with pytest.raises(ValueError, match="lacks columns"):
        predictions.load_gold(bad)
    unscored = write_csv(tmp_path / "u.csv", [*benchmark_csv.UNSCORED_COLUMNS], [])
    with pytest.raises(ValueError, match="no gold"):
        predictions.load_gold(unscored)
    ok = gold_csv(tmp_path, gold_entries())
    row = ok.read_text(encoding="utf-8").splitlines()[1]
    ok.write_text(ok.read_text(encoding="utf-8") + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="repeats"):
        predictions.load_gold(ok)


def test_gold_csv_slices_leave_out_unknown_dimensions(tmp_path: Path) -> None:
    gold = predictions.load_gold(gold_csv(tmp_path, gold_entries()))
    result = metrics.evaluate_entries(gold, parse_predictions([]), n_boot=0)
    dims = {g["group"] for g in result["groups"]}
    assert {"all", "gold", "ecosystem", "kind", "status"} <= dims
    assert not dims & {"stratum", "origins", "tag"}
    values = {g["value"] for g in result["groups"] if g["group"] == "auto_status"}
    assert "unknown" not in values


def test_cli_self_test_on_csv_gold_and_csv_predictions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gold = gold_entries()
    path = gold_csv(tmp_path, gold)
    assert cli.main(["evaluate", "--gold", str(path), "--self-test"]) == 0
    capsys.readouterr()
    pred = pred_csv(tmp_path, scored_only(gold))
    code = cli.main(
        ["evaluate", "--gold", str(path), "--pred", str(pred), "--format", "json"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["summary"]["rows_scored"] > 0
