# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``export``: write the release files under ``<data-dir>/release/``.

Files: ``entries.jsonl``, ``subjects.csv``, ``observations.csv``, ``stats.json``,
``standard-elements.json``, ``benchmark.csv``, ``benchmark-unscored.csv`` and
``SHA256SUMS``. Human reviews are applied over the auto answers first. Output
is sorted, LF, UTF-8 and byte-identical for the same input; a file is written
only when its bytes change. Nothing reads a clock. A string that holds an
e-mail address other than the placeholder stops the export.

See also: ``benchmark_csv.py`` (the two benchmark files), ``export_review.py``
(overlay), ``export_rows.py`` (tables), ``stats.py`` (``stats.json``),
``standards.py`` (``standard-elements.json``).
"""

import argparse
import hashlib
from collections.abc import Iterable
from pathlib import Path

from sbomminbench import benchmark_csv, console, jsonio, standards
from sbomminbench.export_review import load_final
from sbomminbench.export_rows import (
    OBSERVATION_COLUMNS,
    SUBJECT_COLUMNS,
    compact_json,
    csv_text,
    observation_rows,
    subject_rows,
)
from sbomminbench.redact import find_emails
from sbomminbench.schema import Entry
from sbomminbench.stats import compute_stats, frame_dates

RELEASE_FILES = (
    *benchmark_csv.FILES,
    "entries.jsonl",
    "observations.csv",
    "standard-elements.json",
    "stats.json",
    "subjects.csv",
)


def _check_no_email(name: str, strings: Iterable[str]) -> None:
    for text in strings:
        if find_emails(text):
            raise jsonio.LeakError(f"{name} holds an e-mail address; not written")


def release_texts(entries: list[Entry], data_dir: Path) -> dict[str, str]:
    """Return the text of every release file except ``SHA256SUMS``."""
    dumps = [e.model_dump(mode="json") for e in entries]
    texts = {
        "entries.jsonl": "".join(compact_json(d) + "\n" for d in dumps),
        "subjects.csv": csv_text(
            SUBJECT_COLUMNS, (r for e in entries for r in subject_rows(e))
        ),
        "observations.csv": csv_text(
            OBSERVATION_COLUMNS, (r for e in entries for r in observation_rows(e))
        ),
        **benchmark_csv.benchmark_texts(entries),
        "stats.json": jsonio.dumps(compute_stats(entries, frame_dates(data_dir))),
        "standard-elements.json": jsonio.dumps(standards.release_metadata()),
    }
    for name, text in texts.items():
        _check_no_email(name, text.splitlines())
    return texts


def sha256sums(texts: dict[str, str]) -> str:
    """Return ``SHA256SUMS`` text (two spaces, sorted by file name)."""
    return "".join(
        f"{hashlib.sha256(texts[name].encode('utf-8')).hexdigest()}  {name}\n"
        for name in sorted(texts)
    )


def export(data_dir: Path) -> tuple[int, int, int]:
    """Write the release files; return ``(entries, files written, reviews)``."""
    entries, overlay = load_final(data_dir)
    texts = release_texts(entries, data_dir)
    texts["SHA256SUMS"] = sha256sums(texts)
    release = data_dir / "release"
    written = sum(jsonio.write_if_changed(release / n, t) for n, t in texts.items())
    return len(entries), written, overlay.applied


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``export`` options to *parser*."""
    console.add_dir_options(parser)


def run_cli(args: argparse.Namespace) -> int:
    """Run ``export`` for the parsed *args*; return the exit status."""
    n, written, applied = export(args.data_dir)
    release = args.data_dir / "release"
    for name in sorted((*RELEASE_FILES, "SHA256SUMS")):
        console.print_kv(("FILE", release / name))
    console.info(
        f"export: {n} entries, {applied} reviews applied, {written} files written"
    )
    return 0
