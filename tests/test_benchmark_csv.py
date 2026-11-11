# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.benchmark_csv (the rows of benchmark.csv)."""

import csv
import io
from typing import Any

import pytest
from helpers_evaluate import load, set_expected

from sbomminbench import benchmark_csv
from sbomminbench.benchmark_csv import COLUMNS, UNSCORED_COLUMNS, benchmark_rows
from sbomminbench.schema import Entry

NAME = {"name": "magika", "canonical_key": "magika", "accepted": ["magika"]}


def provider(*names: str, group: bool = False) -> dict[str, Any]:
    return {
        "entities": [{"name": n, "role": "author"} for n in names],
        "has_unnamed_group": group,
    }


def entry(mutate: Any = None) -> Entry:
    d = load("magika")
    if mutate:
        mutate(d)
    return Entry.model_validate(d)


def table(e: Entry) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    scored, unscored = benchmark_rows([e])
    return (
        [dict(zip(COLUMNS, r, strict=True)) for r in scored],
        [dict(zip(UNSCORED_COLUMNS, r, strict=True)) for r in unscored],
    )


def test_scored_root_and_unscored_model() -> None:
    scored, unscored = table(entry())
    (row,) = scored
    assert row == {
        "id": "pkg:pypi/magika@0.6.1",
        "subject": "root",
        "ecosystem": "pypi",
        "kind": "package",
        "name_input": "magika",
        "expected_name": "magika",
        "expected_provider": "Magika Developers",
        "provider_has_unnamed_group": "false",
        "expected_license": "Apache-2.0",
        "name_status": "auto_unanimous",
        "provider_status": "auto_unanimous",
        "license_status": "auto_unanimous",
    }
    (blocked,) = unscored
    assert blocked["subject"] == "model:1"
    assert blocked["reason"] == (
        "name=not_assessed; provider=not_assessed; license=not_assessed"
    )
    assert [blocked[c] for c in ("expected_name", "expected_provider")] == ["", ""]


def test_providers_joined_in_stored_order_with_group_flag() -> None:
    def mutate(d: dict[str, Any]) -> None:
        set_expected(
            d, "root", "provider", "auto_unanimous", provider("Zed", "Ann", group=True)
        )

    (row,), _ = table(entry(mutate))
    assert row["expected_provider"] == "Zed; Ann"
    assert row["provider_has_unnamed_group"] == "true"


@pytest.mark.parametrize(
    ("element", "status"),
    [("provider", "absent_in_source"), ("license", "absent_in_source")],
)
def test_absent_in_source_is_a_scored_abstention(element: str, status: str) -> None:
    (row,), _ = table(entry(lambda d: set_expected(d, "root", element, status)))
    assert row[f"expected_{element}"] == ""
    assert row[f"{element}_status"] == status


def test_human_confirmed_without_value_is_an_abstention() -> None:
    def mutate(d: dict[str, Any]) -> None:
        set_expected(d, "root", "license", "human_confirmed", None)

    (row,), _ = table(entry(mutate))
    assert row["license_status"] == "human_confirmed"
    assert row["expected_license"] == ""


@pytest.mark.parametrize("status", ["auto_conflict", "auto_unresolved", "excluded"])
def test_only_the_unscored_element_blocks_the_row(status: str) -> None:
    scored, unscored = table(
        entry(lambda d: set_expected(d, "root", "license", status))
    )
    assert scored == []
    row = next(r for r in unscored if r["subject"] == "root")
    assert row["reason"].startswith(f"license={status}")
    assert row["expected_license"] == ""
    assert row["expected_name"] == "magika"  # scored elements keep their values


def test_provider_name_with_the_separator_is_unscored() -> None:
    def mutate(d: dict[str, Any]) -> None:
        set_expected(d, "root", "provider", "auto_unanimous", provider("A; B", "C"))

    scored, unscored = table(entry(mutate))
    assert scored == []
    row = next(r for r in unscored if r["subject"] == "root")
    assert row["reason"] == benchmark_csv.SEPARATOR_REASON
    assert row["expected_provider"] == ""
    assert row["provider_status"] == "auto_unanimous"


def test_order_is_by_id_then_subject_and_the_text_is_json_free() -> None:
    a, b = entry(), entry(lambda d: d.update(id="pkg:pypi/aaa@0.6.1"))
    scored, _ = benchmark_rows([a, b])
    assert [r[0] for r in scored] == ["pkg:pypi/aaa@0.6.1", "pkg:pypi/magika@0.6.1"]
    assert benchmark_csv.benchmark_texts([a, b]) == benchmark_csv.benchmark_texts(
        [b, a]
    )
    text = benchmark_csv.benchmark_texts([a])["benchmark.csv"]
    assert "\r" not in text and text.endswith("\n")
    assert next(csv.reader(io.StringIO(text))) == COLUMNS
    assert "{" not in text.split("\n", 1)[1]


@pytest.mark.parametrize(
    ("purl", "name"),
    [
        ("pkg:maven/org.slf4j/slf4j-api@2.0.16", "slf4j-api"),
        ("pkg:npm/%40babel/core@7.26.0", "core"),
        ("pkg:nuget/newtonsoft.json@13.0.3", "newtonsoft.json"),
    ],
    ids=["maven", "npm-scope", "nuget"],
)
def test_name_input_is_the_purl_name_segment(purl: str, name: str) -> None:
    assert benchmark_csv.name_input(purl) == name
