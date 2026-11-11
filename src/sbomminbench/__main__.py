# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Command-line entry point.

Every subcommand registers through ``IMPLEMENTED`` (``add_arguments``, ``run_cli``).
``SUBCOMMANDS`` lists names that are still stubs.
"""

import argparse
import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from sbomminbench import (
    __version__,
    build,
    console,
    export,
    fetch,
    jsonio,
    rederive,
    review,
    select,
    stats,
    validate,
)
from sbomminbench.evaluate import cli as evaluate_cli
from sbomminbench.schema import Entry

# Subcommands that are still stubs (print ERROR, exit 2).
SUBCOMMANDS: tuple[str, ...] = ()
DEFAULT_SCHEMA = Path("schema") / "entry.schema.json"
MAX_ERROR_CHARS = 500

type Handler = Callable[[argparse.Namespace], int]


def export_schema_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_SCHEMA, help="schema file to write"
    )


def export_schema(args: argparse.Namespace) -> int:
    """Write the JSON Schema of ``Entry`` (the source of truth is ``schema.py``)."""
    changed = jsonio.write_if_changed(
        args.output, jsonio.dumps(Entry.model_json_schema())
    )
    console.print_kv(
        ("RESULT", "written" if changed else "unchanged"), ("FILE", args.output)
    )
    return 0


IMPLEMENTED: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Handler]] = {
    "select": (select.add_arguments, select.run_cli),
    "fetch": (fetch.add_arguments, fetch.run_cli),
    "build": (build.add_arguments, build.run_cli),
    "rederive-diff": (rederive.add_arguments, rederive.run_cli),
    "export-schema": (export_schema_arguments, export_schema),
    "export": (export.add_arguments, export.run_cli),
    "stats": (stats.add_arguments, stats.run_cli),
    "validate": (validate.add_arguments, validate.run_cli),
    "evaluate": (evaluate_cli.add_arguments, evaluate_cli.run_cli),
    **{name: (add, run) for name, (add, run) in review.COMMANDS.items()},
}


def _build_parser() -> argparse.ArgumentParser:
    """Return the parser: the implemented subcommands, then one stub per name."""
    parser = argparse.ArgumentParser(
        prog="sbomminbench",
        description=(
            "SBOM-MinBench: package and model metadata with expected SBOM elements."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, (add_arguments, _) in IMPLEMENTED.items():
        add_arguments(subparsers.add_parser(name, help=f"{name} (see --help)"))
    for name in SUBCOMMANDS:
        subparsers.add_parser(name, help="not implemented yet")
    return parser


def _run(command: str, handler: Handler, args: argparse.Namespace) -> int:
    try:
        return handler(args)
    except Exception as exc:  # noqa: BLE001 - internal error: report, exit 1
        text = console.escape(str(exc))[:MAX_ERROR_CHARS]
        console.error(f"{command}: {type(exc).__name__}: {text}")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit status.

    Exit 0 also when seeds failed (they are logged in ``failed.tsv``); exit 1
    is an internal error; exit 2 is a stub subcommand.
    """
    console.configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a COMMAND is required")
    if args.command in IMPLEMENTED:
        return _run(args.command, IMPLEMENTED[args.command][1], args)
    console.error(f"{args.command}: not implemented")
    return 2


def console_main() -> None:
    """Script entry point: Ctrl-C still ends the process by SIGINT."""
    try:
        code = main()
    except KeyboardInterrupt:
        console.info("interrupted")
        if sys.platform == "win32":
            sys.exit(130)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGINT)
        sys.exit(130)
    sys.exit(code)


if __name__ == "__main__":
    console_main()
