# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``rederive-diff``: what a rebuild from the cache would change in ``data/entries``.

The entries are rebuilt in memory (nothing is written) and compared with the
files under ``<data-dir>/entries``, per subject and element, on status and value.
Use it after upgrading LicenseID or Pitloom, before reviewing.
"""

import argparse
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sbomminbench import console
from sbomminbench.build import build_all
from sbomminbench.ids import entry_relpath
from sbomminbench.jsonio import read_entry
from sbomminbench.schema import Ecosystem, Entry, Subject

ELEMENTS = ("name", "provider", "license")


@dataclass(frozen=True)
class Change:
    """One difference: an entry that is new, or one subject element that changed."""

    purl: str
    kind: str  # "added" | "changed"
    subject: str = ""
    element: str = ""
    old: tuple[str, str] = ("", "")  # (status, value as compact JSON)
    new: tuple[str, str] = ("", "")


def _compact(value: object) -> str:
    """Compact JSON without spaces, so it stays one ``KEY=VALUE`` token."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text.replace(" ", "\\u0020")


def _state(subject: Subject | None, element: str) -> tuple[str, str]:
    if subject is None:
        return "absent", "null"
    expected = getattr(subject.expected, element)
    value = expected.value.model_dump(mode="json") if expected.value else None
    return str(expected.status), _compact(value)


def diff_entries(old: Entry, new: Entry) -> Iterator[Change]:
    """Yield the subject elements whose status or value differ."""
    before = {s.id: s for s in old.subjects}
    after = {s.id: s for s in new.subjects}
    for sid in sorted(set(before) | set(after), key=lambda i: (i != "root", i)):
        if sid not in before or sid not in after:
            yield Change(
                new.id,
                "changed",
                sid,
                "subject",
                ("present" if sid in before else "absent", "null"),
                ("present" if sid in after else "absent", "null"),
            )
            continue
        for el in ELEMENTS:
            o, n = _state(before[sid], el), _state(after[sid], el)
            if o != n:
                yield Change(new.id, "changed", sid, el, o, n)


def rederive(
    cache_dir: Path, data_dir: Path, ecos: list[Ecosystem], only: list[str]
) -> tuple[int, list[Change]]:
    """Return ``(entries compared, changes)`` for the cache against the data."""
    root = data_dir / "entries"
    compared = 0
    changes: list[Change] = []
    for item in build_all(cache_dir, ecos, only):
        if item.entry is None:
            continue
        compared += 1
        path = root / entry_relpath(item.entry.id)
        if not os.path.isfile(path):
            changes.append(Change(item.entry.id, "added"))
            continue
        changes += diff_entries(read_entry(path), item.entry)
    return compared, changes


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``rederive-diff`` options to *parser*."""
    console.add_common_options(parser)


def run_cli(args: argparse.Namespace) -> int:
    """Print one line per change to stdout and a summary to stderr."""
    compared, changes = rederive(
        args.cache_dir, args.data_dir, console.resolve_ecos(args.eco), args.only
    )
    for c in changes:
        if c.kind == "added":
            console.print_kv(("ID", c.purl), ("CHANGE", "added"))
            continue
        console.print_kv(
            ("ID", c.purl),
            ("CHANGE", c.kind),
            ("SUBJECT", c.subject),
            ("ELEMENT", c.element),
            ("OLD_STATUS", c.old[0]),
            ("NEW_STATUS", c.new[0]),
            ("OLD_VALUE", c.old[1]),
            ("NEW_VALUE", c.new[1]),
        )
    console.info(
        f"rederive-diff: {compared} entries compared, "
        f"{len({c.purl for c in changes})} differ"
    )
    return 0
