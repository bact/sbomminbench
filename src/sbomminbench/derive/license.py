# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Layer 3: the expected licence of one subject, from its observations.

LicenseID is imported lazily and always called with `source="packaged"`.
The gate is `category`, never `expression is None` (`assumed_only` carries an
expression).
"""

import urllib.parse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Literal, cast

from sbomminbench import tags as T
from sbomminbench.classify import license_format
from sbomminbench.derive.origin import ORIGIN
from sbomminbench.license_entries import dict_name, entry_ref
from sbomminbench.schema import (
    ExpectedLicense,
    LicenseidTrail,
    LicenseValue,
    Observation,
    SemanticFormat,
    Status,
)

OK_CATEGORIES = frozenset(
    {
        "exact",
        "lexical",
        "plus_to_or_later",
        "stated_qualifier",
        "separator",
        "version_spelling",
        "listed_name",
        "deprecated_equal_successor",
        "prose_form",
        "text_title",
        "alias_name",
        "license_url",
        "trove_name",
    }
)
BLOCK_CATEGORIES = frozenset(
    {"assumed_only", "deprecated_ambiguous", "malformed", "unresolved"}
)
STRONG_TRUST = frozenset({"identity", "list_data"})

# Fields that name a licence file, not a licence.
_FILE_FIELDS = frozenset({"package.license-file", "licenseFile"})
_IGNORED_FORMATS = frozenset({"empty", "placeholder"})
_NON_VOTING_FORMATS = frozenset({"license_text", "file_ref"})
_HF_PREFIX = "license:"

type Role = Literal["vote", "weak", "block", "skip"]


@dataclass(frozen=True)
class Candidate:
    """One licence string to judge; `key` is the observation it came from."""

    value: str
    key: str
    forced: SemanticFormat | None = None
    decl: str = ""  # the declaration it belongs to (see `license_entries`)
    entry: str = ""  # the licence entry inside it

    @property
    def format(self) -> SemanticFormat:
        return self.forced or license_format(self.value)

    @classmethod
    def of(
        cls,
        o: Observation,
        value: str,
        forced: SemanticFormat | None = None,
        index: int | None = None,
    ) -> "Candidate":
        decl, entry = entry_ref(o, index)
        return cls(value, o.key, forced, decl, entry)


@dataclass(frozen=True)
class _Res:
    """The parts of a LicenseID resolution that derive reads (hashable)."""

    expression: str | None
    category: str
    trust: str
    reason: str | None
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class _Outcome:
    cand: Candidate
    fmt: SemanticFormat
    role: Role
    asked: str | None = None  # the string passed to LicenseID, if any
    res: _Res | None = None


@cache
def _resolve(value: str) -> _Res:
    from licenseid import resolve  # lazy: heavy import, derive-only

    r = cast(Mapping[str, object], resolve(value, source="packaged"))
    expression = r.get("expression")
    reason = r.get("reason")
    cands = r.get("candidates")
    return _Res(
        expression=expression if isinstance(expression, str) else None,
        category=str(r["category"]),
        trust=str(r["trust"]),
        reason=reason if isinstance(reason, str) else None,
        candidates=tuple(str(c) for c in cands) if isinstance(cands, list) else (),
    )


def candidates(o: Observation, by_key: Mapping[str, Observation]) -> list[Candidate]:
    """The licence strings an observation states (none for absent or non-text)."""
    if not o.present:
        return []
    raw = o.raw
    if isinstance(raw, str):
        return [_str_candidate(o, raw, by_key)]
    if isinstance(raw, list):
        return [c for i, item in enumerate(raw) for c in _item_candidates(o, item, i)]
    if isinstance(raw, dict):
        return _dict_candidates(o, raw, None)
    return []


def _str_candidate(
    o: Observation, raw: str, by_key: Mapping[str, Observation]
) -> Candidate:
    if o.raw_truncated:
        return Candidate.of(o, raw, "license_text")
    if o.field_path.endswith("@type") or o.field_path in _FILE_FIELDS:
        return Candidate.of(o, raw, "file_ref")
    sibling = by_key.get(f"{o.key}@type")
    if sibling is not None and sibling.present and sibling.raw == "file":
        return Candidate.of(o, raw, "file_ref")
    if o.source == "hf_api" and raw.startswith(_HF_PREFIX):
        return Candidate.of(o, raw.removeprefix(_HF_PREFIX))
    return Candidate.of(o, raw)


def _item_candidates(o: Observation, item: object, index: int) -> list[Candidate]:
    if isinstance(item, str):
        return [Candidate.of(o, item, index=index)]
    if isinstance(item, dict):
        return _dict_candidates(o, item, index)
    return []


def _dict_candidates(
    o: Observation, d: Mapping[str, object], index: int | None
) -> list[Candidate]:
    out: list[Candidate] = []
    if (name := dict_name(d)) is not None:
        out.append(Candidate.of(o, name, index=index))
    file = d.get("file")
    if isinstance(file, str):
        out.append(Candidate.of(o, file, "file_ref", index))
    url = d.get("url")
    if isinstance(url, str):
        out.append(Candidate.of(o, url, index=index))
    return out


def _nuget_expression(value: str) -> str:
    """`https://licenses.nuget.org/<percent-encoded expression>` -> expression.

    Workaround: LicenseID leaves percent-encoded expression URLs unresolved
    (bact/licenseid#187). Remove once a fixed LicenseID is pinned.
    """
    bare = value.strip("<>")
    parts = urllib.parse.urlsplit(bare if "://" in bare else "http://" + bare)
    return urllib.parse.unquote(parts.path[1:])


def _ask(
    c: Candidate, fmt: SemanticFormat, asked: str, ok_role: Role, blocking: bool = False
) -> _Outcome:
    """Resolve `asked`: an OK category earns `ok_role`; else block or skip."""
    res = _resolve(asked)
    if res.category in OK_CATEGORIES:
        if res.expression is None:
            raise ValueError(f"LicenseID: category {res.category!r}, no expression")
        return _Outcome(c, fmt, ok_role, asked, res)
    return _Outcome(c, fmt, "block" if blocking else "skip", asked, res)


def _judge(c: Candidate, troves_multiple: bool) -> _Outcome:
    fmt = c.format
    if fmt in _IGNORED_FORMATS or fmt in _NON_VOTING_FORMATS:
        return _Outcome(c, fmt, "skip")
    if fmt == "spdx_url":
        return _ask(c, fmt, c.value, "vote")
    if fmt == "nuget_license_url":
        asked = _nuget_expression(c.value)
        return _ask(c, fmt, asked, "vote") if asked else _Outcome(c, fmt, "skip")
    if fmt in {"osi_url", "other_url"}:
        return _ask(c, fmt, c.value, "weak")
    if fmt == "trove_classifier":
        if troves_multiple:
            return _Outcome(c, fmt, "skip")
        return _ask(c, fmt, c.value, "weak")
    return _ask(c, fmt, c.value, "vote", blocking=True)


def _block_tags(o: _Outcome) -> set[str]:
    ambiguous = o.res is not None and o.res.category in {
        "assumed_only",
        "deprecated_ambiguous",
    }
    tags = {T.LICENSE_ASSUMED_ONLY if ambiguous else T.LICENSE_UNRESOLVED_VALUE}
    if o.cand.key.startswith("hf_"):
        tags.add(T.LICENSE_REF_NEEDED)
    return tags


def _outcome_tags(o: _Outcome) -> set[str]:
    fmt, key = o.fmt, o.cand.key
    if fmt == "placeholder":
        if key.startswith("hf_") and o.cand.value.casefold() == "other":
            return {T.HF_LICENSE_OTHER}
        return {T.LICENSE_PLACEHOLDER}
    if fmt == "license_text":
        return {T.LICENSE_TEXT_IN_FIELD}
    if fmt in {"osi_url", "other_url"}:
        return {T.LICENSE_IS_URL}
    if fmt == "trove_classifier" and o.role == "skip" and o.res is not None:
        return {T.LICENSE_TROVE_AMBIGUOUS}
    if o.role == "block":
        return _block_tags(o)
    return set()


def _note_for_block(o: _Outcome) -> str:
    res = o.res
    if res is None:  # blocks always carry a resolution
        return f"{o.cand.value!r}: unresolved"
    return (
        f"{o.cand.value!r}: {res.category} ({res.reason or ', '.join(res.candidates)})"
    )


def _trail(outcomes: Iterable[_Outcome]) -> list[LicenseidTrail]:
    rows = {
        (
            o.cand.key,
            o.asked[:500],
            r.expression,
            r.category,
            r.trust,
            r.reason,
        )
        for o in outcomes
        if o.asked is not None and (r := o.res) is not None
    }
    return [
        LicenseidTrail(key=k, input=i, expression=e, category=c, trust=t, reason=reason)
        for k, i, e, c, t, reason in sorted(
            rows, key=lambda x: (x[0], x[1], x[2] or "")
        )
    ]


def _origins(basis: list[str]) -> int:
    sources = {k.split(":", 1)[0] for k in basis}
    return len({ORIGIN.get(s, s) for s in sources})


@dataclass
class _Decision:
    status: Status
    expression: str | None = None
    note: str | None = None
    candidates: list[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    trail: list[_Outcome] = field(default_factory=list)  # extra resolutions


def _entry_groups(outcomes: list[_Outcome]) -> list[dict[str, list[_Outcome]]]:
    """Declarations that list two or more licence entries: entry -> outcomes."""
    groups: dict[str, dict[str, list[_Outcome]]] = {}
    for o in outcomes:
        if o.fmt != "empty":  # an empty string states no entry
            by_entry = groups.setdefault(o.cand.decl, {})
            by_entry.setdefault(o.cand.entry, []).append(o)
    return [g for _, g in sorted(groups.items()) if len(g) > 1]


def _entry_exprs(entry: list[_Outcome]) -> set[str]:
    return {
        o.res.expression
        for o in entry
        if o.role in {"vote", "weak"} and o.res and o.res.expression
    }


def _or_expression(exprs: list[str], key: str) -> tuple[str, _Outcome]:
    """`A OR B`, canonicalised by LicenseID when it can; plus its trail row."""
    joined = " OR ".join(f"({e})" if " AND " in e else e for e in exprs)
    res = _resolve(joined)
    ok = res.category in OK_CATEGORIES and res.expression is not None
    outcome = _Outcome(Candidate(joined, key), "expression_shaped", "skip", joined, res)
    return (res.expression if ok and res.expression else joined), outcome


@dataclass
class _OrGroups:
    """Maven declarations read as `A OR B`: the outcomes they replace."""

    replaced: set[int] = field(default_factory=set)  # id() of the outcomes
    expressions: list[str] = field(default_factory=list)
    rows: list[_Outcome] = field(default_factory=list)  # their trail rows


def _multi_entry(outcomes: list[_Outcome]) -> _Decision | _OrGroups:
    """Rules for declarations with several licence entries.

    Every entry must vote. All entries voting: a Maven POM means the user may
    choose any (OR); anywhere else the author's intent is not known, so the
    OR expression is only offered as a suggestion.
    """
    groups = _OrGroups()
    for group in _entry_groups(outcomes):
        per_entry = [_entry_exprs(e) for e in group.values()]
        if any(not x for x in per_entry):
            return _Decision(
                "auto_unresolved",
                note="a licence entry of a multi-entry declaration states no licence",
                tags={T.LICENSE_ENTRY_UNRESOLVED},
            )
        distinct = sorted({e for x in per_entry for e in x})
        members = [o for e in group.values() for o in e]
        if len(distinct) < 2 or any(len(x) > 1 for x in per_entry):
            continue  # one licence, or entries contradicting themselves: conflict rule
        combined, row = _or_expression(distinct, min(o.cand.key for o in members))
        if not members[0].cand.decl.startswith("pom.xml:"):
            return _Decision(
                "auto_unresolved",
                note="several licence entries; the intended combination is unknown",
                candidates=[combined],
                tags={T.LICENSE_MULTIPLE_ENTRIES},
                trail=[row],
            )
        groups.replaced |= {id(o) for o in members}
        groups.expressions.append(combined)
        groups.rows.append(row)
    return groups


def _decide(outcomes: list[_Outcome]) -> _Decision:
    """The subject status and, where one is decided, its expression."""
    blocks = sorted(
        (o for o in outcomes if o.role == "block"),
        key=lambda o: (o.cand.key, o.cand.value),
    )
    votes = [o for o in outcomes if o.role in {"vote", "weak"}]
    if blocks:
        note = "; ".join(dict.fromkeys(_note_for_block(o) for o in blocks))
        return _Decision("auto_unresolved", note=note)
    if not votes:
        return _Decision(
            "auto_unresolved", note="only URL / file / text / ambiguous trove"
        )
    multi = _multi_entry(outcomes)
    if isinstance(multi, _Decision):
        return multi
    weak = [o for o in votes if o.role == "weak"]
    if len(weak) == len(votes) and any(
        o.res is not None and o.res.trust not in STRONG_TRUST for o in weak
    ):
        return _Decision("auto_unresolved", note="only weak votes without strong trust")
    exprs = {
        o.res.expression
        for o in votes
        if o.res and o.res.expression and id(o) not in multi.replaced
    } | set(multi.expressions)
    ordered = sorted(exprs)
    if len(ordered) != 1:
        return _Decision("auto_conflict", candidates=ordered)
    tags = {T.LICENSE_LIST_OR} if multi.expressions else set()
    return _Decision("auto_unanimous", ordered[0], tags=tags, trail=multi.rows)


def derive_license(
    platform: list[Observation], manifest: list[Observation]
) -> tuple[ExpectedLicense, list[str]]:
    """Expected licence and the derive-time challenge tags (sorted, unique)."""
    obs = sorted(
        (o for o in [*platform, *manifest] if o.element == "license"),
        key=lambda o: o.key,
    )
    by_key = {o.key: o for o in obs}
    cands = sorted(
        (c for o in obs for c in candidates(o, by_key)),
        key=lambda c: (c.key, c.value, c.forced or ""),
    )
    troves = {c.value for c in cands if c.format == "trove_classifier"}
    outcomes = [_judge(c, len(troves) > 1) for c in cands]
    tags: set[str] = set()
    for o in outcomes:
        tags |= _outcome_tags(o)
    if len(troves) > 1:
        tags.add(T.LICENSE_TROVE_MULTIPLE)
    live = [o for o in outcomes if o.fmt not in _IGNORED_FORMATS]
    if live and all(o.fmt == "file_ref" for o in live):
        tags.add(T.LICENSE_FILE_ONLY)
    if not live:
        return ExpectedLicense(status="absent_in_source"), sorted(tags)
    decision = _decide(outcomes)
    tags |= decision.tags
    if decision.status == "auto_conflict":
        tags.add(T.LICENSE_MULTI_SOURCE_DISAGREE)
    expression = decision.expression
    basis = sorted({o.cand.key for o in outcomes if o.role in {"vote", "weak"}})
    value = (
        LicenseValue(
            spdx_expression=expression, has_license_ref="LicenseRef-" in expression
        )
        if expression is not None
        else None
    )
    expected = ExpectedLicense(
        status=decision.status,
        value=value,
        basis=basis,
        candidates=decision.candidates,
        independent_origins=_origins(basis),
        note=decision.note,
        licenseid=_trail([*outcomes, *decision.trail]),
    )
    return expected, sorted(tags)
