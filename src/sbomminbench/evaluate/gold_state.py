# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""How a gold row counts: the one rule the evaluator and ``benchmark.csv`` share.

See also: ``scoring.py`` (uses it per row), ``sbomminbench.benchmark_csv``
(decides which subjects enter ``benchmark.csv`` with it).
"""

from typing import Literal

from sbomminbench.schema import SCORED_STATUSES

type State = Literal["scored", "abstain", "unscored"]


def gold_class(status: str, value: object) -> State:
    """How a gold row counts: scored, abstention or unscored."""
    if status in SCORED_STATUSES and value is not None:
        return "scored"
    if status == "absent_in_source" or (status == "human_confirmed" and value is None):
        return "abstain"
    return "unscored"
