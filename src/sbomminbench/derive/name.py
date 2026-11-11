# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Expected package or model name (layer 3).

The name is the key set of every voting observation plus the seed name. One
key gives ``auto_unanimous``, several give ``auto_conflict``, none give
``absent_in_source``.
"""

import re

from sbomminbench import tags
from sbomminbench.derive.origin import ORIGIN
from sbomminbench.ids import canonical_name_key
from sbomminbench.schema import (
    PLATFORM_SOURCES,
    Ecosystem,
    ExpectedName,
    NameValue,
    Observation,
)

_SEPARATORS = re.compile(r"[-_.]+")

VOTING_NAME_FIELDS: dict[Ecosystem, frozenset[tuple[str, str]]] = {
    "pypi": frozenset(
        {
            ("pypi_json", "info.name"),
            ("PKG-INFO", "Name"),
            ("METADATA", "Name"),
            ("pyproject.toml", "project.name"),
            ("pyproject.toml", "tool.poetry.name"),
        }
    ),
    "npm": frozenset({("npm_registry", "name"), ("package.json", "name")}),
    "cargo": frozenset(
        {("crates_api", "version.crate"), ("Cargo.toml", "package.name")}
    ),
    "maven": frozenset({("pom.xml", "artifactId")}),
    "nuget": frozenset({("nuget_catalog", "id"), ("nuspec", "metadata.id")}),
    "huggingface": frozenset({("hf_api", "id")}),
}


def _voting(eco: Ecosystem, observations: list[Observation]) -> list[Observation]:
    """Present, non-blank string observations of a voting field, platform first."""
    fields = VOTING_NAME_FIELDS[eco]
    found = [
        o
        for o in observations
        if o.element == "name"
        and (o.source, o.field_path) in fields
        and o.present
        and isinstance(o.raw, str)
        and o.raw.strip()
    ]
    return sorted(found, key=lambda o: (o.source not in PLATFORM_SOURCES, o.key))


def _maven_group(seed_name: str) -> str:
    group, sep, artifact = seed_name.partition(":")
    if not sep or not group or not artifact:
        raise ValueError(f"maven name {seed_name!r} needs the form 'group:artifact'")
    return group


def _name_key(eco: Ecosystem, group: str, raw: str) -> str:
    """Key of one voting string; maven prefixes the seed's group."""
    if eco == "maven":
        return canonical_name_key(eco, f"{group}:{raw}")
    return canonical_name_key(eco, raw)


def _display(eco: Ecosystem, raw: str) -> tuple[str, list[str]]:
    """Return ``(name, accepted)`` for the chosen platform string."""
    if eco == "huggingface":
        segment = raw.rpartition("/")[2]
        return segment, list(dict.fromkeys([segment, raw]))
    return raw, [raw]


def _spelling_tags(raws: list[str]) -> list[str]:
    """Tags for voters that name one key in different spellings.

    Case: two voters differ in letter case (`Foo` / `foo`). Separator: they
    differ in `-`, `_` or `.` (`zope.interface` / `zope_interface`).
    """
    plain = {_SEPARATORS.sub("-", r) for r in raws}
    folded = {p.casefold() for p in plain}
    found: list[str] = []
    if len(plain) > len(folded):
        found.append(tags.NAME_CASE_DIFFERS)
    if len({r.casefold() for r in raws}) > len(folded):
        found.append(tags.NAME_SEPARATOR_DIFFERS)
    return found


def derive_name(
    eco: Ecosystem,
    seed_name: str,
    platform: list[Observation],
    manifest: list[Observation],
) -> tuple[ExpectedName, list[str]]:
    """Return the expected name and the derive-time tags.

    ``seed_name`` is ``group:artifact`` for maven and ``owner/name`` for
    Hugging Face. The display name is the first platform value (manifest
    value when no platform source votes, as for maven).
    """
    group = _maven_group(seed_name) if eco == "maven" else ""
    voters = _voting(eco, [*platform, *manifest])
    if not voters:
        return ExpectedName(status="absent_in_source"), []
    raws = [str(o.raw).strip() for o in voters]
    keys = {_name_key(eco, group, r) for r in raws} | {
        canonical_name_key(eco, seed_name)
    }
    basis = sorted({o.key for o in voters})
    origins = len({ORIGIN[o.source] for o in voters})
    if len(keys) > 1:
        expected = ExpectedName(
            status="auto_conflict",
            basis=basis,
            candidates=sorted(keys),
            independent_origins=origins,
        )
        return expected, []
    name, accepted = _display(eco, raws[0])
    if eco == "maven":
        accepted = [name, f"{group}:{name}"]
    value = NameValue(name=name, canonical_key=next(iter(keys)), accepted=accepted)
    expected = ExpectedName(
        status="auto_unanimous",
        value=value,
        basis=basis,
        independent_origins=origins,
    )
    return expected, _spelling_tags(raws)
