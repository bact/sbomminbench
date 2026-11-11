# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``validate``: the checks that read every file under the data directory.

V08 (no e-mail address other than the placeholder) and V16 (file hygiene).

See also: ``validate.py``, ``validate_findings.py``.
"""

import csv
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path

from sbomminbench.jsonio import dumps
from sbomminbench.redact import find_emails
from sbomminbench.validate_findings import Finding, error

MAX_FILE_BYTES = 200_000
MAX_TREE_BYTES = 100 * 1024 * 1024
_UTF8_BOM = b"\xef\xbb\xbf"
_LF_SUFFIXES = frozenset({".json", ".jsonl", ".tsv", ".csv", ".txt", ".md"})


def data_files(data_dir: Path) -> list[tuple[Path, str]]:
    """Every regular file under *data_dir* as ``(path, posix relative path)``, sorted."""
    pairs = [
        (p, p.relative_to(data_dir).as_posix())
        for p in data_dir.rglob("*")
        if os.path.isfile(p)
    ]
    return sorted(pairs, key=lambda pair: pair[1])


# -- V08 ---------------------------------------------------------------------------


def _json_strings(value: object, where: str) -> Iterator[tuple[str, str]]:
    """Yield ``(location, string)`` for every string and dict key of a JSON value."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _json_strings(item, f"{where}[{i}]")
    elif isinstance(value, dict):
        for k, item in value.items():
            # A key that holds an address must not be echoed in a location.
            here = f"{where}.<key>" if find_emails(k) else f"{where}.{k}"
            yield here, k
            yield from _json_strings(item, here)


def _cell_strings(text: str, suffix: str) -> Iterator[tuple[str, str]]:
    if suffix == ".csv":
        for r, row in enumerate(csv.reader(io.StringIO(text, newline="")), 1):
            for c, cell in enumerate(row, 1):
                yield f"row {r} column {c}", cell
        return
    for n, line in enumerate(text.split("\n"), 1):
        for c, cell in enumerate(line.split("\t"), 1):
            yield f"line {n} column {c}", cell


def _file_strings(text: str, suffix: str) -> Iterator[tuple[str, str]]:
    if suffix == ".json":
        try:
            yield from _json_strings(json.loads(text), "$")
            return
        except ValueError:
            pass
    elif suffix == ".jsonl":
        for n, line in enumerate(text.split("\n"), 1):
            try:
                yield from _json_strings(json.loads(line), f"line {n}")
            except ValueError:
                yield f"line {n}", line
        return
    yield from _cell_strings(text, suffix)


def scan_emails(path: Path, rel: str, target: str) -> list[Finding]:
    """V08: report each string holding an address other than the placeholder.

    The address itself is never printed. Purl tokens are masked by
    :func:`sbomminbench.redact.find_emails`.
    """
    text = path.read_bytes().decode("utf-8", "replace").removeprefix("﻿")
    found: list[Finding] = []
    for where, s in _file_strings(text, path.suffix):
        if find_emails(s):
            found.append(error("V08", target, f"e-mail address in {rel} at {where}"))
    return found


# -- V16 ---------------------------------------------------------------------------


def _unsorted(data: bytes, *, lines: bool) -> bool:
    """Whether any JSON object in *data* (one document, or one per line) is unsorted."""
    flags: list[bool] = []

    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        keys = [k for k, _ in pairs]
        flags.append(keys != sorted(keys))
        return dict(pairs)

    for line in data.split(b"\n") if lines else [data]:
        try:
            json.loads(line, object_pairs_hook=hook)
        except ValueError:
            continue
    return any(flags)


def _hygiene_problems(path: Path, rel: str, data: bytes) -> Iterator[str]:
    suffix = path.suffix
    if data.startswith(_UTF8_BOM):
        yield "starts with a byte order mark"
    if suffix in _LF_SUFFIXES or path.name == "SHA256SUMS":
        if b"\r" in data:
            yield "contains CR (needs LF line endings)"
        if data and not data.endswith(b"\n"):
            yield "does not end with a newline"
    if suffix == ".json":
        yield from _json_problems(rel, data)
    elif suffix == ".jsonl" and _unsorted(data, lines=True):
        yield "JSON keys are not sorted"
    if rel.startswith("entries/") and len(data) > MAX_FILE_BYTES:
        yield f"{len(data)} bytes is over {MAX_FILE_BYTES}"


def _json_problems(rel: str, data: bytes) -> Iterator[str]:
    if _unsorted(data, lines=False):
        yield "JSON keys are not sorted"
        return
    try:
        canonical = dumps(json.loads(data))
    except ValueError:
        return
    if rel.startswith("entries/") and data.decode("utf-8", "replace") != canonical:
        yield "not in canonical form (sorted keys, 2-space indent, one final LF)"


def check_hygiene(path: Path, rel: str, target: str) -> list[Finding]:
    """V16: LF, sorted keys, final newline, canonical entry form, size per file."""
    data = path.read_bytes()
    return [
        error("V16", target, f"{rel}: {p}") for p in _hygiene_problems(path, rel, data)
    ]


def check_tree_size(files: list[tuple[Path, str]]) -> list[Finding]:
    """V16: the whole data directory stays under ``MAX_TREE_BYTES``."""
    total = sum(os.path.getsize(p) for p, _ in files)
    if total > MAX_TREE_BYTES:
        return [
            error("V16", "-", f"data directory is {total} bytes, over {MAX_TREE_BYTES}")
        ]
    return []
