# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Review tooling: ``review-export``, ``review-import``, ``audit-sample``.

Each subcommand follows the ``add_arguments(parser)`` / ``run_cli(args)``
convention; :data:`COMMANDS` maps the three names to their pairs and
:func:`run_cli` dispatches on ``args.command``.
See also: ``review_export``, ``review_import``, ``review_audit``, ``review_store``.
"""

import argparse
from collections.abc import Callable
from typing import Final

from sbomminbench.review_audit import (
    add_audit_sample_arguments,
    audit_sample,
    run_audit_sample_cli,
)
from sbomminbench.review_common import ReviewError
from sbomminbench.review_export import (
    add_review_export_arguments,
    queue_rows,
    run_review_export_cli,
)
from sbomminbench.review_import import (
    add_review_import_arguments,
    check_license,
    import_reviews,
    run_review_import_cli,
)
from sbomminbench.review_store import StaleReview, stale_reviews
from sbomminbench.wilson import wilson

__all__ = [
    "COMMANDS",
    "ReviewError",
    "StaleReview",
    "add_arguments",
    "add_audit_sample_arguments",
    "add_review_export_arguments",
    "add_review_import_arguments",
    "audit_sample",
    "check_license",
    "import_reviews",
    "queue_rows",
    "run_cli",
    "stale_reviews",
    "wilson",
]

COMMANDS: Final[
    dict[
        str,
        tuple[
            Callable[[argparse.ArgumentParser], None],
            Callable[[argparse.Namespace], int],
        ],
    ]
] = {
    "review-export": (add_review_export_arguments, run_review_export_cli),
    "review-import": (add_review_import_arguments, run_review_import_cli),
    "audit-sample": (add_audit_sample_arguments, run_audit_sample_cli),
}


def add_arguments(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Register the three subcommands on *subparsers*."""
    for name, (add, _) in COMMANDS.items():
        add(subparsers.add_parser(name, help=f"{name} (see --help)"))


def run_cli(args: argparse.Namespace) -> int:
    """Run the subcommand named by ``args.command``."""
    return COMMANDS[args.command][1](args)
