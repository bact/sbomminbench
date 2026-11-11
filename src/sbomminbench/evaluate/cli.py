# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``sbomminbench evaluate``: score predictions against the gold entries.

Stdout carries the result (``KEY=VALUE`` lines, JSON or a Markdown table);
summaries go to stderr as ``INFO:``. The imports are the same as
:mod:`sbomminbench.evaluate.metrics`: never ``licenseid`` or ``pitloom``.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sbomminbench import console
from sbomminbench.evaluate import metrics, predictions, render
from sbomminbench.evaluate.caveats import NOT_COMPLIANCE
from sbomminbench.evaluate.predictions import Predictions
from sbomminbench.schema import Entry

DEFAULT_GOLD = Path("data") / "release" / "entries.jsonl"
FORMATS = ("kv", "json", "markdown")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``evaluate`` options to *parser*."""
    parser.add_argument(
        "--gold",
        type=Path,
        default=DEFAULT_GOLD,
        help=(
            "entries JSONL (export output), a directory of entry files, or "
            "benchmark.csv (selected by the .csv extension)"
        ),
    )
    parser.add_argument(
        "--pred",
        type=Path,
        help=(
            "predictions, JSONL (one object per subject) or CSV (a .csv file, one "
            "row per subject, provider names joined by '; '): id (must equal the "
            "gold purl exactly), subject (default root), name, provider, license. "
            "A missing row, line, key or column is a miss, never an abstention; "
            "in a CSV column that is present an empty cell is an abstention"
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "score the gold values as predictions, then a respelled copy (licence "
            "operands reversed, names and providers re-cased); exit 1 unless "
            "every rate is 1.0 (licence exact excepted on the copy)"
        ),
    )
    parser.add_argument(
        "--by-standard", action="store_true", help="per standard element"
    )
    parser.add_argument("--format", choices=FORMATS, default="kv")
    parser.add_argument("--seed", type=int, default=metrics.DEFAULT_SEED)
    parser.add_argument("--n-boot", type=int, default=metrics.DEFAULT_BOOT)


def _perfect(result: dict[str, Any]) -> list[str]:
    """Paths of rates below 1.0 (rates with n=0 are not judged)."""
    return [
        path
        for path, r in metrics.iter_rates(result)
        if r["rate"] is not None and r["rate"] < 1.0
    ]


def _emit(result: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        sys.stdout.write(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    elif fmt == "markdown":
        sys.stdout.write(render.markdown(result))
    else:
        for pairs in render.kv_lines(result):
            console.print_kv(*pairs)


def _below_one(result: dict[str, Any], *, respelled: bool = False) -> list[str]:
    """Paths of rates below 1.0; respelled predictions may differ in ``exact``."""
    paths = _perfect(result)
    return [p for p in paths if not (respelled and p.endswith("/exact"))]


def _self_test(
    entries: list[Entry], args: argparse.Namespace
) -> tuple[Predictions, list[str]]:
    """Gold as predictions, and the pass-two paths below 1.0 of the respelled copy."""
    records = predictions.predictions_from_gold(entries)
    exact = predictions.parse_predictions(json.dumps(r) for r in records)
    spelled = predictions.parse_predictions(
        json.dumps(r) for r in predictions.respell_equivalent(records, entries)
    )
    second = metrics.evaluate_entries(entries, spelled, seed=args.seed, n_boot=0)
    return exact, _below_one(second, respelled=True)


def _warn(s: dict[str, Any]) -> None:
    if s["rows_scored"] == 0:
        console.warning("evaluate: no scored rows; every rate is undefined")
    if s["prediction_malformed_lines"] or s["prediction_unmatched"]:
        console.warning(
            f"evaluate: {s['prediction_malformed_lines']} malformed prediction lines "
            f"skipped, {s['prediction_unmatched']} predictions match no gold subject"
        )
    missing = sum(s["rows_not_covered"].values())
    if s["subjects_without_prediction"] or missing:
        console.warning(
            f"evaluate: {s['subjects_without_prediction']} gold subjects have no "
            f"prediction line, {missing} scored or abstention rows lack a prediction; "
            "they count as misses and are not credited as abstentions"
        )


def run_cli(args: argparse.Namespace) -> int:
    """Evaluate and print; exit 1 when ``--self-test`` finds a rate below 1.0."""
    if args.pred is None and not args.self_test:
        console.error("evaluate: --pred is required unless --self-test is given")
        return 2
    entries = predictions.load_gold(args.gold)
    respelled_below: list[str] = []
    if args.self_test:
        preds, respelled_below = _self_test(entries, args)
    else:
        preds = predictions.load_predictions(args.pred)
    result = metrics.evaluate_entries(
        entries,
        preds,
        seed=args.seed,
        n_boot=args.n_boot,
        by_standard=args.by_standard,
    )
    _emit(result, args.format)
    s = result["summary"]
    console.info(
        f"evaluate: {s['entries']} entries, {s['rows_scored']} scored rows, "
        f"{s['rows_abstain']} abstention rows, "
        f"{sum(s['rows_unscored'].values())} rows not scored"
    )
    if args.by_standard:
        console.info(f"evaluate: per-standard rows are {NOT_COMPLIANCE}")
    _warn(s)
    if args.self_test:
        below = _below_one(result)
        if below or respelled_below:
            console.error(
                f"evaluate: self-test below 1.0 at {len(below)} rates, first "
                f"{(below or respelled_below)[0]}; respelled predictions below 1.0 "
                f"at {len(respelled_below)} rates"
            )
            return 1
        console.info(
            "evaluate: self-test passed, every rate is 1.0 (also on respelled "
            "predictions)"
        )
    return 0
