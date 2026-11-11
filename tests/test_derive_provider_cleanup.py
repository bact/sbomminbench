# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""cleanup() and split_nuget_authors(); see also test_derive_provider.py."""

import unicodedata

import pytest

from sbomminbench import tags
from sbomminbench.derive.provider import _COLLECTIVE_TAIL, cleanup, split_nuget_authors
from sbomminbench.schema import EMAIL_PLACEHOLDER as PH

# ---- split_nuget_authors --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_names"),
    [
        ("", []),
        (",,", []),
        ("Foo, Inc.", ["Foo, Inc."]),
        ("A, Foo, Inc., B", ["A", "Foo, Inc.", "B"]),
        ("Inc.", ["Inc."]),
        ("A,Inc", ["A, Inc"]),
        ("A, LLC, B GmbH", ["A, LLC", "B GmbH"]),
        ("Ann Lee, Incorporated Corp", ["Ann Lee", "Incorporated Corp"]),
    ],
    ids=[
        "empty",
        "commas",
        "suffix",
        "mixed",
        "leading-suffix",
        "no-space",
        "llc",
        "not-a-suffix",
    ],
)
def test_split_nuget_authors_cases(text: str, expected_names: list[str]) -> None:
    assert split_nuget_authors(text) == expected_names


# ---- cleanup ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_names", "group"),
    [
        ("Foo and others", ["Foo"], True),
        ("Foo, others", ["Foo"], True),
        ("Foo; Contributors", ["Foo"], True),
        ("Foo & the contributors", ["Foo"], True),
        ("Foo and all authors", ["Foo"], True),
        ("Foo and other contributors", ["Foo"], True),
        ("Foo et al.", ["Foo"], True),
        ("Foo et al", ["Foo"], True),
        ("Foo, et al.", ["Foo"], True),
        ("Foo ET. AL.", ["Foo"], True),
        ("Foo, Bar, Others (See AUTHORS)", ["Foo, Bar"], True),
        ("Others (See AUTHORS)", [], True),
        ("Others", [], True),
        ("et al.", [], True),
        ("Contributors", [], True),
        ("and others", [], True),
        ("Contributors and others", [], True),
        ("Foo and its contributors and others", ["Foo"], True),
        ("Foo and others et al.", ["Foo"], True),
        ("Unknown and others", [], True),
        ("Foo, Inc.", ["Foo, Inc."], False),
        ("Anderson", ["Anderson"], False),
        ("Authors", [], False),
        ("Others Inc.", ["Others Inc."], False),
        ("Foo andothers", ["Foo andothers"], False),
        ("Foo etal", ["Foo etal"], False),
    ],
)
def test_cleanup_collectives(raw: str, expected_names: list[str], group: bool) -> None:
    out_names, out_group, _ = cleanup([raw])
    assert (out_names, out_group) == (expected_names, group)


def test_collective_tail_regex_is_quoted_from_the_spec() -> None:
    assert _COLLECTIVE_TAIL.search("Foo & authors") is not None
    assert _COLLECTIVE_TAIL.search("Foo authors") is None


def test_collective_inside_a_name_stays() -> None:
    """Only a tail is cut: `The Contributors Guild` is a legitimate name."""
    assert cleanup(["The Contributors Guild"]) == (
        ["The Contributors Guild"],
        False,
        [],
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Law & Authors", (["Law & Authors"], False, ["author_contains_collective"])),
        ("Law & Others", (["Law & Others"], False, ["author_contains_collective"])),
        ("Law & authors", (["Law"], True, [])),
        ("Law and Authors", (["Law"], True, [])),
        ("Law, Others", (["Law"], True, [])),
        ("Law and others", (["Law"], True, [])),
    ],
    ids=["amp-authors", "amp-others", "lower", "and", "comma", "and-lower"],
)
def test_capitalised_amp_tail_is_part_of_the_name(
    raw: str, expected: tuple[list[str], bool, list[str]]
) -> None:
    assert cleanup([raw]) == expected


@pytest.mark.parametrize(
    ("raw", "expected_tags"),
    [
        ("Rust Team", [tags.AUTHOR_CONTAINS_COLLECTIVE]),
        ("rust contributors", [tags.AUTHOR_CONTAINS_COLLECTIVE]),
        ("Team Rocket", []),
        ("Teamwork", []),
        ("Foo Inc.", [tags.AUTHOR_ORG_LIKE]),
        ("Foo, Inc", [tags.AUTHOR_ORG_LIKE]),
        ("Foo GmbH", [tags.AUTHOR_ORG_LIKE]),
        ("Foo Pty Ltd", [tags.AUTHOR_ORG_LIKE]),
        ("Inc.", [tags.AUTHOR_ORG_LIKE]),
        ("Tobacco", []),
        ("Marco", []),
        ("Doug", []),
        ("Prince", []),
        ("Craig", []),
        ("Qwen", []),
    ],
)
def test_cleanup_tags(raw: str, expected_tags: list[str]) -> None:
    assert cleanup([raw])[2] == expected_tags


@pytest.mark.parametrize(
    ("raw", "expected_names", "expected_tags"),
    [
        (f"<{PH}>", [], [tags.AUTHOR_EMAIL_ONLY]),
        (PH, [], [tags.AUTHOR_EMAIL_ONLY]),
        (f"({PH})", [], [tags.AUTHOR_EMAIL_ONLY]),
        (f"Foo <{PH}>", ["Foo"], []),
        (f"Foo ({PH})", ["Foo"], []),
        (f"Foo {PH} Bar", ["Foo Bar"], []),
        ("", [], []),
        ("   \t\n", [], []),
    ],
    ids=[
        "angle",
        "bare",
        "paren",
        "name-angle",
        "name-paren",
        "middle",
        "empty",
        "blank",
    ],
)
def test_cleanup_email_placeholder(
    raw: str, expected_names: list[str], expected_tags: list[str]
) -> None:
    assert cleanup([raw]) == (expected_names, False, expected_tags)


@pytest.mark.parametrize(
    "raw",
    [
        "N/A",
        "n/a",
        "NA",
        "None",
        "NULL",
        "-",
        "--",
        "?",
        "TBD",
        "ToDo",
        "Author",
        "Your Name",
        "name",
        "UNKNOWN",
    ],
)
def test_cleanup_placeholders_are_dropped_silently(raw: str) -> None:
    assert cleanup([raw]) == ([], False, [])


def test_placeholder_with_a_real_name_neighbour() -> None:
    assert cleanup(["N/A", "Alice", "unknown"]) == (["Alice"], False, [])


def test_placeholder_only_strings() -> None:
    assert cleanup(["N/A", "-", "UNKNOWN", ""]) == ([], False, [])


def test_cleanup_normalises_unicode_and_space() -> None:
    nfd = unicodedata.normalize("NFD", "René")
    assert cleanup([f"  {nfd}  Descartes\t"])[0] == ["René Descartes"]


def test_cleanup_dedupes_by_casefold_first_wins() -> None:
    nfd = unicodedata.normalize("NFD", "Jürgen")
    assert cleanup(["Foo", "FOO", "foo", nfd, "JÜRGEN"])[0] == ["Foo", "Jürgen"]


def test_cleanup_order_is_input_order() -> None:
    assert cleanup(["B", "A"])[0] == ["B", "A"]


def test_cleanup_empty_input() -> None:
    assert cleanup([]) == ([], False, [])


def test_cleanup_does_not_split() -> None:
    assert cleanup(["A, B"])[0] == ["A, B"]


def test_cleanup_tags_are_sorted_and_unique() -> None:
    out = cleanup(["Foo Inc.", "Bar Ltd.", "Rust Team", f"<{PH}>"])[2]
    assert out == sorted(
        [tags.AUTHOR_CONTAINS_COLLECTIVE, tags.AUTHOR_EMAIL_ONLY, tags.AUTHOR_ORG_LIKE]
    )
