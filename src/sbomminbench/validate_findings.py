# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""The result type shared by the ``validate`` checks (V01-V21).

See also: ``validate.py`` (driver and CLI), ``validate_entry.py`` (per-entry
checks), ``validate_files.py`` (per-file checks), ``validate_tree.py`` (checks
across the whole data tree).
"""

from dataclasses import dataclass
from typing import Literal

type Severity = Literal["ERROR", "WARN"]


@dataclass(frozen=True)
class Finding:
    """One problem: the check, its severity, what it is about and why.

    ``target`` is the entry purl when known, else a path relative to the data
    directory, else ``-``.
    """

    check: str
    severity: Severity
    target: str
    detail: str


def error(check: str, target: str, detail: str) -> Finding:
    """Return an ``ERROR`` finding (the exit status becomes 1)."""
    return Finding(check, "ERROR", target, detail)


def warn(check: str, target: str, detail: str) -> Finding:
    """Return a ``WARN`` finding (never changes the exit status)."""
    return Finding(check, "WARN", target, detail)
