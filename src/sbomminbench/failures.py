# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Record a failed seed once in ``data/seeds/failed.tsv``."""

import os
from pathlib import Path

from sbomminbench.seeds import record_failure


def _flat(text: str) -> str:
    """The form ``seeds.record_failure`` writes for a reason."""
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _known(path: Path) -> set[tuple[str, ...]]:
    if not os.path.isfile(path):
        return set()
    lines = path.read_bytes().decode("utf-8", "replace").split("\n")
    return {tuple(ln.split("\t")[:5]) for ln in lines[1:] if ln}


def record_once(
    path: Path, eco: str, name: str, version: str, stage: str, reason: str
) -> bool:
    """Append the failure unless the same row is already there.

    Returns ``True`` when a row was added. A rerun therefore adds nothing.
    """
    if (eco, name, version, stage, _flat(reason)) in _known(path):
        return False
    record_failure(path, eco, name, version, stage, reason)
    return True
