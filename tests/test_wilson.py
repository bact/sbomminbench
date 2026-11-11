# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``sbomminbench.wilson``: the one shared Wilson interval."""

import pytest

from sbomminbench import review, stats
from sbomminbench.evaluate import metrics
from sbomminbench.wilson import wilson

# Computed independently with 40-digit decimal arithmetic at z = 1.959964.
CASES = [
    (0, 0, 0.0, 1.0),
    (0, 10, 0.0, 0.2775328030260577),
    (10, 10, 0.7224671969739422, 1.0),
    (57, 60, 0.8629948343000966, 0.982850497969099),
    (50, 100, 0.40383152963549296, 0.596168470364507),
]


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    CASES,
    ids=["0of0", "0of10", "10of10", "57of60", "50of100"],
)
def test_wilson_matches_hand_computation(
    k: int, n: int, low: float, high: float
) -> None:
    assert wilson(k, n, 1.959964) == pytest.approx((low, high), abs=1e-12)
    assert wilson(k, n) == wilson(k, n, 1.959964)


def test_every_importer_shares_the_one_function() -> None:
    assert stats.wilson is wilson
    assert review.wilson is wilson
    assert metrics.wilson is wilson
