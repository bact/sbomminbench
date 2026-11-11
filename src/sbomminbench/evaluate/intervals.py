# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Confidence intervals: Wilson for per-row rates, a row bootstrap for set metrics.

See also: ``metrics.py`` (the blocks that use them), ``scoring.py``.
"""

import random
from collections.abc import Sequence
from typing import Any

from sbomminbench.evaluate.scoring import Score
from sbomminbench.wilson import wilson

MIN_BOOT_ROWS = 30  # a slice with fewer rows gets no bootstrap interval
SIDES = ("strict", "lenient")
SET_METRICS = ("precision", "recall", "f1", "set_exact")

type Interval = tuple[float, float] | None
type RowCounts = tuple[int, int, int, int]  # tp, fp, fn, set-exact (0 or 1)


def rate(k: int, n: int) -> dict[str, Any]:
    """A proportion with its Wilson interval; ``None`` values when ``n`` is 0."""
    if n <= 0:
        return {"k": k, "n": n, "rate": None, "low": None, "high": None}
    low, high = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n, "low": low, "high": high}


def set_rate(k: int | None, n: int | None, value: float | None, ci: Interval) -> Any:
    """A set metric: its point value and the bootstrap interval (or ``None``)."""
    has = value is not None and ci is not None
    return {
        "k": k,
        "n": n,
        "rate": value,
        "low": ci[0] if ci and has else None,
        "high": ci[1] if ci and has else None,
    }


def f1(tp: int, fp: int, fn: int) -> float | None:
    """Micro F1, ``None`` when nothing is predicted and nothing is expected."""
    denominator = 2 * tp + fp + fn
    return None if denominator == 0 else 2 * tp / denominator


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def set_values(
    tp: int, fp: int, fn: int, exact: int, n: int
) -> dict[str, float | None]:
    """Precision, recall, F1 and set-exact from pooled counts over ``n`` rows."""
    return {
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": f1(tp, fp, fn),
        "set_exact": _ratio(exact, n),
    }


def row_counts(row: Score, side: int) -> RowCounts:
    """The pooled-count contribution of one provider row."""
    tp, fp, fn = row.provider[side]
    return (tp, fp, fn, int(fp == 0 and fn == 0))


def _percentile(values: list[float]) -> Interval:
    if not values:
        return None
    values.sort()
    last = len(values) - 1
    return (values[round(0.025 * last)], values[round(0.975 * last)])


def bootstrap_set_metrics(
    rows: Sequence[Score], seed: int, n_boot: int
) -> dict[str, Interval]:
    """Seeded percentile 95% intervals, resampling rows, for every set metric.

    Keys are ``<side>_<metric>``. ``None`` below :data:`MIN_BOOT_ROWS` rows or
    with ``n_boot`` <= 0. A resample whose metric has a zero denominator is
    dropped for that metric, never counted as 0.0.
    """
    keys = [f"{s}_{m}" for s in SIDES for m in SET_METRICS]
    n = len(rows)
    if n < MIN_BOOT_ROWS or n_boot <= 0:
        return dict.fromkeys(keys)
    table = [[row_counts(r, side) for r in rows] for side in range(2)]
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {k: [] for k in keys}
    for _ in range(n_boot):
        pick = rng.choices(range(n), k=n)
        for side, label in enumerate(SIDES):
            sample = [table[side][i] for i in pick]
            tp, fp, fn, exact = (sum(c[j] for c in sample) for j in range(4))
            for metric, value in set_values(tp, fp, fn, exact, n).items():
                if value is not None:
                    draws[f"{label}_{metric}"].append(value)
    return {k: _percentile(v) for k, v in draws.items()}
