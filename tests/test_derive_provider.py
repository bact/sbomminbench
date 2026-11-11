# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""derive_provider() and names_from_source(); see also
test_derive_provider_cleanup.py."""

import random
import unicodedata

import pytest

from sbomminbench import tags
from sbomminbench.derive.origin import ORIGIN
from sbomminbench.derive.provider import (
    _MULTI,
    VALUE_PRIORITY,
    cleanup,
    derive_provider,
    names_from_source,
    split_nuget_authors,
)
from sbomminbench.schema import (
    EMAIL_PLACEHOLDER as PH,
)
from sbomminbench.schema import (
    ExpectedProvider,
    JsonValue,
    Observation,
    Role,
    Source,
    native_type_of,
)


def pv(
    source: Source,
    path: str,
    raw: JsonValue,
    role: Role = "author",
    present: bool = True,
) -> Observation:
    return Observation(
        element="provider",
        source=source,
        field_path=path,
        present=present,
        raw=raw if present else None,
        native_type=native_type_of(raw, present),
        role=role,
        has_email=isinstance(raw, str) and PH in raw,
    )


def pypi_pair(
    author: str | None, email: str | None
) -> tuple[list[Observation], list[Observation]]:
    """The same free-text pair seen by pypi_json and by PKG-INFO."""
    return (
        [
            pv("pypi_json", "info.author", author),
            pv("pypi_json", "info.author_email", email),
        ],
        [pv("PKG-INFO", "Author", author), pv("PKG-INFO", "Author-email", email)],
    )


def names(expected: ExpectedProvider) -> list[str]:
    assert expected.value is not None
    return [e.name for e in expected.value.entities]


# ---- the 11 literal tests -------------------------------------------------


def test_requests() -> None:
    platform, manifest = pypi_pair("Kenneth Reitz", PH)
    expected, found = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_unanimous" and names(expected) == ["Kenneth Reitz"]
    assert found == []


def test_pytest_name_list_in_one_string() -> None:
    text = (
        "Holger Krekel, Bruno Oliveira, Ronny Pfannschmidt, Floris Bruynooghe, "
        "Brianna Laugher, Florian Bruhin, Others (See AUTHORS)"
    )
    platform, manifest = pypi_pair(text, None)
    expected, found = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_unresolved" and expected.value is None
    assert tags.AUTHOR_MULTI_IN_ONE_STRING in found


def test_numpy_et_al() -> None:
    platform, manifest = pypi_pair("Travis E. Oliphant et al.", None)
    expected, _ = derive_provider("pypi", platform, manifest)
    assert names(expected) == ["Travis E. Oliphant"]
    assert expected.value is not None and expected.value.has_unnamed_group


def test_flask_maintainer_only() -> None:
    platform = [
        pv("pypi_json", "info.author", None, present=False),
        pv("pypi_json", "info.maintainer_email", f"Pallets <{PH}>", role="maintainer"),
    ]
    manifest = [
        pv("PKG-INFO", "Maintainer-email", f"Pallets <{PH}>", role="maintainer")
    ]
    expected, found = derive_provider("pypi", platform, manifest)
    assert expected.status == "absent_in_source" and expected.value is None
    assert found == [tags.AUTHOR_ABSENT_MAINTAINER_ONLY]


def test_express() -> None:
    platform = [pv("npm_registry", "author", {"name": "TJ Holowaychuk", "email": PH})]
    manifest = [pv("package.json", "author", f"TJ Holowaychuk <{PH}>")]
    expected, found = derive_provider("npm", platform, manifest)
    assert names(expected) == ["TJ Holowaychuk"] and expected.independent_origins == 1
    assert found == [tags.AUTHOR_OBJECT_FORM]


def test_jquery_nuget() -> None:
    platform = [pv("nuget_catalog", "authors", "jQuery Foundation, Inc.")]
    manifest = [pv("nuspec", "metadata.authors", "jQuery Foundation, Inc.")]
    expected, found = derive_provider("nuget", platform, manifest)
    assert names(expected) == ["jQuery Foundation, Inc."]
    assert found == [tags.AUTHOR_ORG_LIKE]


def test_split_nuget_authors() -> None:
    assert split_nuget_authors("Microsoft, Contoso Ltd., Jane Doe") == [
        "Microsoft",
        "Contoso Ltd.",
        "Jane Doe",
    ]


def test_cleanup_and_its_contributors() -> None:
    assert cleanup(["SPDX project and its contributors"])[:2] == (
        ["SPDX project"],
        True,
    )


def test_cleanup_keeps_a_contributors_name() -> None:
    assert cleanup(["Tokio Contributors"]) == (
        ["Tokio Contributors"],
        False,
        [tags.AUTHOR_CONTAINS_COLLECTIVE],
    )


def test_magika_two_origins() -> None:
    platform = [
        pv("pypi_json", "info.author", None),
        pv("pypi_json", "info.author_email", f"Magika Developers <{PH}>"),
    ]
    manifest = [
        pv("PKG-INFO", "Author", None, present=False),
        pv("PKG-INFO", "Author-email", f"Magika Developers <{PH}>"),
        pv(
            "pyproject.toml",
            "project.authors",
            [{"name": "Magika Developers", "email": PH}],
        ),
    ]
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_unanimous" and names(expected) == [
        "Magika Developers"
    ]
    assert expected.independent_origins == 2
    assert expected.basis == [
        "PKG-INFO:Author-email",
        "pypi_json:info.author_email",
        "pyproject.toml:project.authors",
    ]


def test_qwen_owner() -> None:
    expected, _ = derive_provider(
        "huggingface", [pv("hf_api", "author", "Qwen", role="owner")], []
    )
    assert expected.value is not None
    assert [e.model_dump() for e in expected.value.entities] == [
        {"name": "Qwen", "role": "owner"}
    ]
    assert (expected.basis, expected.independent_origins) == (["hf_api:author"], 1)


# ---- rule 1: absent --------------------------------------------------------


@pytest.mark.parametrize(
    "raw", [None, "", "  ", "UNKNOWN", "N/A", "None", "-", f"<{PH}>"]
)
def test_unusable_author_values_are_absent(raw: str | None) -> None:
    platform, manifest = pypi_pair(raw, None)
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.status == "absent_in_source"
    assert (expected.basis, expected.independent_origins) == ([], 0)


def test_no_observation_at_all() -> None:
    expected, found = derive_provider("pypi", [], [])
    assert expected.status == "absent_in_source" and found == []


@pytest.mark.parametrize("role", ["maintainer", "contributor", "owner"])
def test_other_role_with_content_sets_tag(role: Role) -> None:
    obs = [pv("nuspec", "metadata.owners", "Someone", role=role)]
    _, found = derive_provider("nuget", [], obs)
    assert found == [tags.AUTHOR_ABSENT_MAINTAINER_ONLY]


@pytest.mark.parametrize("role", ["publisher", "organization"])
def test_unlisted_role_sets_no_tag(role: Role) -> None:
    _, found = derive_provider(
        "npm", [pv("npm_registry", "_npmUser", "x", role=role)], []
    )
    assert found == []


@pytest.mark.parametrize("raw", [None, "", []])
def test_empty_maintainer_row_sets_no_tag(raw: JsonValue) -> None:
    obs = [pv("PKG-INFO", "Maintainer", raw, role="maintainer")]
    assert derive_provider("pypi", [], obs)[1] == []


def test_absent_maintainer_row_sets_no_tag() -> None:
    obs = [pv("PKG-INFO", "Maintainer", None, role="maintainer", present=False)]
    assert derive_provider("pypi", [], obs)[1] == []


def test_huggingface_without_owner_has_no_maintainer_tag() -> None:
    obs = [pv("hf_api", "author", None, role="owner", present=False)]
    expected, found = derive_provider("huggingface", obs, [])
    assert expected.status == "absent_in_source" and found == []


def test_huggingface_ignores_author_role() -> None:
    expected, found = derive_provider(
        "huggingface", [pv("hf_api", "author", "Qwen")], []
    )
    assert expected.status == "absent_in_source" and found == []


def test_author_wins_over_maintainer() -> None:
    platform, manifest = pypi_pair("Alice", None)
    manifest.append(pv("PKG-INFO", "Maintainer", "Bob", role="maintainer"))
    expected, found = derive_provider("pypi", platform, manifest)
    assert names(expected) == ["Alice"] and found == []


# ---- rule 2: several in one string, group only ------------------------------


@pytest.mark.parametrize(
    ("text", "multi"),
    [
        ("Foo, Inc.", False),
        ("Foo, Inc", False),
        ("Foo , Inc.", False),
        ("Foo, Corp", False),
        ("Foo, Pty Ltd", False),
        ("Foo, Co., Ltd.", True),
        ("Foo, Inc. and Bar", True),
        ("A and B", True),
        ("A AND B", True),
        ("A & B", True),
        ("AT&T", False),
        ("Anderson", False),
        ("A; B", True),
        ("A;B", True),
        ("Smith, John", True),
        ("Bar Ltd.", False),
        ("Foo Inc.", False),
    ],
)
def test_multi_pattern(text: str, multi: bool) -> None:
    assert bool(_MULTI.search(text)) is multi


@pytest.mark.parametrize("text", ["A and B", "A & B", "A; B", "Smith, John"])
def test_derive_sends_multi_to_unresolved(text: str) -> None:
    expected, found = derive_provider("pypi", *pypi_pair(text, None))
    assert expected.status == "auto_unresolved"
    assert found == [tags.AUTHOR_MULTI_IN_ONE_STRING]


def test_one_multi_source_is_enough() -> None:
    platform = [pv("pypi_json", "info.author", "Alice")]
    manifest = [pv("PKG-INFO", "Author", "Alice and Bob")]
    assert derive_provider("pypi", platform, manifest)[0].status == "auto_unresolved"


@pytest.mark.parametrize("text", ["Others", "et al.", "Contributors", "and others"])
def test_group_only_is_unresolved(text: str) -> None:
    expected, found = derive_provider("pypi", *pypi_pair(text, None))
    assert expected.status == "auto_unresolved" and expected.value is None
    assert found == [tags.AUTHOR_MULTI_IN_ONE_STRING]
    assert expected.basis == ["PKG-INFO:Author", "pypi_json:info.author"]


def test_group_only_source_beats_named_source() -> None:
    platform = [pv("pypi_json", "info.author", "Alice")]
    manifest = [pv("PKG-INFO", "Author", "Others")]
    assert derive_provider("pypi", platform, manifest)[0].status == "auto_unresolved"


# ---- rule 3 and 4: unanimous or conflict -------------------------------------


def test_same_set_different_order_and_case_is_unanimous() -> None:
    platform = [pv("pypi_json", "info.author_email", f"B <{PH}>, A <{PH}>")]
    manifest = [pv("pyproject.toml", "project.authors", [{"name": "a"}, {"name": "b"}])]
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_unanimous"
    assert names(expected) == ["a", "b"]  # highest-priority source: pyproject


def test_different_sets_conflict_with_sorted_candidates() -> None:
    platform = [pv("pypi_json", "info.author_email", f"Zed <{PH}>")]
    manifest = [pv("pyproject.toml", "project.authors", [{"name": "Alice"}])]
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_conflict" and expected.value is None
    assert expected.candidates == ["Alice", "Zed"]


def test_subset_is_a_conflict() -> None:
    platform = [pv("pypi_json", "info.author_email", f"A <{PH}>, B <{PH}>")]
    manifest = [pv("PKG-INFO", "Author-email", f"A <{PH}>")]
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_conflict"
    assert expected.candidates == ["A", "A | B"]


def test_empty_source_does_not_vote() -> None:
    platform = [pv("pypi_json", "info.author", "UNKNOWN")]
    manifest = [pv("PKG-INFO", "Author", "Alice")]
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_unanimous" and expected.basis == ["PKG-INFO:Author"]


def test_group_flag_comes_from_any_source() -> None:
    platform = [pv("pypi_json", "info.author", "Alice")]
    manifest = [pv("PKG-INFO", "Author", "Alice and others")]
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.value is not None and expected.value.has_unnamed_group
    assert names(expected) == ["Alice"]


def test_no_group_flag_without_group() -> None:
    expected, _ = derive_provider("pypi", *pypi_pair("Alice", None))
    assert expected.value is not None and not expected.value.has_unnamed_group


def test_entities_come_from_the_highest_priority_source() -> None:
    platform = [pv("pypi_json", "info.author", "ACME")]
    manifest = [pv("pyproject.toml", "project.authors", [{"name": "Acme"}])]
    assert names(derive_provider("pypi", platform, manifest)[0]) == ["Acme"]
    assert VALUE_PRIORITY.index("pyproject.toml") < VALUE_PRIORITY.index("pypi_json")


def test_nfc_and_nfd_agree() -> None:
    nfc = unicodedata.normalize("NFC", "José Álvarez")
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    platform = [pv("pypi_json", "info.author", nfd)]
    manifest = [pv("PKG-INFO", "Author", nfc)]
    expected, _ = derive_provider("pypi", platform, manifest)
    assert expected.status == "auto_unanimous" and names(expected) == [nfc]


def test_non_ascii_names() -> None:
    expected, _ = derive_provider("pypi", *pypi_pair("张伟 (Wei Zhang)", None))
    assert names(expected) == ["张伟 (Wei Zhang)"]


def test_reversed_and_shuffled_observations_give_identical_output() -> None:
    platform = [
        pv("pypi_json", "info.author", "Kenneth Reitz"),
        pv("pypi_json", "info.author_email", f"Kenneth Reitz <{PH}>"),
    ]
    manifest = [
        pv("PKG-INFO", "Author-email", f"Kenneth Reitz <{PH}>"),
        pv("PKG-INFO", "Author", "Kenneth Reitz"),
        pv("pyproject.toml", "project.authors", [{"name": "Kenneth Reitz"}]),
    ]
    reference = derive_provider("pypi", platform, manifest)
    rng = random.Random(7)
    for _ in range(5):
        a, b = platform[:], manifest[:]
        rng.shuffle(a)
        rng.shuffle(b)
        out = derive_provider("pypi", a, b)
        assert (
            out[0].model_dump() == reference[0].model_dump() and out[1] == reference[1]
        )
    assert (
        derive_provider("pypi", platform[::-1], manifest[::-1])[0].model_dump()
        == reference[0].model_dump()
    )


def test_conflict_is_order_independent() -> None:
    obs = [
        pv("pom.xml", f"developers.developer[{i}].name", n)
        for i, n in enumerate(["B", "A"])
    ]
    other = [pv("pyproject.toml", "project.authors", [{"name": "C"}])]
    first = derive_provider("maven", [], [*obs, *other])
    second = derive_provider("maven", [], [*other, *obs[::-1]])
    assert first[0].model_dump() == second[0].model_dump()


# ---- names_from_source --------------------------------------------------------


def test_pypi_free_text_pairs_name_with_address() -> None:
    obs = [
        pv("pypi_json", "info.author", "Alice"),
        pv("pypi_json", "info.author_email", PH),
    ]
    assert names_from_source("pypi_json", obs) == (["Alice"], [])


def test_pypi_address_only_is_tagged() -> None:
    obs = [pv("PKG-INFO", "Author", None), pv("PKG-INFO", "Author-email", PH)]
    assert names_from_source("PKG-INFO", obs) == ([], [tags.AUTHOR_EMAIL_ONLY])


def test_pypi_wrong_type_is_a_violation() -> None:
    obs = [pv("pypi_json", "info.author", ["Alice"])]
    assert names_from_source("pypi_json", obs) == ([], [tags.SCHEMA_TYPE_VIOLATION])


def test_pypi_maintainer_fields_are_not_read() -> None:
    obs = [pv("pypi_json", "info.maintainer", "Bob")]
    assert names_from_source("pypi_json", obs) == ([], [])


def test_pypi_list_of_addresses_splits() -> None:
    obs = [pv("PKG-INFO", "Author-email", f"A <{PH}>, B <{PH}>")]
    assert names_from_source("PKG-INFO", obs)[0] == ["A", "B"]


def test_pitloom_does_not_split_a_name_only_list() -> None:
    """Pitloom keeps `A, B` as one entity; _MULTI is what catches it."""
    obs = [pv("PKG-INFO", "Author", "Alice Adams, Mark Twain")]
    assert names_from_source("PKG-INFO", obs)[0] == ["Alice Adams, Mark Twain"]


def test_pyproject_authors() -> None:
    raw = [{"name": "A", "email": PH}, {"name": "B"}, {"email": PH}, {}]
    obs = [pv("pyproject.toml", "project.authors", raw)]
    assert names_from_source("pyproject.toml", obs) == (
        ["A", "B"],
        [tags.AUTHOR_EMAIL_ONLY],
    )


@pytest.mark.parametrize(
    ("raw", "expected_names"),
    [
        (f"Alice <{PH}>", ["Alice"]),
        ([f"Alice <{PH}>", "Bob"], ["Alice", "Bob"]),
        (["Alice", 5], ["Alice"]),
        ({"name": "A"}, []),
        (5, []),
    ],
    ids=["string", "string-in-list", "number-in-list", "dict", "number"],
)
def test_pyproject_authors_wrong_shapes(
    raw: JsonValue, expected_names: list[str]
) -> None:
    found, found_tags = names_from_source(
        "pyproject.toml", [pv("pyproject.toml", "project.authors", raw)]
    )
    assert found == expected_names and found_tags == [tags.SCHEMA_TYPE_VIOLATION]


def test_pyproject_name_of_wrong_type() -> None:
    obs = [pv("pyproject.toml", "project.authors", [{"name": 5}])]
    assert names_from_source("pyproject.toml", obs) == (
        [],
        [tags.SCHEMA_TYPE_VIOLATION],
    )


def test_poetry_and_cargo_angle_names() -> None:
    raw = [f"Alice <{PH}>", "Bob", f"<{PH}>"]
    assert names_from_source(
        "pyproject.toml", [pv("pyproject.toml", "tool.poetry.authors", raw)]
    )[0] == ["Alice", "Bob", ""]
    assert names_from_source("Cargo.toml", [pv("Cargo.toml", "package.authors", raw)])[
        0
    ] == ["Alice", "Bob", ""]


def test_cargo_workspace_inherited_states_nothing() -> None:
    obs = [pv("Cargo.toml", "package.authors", {"workspace": True})]
    assert names_from_source("Cargo.toml", obs) == ([], [])


def test_cargo_authors_as_one_string_is_a_violation() -> None:
    obs = [pv("Cargo.toml", "package.authors", "Alice")]
    assert names_from_source("Cargo.toml", obs) == (
        ["Alice"],
        [tags.SCHEMA_TYPE_VIOLATION],
    )


@pytest.mark.parametrize(
    ("raw", "expected_names", "expected_tags"),
    [
        ("Alice", ["Alice"], []),
        (f"Alice <{PH}> (https://example.org)", ["Alice"], []),
        ("Alice (https://example.org)", ["Alice"], []),
        (f"<{PH}>", [""], []),
        ("John (Jack) Smith", ["John (Jack) Smith"], []),
        ({"name": "Alice", "url": "u"}, ["Alice"], [tags.AUTHOR_OBJECT_FORM]),
        ({"email": PH}, [], [tags.AUTHOR_EMAIL_ONLY, tags.AUTHOR_OBJECT_FORM]),
        (["Alice"], [], [tags.SCHEMA_TYPE_VIOLATION]),
        (None, [], []),
    ],
    ids=[
        "plain",
        "mail-url",
        "url",
        "mail-only",
        "parens",
        "object",
        "object-mail",
        "list",
        "null",
    ],
)
def test_npm_author(
    raw: JsonValue, expected_names: list[str], expected_tags: list[str]
) -> None:
    out = names_from_source("package.json", [pv("package.json", "author", raw)])
    assert out == (expected_names, expected_tags)


def test_npm_contributors_are_not_read() -> None:
    obs = [pv("npm_registry", "contributors", [{"name": "X"}], role="contributor")]
    assert names_from_source("npm_registry", obs) == ([], [])


def test_maven_developer_names_in_natural_order() -> None:
    obs = [
        pv("pom.xml", f"developers.developer[{i}].name", f"Dev {i}") for i in (10, 2, 1)
    ] + [pv("pom.xml", "developers.developer[1].email", PH)]
    assert names_from_source("pom.xml", obs)[0] == ["Dev 1", "Dev 2", "Dev 10"]


def test_nuget_and_hf() -> None:
    obs = [pv("nuspec", "metadata.authors", "A, B Ltd., C")]
    assert names_from_source("nuspec", obs)[0] == ["A", "B Ltd.", "C"]
    wrong = [pv("nuget_catalog", "authors", ["A"])]
    assert names_from_source("nuget_catalog", wrong) == (
        [],
        [tags.SCHEMA_TYPE_VIOLATION],
    )
    assert names_from_source("hf_api", [pv("hf_api", "author", "Qwen", role="owner")])[
        0
    ] == ["Qwen"]
    assert (
        names_from_source("hf_api", [pv("hf_api", "author", "", role="owner")])[0] == []
    )


def test_unknown_source_and_absent_rows() -> None:
    assert names_from_source("hf_card", [pv("hf_card", "x", "y")]) == ([], [])
    assert names_from_source(
        "PKG-INFO", [pv("PKG-INFO", "Author", None, present=False)]
    ) == ([], [])


def test_raw_value_is_not_modified() -> None:
    o = pv("PKG-INFO", "Author", f"  Alice <{PH}>  and others ")
    names_from_source("PKG-INFO", [o])
    assert o.raw == f"  Alice <{PH}>  and others "


# ---- derive_provider across ecosystems ----------------------------------------------


def test_maven_developers_vote_but_organisation_does_not() -> None:
    obs = [
        pv("pom.xml", "developers.developer[0].name", "Alice"),
        pv(
            "pom.xml",
            "developers.developer[0].organization",
            "Acme",
            role="organization",
        ),
        pv("pom.xml", "organization.name", "Acme", role="organization"),
        pv("pom.xml", "contributors.contributor[0].name", "Carol", role="contributor"),
    ]
    expected, found = derive_provider("maven", [], obs)
    assert names(expected) == ["Alice"] and found == []
    assert expected.basis == ["pom.xml:developers.developer[0].name"]


def test_nuget_owner_does_not_vote_for_the_provider() -> None:
    platform = [pv("nuget_catalog", "authors", "Alice")]
    manifest = [
        pv("nuspec", "metadata.authors", "Alice"),
        pv("nuspec", "metadata.owners", "Bob", role="owner"),
    ]
    expected, _ = derive_provider("nuget", platform, manifest)
    assert names(expected) == ["Alice"] and expected.independent_origins == 1


def test_cargo_authors() -> None:
    manifest = [pv("Cargo.toml", "package.authors", [f"Alice <{PH}>", f"Bob <{PH}>"])]
    expected, _ = derive_provider("cargo", [], manifest)
    assert names(expected) == ["Alice", "Bob"]


def test_cargo_workspace_inherited_is_absent() -> None:
    manifest = [pv("Cargo.toml", "package.authors", {"workspace": True})]
    assert derive_provider("cargo", [], manifest)[0].status == "absent_in_source"


def test_entities_have_unique_names_and_no_placeholder() -> None:
    manifest = [
        pv(
            "pyproject.toml",
            "project.authors",
            [{"name": "A", "email": PH}, {"name": "a"}],
        )
    ]
    expected, _ = derive_provider("pypi", [], manifest)
    assert names(expected) == ["A"]


def test_value_priority_covers_every_source_with_a_provider_reader() -> None:
    readers = {
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
    }
    assert set(VALUE_PRIORITY) == readers and readers <= set(ORIGIN)
    assert len(VALUE_PRIORITY) == len(set(VALUE_PRIORITY))
