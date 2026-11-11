# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Author-string rules shared by the structural tags and the provider derive.

One definition of "several authors in one string" and "organisation-like", so
`sbomminbench.tags` and `sbomminbench.derive.provider` cannot disagree.
"""

import re
from collections.abc import Iterable

LEGAL_SUFFIX = (
    r"(?:inc|ltd|llc|l\.l\.c|gmbh|co|corp|corporation|s\.a|b\.v|ag|plc|limited"
    r"|pty\.?\s*ltd)\.?"
)
# A comma before a trailing legal suffix ("Foo, Inc.") is not a separator.
# Also: " / ", " + ", "|", full-width ，、； and the Thai/Chinese "and" words
# between spaces. A "/" without a space before it ("A/S", a URL) is no separator.
MULTI = re.compile(
    rf"(?i);|\s&\s|\sand\s|\s[/+]\s?|\||[，、；]|\sและ\s|\s(?:和|與|及)\s"
    rf"|,(?!\s*{LEGAL_SUFFIX}\s*$)"
)
_LEGAL_PIECE = re.compile(rf"(?i){LEGAL_SUFFIX}")
# The suffix must start a word: "Tobacco" and "Marco" are not companies.
ORG_LIKE = re.compile(rf"(?i)(?<!\w){LEGAL_SUFFIX}$")


def has_line_break(text: str) -> bool:
    """Is the value several lines? Whitespace collapse would hide the break."""
    return len(text.strip().splitlines()) > 1


def merge_legal_suffixes(pieces: Iterable[str]) -> list[str]:
    """Join a piece that is only a legal suffix to the name before it.

    `["Foo", "Inc."]` -> `["Foo, Inc."]`; empty pieces are dropped.
    """
    out: list[str] = []
    for piece in (p.strip() for p in pieces):
        if out and _LEGAL_PIECE.fullmatch(piece):
            out[-1] = f"{out[-1]}, {piece}"
        elif piece:
            out.append(piece)
    return out
