# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Separators, joined lists and collective tails."""

import pytest
from test_derive_provider import PH, names, pv, pypi_pair

from sbomminbench import tags
from sbomminbench.derive.provider import (
    _MULTI,
    cleanup,
    derive_provider,
    split_nuget_authors,
)

UNRESOLVED = ("auto_unresolved", [tags.AUTHOR_MULTI_IN_ONE_STRING])


@pytest.mark.parametrize(
    "text",
    [
        "Alice\nBob",
        "Alice\r\nBob",
        "Alice / Bob",
        "Alice + Bob",
        "Alice|Bob",
        "Alice | Bob",
        "สมชาย และ สมหญิง",
        "张三，李四",
        "张三、李四",
        "张三；李四",
        "张三 和 李四",
        "張三 與 李四",
        "张三 及 李四",
    ],
    ids=lambda t: t.replace("\n", "NL").replace("\r", "CR")[:12],
)
def test_unknown_separators_are_several_authors(text: str) -> None:
    # PyPI free text (Pitloom collapses the newline) and a list-valued source
    expected, found = derive_provider("pypi", *pypi_pair(text, None))
    assert (expected.status, found) == UNRESOLVED
    nuget = [pv("nuspec", "metadata.authors", text)]
    expected, found = derive_provider("nuget", [], nuget)
    assert (expected.status, found) == UNRESOLVED


@pytest.mark.parametrize(
    "text",
    [
        "AT&T",
        "Google, LLC",
        "Acme Co., Ltd.",
        "The Apache Software Foundation",
        "Foo A/S",
        "C++ Team",
        "Alice (https://alice.dev/a+b)",
    ],
)
def test_legitimate_names_are_not_multi(text: str) -> None:
    only = [pv("hf_api", "author", text, role="owner")]
    expected, found = derive_provider("huggingface", [], only)
    assert tags.AUTHOR_MULTI_IN_ONE_STRING not in found
    assert expected.status == "auto_unanimous"


def test_a_line_break_in_a_dict_name_is_several_authors() -> None:
    row = pv("pyproject.toml", "project.authors", [{"name": "Alice\nBob"}])
    expected, found = derive_provider("pypi", [], [row])
    assert (expected.status, found) == UNRESOLVED


def test_inc_after_a_comma_stays_in_the_name_pitloom_splits() -> None:
    expected, found = derive_provider(
        "pypi", *pypi_pair(None, f"Jane Doe, Inc. <{PH}>")
    )
    assert names(expected) == ["Jane Doe, Inc."]
    assert found == [tags.AUTHOR_ORG_LIKE]


def test_quoted_surname_first_form_stays_one_piece() -> None:
    expected, found = derive_provider("pypi", *pypi_pair(None, f'"Smith, John" <{PH}>'))
    assert expected.status == "auto_unresolved"  # the comma is a separator for us
    assert found == [tags.AUTHOR_MULTI_IN_ONE_STRING]


def test_free_text_join_of_a_structured_list_agrees_with_it() -> None:
    pyproject = pv(
        "pyproject.toml",
        "project.authors",
        [{"name": "Armin Rigo"}, {"name": "Maciej Fijalkowski"}],
    )
    platform, manifest = pypi_pair("Armin Rigo, Maciej Fijalkowski", None)
    expected, found = derive_provider("pypi", platform, [*manifest, pyproject])
    assert expected.status == "auto_unanimous"
    assert names(expected) == ["Armin Rigo", "Maciej Fijalkowski"]
    assert found == []


@pytest.mark.parametrize(
    "joined",
    ["Armin Rigo, Maciej Fijalkowski, Eve", "Armin Rigo, Someone Else", "Armin Rigo"],
    ids=["superset", "different", "subset"],
)
def test_free_text_that_is_not_the_same_set_is_not_reconciled(joined: str) -> None:
    pyproject = pv(
        "pyproject.toml",
        "project.authors",
        [{"name": "Armin Rigo"}, {"name": "Maciej Fijalkowski"}],
    )
    platform, manifest = pypi_pair(joined, None)
    expected, _ = derive_provider("pypi", platform, [*manifest, pyproject])
    assert expected.status != "auto_unanimous"


def test_free_text_join_without_a_structured_source_stays_unresolved() -> None:
    expected, _ = derive_provider(
        "pypi", *pypi_pair("Armin Rigo, Maciej Fijalkowski", None)
    )
    assert expected.status == "auto_unresolved"


def test_individual_contributors_tail() -> None:
    raw = "The Python Cryptographic Authority and individual contributors"
    assert cleanup([raw])[:2] == (["The Python Cryptographic Authority"], True)


@pytest.mark.parametrize(
    "raw",
    ["Authors", "The Authors", "team", "Developers", "maintainers", "Contributors"],
)
def test_generic_group_words_are_not_an_author_claim(raw: str) -> None:
    names_, _, found = cleanup([raw])
    assert names_ == [] and found == []


@pytest.mark.parametrize("raw", ["Rust Team", "Pallets", "Foo Developers"])
def test_named_teams_are_kept(raw: str) -> None:
    assert cleanup([raw])[0] == [raw]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Smith, John", ["Smith, John"]),
        (
            "Microsoft, Contoso Ltd., Jane Doe",
            ["Microsoft", "Contoso Ltd.", "Jane Doe"],
        ),
        ("Jane Doe, John Smith", ["Jane Doe", "John Smith"]),
        ("Foo, Inc.", ["Foo, Inc."]),
    ],
)
def test_nuget_two_piece_surname_first_is_left_whole(
    text: str, expected: list[str]
) -> None:
    assert split_nuget_authors(text) == expected


def test_nuget_surname_first_is_unresolved() -> None:
    nuspec = [pv("nuspec", "metadata.authors", "Smith, John")]
    expected, found = derive_provider("nuget", [], nuspec)
    assert (expected.status, found) == UNRESOLVED


def test_a_url_with_a_slash_is_not_a_separator() -> None:
    assert _MULTI.search("https://alice.dev/x") is None
    assert _MULTI.search("Foo A/S") is None
    assert _MULTI.search("Alice /Bob") is not None


@pytest.mark.parametrize("raw", ["https://alice.dev", "www.alice.dev/me", "git://x/y"])
def test_a_url_is_not_an_author_name(raw: str) -> None:
    assert cleanup([raw]) == ([], False, [tags.AUTHOR_URL_ONLY])
    npm = [pv("package.json", "author", raw)]
    expected, found = derive_provider("npm", [], npm)
    assert expected.status == "absent_in_source"
    assert found == [tags.AUTHOR_URL_ONLY]
