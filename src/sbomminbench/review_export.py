# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``review-export``: the review queue as a CSV file.

The queue holds the answers whose status is not scored (``--status``) and, with
``--audit N``, a stratified sample of unanimous answers. The file is UTF-8 with a
byte order mark, LF line endings, written with the ``csv`` module, in id order.
"""

import argparse
import csv
import io
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from sbomminbench import console
from sbomminbench.review_audit import audit_sample
from sbomminbench.review_common import (
    CANDIDATE_SEP,
    NON_SCORED,
    RowKey,
    element_cells,
    evidence_lines,
    expected_of,
    load_entries,
    row_sort_key,
    value_text,
)
from sbomminbench.schema import Element, Entry, ProviderValue, Subject

COLUMNS: Final = (
    "id",
    "subject",
    "element",
    "auto_status",
    "auto_value",
    "candidates",
    "evidence",
    "tags",
    "decision",
    "value",
    "has_unnamed_group",
    "note",
)
BOM: Final = "﻿"


def _row(entry: Entry, subject: Subject, element: Element, blind: bool) -> list[str]:
    expected = expected_of(subject, element)
    value = expected.value
    shown = not blind
    return [
        entry.id,
        subject.id,
        element,
        expected.status,
        value_text(value) if shown else "",
        CANDIDATE_SEP.join(expected.candidates) if shown else "",
        "\n".join(evidence_lines(entry, subject, element)),
        " ".join(subject.challenge_tags),
        "",
        "",
        "y" if isinstance(value, ProviderValue) and value.has_unnamed_group else "n",
        "",
    ]


def queue_rows(
    entries: Iterable[Entry],
    statuses: Iterable[str],
    *,
    blind: bool = False,
    audit: Iterable[RowKey] = (),
) -> list[list[str]]:
    """Return the queue rows (no header) in ``(id, subject, element)`` order."""
    wanted = set(statuses)
    extra = set(audit)
    chosen: dict[RowKey, list[str]] = {}
    for entry, subject, element in element_cells(entries):
        key = (entry.id, subject.id, element)
        if expected_of(subject, element).status in wanted or key in extra:
            chosen[key] = _row(entry, subject, element, blind)
    return [chosen[k] for k in sorted(chosen, key=row_sort_key)]


def sample_rows(rows: list[list[str]], n: int, seed: int) -> list[list[str]]:
    """Return *n* rows drawn with *seed*, keeping the order (all when n is larger)."""
    if n >= len(rows):
        return rows
    picked = sorted(random.Random(seed).sample(range(len(rows)), n))
    return [rows[i] for i in picked]


def csv_text(rows: Iterable[list[str]]) -> str:
    """Return the header and *rows* as CSV text with LF line endings."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def write_csv(path: Path, rows: Iterable[list[str]]) -> None:
    """Write the queue to *path* as UTF-8 with a BOM, LF endings, byte for byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((BOM + csv_text(rows)).encode("utf-8"))


def _statuses(text: str) -> list[str]:
    names = [s.strip() for s in text.split(",") if s.strip()]
    bad = [s for s in names if s not in NON_SCORED]
    if bad or not names:
        raise argparse.ArgumentTypeError(
            f"--status takes a comma list of {', '.join(NON_SCORED)}"
        )
    return names


def add_review_export_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``review-export`` options to *parser*."""
    console.add_dir_options(parser)
    parser.add_argument(
        "--status",
        type=_statuses,
        default=["auto_conflict", "auto_unresolved"],
        metavar="LIST",
        help="comma list of statuses to queue (default auto_conflict,auto_unresolved)",
    )
    parser.add_argument("--out", type=Path, default=Path("review-queue.csv"))
    parser.add_argument(
        "--blind",
        action="store_true",
        help="leave auto_value and candidates empty",
    )
    parser.add_argument("--sample", type=int, metavar="N", help="queue only N rows")
    parser.add_argument(
        "--audit",
        type=int,
        default=0,
        metavar="N",
        help="add an audit sample of N unanimous answers",
    )
    parser.add_argument(
        "--seed", type=int, default=1, help="seed for --sample and --audit"
    )


def run_review_export_cli(args: argparse.Namespace) -> int:
    """Write the queue to ``--out`` and a summary to stderr."""
    entries = load_entries(args.data_dir)
    rows = queue_rows(entries, args.status, blind=args.blind)
    if args.sample is not None:
        rows = sample_rows(rows, args.sample, args.seed)
    audit = [i.key for i in audit_sample(entries, args.audit, args.seed)]
    if audit:
        extra = queue_rows(entries, (), blind=args.blind, audit=audit)
        rows = sorted(rows + extra, key=lambda r: row_sort_key((r[0], r[1], r[2])))
    write_csv(args.out, rows)
    console.print_kv(("FILE", console.escape(str(args.out))))
    console.info(
        f"review-export: {len(rows)} rows from {len(entries)} entries "
        f"({len(audit)} audit)"
    )
    return 0
