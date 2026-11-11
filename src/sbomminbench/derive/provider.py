# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Expected provider (layer 3): the authors a package or model declares.

Only author roles vote (Hugging Face: the owner). Maintainers, contributors,
publishers and organisations are never part of the expected provider.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from sbomminbench import authors_rules, tags
from sbomminbench.derive.origin import ORIGIN
from sbomminbench.schema import (
    EMAIL_PLACEHOLDER,
    Ecosystem,
    ExpectedProvider,
    JsonValue,
    Observation,
    ProviderEntity,
    ProviderValue,
    Role,
)

_MULTI = authors_rules.MULTI
_COLLECTIVE_TAIL = re.compile(
    r"(?i)(?:\s*(?P<join>,|;|\band\b|&)\s*"
    r"(?:(?:its|the|all|other|individual)\s+)?"
    r"(?P<word>contributors|others|authors)(?:\s*\(see\s+authors[^)]*\))?"
    r"|\s+et\.?\s+al\.?)\s*$"
)
_COLLECTIVE_WHOLE = re.compile(
    r"(?i)^\s*(?:and\s+)?(?:others|et\.?\s+al\.?|contributors)"
    r"(?:\s*\(see\s+authors[^)]*\))?\s*$"
)
_PLACEHOLDER_NAMES = frozenset(
    {
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
        "-",
        "--",
        "?",
        "tbd",
        "todo",
        "author",
        "your name",
        "name",
        "authors",
        "the authors",
        "team",
        "developers",
        "maintainers",
        "contributors",
    }
)
_NPM_PERSON = re.compile(r"^\s*(?P<name>[^<(]*?)\s*(?:<[^>]*>)?\s*(?:\([^)]*\))?\s*$")
_URL_NAME = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)\S+")
_ANGLE_NAME = re.compile(r"^\s*(?P<name>.*?)\s*(?:<[^>]*>)?\s*$")
VALUE_PRIORITY = [
    "pyproject.toml",
    "PKG-INFO",
    "METADATA",
    "pypi_json",
    "package.json",
    "npm_registry",
    "Cargo.toml",
    "Cargo.toml.orig",
    "pom.xml",
    "nuspec",
    "nuget_catalog",
    "hf_api",
]

_ORG_LIKE = authors_rules.ORG_LIKE
_COLLECTIVE_ENDING = re.compile(r"(?i)\b(?:contributors|team)$")
_EMAIL_FRAGMENT = re.compile(rf"\s*[<(]?\s*{re.escape(EMAIL_PLACEHOLDER)}\s*[>)]?")
_NATURAL = re.compile(r"(\d+)")
_OTHER_ROLES = frozenset({"maintainer", "owner", "contributor"})
_FREE_TEXT = {
    "pypi_json": ("info.author", "info.author_email"),
    "PKG-INFO": ("Author", "Author-email"),
    "METADATA": ("Author", "Author-email"),
}


def split_nuget_authors(s: str) -> list[str]:
    """Split a NuGet ``authors`` string on commas, keeping ``Foo, Inc.`` whole.

    Heuristic limit: ``Smith, John`` (surname first) and ``Foo, Bar`` look the
    same as two one-word authors. A comma list of exactly two pieces with a
    one-word piece is therefore left whole, which derive reads as several
    authors in one string.
    """
    out = authors_rules.merge_legal_suffixes(s.split(","))
    if len(out) == 2 and any(len(p.split()) == 1 for p in out):
        return [", ".join(out)]
    return out


def _natural_key(path: str) -> list[tuple[int, int, str]]:
    """Sort ``a[2]`` before ``a[10]`` with one comparable tuple type."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in _NATURAL.split(path)]


def _text(raw: JsonValue) -> str | None:
    """A non-blank string raw, else None (null, empty or wrong type)."""
    return raw if isinstance(raw, str) and raw.strip() else None


def _read_fields(
    author: str | None, email: str | None, out_tags: list[str]
) -> list[str]:
    """Names Pitloom reads from an author and an address field.

    An address-only result adds ``author_email_only``. Pitloom is imported
    here because its import pulls in the whole SBOM stack.
    """
    from pitloom.extract.authors import read_author_fields

    parsed = read_author_fields(author, email, quiet=True)
    if any("name" not in d for d in parsed):
        out_tags.append(tags.AUTHOR_EMAIL_ONLY)
    # Pitloom splits `Jane Doe, Inc. <mail>` at the comma; the suffix belongs
    # to the name.
    return authors_rules.merge_legal_suffixes(d["name"] for d in parsed if "name" in d)


def _free_text(source: str, live: list[Observation], out_tags: list[str]) -> list[str]:
    """PyPI author and address fields, read together by Pitloom."""
    author_path, email_path = _FREE_TEXT[source]
    raws: dict[str, JsonValue] = {o.field_path: o.raw for o in live}
    for path in (author_path, email_path):
        if raws.get(path) is not None and not isinstance(raws[path], str):
            out_tags.append(tags.SCHEMA_TYPE_VIOLATION)
    return _read_fields(
        _text(raws.get(author_path)), _text(raws.get(email_path)), out_tags
    )


def _person(pattern: re.Pattern[str], text: str) -> str:
    """The name part of ``Name <mail> (url)``; angle form when it has no match."""
    m = pattern.fullmatch(text) or _ANGLE_NAME.fullmatch(text)
    return text if m is None else m.group("name")


def _dict_item(item: dict[str, JsonValue], out_tags: list[str]) -> list[str]:
    """``{"name": ..., "email": ...}``; an address-only dict states no name."""
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        return [name]
    if name is not None and not isinstance(name, str):
        out_tags.append(tags.SCHEMA_TYPE_VIOLATION)
    elif item.get("email") is not None:
        out_tags.append(tags.AUTHOR_EMAIL_ONLY)
    return []


def _list_items(raw: JsonValue, out_tags: list[str]) -> list[JsonValue]:
    """Items of an authors list; a lone string is one item and a violation."""
    if isinstance(raw, list):
        return raw
    if raw is None or (isinstance(raw, dict) and "workspace" in raw):
        return []  # Cargo `{workspace = true}` inherits; nothing stated here
    out_tags.append(tags.SCHEMA_TYPE_VIOLATION)
    return [raw] if isinstance(raw, str) else []


def _authors_list(path: str, raw: JsonValue, out_tags: list[str]) -> list[str]:
    """pyproject (dict items), poetry and Cargo (``Name <mail>`` strings)."""
    found: list[str] = []
    for item in _list_items(raw, out_tags):
        if isinstance(item, dict):
            found += _dict_item(item, out_tags)
        elif isinstance(item, str) and path == "project.authors":
            out_tags.append(tags.SCHEMA_TYPE_VIOLATION)
            found += _read_fields(item, None, out_tags)
        elif isinstance(item, str):
            found.append(_person(_ANGLE_NAME, item))
        else:
            out_tags.append(tags.SCHEMA_TYPE_VIOLATION)
    return found


def _npm_author(raw: JsonValue, out_tags: list[str]) -> list[str]:
    if isinstance(raw, str):
        return [_person(_NPM_PERSON, raw)]
    if isinstance(raw, dict):
        out_tags.append(tags.AUTHOR_OBJECT_FORM)
        return _dict_item(raw, out_tags)
    if raw is not None:
        out_tags.append(tags.SCHEMA_TYPE_VIOLATION)
    return []


def _nuget_authors(raw: JsonValue, out_tags: list[str]) -> list[str]:
    if isinstance(raw, str):
        return split_nuget_authors(raw)
    if raw is not None:
        out_tags.append(tags.SCHEMA_TYPE_VIOLATION)
    return []


def _one_observation(source: str, o: Observation, out_tags: list[str]) -> list[str]:
    path, raw = o.field_path, o.raw
    if source in {"pyproject.toml", "Cargo.toml", "Cargo.toml.orig"}:
        if path in {"project.authors", "tool.poetry.authors", "package.authors"}:
            return _authors_list(path, raw, out_tags)
    elif source in {"npm_registry", "package.json"}:
        if path == "author":
            return _npm_author(raw, out_tags)
    elif source == "pom.xml":
        # developers.developer[i].name, also under a parent[n]. prefix
        if path.endswith(".name") and (text := _text(raw)):
            return [text]
    elif source in {"nuget_catalog", "nuspec"}:
        if path in {"authors", "metadata.authors"}:
            return _nuget_authors(raw, out_tags)
    elif source == "hf_api" and path == "author" and (text := _text(raw)):
        return [text]
    return []


def _raw_strings(raw: JsonValue) -> list[str]:
    """Author strings of a raw value: itself, list items, ``name`` of objects."""
    items = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for item in items:
        value = item.get("name") if isinstance(item, dict) else item
        if isinstance(value, str):
            out.append(value)
    return out


def names_from_source(
    source: str, obs: list[Observation]
) -> tuple[list[str], list[str]]:
    """Return ``(names, tags)`` one source states; names are not yet cleaned.

    ``obs`` are the author-role observations of ``source``. Absent, null and
    empty values state nothing; a value of the wrong type adds
    ``schema_type_violation``. Observation order does not matter.
    """
    live = sorted(
        (o for o in obs if o.present), key=lambda o: _natural_key(o.field_path)
    )
    out_tags: list[str] = []
    # Whitespace collapse (here and in Pitloom) hides a line break: test raw.
    if any(authors_rules.has_line_break(t) for o in live for t in _raw_strings(o.raw)):
        out_tags.append(tags.AUTHOR_MULTI_IN_ONE_STRING)
    if source in _FREE_TEXT:
        names = _free_text(source, live, out_tags)
    else:
        names = [n for o in live for n in _one_observation(source, o, out_tags)]
    return names, sorted(set(out_tags))


def _drop_email(name: str) -> tuple[str, bool]:
    """Remove the redacted address (``<...>`` or bare); say whether one was there."""
    cleaned = " ".join(_EMAIL_FRAGMENT.sub(" ", name).split())
    return cleaned, cleaned != name


def _is_placeholder(name: str) -> bool:
    return name.casefold() in _PLACEHOLDER_NAMES


def _strip_collective(name: str) -> tuple[str, bool, bool]:
    """Cut trailing ``and others`` style tails until none is left.

    Returns ``(name, group, kept)``. ``("", True, False)``: nothing but a
    collective remains. ``kept``: a capitalised ``& Authors`` / ``& Others`` is
    part of a name (``Law & Authors``) and stays.
    """
    group = False
    while name:
        if _COLLECTIVE_WHOLE.match(name):
            return "", True, False
        m = _COLLECTIVE_TAIL.search(name)
        if m is None:
            break
        word = m.group("word") or ""
        if (
            m.group("join") == "&"
            and word[:1].isupper()
            and word.lower() != "contributors"
        ):
            return name, group, True
        name, group = name[: m.start()].rstrip(" ,;"), True
    return name, group, False


def _clean_one(raw: str, out_tags: set[str]) -> tuple[str, bool]:
    """Clean one name; ``""`` means dropped. Second item: a group was seen."""
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    name, had_email = _drop_email(name)
    if not name:
        if had_email:
            out_tags.add(tags.AUTHOR_EMAIL_ONLY)
        return "", False
    if _URL_NAME.fullmatch(name):
        out_tags.add(tags.AUTHOR_URL_ONLY)  # a link, not a name
        return "", False
    name, group, kept = _strip_collective(name)
    if not name or _is_placeholder(name):
        return "", group
    if kept or _COLLECTIVE_ENDING.search(name):
        out_tags.add(tags.AUTHOR_CONTAINS_COLLECTIVE)
    if _ORG_LIKE.search(name):
        out_tags.add(tags.AUTHOR_ORG_LIKE)
    return name, group


def cleanup(names: list[str]) -> tuple[list[str], bool, list[str]]:
    """Return ``(names, has_unnamed_group, tags)``.

    NFC and whitespace are normalised, the redacted address and placeholders
    are dropped, collective tails (``and others``, ``et al.``) are cut and
    flag the group, and duplicates (case-folded) keep the first spelling.
    """
    out_tags: set[str] = set()
    result: dict[str, str] = {}
    group = False
    for raw in names:
        name, was_group = _clean_one(raw, out_tags)
        group |= was_group
        if name:
            result.setdefault(name.casefold(), name)
    return list(result.values()), group, sorted(out_tags)


@dataclass(frozen=True)
class _SourceNames:
    source: str
    names: tuple[str, ...]
    group: bool
    keys: tuple[str, ...]  # observation keys that were read for this source

    @property
    def folded(self) -> frozenset[str]:
        return frozenset(n.casefold() for n in self.names)


@dataclass
class _Collected:
    sources: list[_SourceNames] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)


def _has_content(raw: JsonValue) -> bool:
    return raw is not None and raw != "" and raw != [] and raw != {}


def _collect(role: Role, observations: list[Observation]) -> _Collected:
    """Read, clean and group the author observations by source."""
    by_source: dict[str, list[Observation]] = {}
    for o in observations:
        if o.element == "provider" and o.role == role:
            by_source.setdefault(o.source, []).append(o)
    collected = _Collected()
    for source in sorted(by_source):
        raw_names, src_tags = names_from_source(source, by_source[source])
        names, group, clean_tags = cleanup(raw_names)
        collected.tags.update(src_tags, clean_tags)
        if names or group:
            keys = sorted(o.key for o in by_source[source] if _has_content(o.raw))
            collected.sources.append(
                _SourceNames(source, tuple(names), group, tuple(keys))
            )
    return collected


def _priority(s: _SourceNames) -> tuple[int, str]:
    known = s.source in VALUE_PRIORITY
    return (VALUE_PRIORITY.index(s.source) if known else len(VALUE_PRIORITY), s.source)


def _absent(role: Role, observations: list[Observation]) -> list[str]:
    """Tags for a subject without authors: only other roles, when any exist."""
    other = any(
        o.element == "provider"
        and o.present
        and o.role in _OTHER_ROLES
        and _has_content(o.raw)
        for o in observations
    )
    return [tags.AUTHOR_ABSENT_MAINTAINER_ONLY] if other else []


def _reconcile_joined(sources: list[_SourceNames]) -> list[_SourceNames]:
    """Read a free-text list joined by a tool as the structured list it came from.

    setuptools writes pyproject ``[{name = "A"}, {name = "B"}]`` as the single
    Author string ``A, B``. When the comma pieces of a free-text source are
    exactly (case-folded) the names of a structured source, the structured
    names stand for it.
    """
    structured = [s for s in sources if s.source not in _FREE_TEXT and len(s.names) > 1]
    out: list[_SourceNames] = []
    for s in sources:
        pieces = (
            authors_rules.merge_legal_suffixes(s.names[0].split(","))
            if s.source in _FREE_TEXT and len(s.names) == 1
            else []
        )
        folded = frozenset(p.casefold() for p in pieces)
        match = next((t for t in structured if t.folded == folded), None)
        out.append(
            _SourceNames(s.source, match.names, s.group, s.keys)
            if match is not None and len(pieces) > 1
            else s
        )
    return out


def _is_multi(sources: list[_SourceNames]) -> bool:
    return any(_MULTI.search(n) for s in sources for n in s.names) or any(
        not s.names for s in sources
    )


def derive_provider(
    eco: Ecosystem, platform: list[Observation], manifest: list[Observation]
) -> tuple[ExpectedProvider, list[str]]:
    """Return the expected provider and the derive-time tags (see the spec table)."""
    observations = [*platform, *manifest]
    role: Role = "owner" if eco == "huggingface" else "author"
    collected = _collect(role, observations)
    sources = _reconcile_joined(sorted(collected.sources, key=_priority))
    if not sources:
        extra = [] if eco == "huggingface" else _absent(role, observations)
        return ExpectedProvider(status="absent_in_source"), sorted(
            collected.tags | set(extra)
        )
    basis = sorted({k for s in sources for k in s.keys})
    origins = len({ORIGIN[s.source] for s in sources})
    common = {"basis": basis, "independent_origins": origins}
    if _is_multi(sources) or tags.AUTHOR_MULTI_IN_ONE_STRING in collected.tags:
        collected.tags.add(tags.AUTHOR_MULTI_IN_ONE_STRING)
        expected = ExpectedProvider(status="auto_unresolved", **common)
    elif len({s.folded for s in sources}) == 1:
        value = ProviderValue(
            entities=[ProviderEntity(name=n, role=role) for n in sources[0].names],
            has_unnamed_group=any(s.group for s in sources),
        )
        expected = ExpectedProvider(status="auto_unanimous", value=value, **common)
    else:
        candidates = sorted({" | ".join(s.names) for s in sources})
        expected = ExpectedProvider(
            status="auto_conflict", candidates=candidates, **common
        )
    return expected, sorted(collected.tags)
