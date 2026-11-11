# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Command-line output: tagged stderr lines, ``KEY=VALUE`` data lines, quoting.

Stdout carries data only. Everything else goes to stderr, each line starting
with exactly one of ``ERROR: ``, ``WARNING: `` or ``INFO: ``. A path or an
untrusted value in a message goes through :func:`quote`.
"""

import argparse
import logging
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import get_args

from sbomminbench.schema import Ecosystem

_NAMED = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
LOGGER_NAME = "sbomminbench"


def _escape_char(ch: str) -> str:
    code = ord(ch)
    if ch in _NAMED:
        return _NAMED[ch]
    if code < 0x100:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


def escape(text: str) -> str:
    """Return *text* on one line: control and invisible characters escaped.

    Backslashes stay as they are, so a Windows path reads as written.
    """
    return "".join(
        _escape_char(ch) if ch != " " and unicodedata.category(ch)[0] in "CZ" else ch
        for ch in text
    )


def quote(value: object) -> str:
    """Return ``str(value)`` in double quotes, on one line (see :func:`escape`)."""
    return '"' + escape(str(value)).replace('"', '\\"') + '"'


def info(message: str) -> None:
    """Write ``INFO: <message>`` to stderr."""
    print(f"INFO: {message}", file=sys.stderr)


def warning(message: str) -> None:
    """Write ``WARNING: <message>`` to stderr."""
    print(f"WARNING: {message}", file=sys.stderr)


def error(message: str) -> None:
    """Write ``ERROR: <message>`` to stderr."""
    print(f"ERROR: {message}", file=sys.stderr)


def kv_line(pairs: Iterable[tuple[str, object]]) -> str:
    """Return ``KEY=VALUE KEY=VALUE`` for one data point.

    Values are escaped onto one line; a value that may hold a space goes last.
    """
    return " ".join(f"{key}={escape(str(value))}" for key, value in pairs)


def print_kv(*pairs: tuple[str, object]) -> None:
    """Write one data point to stdout."""
    print(kv_line(pairs))


class _StderrHandler(logging.Handler):
    """Write ``LEVEL: message`` on one line to the current ``sys.stderr``."""

    def emit(self, record: logging.LogRecord) -> None:
        level = record.levelname if record.levelno >= logging.INFO else "INFO"
        print(f"{level}: {escape(record.getMessage())}", file=sys.stderr)


def configure_logging() -> None:
    """Route library logging to stderr in the CLI format (idempotent).

    Pitloom is limited to errors; it is only a library here.
    """
    logging.getLogger("pitloom").setLevel(logging.ERROR)
    log = logging.getLogger(LOGGER_NAME)
    log.handlers = [h for h in log.handlers if not isinstance(h, _StderrHandler)]
    log.addHandler(_StderrHandler())
    log.setLevel(logging.INFO)


def add_dir_options(parser: argparse.ArgumentParser) -> None:
    """Add ``--data-dir`` and ``--cache-dir``."""
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))


def add_common_options(parser: argparse.ArgumentParser) -> None:
    """Add the options shared by ``fetch``, ``build`` and ``rederive-diff``."""
    parser.add_argument(
        "--eco",
        choices=[*sorted(get_args(Ecosystem)), "all"],
        default="all",
        help="ecosystem, or all (default)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="only this package or model name (repeatable)",
    )
    add_dir_options(parser)


def resolve_ecos(eco: str) -> list[Ecosystem]:
    """Return the ecosystems named by ``--eco`` (``all`` = every one, sorted)."""
    names: list[Ecosystem] = sorted(get_args(Ecosystem))
    return names if eco == "all" else [e for e in names if e == eco]
