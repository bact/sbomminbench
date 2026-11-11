# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

from typing import get_args

import pytest

from sbomminbench import tags
from sbomminbench.derive.name import VOTING_NAME_FIELDS, derive_name
from sbomminbench.derive.origin import ORIGIN
from sbomminbench.schema import (
    Ecosystem,
    JsonValue,
    Observation,
    Source,
    native_type_of,
)


def nm(source: Source, path: str, raw: JsonValue, present: bool = True) -> Observation:
    return Observation(
        element="name",
        source=source,
        field_path=path,
        present=present,
        raw=raw if present else None,
        native_type=native_type_of(raw, present),
    )


def pypi(a: str, b: str | None = None, c: str | None = None) -> tuple[list, list]:
    platform = [nm("pypi_json", "info.name", a)]
    manifest = [nm("PKG-INFO", "Name", b)] if b is not None else []
    if c is not None:
        manifest.append(nm("pyproject.toml", "project.name", c))
    return platform, manifest


def test_flask() -> None:
    platform, manifest = pypi("Flask", "Flask", "Flask")
    expected, found = derive_name("pypi", "flask", platform, manifest)
    assert expected.status == "auto_unanimous" and found == []
    assert expected.value is not None
    assert (expected.value.name, expected.value.canonical_key) == ("Flask", "flask")
    assert expected.value.accepted == ["Flask"]
    assert expected.basis == [
        "PKG-INFO:Name",
        "pypi_json:info.name",
        "pyproject.toml:project.name",
    ]
    assert expected.independent_origins == 2


def test_scoped_npm() -> None:
    obs = [
        nm("npm_registry", "name", "@babel/core"),
        nm("package.json", "name", "@babel/core"),
    ]
    expected, _ = derive_name("npm", "@babel/core", obs[:1], obs[1:])
    assert expected.value is not None
    assert expected.value.canonical_key == "@babel/core"
    assert expected.independent_origins == 1


def test_cargo_underscore_and_hyphen_share_a_key() -> None:
    platform = [nm("crates_api", "version.crate", "serde_json")]
    manifest = [nm("Cargo.toml", "package.name", "serde_json")]
    expected, found = derive_name("cargo", "serde_json", platform, manifest)
    assert expected.value is not None
    assert (expected.value.name, expected.value.canonical_key) == (
        "serde_json",
        "serde-json",
    )
    assert found == []


def test_conflict() -> None:
    platform, manifest = pypi("bar", None, "foo")
    expected, found = derive_name("pypi", "bar", platform, manifest)
    assert expected.status == "auto_conflict" and expected.value is None
    assert expected.candidates == ["bar", "foo"] and found == []
    assert expected.basis == ["pypi_json:info.name", "pyproject.toml:project.name"]


def test_maven_junit() -> None:
    manifest = [nm("pom.xml", "artifactId", "junit")]
    expected, _ = derive_name("maven", "junit:junit", [], manifest)
    assert expected.value is not None
    assert expected.value.name == "junit"
    assert expected.value.canonical_key == "junit:junit"
    assert expected.value.accepted == ["junit", "junit:junit"]


def test_huggingface_qwen() -> None:
    platform = [nm("hf_api", "id", "Qwen/Qwen2.5-72B-Instruct")]
    expected, _ = derive_name("huggingface", "Qwen/Qwen2.5-72B-Instruct", platform, [])
    assert expected.value is not None
    assert expected.value.name == "Qwen2.5-72B-Instruct"
    assert expected.value.canonical_key == "qwen/qwen2.5-72b-instruct"
    assert expected.value.accepted == [
        "Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
    ]
    assert (expected.basis, expected.independent_origins) == (["hf_api:id"], 1)


def test_huggingface_legacy_id_without_owner() -> None:
    expected, _ = derive_name("huggingface", "gpt2", [nm("hf_api", "id", "gpt2")], [])
    assert expected.value is not None and expected.value.accepted == ["gpt2"]


# A and B of "unanimous": the observations agree among themselves (A), and
# they agree with the seed (B). Each alone failing gives a conflict.
def test_seed_alone_disagrees() -> None:
    platform, manifest = pypi("foo", "foo", "foo")
    expected, _ = derive_name("pypi", "bar", platform, manifest)
    assert expected.status == "auto_conflict" and expected.candidates == ["bar", "foo"]


def test_observations_disagree_seed_matches_one() -> None:
    platform, manifest = pypi("foo", "bar")
    expected, _ = derive_name("pypi", "foo", platform, manifest)
    assert expected.status == "auto_conflict"


def test_maven_artifact_differs_from_seed() -> None:
    expected, _ = derive_name("maven", "g:a", [], [nm("pom.xml", "artifactId", "b")])
    assert expected.status == "auto_conflict"
    assert expected.candidates == ["g:a", "g:b"]


def test_maven_group_is_not_voted() -> None:
    """The seed's group is part of every key, so a matching artifactId agrees."""
    expected, _ = derive_name(
        "maven", "org.x:a", [], [nm("pom.xml", "artifactId", "a")]
    )
    assert expected.value is not None and expected.value.canonical_key == "org.x:a"


@pytest.mark.parametrize("seed", ["junit", ":junit", "junit:", ""])
def test_maven_seed_needs_group_and_artifact(seed: str) -> None:
    with pytest.raises(ValueError, match="group:artifact"):
        derive_name("maven", seed, [], [nm("pom.xml", "artifactId", "junit")])


@pytest.mark.parametrize(
    "obs",
    [
        [],
        [nm("pypi_json", "info.name", None, present=False)],
        [nm("pypi_json", "info.name", None)],
        [nm("pypi_json", "info.name", "")],
        [nm("pypi_json", "info.name", "  \t")],
        [nm("pypi_json", "info.name", ["Flask"])],
        [nm("pypi_json", "info.name", 5)],
        [nm("pypi_json", "info.summary", "Flask")],
        [nm("npm_registry", "name", "Flask")],
    ],
    ids=[
        "none",
        "absent",
        "null",
        "empty",
        "blank",
        "list",
        "number",
        "other-field",
        "other-eco",
    ],
)
def test_no_voting_observation(obs: list[Observation]) -> None:
    expected, found = derive_name("pypi", "flask", obs, [])
    assert expected.status == "absent_in_source" and expected.value is None
    assert (expected.basis, expected.independent_origins, found) == ([], 0, [])


def test_empty_observation_does_not_vote_against_others() -> None:
    platform, manifest = pypi("Flask", "")
    expected, _ = derive_name("pypi", "flask", platform, manifest)
    assert expected.status == "auto_unanimous"
    assert expected.basis == ["pypi_json:info.name"]


def test_name_is_stripped_not_the_observation() -> None:
    obs = nm("pypi_json", "info.name", " Flask ")
    expected, _ = derive_name("pypi", "flask", [obs], [])
    assert expected.value is not None and expected.value.name == "Flask"
    assert obs.raw == " Flask "


def test_case_differs_tag_and_platform_display() -> None:
    platform, manifest = pypi("Foo_Bar", "foo-bar")
    expected, found = derive_name("pypi", "foo-bar", platform, manifest)
    assert found == [tags.NAME_CASE_DIFFERS, tags.NAME_SEPARATOR_DIFFERS]
    assert expected.value is not None
    # PKG-INFO sorts before pypi_json, yet the platform value is displayed.
    assert expected.value.name == "Foo_Bar"
    assert expected.value.canonical_key == "foo-bar"


def test_same_raw_has_no_case_tag() -> None:
    platform, manifest = pypi("Foo", "Foo")
    assert derive_name("pypi", "foo", platform, manifest)[1] == []


def test_conflict_has_no_case_tag() -> None:
    platform, manifest = pypi("Foo", "foo", "other")
    assert derive_name("pypi", "foo", platform, manifest)[1] == []


def test_manifest_value_displayed_when_no_platform_votes() -> None:
    expected, _ = derive_name("pypi", "foo", [], [nm("PKG-INFO", "Name", "Foo")])
    assert expected.value is not None and expected.value.name == "Foo"


def test_reversed_order_gives_identical_output() -> None:
    platform = [nm("pypi_json", "info.name", "Foo_Bar")]
    manifest = [
        nm("PKG-INFO", "Name", "foo-bar"),
        nm("pyproject.toml", "tool.poetry.name", "FOO.BAR"),
        nm("pyproject.toml", "project.name", "foo_bar"),
    ]
    first = derive_name("pypi", "foo-bar", platform, manifest)
    second = derive_name("pypi", "foo-bar", platform[::-1], manifest[::-1])
    assert first[0].model_dump() == second[0].model_dump() and first[1] == second[1]


def test_conflict_candidates_are_sorted_and_deduplicated() -> None:
    manifest = [nm("PKG-INFO", "Name", "b"), nm("pyproject.toml", "project.name", "B")]
    expected, _ = derive_name(
        "pypi", "a", [nm("pypi_json", "info.name", "a")], manifest
    )
    assert expected.candidates == ["a", "b"]


def test_every_voting_field_is_a_known_source() -> None:
    assert set(VOTING_NAME_FIELDS) == set(get_args(Ecosystem))
    assert {s for fields in VOTING_NAME_FIELDS.values() for s, _ in fields} <= set(
        ORIGIN
    )


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Foo", "foo", [tags.NAME_CASE_DIFFERS]),
        ("zope.interface", "zope_interface", [tags.NAME_SEPARATOR_DIFFERS]),
        ("zope.interface", "zope.interface", []),
    ],
    ids=["case", "separator", "same"],
)
def test_spelling_tags_are_separate(a: str, b: str, expected: list[str]) -> None:
    platform, manifest = pypi(a, b)
    _, found = derive_name("pypi", a, platform, manifest)
    assert found == expected
