# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Declarations that list several licence entries."""

import pytest

from sbomminbench import tags as T
from sbomminbench.classify import format_validity, license_format
from sbomminbench.derive.license import derive_license
from sbomminbench.schema import (
    ExpectedLicense,
    JsonValue,
    Observation,
    native_type_of,
)

APACHE = "Apache-2.0"
GPL_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
MIT_URL = "https://opensource.org/license/mit"


def ob(source: str, path: str, raw: JsonValue) -> Observation:
    fmt = license_format(raw) if isinstance(raw, str) else None
    return Observation.model_validate(
        {
            "element": "license",
            "source": source,
            "field_path": path,
            "raw": raw,
            "native_type": native_type_of(raw),
            "semantic_format": fmt,
            "format_validity": format_validity(raw, fmt)
            if isinstance(raw, str) and fmt
            else None,
        }
    )


def run(*obs: Observation) -> tuple[ExpectedLicense, list[str]]:
    return derive_license(list(obs), [])


def pom(path: str, raw: str) -> Observation:
    return ob("pom.xml", f"licenses.license{path}", raw)


@pytest.mark.parametrize(
    "rows",
    [
        [pom("[0].name", APACHE), pom("[1].url", GPL_URL)],
        [pom("[0].name", APACHE), pom("[1].url", "http://example.org/mylicense")],
        [pom("[0].name", APACHE), pom("[1].name", "LICENSE.txt")],
        [ob("package.json", "licenses", [{"type": "MIT"}, {"url": GPL_URL}])],
        [ob("hf_card", "cardData.license", ["mit", "other"])],
    ],
    ids=["gpl-url", "unknown-url", "file", "npm", "hf-other"],
)
def test_an_entry_without_a_vote_makes_the_declaration_unresolved(
    rows: list[Observation],
) -> None:
    e, tags = run(*rows)
    assert (e.status, e.value) == ("auto_unresolved", None)
    assert T.LICENSE_ENTRY_UNRESOLVED in tags


def test_a_name_and_url_of_one_entry_are_one_entry() -> None:
    e, tags = run(pom("[0].name", "MIT License"), pom("[0].url", GPL_URL))
    assert e.status == "auto_unanimous"
    assert e.value is not None and e.value.spdx_expression == "MIT"
    assert T.LICENSE_ENTRY_UNRESOLVED not in tags


def test_a_single_unvoting_entry_in_another_source_does_not_matter() -> None:
    e, _ = run(pom("[0].name", APACHE), ob("pypi_json", "info.license", "Apache-2.0"))
    assert e.status == "auto_unanimous"


def test_maven_entries_that_all_vote_are_or() -> None:
    e, tags = run(
        pom("[0].name", "EPL-2.0"),
        pom("[0].url", "https://www.eclipse.org/legal/epl-v20.html"),
        pom("[1].name", "LGPL-2.1-only"),
    )
    assert e.status == "auto_unanimous"
    assert e.value is not None and e.value.spdx_expression == "EPL-2.0 OR LGPL-2.1-only"
    assert T.LICENSE_LIST_OR in tags and T.LICENSE_MULTI_SOURCE_DISAGREE not in tags
    assert set(e.basis) >= {"pom.xml:licenses.license[0].name"}
    assert "pom.xml:licenses.license[1].name" in e.basis
    assert any(t.input == "EPL-2.0 OR LGPL-2.1-only" for t in e.licenseid)


def test_maven_entries_in_a_parent_prefix_group_separately() -> None:
    rows = [
        ob("pom.xml", "parent[1].licenses.license[0].name", "MIT"),
        ob("pom.xml", "parent[1].licenses.license[1].name", APACHE),
    ]
    e, tags = run(*rows)
    assert e.value is not None and e.value.spdx_expression == "Apache-2.0 OR MIT"
    assert T.LICENSE_LIST_OR in tags


def test_maven_or_group_disagreeing_with_another_source_conflicts() -> None:
    e, tags = run(
        pom("[0].name", "MIT"), pom("[1].name", APACHE), ob("pypi_json", "x", "ISC")
    )
    assert e.status == "auto_conflict"
    assert T.LICENSE_MULTI_SOURCE_DISAGREE in tags


def test_maven_entries_with_one_expression_are_not_an_or() -> None:
    e, tags = run(pom("[0].name", "MIT"), pom("[1].name", "MIT License"))
    assert e.value is not None and e.value.spdx_expression == "MIT"
    assert T.LICENSE_LIST_OR not in tags


@pytest.mark.parametrize(
    "row",
    [
        ob("package.json", "licenses", [{"type": "MIT"}, {"type": "Apache-2.0"}]),
        ob("hf_card", "cardData.license", ["mit", "apache-2.0"]),
    ],
    ids=["npm", "hf"],
)
def test_other_ecosystems_offer_the_or_as_a_suggestion(row: Observation) -> None:
    e, tags = run(row)
    assert e.status == "auto_unresolved" and e.value is None
    assert e.candidates == ["Apache-2.0 OR MIT"]
    assert T.LICENSE_MULTIPLE_ENTRIES in tags
    assert T.LICENSE_LIST_OR not in tags


def test_and_inside_an_entry_is_parenthesised() -> None:
    e, _ = run(pom("[0].name", "MIT AND ISC"), pom("[1].name", APACHE))
    assert e.value is not None
    assert "(" in e.value.spdx_expression or "AND" in e.value.spdx_expression


@pytest.mark.parametrize(
    ("raws", "fires"),
    [
        (["MIT", "Apache-2.0"], True),
        (["MIT", "mit"], False),
        (["MIT", "https://opensource.org/license/mit"], False),
        (["MIT", "UNKNOWN"], False),
        (["MIT", "LICENSE"], False),
    ],
    ids=["differ", "case", "url", "placeholder", "file"],
)
def test_multiple_values_tag_is_per_declaration(raws: list[str], fires: bool) -> None:
    names: list[JsonValue] = list(raws)
    row = ob("package.json", "licenses", names)
    assert (T.LICENSE_MULTIPLE_VALUES in T.structural_tags([row], [], "npm")) == fires


def test_nuspec_type_row_is_not_a_second_licence() -> None:
    rows = [
        ob("nuspec", "metadata.license", "MIT"),
        ob("nuspec", "metadata.license@type", "expression"),
    ]
    assert T.LICENSE_MULTIPLE_VALUES not in T.structural_tags(rows, [], "nuget")
