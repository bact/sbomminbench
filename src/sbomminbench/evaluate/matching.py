# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Matching rules: licence equivalence, name keys and provider name sets.

Imports neither ``licenseid`` nor ``pitloom``: licence equivalence
comes from ``license-expression`` and the name key is re-implemented here.
"""

import functools
import re
import unicodedata
from collections.abc import Iterable
from typing import Any, Literal, cast, get_args

from license_expression import ExpressionError, get_spdx_licensing
from packaging.utils import canonicalize_name

from sbomminbench.schema import EMAIL_PLACEHOLDER, Ecosystem, NameValue, ProviderValue

type Verdict = Literal["exact", "equivalent", "different", "invalid", "missing"]
type Counts = tuple[int, int, int]  # tp, fp, fn

VERDICTS: tuple[Verdict, ...] = get_args(Verdict.__value__)
_MISSING_LICENSE = frozenset({"", "NOASSERTION", "NONE"})
_NAME_SEPARATORS = re.compile(r"[-_. ]+")
_TRAILING = re.compile(r"\s*(?:<([^<>]*)>|\(([^()]*)\))\s*$")
# An e-mail address or a URL: the only trailing <...> or (...) content that is dropped.
_CONTACT = re.compile(r"\s*(?:[^\s@]+@[^\s@]+|(?:[a-z][a-z0-9+.-]*://|www\.)\S+)\s*")
_COLLECTIVE = re.compile(
    r"\s*[,;&]?\s*(?:\band\s+others|\bet\s+al)\.?\s*$", re.IGNORECASE
)
_PUNCT = re.compile(r"[^\w\s]|_")
_CORPORATE = frozenset({"inc", "ltd", "llc", "gmbh", "corp", "co"})

# -- licence -------------------------------------------------------------------------


@functools.cache
def _licensing() -> Any:
    return get_spdx_licensing()


def _parse(text: str) -> Any | None:
    try:
        return _licensing().parse(text)
    except (ExpressionError, ValueError, TypeError, AttributeError):
        return None


def _unknown_keys(parsed: Any, gold_keys: frozenset[str]) -> list[str]:
    """Unknown keys that are neither ``LicenseRef-`` nor a key the gold also uses."""
    keys = cast(list[str], _licensing().unknown_license_keys(parsed, unique=True))
    return [
        k
        for k in keys
        if not k.startswith("LicenseRef-") and k.casefold() not in gold_keys
    ]


def license_equivalent(pred: str | None, gold: str) -> Verdict:
    """Compare a predicted licence expression with the gold expression.

    ``missing``: None, empty, NOASSERTION or NONE. ``exact``: equal after
    stripping. ``invalid``: a parse error, or an unknown key that is not
    ``LicenseRef-`` and not a key the gold uses (list-version gaps such as
    ``OpenMDW-1.0`` are not the tool's fault). ``equivalent``: same expression
    by ``license-expression`` (case, order, parentheses, ``+`` and ``-or-later``
    spellings). Otherwise ``different``.
    """
    if pred is None or pred.strip().upper() in _MISSING_LICENSE:
        return "missing"
    if pred.strip() == gold:
        return "exact"
    parsed = _parse(pred)
    if parsed is None:
        return "invalid"
    gold_parsed = _parse(gold)
    gold_keys = (
        frozenset(k.casefold() for k in _licensing().license_keys(gold_parsed))
        if gold_parsed is not None
        else frozenset[str]()
    )
    if _unknown_keys(parsed, gold_keys):
        return "invalid"
    if gold_parsed is not None and _licensing().is_equivalent(parsed, gold_parsed):
        return "equivalent"
    return "different"


# -- name ----------------------------------------------------------------------------


def canonical_key(eco: Ecosystem, name: str) -> str:
    """The key under which two spellings of one name compare equal."""
    match eco:
        case "pypi":
            return str(canonicalize_name(name))
        case "cargo":
            return name.lower().replace("_", "-")
        case "nuget":
            return name.lower()
        case "huggingface":
            return name.casefold()
        case "npm" | "maven":
            return name


def _lenient_name(text: str) -> str:
    return _NAME_SEPARATORS.sub("-", text.casefold())


def name_match(eco: Ecosystem, pred: str | None, gold: NameValue) -> tuple[bool, bool]:
    """Return ``(strict, spelling_tolerant)``; spelling-tolerant includes strict.

    The prediction is stripped of surrounding whitespace first. Strict: the
    ecosystem's own identity key (PEP 503 for PyPI, ``-``/``_`` for Cargo,
    case for NuGet and Hugging Face) of the prediction equals the key of the
    gold name or of any accepted name; npm and Maven compare exactly. It is the
    headline. Spelling-tolerant: separators (``-``, ``_``, ``.``, space) and case
    are also ignored. That can credit a *different* package (npm ``my.pkg`` for
    ``my-pkg``, Maven ``org-foo:bar`` for ``org.foo:bar``): report it only
    beside strict, never as the headline.
    """
    text = None if pred is None else pred.strip()
    if not text:
        return (False, False)
    key = canonical_key(eco, text)
    accepted = {canonical_key(eco, a) for a in gold.accepted}
    if key == gold.canonical_key or key in accepted:
        return (True, True)
    wanted = _lenient_name(text)
    candidates = [*gold.accepted, gold.canonical_key]
    return (False, any(_lenient_name(c) == wanted for c in candidates))


# -- provider ------------------------------------------------------------------------


def _strip_trailing(text: str) -> str:
    """Drop trailing ``<...>``/``(...)`` groups that hold an e-mail or a URL."""
    while True:
        m = _TRAILING.search(text)
        inner = (m.group(1) or m.group(2) or "") if m else ""
        if m is None or not _CONTACT.fullmatch(inner):
            return text.strip()
        text = text[: m.start()]


def normalise_provider(name: str, *, lenient: bool) -> str:
    """Normalise one provider name; ``""`` means nothing to compare.

    Strict: NFKC, casefold, collapsed whitespace, trailing ``<e-mail>``,
    ``(e-mail)`` and ``<URL>`` removed (any other bracketed text is part of the
    name). Lenient also drops punctuation and corporate words.
    """
    text = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    text = _strip_trailing(text)
    if EMAIL_PLACEHOLDER in text:
        return ""
    if not lenient:
        return text
    words = [w for w in _PUNCT.sub(" ", text).split() if w not in _CORPORATE]
    return " ".join(words) or text


def _provider_set(names: Iterable[str], *, lenient: bool) -> frozenset[str]:
    return frozenset(
        n for n in (normalise_provider(x, lenient=lenient) for x in names) if n
    )


def provider_counts(
    pred: list[str] | None, gold: ProviderValue, *, lenient: bool
) -> tuple[Counts, int]:
    """Return ``((tp, fp, fn), unmatched_under_group)``.

    When the gold list is incomplete (``has_unnamed_group``), a predicted name
    that matches no gold name is not a false positive (the gold cannot tell):
    it is counted in ``unmatched_under_group`` instead, and a collective tail
    (``et al.``, ``and others``) is stripped from the prediction first.
    """
    names = pred or []
    if gold.has_unnamed_group:
        names = [_COLLECTIVE.sub("", n) for n in names]
    got = _provider_set(names, lenient=lenient)
    want = _provider_set((e.name for e in gold.entities), lenient=lenient)
    extra = len(got - want)
    if gold.has_unnamed_group:
        return ((len(got & want), 0, len(want - got)), extra)
    return ((len(got & want), extra, len(want - got)), 0)


def provider_prf(
    pred: list[str] | None, gold: ProviderValue, *, lenient: bool
) -> Counts:
    """Return ``(tp, fp, fn)`` of the predicted name set against the gold set."""
    return provider_counts(pred, gold, lenient=lenient)[0]
