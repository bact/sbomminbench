# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Replace every e-mail address in a raw value by a fixed placeholder.

Applied when a value is read, before an ``Observation`` is built. Surrounding
syntax stays (``<...>``, ``mailto:``, URL userinfo). A purl (``pkg:`` token) has
an ``@`` that separates a version (``windup-exec@6.3.9.Final`` looks like an
address), so its core, up to ``?`` or ``#``, is masked: never detected, never
rewritten. Qualifiers and text around the token are still scanned.
"""

import re
from typing import Final

from sbomminbench.schema import JsonValue

PLACEHOLDER: Final = "email.redacted@example.com"
# \w is Unicode-aware, so a local part such as "josé" is covered.
_LOCAL = r"[\w.+-]+"
_DOMAIN = r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
EMAIL_RE: Final = re.compile(
    rf"(?<![\w.+-]){_LOCAL}(?:@|%40){_DOMAIN}(?![A-Za-z0-9-])", re.IGNORECASE
)
OBFUSCATED_RE: Final = re.compile(
    r"""(?ix)
    [A-Za-z0-9._+-]+\s*(?:\[at\]|\(at\)|\{at\})\s*[A-Za-z0-9-]+
        (?:\s*(?:\[dot\]|\(dot\)|\{dot\}|\.)\s*[A-Za-z0-9-]+)+
  | [A-Za-z0-9._+-]+\s+at\s+[A-Za-z0-9-]+(?:\s+dot\s+[A-Za-z0-9-]+)+"""
)
# The purl core ends at whitespace, a quote, , ; < > ) ] or a qualifier/subpath mark.
PURL_RE: Final = re.compile(r"""(?<!\w)pkg:[^\s"',;<>)\]?#]+""", re.IGNORECASE)


def _split_purls(text: str) -> list[tuple[bool, str]]:
    """Split *text* into ``(is_purl, piece)`` pieces; joining them gives *text*."""
    pieces: list[tuple[bool, str]] = []
    last = 0
    for m in PURL_RE.finditer(text):
        pieces.append((False, text[last : m.start()]))
        pieces.append((True, m.group(0)))
        last = m.end()
    pieces.append((False, text[last:]))
    return pieces


def redact_emails(text: str) -> tuple[str, bool]:
    """Return ``(redacted text, whether an address was replaced)``."""
    found = False

    def sub(m: re.Match[str]) -> str:
        nonlocal found
        found |= m.group(0) != PLACEHOLDER
        return PLACEHOLDER

    out = [
        piece if is_purl else EMAIL_RE.sub(sub, OBFUSCATED_RE.sub(sub, piece))
        for is_purl, piece in _split_purls(text)
    ]
    return "".join(out), found


def redact_json(value: JsonValue) -> tuple[JsonValue, bool]:
    """Redact every string value recursively; dict keys are untouched."""
    if isinstance(value, str):
        return redact_emails(value)
    if isinstance(value, list):
        items = [redact_json(v) for v in value]
        return [v for v, _ in items], any(f for _, f in items)
    if isinstance(value, dict):
        pairs = {k: redact_json(v) for k, v in value.items()}
        return {k: v for k, (v, _) in pairs.items()}, any(f for _, f in pairs.values())
    return value, False


def find_emails(text: str) -> list[str]:
    """Return addresses other than the placeholder, in order (for validation).

    Purl cores are masked, so no caller needs its own ``pkg:`` check.
    """
    return [
        m.group(0)
        for is_purl, piece in _split_purls(text)
        if not is_purl
        for m in EMAIL_RE.finditer(piece)
        if m.group(0) != PLACEHOLDER
    ]
