# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Seed lists (``data/seeds/<eco>.tsv``) and the failure log (``failed.tsv``).

Both are UTF-8 tab-separated files. Lines starting with ``#`` are ignored.
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast, get_args

from sbomminbench.schema import Ecosystem, Stratum

SEED_HEADER = ("name", "version", "stratum", "rank", "note")
FAILURE_HEADER = ("eco", "name", "version", "stage", "reason", "at")
_STRATA: frozenset[str] = frozenset(get_args(Stratum))
_ECOSYSTEMS: frozenset[str] = frozenset(get_args(Ecosystem))


@dataclass(frozen=True)
class Seed:
    eco: Ecosystem
    name: str
    version: str
    stratum: Stratum
    rank: int | None
    note: str = ""


def _rows(path: Path, header: tuple[str, ...]) -> list[list[str]]:
    """Return the data rows of ``path`` (header checked, comments dropped).

    A missing file has no rows. Each row keeps its ``len(header)`` fields; the
    last field may contain tabs.
    """
    if not os.path.exists(path):
        return []
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not UTF-8 ({exc.reason})") from exc
    numbered = [
        (lineno, ln.rstrip("\r")) for lineno, ln in enumerate(text.split("\n"), start=1)
    ]
    numbered = [(n, ln) for n, ln in numbered if ln and not ln.startswith("#")]
    if not numbered:
        return []
    expected = "\t".join(header)
    if numbered[0][1] != expected:
        raise ValueError(f"{path}: header must be {expected!r}")
    rows: list[list[str]] = []
    for lineno, line in numbered[1:]:
        fields = line.split("\t", len(header) - 1)
        if len(fields) != len(header):
            raise ValueError(f"{path}:{lineno}: expected {len(header)} fields")
        rows.append(fields)
    return rows


def _check_field(value: str, what: str) -> None:
    if "\t" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{what} must not contain tab or newline: {value!r}")


def read_seeds(path: Path, eco: Ecosystem) -> list[Seed]:
    """Read the seeds of ecosystem ``eco`` from ``path``, in file order."""
    seeds: list[Seed] = []
    for name, version, stratum, rank, note in _rows(path, SEED_HEADER):
        if stratum not in _STRATA:
            raise ValueError(f"{path}: unknown stratum {stratum!r} for {name!r}")
        seeds.append(
            Seed(
                eco=eco,
                name=name,
                version=version,
                stratum=cast(Stratum, stratum),
                rank=int(rank) if rank else None,
                note=note,
            )
        )
    return seeds


def append_seeds(path: Path, seeds: Iterable[Seed]) -> int:
    """Append seeds whose ``(name, version)`` is not yet in ``path``.

    A seed repeated in ``seeds`` is added once. Writes the header when the file
    is new. Returns the number of rows added.
    """
    known = {(name, version) for name, version, *_ in _rows(path, SEED_HEADER)}
    lines: list[str] = []
    for seed in seeds:
        key = (seed.name, seed.version)
        if key in known:
            continue
        known.add(key)
        for what, value in (
            ("name", seed.name),
            ("version", seed.version),
            ("note", seed.note),
        ):
            _check_field(value, what)
        rank = "" if seed.rank is None else str(seed.rank)
        lines.append(
            f"{seed.name}\t{seed.version}\t{seed.stratum}\t{rank}\t{seed.note}"
        )
    if lines:
        _append(path, SEED_HEADER, lines)
    return len(lines)


def record_failure(
    path: Path, eco: str, name: str, version: str, stage: str, reason: str
) -> None:
    """Append one failed seed to ``path``.

    Tabs and line breaks in ``reason`` become spaces, so each failure is one row.
    The ``at`` column is left empty: a timestamp from the clock would break the
    determinism of ``data/``.
    """
    if eco not in _ECOSYSTEMS:
        raise ValueError(f"unknown ecosystem {eco!r}")
    for what, value in (("name", name), ("version", version), ("stage", stage)):
        _check_field(value, what)
    flat = reason.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    row = f"{eco}\t{name}\t{version}\t{stage}\t{flat}\t"
    _append(path, FAILURE_HEADER, [row])


def _append(path: Path, header: tuple[str, ...], lines: list[str]) -> None:
    """Append ``lines`` to ``path``, writing ``header`` first if the file is empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not os.path.exists(path) or os.path.getsize(path) == 0
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        if fresh:
            fh.write("\t".join(header) + "\n")
        for line in lines:
            fh.write(line + "\n")
