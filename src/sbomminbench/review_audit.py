# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``audit-sample``: a stratified sample of unanimous answers, and the Wilson interval.

The sampling frame is every ``auto_unanimous`` element of a root subject. Strata
are ``(ecosystem, independent_origins is 1 or >= 2)``; the sample size is shared
out in proportion to the stratum sizes (largest remainder, ties by stratum key)
and each stratum is drawn with a generator seeded from ``--seed`` and the
stratum, so the result does not depend on the order of the input.
"""

import argparse
import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sbomminbench import console
from sbomminbench.review_common import (
    ELEMENTS,
    element_cells,
    expected_of,
    load_entries,
    row_sort_key,
)
from sbomminbench.schema import Element, Entry


@dataclass(frozen=True)
class AuditItem:
    """One sampled answer: an element of a root subject."""

    entry_id: str
    ecosystem: str
    element: Element
    origins: str  # "1" or "2+"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.entry_id, "root", self.element)


def audit_frame(
    entries: Iterable[Entry], elements: Iterable[Element] = ELEMENTS
) -> list[AuditItem]:
    """Return every ``auto_unanimous`` root element, sorted."""
    wanted = set(elements)
    items = [
        AuditItem(
            entry.id,
            entry.ecosystem,
            element,
            "1" if expected_of(subject, element).independent_origins <= 1 else "2+",
        )
        for entry, subject, element in element_cells(entries)
        if subject.id == "root"
        and element in wanted
        and expected_of(subject, element).status == "auto_unanimous"
    ]
    return sorted(items, key=lambda i: row_sort_key(i.key))


def allocate(sizes: dict[tuple[str, str], int], n: int) -> dict[tuple[str, str], int]:
    """Share *n* draws over the strata in proportion to their sizes."""
    total = sum(sizes.values())
    if n >= total:
        return dict(sizes)
    quotas = {k: (n * s) // total for k, s in sizes.items()}
    rest = sorted(sizes, key=lambda k: (-((n * sizes[k]) % total), k))
    for key in rest[: n - sum(quotas.values())]:
        quotas[key] += 1
    return quotas


def audit_sample(
    entries: Iterable[Entry],
    n: int,
    seed: int,
    elements: Iterable[Element] = ELEMENTS,
) -> list[AuditItem]:
    """Return *n* items (or the whole frame when smaller), sorted."""
    strata: dict[tuple[str, str], list[AuditItem]] = defaultdict(list)
    for item in audit_frame(entries, elements):
        strata[(item.ecosystem, item.origins)].append(item)
    quotas = allocate({k: len(v) for k, v in strata.items()}, n)
    chosen: list[AuditItem] = []
    for key in sorted(strata):
        rng = random.Random(f"{seed}|{key[0]}|{key[1]}")
        chosen += rng.sample(strata[key], quotas[key])
    return sorted(chosen, key=lambda i: row_sort_key(i.key))


def add_audit_sample_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``audit-sample`` options to *parser*."""
    console.add_dir_options(parser)
    parser.add_argument("--n", type=int, default=60, help="sample size (default 60)")
    parser.add_argument("--seed", type=int, default=1, help="random seed (default 1)")
    parser.add_argument(
        "--element",
        action="append",
        choices=list(ELEMENTS),
        default=None,
        help="only this element (repeatable; default all)",
    )


def run_audit_sample_cli(args: argparse.Namespace) -> int:
    """Print one line per sampled answer to stdout and strata sizes to stderr."""
    entries = load_entries(args.data_dir)
    elements: list[Element] = args.element or list(ELEMENTS)
    frame = audit_frame(entries, elements)
    sample = audit_sample(entries, args.n, args.seed, elements)
    for item in sample:
        console.print_kv(
            ("ID", item.entry_id),
            ("SUBJECT", "root"),
            ("ELEMENT", item.element),
            ("ECO", item.ecosystem),
            ("ORIGINS", item.origins),
        )
    console.info(
        f"audit-sample: {len(sample)} of {len(frame)} answers, seed {args.seed}"
    )
    if len(sample) < args.n:
        console.warning(
            f"audit-sample: the frame holds {len(frame)} answers, fewer than --n {args.n}"
        )
    return 0
