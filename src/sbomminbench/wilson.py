# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""The Wilson score interval (pure standard library).

The one implementation shared by ``review_audit``, ``stats`` and the
standalone ``evaluate`` package, which must not import anything else of
sbomminbench that needs ``pitloom`` or ``licenseid``.
"""

from math import sqrt

Z95 = 1.959964


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Return the Wilson score interval of *k* successes in *n* trials.

    ``(0.0, 1.0)`` when *n* is not positive; the bounds are clamped to [0, 1],
    with 0 exactly for ``k == 0`` and 1 exactly for ``k == n``.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = z * sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    low = 0.0 if k == 0 else max(0.0, centre - half)
    high = 1.0 if k == n else min(1.0, centre + half)
    return (low, high)
