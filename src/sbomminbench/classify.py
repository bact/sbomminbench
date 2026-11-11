# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Licence value classifiers: form (`license_format`) and validity.

Independent of LicenseID and Pitloom: SPDX validity comes from
`packaging.licenses` (private `_spdx` tables, `packaging==26.3`).
"""

import re
import unicodedata
import urllib.parse

from packaging.licenses import (
    InvalidLicenseExpression,
    canonicalize_license_expression,
)

from sbomminbench.schema import FormatValidity, SemanticFormat
from sbomminbench.schema import native_type_of as native_type_of  # noqa: PLC0414

try:  # private module: degrade to "no deprecation data" if its shape changes
    from packaging.licenses import _spdx

    _LICENSES: dict[str, object] = dict(_spdx.LICENSES)
    _EXCEPTIONS: dict[str, object] = dict(_spdx.EXCEPTIONS)
    SPDX_LIST_VERSION: str = str(_spdx.VERSION)
except (ImportError, AttributeError, TypeError, ValueError):
    _LICENSES, _EXCEPTIONS, SPDX_LIST_VERSION = {}, {}, "unknown"

HF_LICENSE_PREFIX = "license:"  # hf_api tags[] licence rows

PLACEHOLDERS = frozenset(
    {
        "unknown",
        "noassertion",
        "none",
        "n/a",
        "na",
        "null",
        "-",
        "other",
        "undefined",
        "non-standard",
    }
)
_ID = (
    r"(?:LicenseRef-[A-Za-z0-9.-]+"
    r"|DocumentRef-[A-Za-z0-9.-]+:LicenseRef-[A-Za-z0-9.-]+"
    r"|[A-Za-z0-9][A-Za-z0-9.-]*\+?)"
)
_ID_RE = re.compile(_ID)
_URL = re.compile(
    r"(?i)<?(?:[a-z][a-z0-9+.-]*://\S*|www\.\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*)>?"
)
_FILE_PREFIX = re.compile(r"(?i)see\s+licen[cs]e\s+in\s+\S+|file:\s*\S+")
_FILE_WORD = re.compile(r"(?i)licen[cs]e|copying|notice")
_FILE_PATH = re.compile(r"[\w./\\-]+")
# A bare licence file name, with an optional suffix: LICENSE-MIT, COPYING.txt.
_FILE_BARE = re.compile(
    r"(?i)(?:licen[cs]e|copying|notice)(?:[-_][a-z0-9]+)*(?:\.[a-z]\w*)?"
)
# A name ending in an alphabetic extension: "MIT-License.txt" (not "License_2.0").
_FILE_EXT = re.compile(r"[\w-]*\.[A-Za-z]\w{0,4}")
_NONSTD = re.compile(rf"{_ID}(?:\s*(?:/|;|,|\s&\s)\s*{_ID})+")
_TOK = re.compile(r"\(|\)|[^\s()]+")
_OPS = frozenset({"AND", "OR", "WITH"})
# PEP 639 lacks DocumentRef
_DOCREF = re.compile(r"DocumentRef-[A-Za-z0-9.-]+:(LicenseRef-[A-Za-z0-9.-]+)")
_SPDX_PATH_ID = re.compile(r"/licenses/([A-Za-z0-9.+-]+?)(?:\.html|\.json)?/?")
_SEVERITY: tuple[FormatValidity, ...] = (
    "valid",
    "noncanonical",
    "deprecated",
    "unknown_id",
    "malformed",
)


def _hidden(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch) in {"Cc", "Cf"}


def _trim(v: str) -> tuple[str, bool]:
    """Strip whitespace and control/format characters at both ends.

    Returns (value, hidden_stripped); plain whitespace does not count as hidden.
    """
    start, end = 0, len(v)
    while start < end and _hidden(v[start]):
        start += 1
    while end > start and _hidden(v[end - 1]):
        end -= 1
    stripped = (*range(start), *range(end, len(v)))
    return v[start:end], any(not v[i].isspace() for i in stripped)


def _well_formed(toks: list[str]) -> bool:
    """Operand/operator alternation, brackets balanced, no empty bracket."""
    depth, expect_operand = 0, True
    for t in toks:
        if t == "(":
            if not expect_operand:
                return False
            depth += 1
        elif t == ")":
            depth -= 1
            if depth < 0 or expect_operand:
                return False
        elif t.upper() in _OPS:
            if expect_operand:
                return False
            expect_operand = True
        else:
            if not expect_operand:
                return False
            expect_operand = False
    return depth == 0 and not expect_operand


def _expression_shaped(v: str) -> bool:
    toks = _TOK.findall(v)
    if len(toks) < 2:
        return False
    all_ok = all(t in "()" or t.upper() in _OPS or _ID_RE.fullmatch(t) for t in toks)
    if all_ok and (_OPS & set(toks) or v.startswith("(")):
        return True
    return _well_formed(toks)


def _url_parts(v: str) -> urllib.parse.SplitResult:
    bare = v.strip("<>")
    return urllib.parse.urlsplit(bare if "://" in bare else "http://" + bare)


def _file_like(v: str) -> bool:
    """Real file evidence: a prefix, a path separator, an extension or a bare name.

    A name that merely contains "licence" (`Unlicense`, `MIT-License`,
    `llama3.1-community-license`) is not a file.
    """
    if _FILE_PREFIX.fullmatch(v):
        return True
    if not _FILE_PATH.fullmatch(v) or not _FILE_WORD.search(v):
        return False
    return bool(
        "/" in v or "\\" in v or _FILE_BARE.fullmatch(v) or _FILE_EXT.fullmatch(v)
    )


def _spdx_id(v: str) -> bool:
    """Is *v* (case-insensitively, `+` allowed) a listed SPDX licence or exception?"""
    key = v.lower().removesuffix("+")
    return key in _LICENSES or key in _EXCEPTIONS


def license_format(value: str) -> SemanticFormat:
    """Classify the form of a licence string (independent of LicenseID)."""
    v, _ = _trim(value)
    if not v:
        return "empty"
    if "\n" in v or len(v) > 200:
        return "license_text"
    if v.casefold() in PLACEHOLDERS:
        return "placeholder"
    if _URL.fullmatch(v):
        u = _url_parts(v)
        host = u.netloc.lower().removeprefix("www.")
        if host == "spdx.org" and u.path.startswith("/licenses/"):
            return "spdx_url"
        if host == "opensource.org":
            return "osi_url"
        if host == "licenses.nuget.org":
            return "nuget_license_url"
        return "other_url"
    if v.startswith("License ::"):
        return "trove_classifier"
    if not _spdx_id(v) and _file_like(v):
        return "file_ref"
    if _ID_RE.fullmatch(v):
        return "id_shaped"
    if _expression_shaped(v):
        return "expression_shaped"
    if _NONSTD.fullmatch(v):
        return "nonstandard_compound"
    return "prose"


def _deprecated(key: str) -> bool:
    entry = _LICENSES.get(key) or _EXCEPTIONS.get(key)
    return isinstance(entry, dict) and entry.get("deprecated") is True


def spdx_validity(v: str) -> FormatValidity:
    """Validity of an SPDX id or expression against the packaging list."""
    s = _DOCREF.sub(r"\1", v)
    try:
        c = canonicalize_license_expression(s)
    except InvalidLicenseExpression as e:
        return "unknown_id" if str(e).startswith("Unknown license") else "malformed"
    keys = [
        t.removesuffix("+").lower()
        for t in _TOK.findall(c)
        if t not in "()" and t not in _OPS
    ]
    if any(_deprecated(k) for k in keys):
        return "deprecated"
    same = [t for t in _TOK.findall(s) if t not in "()"] == [
        t for t in _TOK.findall(c) if t not in "()"
    ]
    return "valid" if same else "noncanonical"


def worst(*items: FormatValidity) -> FormatValidity:
    """The most severe validity (malformed > unknown_id > deprecated > ...)."""
    return max(items, key=_SEVERITY.index)


def _url_validity(v: str) -> FormatValidity:
    u = _url_parts(v)
    has_scheme = "://" in v.strip("<>")
    if has_scheme and u.scheme.lower() not in {"http", "https"}:
        return "malformed"
    if "." not in u.netloc:
        return "malformed"
    return "noncanonical" if (not has_scheme or v.startswith("<")) else "valid"


def _nested_validity(v: str, fmt: SemanticFormat) -> FormatValidity:
    url = _url_validity(v)
    path = _url_parts(v).path
    if fmt == "spdx_url":
        m = _SPDX_PATH_ID.fullmatch(path)
        return worst(url, spdx_validity(m.group(1)) if m else "malformed")
    if fmt == "nuget_license_url":
        ident = urllib.parse.unquote(path[1:])
        return worst(url, spdx_validity(ident) if ident else "malformed")
    return url


def format_validity(raw: str, fmt: SemanticFormat) -> FormatValidity:
    """Validity of `raw` given its form; `not_applicable` where nothing is checkable."""
    v, hidden = _trim(raw)
    if fmt in {"id_shaped", "expression_shaped"}:
        result = spdx_validity(v)
    elif fmt in {"spdx_url", "nuget_license_url", "osi_url", "other_url"}:
        result = _nested_validity(v, fmt)
    else:
        return "not_applicable"
    return worst(result, "noncanonical") if hidden else result
