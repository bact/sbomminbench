# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Deterministic JSON writing and reading of entries.

Every file under ``data/`` is written by :func:`dumps`: sorted keys, LF line
endings, UTF-8 without escapes, and a trailing newline. An entry that still holds
an e-mail address is refused, never silently fixed.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sbomminbench.ids import entry_relpath
from sbomminbench.redact import redact_json
from sbomminbench.schema import Entry

_UTF8_BOM = b"\xef\xbb\xbf"


class LeakError(ValueError):
    """An entry still contains an e-mail address other than the placeholder."""


def dumps(data: Any) -> str:
    """Serialise ``data`` as the canonical JSON text, ending in one LF."""
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def write_if_changed(path: Path, text: str) -> bool:
    """Write ``text`` as UTF-8 bytes unless the file already holds them.

    Returns ``True`` when the file was written.
    """
    data = text.encode("utf-8")
    if os.path.isfile(path) and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def write_entry(entry: Entry, root: Path) -> Path:
    """Write ``entry`` to its path under ``root`` and return that path.

    Raises :class:`LeakError` when the serialised entry holds an address.
    """
    data = entry.model_dump(mode="json")
    _, leaked = redact_json(data)
    if leaked:
        raise LeakError(f"entry {entry.id} holds an e-mail address; not written")
    path = root / entry_relpath(entry.id)
    write_if_changed(path, dumps(data))
    return path


def read_entry(path: Path) -> Entry:
    """Read and validate one entry; a leading UTF-8 BOM is dropped."""
    data = path.read_bytes().removeprefix(_UTF8_BOM)
    return Entry.model_validate_json(data)


def iter_entries(root: Path) -> Iterator[tuple[Path, Entry]]:
    """Yield ``(path, entry)`` for each ``.json`` file under ``root``, by path."""
    paths = sorted(
        (p for p in root.rglob("*.json") if os.path.isfile(p)),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    for path in paths:
        yield path, read_entry(path)
