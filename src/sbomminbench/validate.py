# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``validate``: integrity checks V01-V21 over the data tree.

One finding is one stdout line, ``CHECK=Vnn SEVERITY=ERROR|WARN ID=<purl or
path> DETAIL=<text>``; counts go to stderr as ``INFO:``. The exit status is 1
when any finding is an ``ERROR``; a ``WARN`` never changes it. The checks live
in ``validate_entry.py`` (one entry), ``validate_files.py`` (every file) and
``validate_tree.py`` (the entries against seeds, reviews, release, schema and
the BOM pin) and ``validate_benchmark.py`` (V21, the benchmark CSV files).
"""

import argparse
import os
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sbomminbench import console
from sbomminbench.schema import Entry
from sbomminbench.validate_benchmark import check_benchmark
from sbomminbench.validate_entry import (
    check_entry,
    check_path,
    check_unique_paths,
    drift_findings,
    load_entry,
    raw_id,
)
from sbomminbench.validate_files import (
    check_hygiene,
    check_tree_size,
    data_files,
    scan_emails,
)
from sbomminbench.validate_findings import Finding, error
from sbomminbench.validate_tree import (
    check_bom_pin,
    check_collected_with,
    check_determinism,
    check_release,
    check_reviews,
    check_schema_file,
    check_seeds,
)

DEFAULT_SCHEMA = Path("schema") / "entry.schema.json"
DEFAULT_BOM_DIR = Path("vendor") / "bom"


@dataclass
class Report:
    """The entries read and every finding, sorted."""

    entries: dict[str, Entry]
    findings: list[Finding]

    @property
    def errors(self) -> int:
        return sum(f.severity == "ERROR" for f in self.findings)

    @property
    def warnings(self) -> int:
        return len(self.findings) - self.errors


def _entry_files(root: Path) -> list[tuple[Path, str]]:
    paths = [p for p in root.rglob("*.json") if os.path.isfile(p)]
    return sorted(
        ((p, p.relative_to(root).as_posix()) for p in paths), key=lambda x: x[1]
    )


def _read_entries(
    root: Path,
) -> tuple[dict[str, Entry], dict[str, str], list[Finding]]:
    """Return the valid entries by id, the target of each file, and V01-V07 findings."""
    entries: dict[str, Entry] = {}
    targets: dict[str, str] = {}
    records: list[tuple[str, str]] = []
    found: list[Finding] = []
    for path, rel in _entry_files(root):
        entry, problems = load_entry(path, rel)
        found += problems
        purl = entry.id if entry else raw_id(path)
        targets[f"entries/{rel}"] = purl or rel
        if purl is not None:
            records.append((purl, rel))
        if entry is not None:
            entries.setdefault(entry.id, entry)
            found += check_entry(entry, rel)
        elif purl is not None:
            found += check_path(rel, purl)
    return entries, targets, found + check_unique_paths(records)


def _file_findings(data_dir: Path, targets: dict[str, str]) -> list[Finding]:
    files = data_files(data_dir)
    found = check_tree_size(files)
    for path, rel in files:
        target = targets.get(rel, rel)
        found += scan_emails(path, rel, target)
        found += check_hygiene(path, rel, target)
    return found


def run_validate(
    data_dir: Path, cache_dir: Path, schema_path: Path, bom_dir: Path
) -> Report:
    """Run V01-V21 and return the report (nothing is written)."""
    root = data_dir / "entries"
    if not os.path.isdir(root):
        return Report(
            {}, [error("V01", "entries", f"no entries directory under {data_dir}")]
        )
    entries, targets, found = _read_entries(root)
    found += _file_findings(data_dir, targets)
    found += check_seeds(data_dir, entries)
    found += check_reviews(data_dir, entries)
    found += check_collected_with(entries)
    found += check_determinism(entries, data_dir, cache_dir)
    found += check_release(data_dir, entries)
    found += check_benchmark(data_dir)
    found += check_schema_file(schema_path)
    found += check_bom_pin(bom_dir, data_dir)
    found += drift_findings()
    return Report(entries, sorted(set(found), key=_order))


def _order(f: Finding) -> tuple[str, str, str, str]:
    return f.check, f.target, f.severity, f.detail


def _summary(findings: Iterable[Finding]) -> str:
    counts = Counter((f.check, f.severity) for f in findings)
    return ", ".join(f"{c} {s} x{n}" for (c, s), n in sorted(counts.items()))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``validate`` options to *parser*."""
    console.add_dir_options(parser)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--bom-dir", type=Path, default=DEFAULT_BOM_DIR)


def run_cli(args: argparse.Namespace) -> int:
    """Print one line per finding to stdout and the totals to stderr; 1 on any ERROR."""
    report = run_validate(args.data_dir, args.cache_dir, args.schema, args.bom_dir)
    for f in report.findings:
        console.print_kv(
            ("CHECK", f.check),
            ("SEVERITY", f.severity),
            ("ID", f.target),
            ("DETAIL", f.detail),
        )
    console.info(
        f"validate: {len(report.entries)} entries, {report.errors} errors, "
        f"{report.warnings} warnings"
    )
    if report.findings:
        console.info(f"validate: {_summary(report.findings)}")
    if report.errors:
        console.error(f"validate: {report.errors} errors (see CHECK= lines on stdout)")
        return 1
    return 0
