# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import pytest

from sbomminbench import tags
from sbomminbench.classify import format_validity, license_format
from sbomminbench.schema import (
    EMAIL_PLACEHOLDER,
    JsonValue,
    Observation,
    Role,
    Source,
    native_type_of,
)


def lic(
    raw: str | None, source: Source = "pypi_json", path: str = "info.license"
) -> Observation:
    if raw is None:
        return Observation(
            element="license",
            source=source,
            field_path=path,
            present=False,
            native_type="absent",
        )
    fmt = license_format(raw)
    return Observation(
        element="license",
        source=source,
        field_path=path,
        raw=raw,
        native_type="string",
        semantic_format=fmt,
        format_validity=format_validity(raw, fmt),
    )


def prov(raw: JsonValue, role: Role, present: bool = True, **kw: object) -> Observation:
    return Observation(
        element="provider",
        source="pypi_json",
        field_path=f"info.{role}",
        present=present,
        raw=raw if present else None,
        native_type=native_type_of(raw, present),
        role=role,
        **kw,  # type: ignore[arg-type]
    )


def test_vocabulary_closed() -> None:
    assert "license_absent" in tags.ALL_TAGS
    assert "email.redacted@example.com" not in tags.ALL_TAGS
    assert len(tags.ALL_TAGS) == 46


@pytest.mark.parametrize(
    ("raws", "expected"),
    [
        ([], {"license_absent"}),
        ([None], {"license_absent"}),
        (["MIT"], set()),
        (["mit"], {"license_spdx_noncanonical"}),
        (["GPL-3.0"], {"license_spdx_deprecated"}),
        (["GPLv3"], {"license_spdx_invalid"}),
        (["MIT AND"], {"license_spdx_invalid", "license_compound"}),
        (["Apache License 2.0"], {"license_prose"}),
        (["https://opensource.org/license/mit"], {"license_is_url"}),
        (["UNKNOWN"], {"license_placeholder"}),
        (["a\nb"], {"license_text_in_field"}),
        (["SEE LICENSE IN LICENSE"], {"license_file_only"}),
        (["MIT/ISC"], {"license_nonstandard_compound"}),
        (["MIT", "Apache-2.0"], set()),  # two sources: derive's conflict, not a list
        (["MIT", "MIT"], set()),
        (["http://x.io/l", "MIT"], {"license_is_url"}),
    ],
    ids=lambda p: str(p)[:30],
)
def test_license_tags(raws: list[str | None], expected: set[str]) -> None:
    obs = [lic(r, path=f"info.l{i}") for i, r in enumerate(raws)]
    assert tags.structural_tags(obs, [], "pypi") == sorted(expected)


def test_trove_tags_and_split_across_lists() -> None:
    t1 = "License :: OSI Approved :: MIT License"
    t2 = "License :: OSI Approved :: BSD License"
    one = [lic(t1, path="info.classifiers[0]")]
    assert tags.structural_tags(one, [], "pypi") == ["license_trove_only"]
    two = [*one, lic(t2, path="info.classifiers[1]")]
    assert tags.structural_tags(two, [], "pypi") == [
        "license_trove_multiple",
        "license_trove_only",
    ]
    mixed = [*one, lic("MIT", source="METADATA", path="License")]
    assert tags.structural_tags(one, mixed[1:], "pypi") == []


@pytest.mark.parametrize(
    ("obs", "expected"),
    [
        (
            [prov(None, "author", present=False), prov("Bo", "maintainer")],
            {"author_absent_maintainer_only"},
        ),
        ([prov("Bo", "author"), prov("Al", "maintainer")], set()),
        ([prov("A, B", "author")], {"author_multi_in_one_string"}),
        ([prov("A and B", "author")], {"author_multi_in_one_string"}),
        ([prov("A & B", "author")], {"author_multi_in_one_string"}),
        ([prov("A <a@b.c, d@e.f>", "author")], set()),
        ([prov(f"<{EMAIL_PLACEHOLDER}>", "author")], {"author_email_only"}),
        ([prov({"name": "A"}, "author")], {"author_object_form"}),
        ([prov([{"name": "A"}], "author")], {"author_object_form"}),
        ([prov("Bo", "maintainer")], {"author_absent_maintainer_only"}),
        # A present-but-null, empty or placeholder author is not an author.
        (
            [prov(None, "author"), prov("Bo", "maintainer")],
            {"author_absent_maintainer_only"},
        ),
        (
            [prov("  ", "author"), prov("Bo", "maintainer")],
            {"author_absent_maintainer_only"},
        ),
        (
            [prov("UNKNOWN", "author"), prov("Bo", "maintainer")],
            {"author_absent_maintainer_only"},
        ),
        (
            [prov([], "author"), prov("Bo", "maintainer")],
            {"author_absent_maintainer_only"},
        ),
        (
            [prov(None, "author"), prov("Bo", "author"), prov("Al", "maintainer")],
            set(),
        ),
    ],
    ids=lambda p: str(p)[:30],
)
def test_author_tags(obs: list[Observation], expected: set[str]) -> None:
    got = set(tags.structural_tags(obs, [], "pypi")) - {"license_absent"}
    assert got == expected


def hf_lic(raw: str, source: Source, path: str) -> Observation:
    return lic(raw, source=source, path=path)


@pytest.mark.parametrize(
    ("api", "card", "expected"),
    [
        ("license:apache-2.0", "apache-2.0", set()),
        ("license:apache-2.0", "mit", set()),
    ],
    ids=["same", "differ"],
)
def test_hf_prefix_not_a_disagreement(api: str, card: str, expected: set[str]) -> None:
    rows = [
        hf_lic(api, "hf_api", "tags[1]"),
        hf_lic(card, "hf_card", "cardData.license"),
    ]
    assert api != card
    got = set(tags.structural_tags(rows, [], "huggingface"))
    assert got & {"license_multiple_values"} == expected


def test_source_tags() -> None:
    inh = prov("A", "author", inherited_from="parent[0]")
    ref = lic("${license.name}", source="pom.xml", path="licenses.license[0].name")
    out = tags.structural_tags([inh, ref], [], "maven")
    assert {"maven_inherited", "maven_property_ref"} <= set(out)
    assert "maven_inherited" not in tags.structural_tags([inh], [], "pypi")
    ws = Observation(
        element="license",
        source="Cargo.toml",
        field_path="package.license",
        raw={"workspace": True},
        native_type="object",
    )
    assert "workspace_inherited" in tags.structural_tags([], [ws], "cargo")
    assert "workspace_inherited" not in tags.structural_tags([], [ws], "npm")


def test_sorted_unique_and_element_kind() -> None:
    out = tags.structural_tags(
        [lic("mit"), lic("GPLv3", path="x")], [lic("mit", path="y")], "pypi"
    )
    assert out == sorted(set(out))


@pytest.mark.parametrize(
    ("name", "multi"),
    [
        ("jQuery Foundation, Inc.", False),
        ("Foo, Ltd", False),
        ("A, B", True),
        ("Foo, Inc. and Bar", True),
    ],
    ids=["comma-inc", "comma-ltd", "two-people", "suffix-not-final"],
)
def test_author_multi_ignores_legal_suffix_comma(name: str, multi: bool) -> None:
    got = tags.structural_tags([prov(name, "author")], [], "pypi")
    assert (tags.AUTHOR_MULTI_IN_ONE_STRING in got) is multi


def test_author_rules_shared_with_provider() -> None:
    from sbomminbench import authors_rules
    from sbomminbench.derive import provider

    assert provider._MULTI is authors_rules.MULTI
    assert provider._ORG_LIKE is authors_rules.ORG_LIKE
