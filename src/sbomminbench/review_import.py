# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``review-import``: validate a filled-in queue and store the decisions.

Every row with a ``decision`` is checked against the current entries; one bad row
rejects the whole file and nothing is written. A decision identical to the latest
one stored for the cell (apart from its time) is skipped, so importing a file
twice changes nothing.
"""

import argparse
import csv
import io
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from sbomminbench import console
from sbomminbench.ids import canonical_name_key, entry_relpath
from sbomminbench.jsonio import read_entry
from sbomminbench.redact import find_emails
from sbomminbench.review_common import (
    NAME_SEP,
    AnyValue,
    ReviewError,
    as_element,
    expected_of,
)
from sbomminbench.review_export import COLUMNS
from sbomminbench.review_store import read_lines, reviews_dir, write_lines
from sbomminbench.schema import (
    Element,
    Entry,
    LicenseValue,
    NameValue,
    ProviderEntity,
    ProviderValue,
    ReviewLine,
    Subject,
)

DECISIONS: Final = ("confirm", "correct", "exclude")
NO_CONFIRM: Final = ("auto_conflict", "auto_unresolved", "not_assessed")
LICENSE_REF: Final = "LicenseRef-"
UNKNOWN_KEYS: Final = "Unknown license key(s)"


@dataclass(frozen=True)
class ImportResult:
    """How many decisions were added, and how many were already stored."""

    added: int
    unchanged: int
    skipped: int  # rows with an empty decision


def check_license(text: str) -> str | None:
    """Return why *text* is not an accepted licence expression, else ``None``.

    It must parse with ``license-expression``; an unknown key is accepted only
    when it starts with ``LicenseRef-``.
    """
    from license_expression import get_spdx_licensing

    if not text:
        return "empty licence expression"
    try:
        info = get_spdx_licensing().validate(text)
    except Exception as exc:  # noqa: BLE001 - the library raises odd errors on odd input
        return f"licence expression does not parse ({type(exc).__name__})"
    bad = [s for s in info.invalid_symbols if not s.startswith(LICENSE_REF)]
    if bad:
        return f"unknown licence key {bad[0]!r} (only LicenseRef-* may be unknown)"
    if any(not e.startswith(UNKNOWN_KEYS) for e in info.errors):
        return f"licence expression does not parse ({info.errors[0]})"
    return None


def _provider_value(
    entry: Entry, subject: Subject, text: str, flag: str
) -> ProviderValue:
    if flag not in ("", "y", "n"):
        raise ValueError(f"has_unnamed_group must be y or n, not {flag!r}")
    names = [n.strip() for n in text.split(NAME_SEP)] if text.strip() else []
    if any(not n or "|" in n for n in names):
        raise ValueError("empty provider name, or a name holding '|'")
    if not names and flag != "y":
        raise ValueError("provider needs a name or has_unnamed_group=y")
    old = subject.expected.provider.value
    roles = {e.name.casefold(): e.role for e in old.entities} if old else {}
    entities = [
        ProviderEntity(name=n, role=roles.get(n.casefold(), "author")) for n in names
    ]
    return ProviderValue(entities=entities, has_unnamed_group=flag == "y")


def correction(
    entry: Entry, subject: Subject, element: Element, row: dict[str, str]
) -> AnyValue:
    """Build the typed value of a ``correct`` row, or raise :class:`ValueError`."""
    text = row["value"].strip()
    if element == "provider":
        return _provider_value(
            entry, subject, row["value"], row["has_unnamed_group"].strip()
        )
    if row["has_unnamed_group"].strip() not in ("", "n"):
        raise ValueError("has_unnamed_group applies to provider only")
    if element == "license":
        why = check_license(text)
        if why:
            raise ValueError(why)
        return LicenseValue(spdx_expression=text, has_license_ref=LICENSE_REF in text)
    if not text:
        raise ValueError("empty name")
    return NameValue(
        name=text,
        canonical_key=canonical_name_key(entry.ecosystem, text),
        accepted=[text],
    )


def _row_line(entry: Entry, row: dict[str, str], reviewer: str, at: str) -> ReviewLine:
    """Validate one decided row and return its review line."""
    subject = next((s for s in entry.subjects if s.id == row["subject"]), None)
    if subject is None:
        raise ValueError(f"unknown subject {row['subject']!r}")
    element = as_element(row["element"])
    expected = expected_of(subject, element)
    decision = row["decision"].strip()
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision {decision!r}")
    if row["auto_status"] != expected.status:
        raise ValueError(
            f"auto_status {row['auto_status']!r} is now {expected.status!r}; export again"
        )
    if decision == "confirm" and expected.status in NO_CONFIRM:
        raise ValueError(f"confirm is not allowed for {expected.status}")
    if decision != "correct" and (
        row["value"].strip() or row["has_unnamed_group"].strip() == "y"
    ):
        raise ValueError(f"{decision} takes no value")
    value = correction(entry, subject, element, row) if decision == "correct" else None
    note = row["note"] or None
    if find_emails(row["value"] + "\n" + row["note"]):
        raise ValueError("value or note holds an e-mail address")
    return ReviewLine(
        id=entry.id,
        subject=subject.id,
        element=element,
        decision=decision,
        value=value,
        reviewer=reviewer,
        at=at,
        note=note,
        auto_status=expected.status,
        auto_value_at_review=expected.value,
    )


def read_queue(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield ``(line number, row)`` of a queue file; a wrong header raises."""
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if tuple(header or ()) != COLUMNS:
        raise ReviewError(
            [f"{console.quote(path)}: header is not the review-export header"]
        )
    for row in reader:
        if len(row) != len(COLUMNS):
            raise ReviewError(
                [
                    f"{console.quote(path)}:{reader.line_num}: {len(row)} columns, need {len(COLUMNS)}"
                ]
            )
        yield reader.line_num, dict(zip(COLUMNS, row, strict=True))


def parse_queue(
    path: Path, data_dir: Path, reviewer: str, at: str
) -> tuple[list[ReviewLine], int]:
    """Validate every decided row; return ``(review lines, skipped)``."""
    errors: list[str] = []
    out: dict[tuple[str, str, str], ReviewLine] = {}
    skipped = 0
    cache: dict[str, Entry | None] = {}
    for number, row in read_queue(path):
        if not row["decision"].strip():
            skipped += 1
            continue
        where = f"{console.quote(path)}:{number}: {console.escape(row['id'])}"
        entry_path = None
        try:
            if row["id"] not in cache:
                entry_path = data_dir / "entries" / entry_relpath(row["id"])
                cache[row["id"]] = (
                    read_entry(entry_path) if os.path.isfile(entry_path) else None
                )
            entry = cache[row["id"]]
            if entry is None or entry.id != row["id"]:
                raise ValueError("unknown id")
            line = _row_line(entry, row, reviewer, at)
        except (ValueError, ValidationError) as exc:
            errors.append(
                f"{where}: {console.escape(str(exc)).splitlines()[0] if str(exc) else 'invalid'}"
            )
            continue
        key = (line.id, line.subject, line.element)
        if key in out:
            errors.append(f"{where}: {line.subject} {line.element} decided twice")
            continue
        out[key] = line
    if errors:
        raise ReviewError(errors)
    return [out[k] for k in sorted(out)], skipped


def _same_decision(a: ReviewLine, b: ReviewLine) -> bool:
    return a.model_copy(update={"at": ""}) == b.model_copy(update={"at": ""})


def import_reviews(queue: Path, data_dir: Path, reviewer: str, at: str) -> ImportResult:
    """Store the decisions of *queue*; raise :class:`ReviewError` on a bad row."""
    new, skipped = parse_queue(queue, data_dir, reviewer, at)
    path = reviews_dir(data_dir) / f"{reviewer}.jsonl"
    lines = read_lines(path)
    latest = {(x.id, x.subject, x.element): x for x in lines}
    added = 0
    for line in new:
        key = (line.id, line.subject, line.element)
        if key in latest and _same_decision(latest[key], line):
            continue
        lines.append(line)
        latest[key] = line
        added += 1
    if added:
        write_lines(path, lines)
    return ImportResult(added, len(new) - added, skipped)


def add_review_import_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``review-import`` options to *parser*."""
    console.add_dir_options(parser)
    parser.add_argument("queue", type=Path, help="CSV file from review-export")
    parser.add_argument("--reviewer", required=True, help="[a-z0-9_-]+")
    parser.add_argument(
        "--at", help="decision time, YYYY-MM-DDTHH:MM:SSZ (default: now)"
    )


def run_review_import_cli(args: argparse.Namespace) -> int:
    """Import the queue; one ``ERROR:`` line per rejected row, exit 1 on any."""
    at = args.at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result = import_reviews(args.queue, args.data_dir, args.reviewer, at)
    except ReviewError as exc:
        for message in exc.errors:
            console.error(f"review-import: {message}")
        return 1
    except ValidationError as exc:
        console.error(f"review-import: {console.escape(str(exc).splitlines()[0])}")
        return 1
    console.print_kv(
        ("ADDED", result.added),
        ("UNCHANGED", result.unchanged),
        ("SKIPPED", result.skipped),
    )
    console.info(
        f"review-import: {result.added} added, {result.unchanged} unchanged, {result.skipped} without decision"
    )
    return 0
