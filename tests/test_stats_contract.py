# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.stats_contract (FIELD_CONTRACT and conformance)."""

import pytest

from sbomminbench.stats import (
    CONTRACTS,
    FIELD_CONTRACT,
    conformance,
    field_contract,
)
from sbomminbench.stats_contract import _NUSPEC_LICENSE


def test_table_sanity() -> None:
    used = {c for _, _, c in FIELD_CONTRACT}
    assert used - {_NUSPEC_LICENSE} <= set(CONTRACTS)
    # a contract with no row would never be exercised
    assert set(CONTRACTS) <= used | {"spdx_required_nuget"}
    assert len(set(CONTRACTS)) == len(CONTRACTS)


@pytest.mark.parametrize(
    ("source", "path", "expected"),
    [
        ("PKG-INFO", "License-Expression", "spdx_required"),
        ("METADATA", "License", "free_text"),
        ("pypi_json", "info.license", "free_text"),
        ("pypi_json", "info.license_expression", "spdx_required"),
        ("pyproject.toml", "project.license", "spdx_required"),
        ("pyproject.toml", "tool.poetry.license", "spdx_recommended"),
        ("pypi_json", "info.classifiers[12]", "classifier"),
        ("PKG-INFO", "Classifier[0]", "classifier"),
        ("pyproject.toml", "project.classifiers[3]", "classifier"),
        ("npm_registry", "license", "npm_license"),
        ("package.json", "licenses", "free_text"),
        ("Cargo.toml.orig", "package.license", "cargo_license"),
        ("crates_api", "version.license", "cargo_license"),
        ("Cargo.toml", "package.license-file", "file_ref"),
        ("nuget_catalog", "licenseFile", "file_ref"),
        ("nuget_catalog", "licenseExpression", "spdx_required_nuget"),
        ("pom.xml", "licenses.license[0].name", "spdx_recommended"),
        ("pom.xml", "parent[1].licenses.license[2].url", "url"),
        ("nuspec", "metadata.licenseUrl", "url"),
        ("nuget_catalog", "licenseUrl", "url"),
        ("hf_card", "cardData.license_link", "url"),
        ("hf_card", "cardData.license", "hub_key"),
        ("hf_api", "tags[4]", "hub_key"),
        ("hf_card", "cardData.license_name", "free_text"),
        ("pypi_json", "info.name", None),
        ("pypi_json", "info.license_expression_x", None),
    ],
)
def test_field_contract(source: str, path: str, expected: str | None) -> None:
    assert field_contract(source, path) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("file", "file_ref"),
        ("expression", "spdx_required_nuget"),
        (None, "free_text"),
        ("other", "free_text"),
    ],
)
def test_nuspec_license_follows_type(kind: str | None, expected: str) -> None:
    siblings = {} if kind is None else {"nuspec:metadata.license@type": kind}
    assert field_contract("nuspec", "metadata.license", siblings) == expected


# One row per contract, plus the rule branches.
CASES = [
    ("spdx_required", "id_shaped", "valid", "MIT", "conforms"),
    ("spdx_required", "id_shaped", "noncanonical", "mit", "lenient"),
    ("spdx_required", "expression_shaped", "deprecated", "GPL-2.0+ OR MIT", "lenient"),
    ("spdx_required", "id_shaped", "unknown_id", "GPLv3", "violates"),
    ("spdx_required", "expression_shaped", "malformed", "MIT AND", "violates"),
    ("spdx_required", "prose", "not_applicable", "MIT License", "violates"),
    ("spdx_required_nuget", "id_shaped", "valid", "MIT", "conforms"),
    ("spdx_required_nuget", "id_shaped", "valid", "LicenseRef-x", "violates"),
    ("spdx_recommended", "id_shaped", "valid", "Apache-2.0", "conforms"),
    ("spdx_recommended", "prose", "not_applicable", "Apache License", "not_spdx"),
    ("spdx_recommended", "expression_shaped", "malformed", "MIT AND", "not_spdx"),
    ("spdx_recommended", "id_shaped", "noncanonical", "mit", "lenient"),
    (
        "npm_license",
        "file_ref",
        "not_applicable",
        "SEE LICENSE IN LICENSE.md",
        "conforms",
    ),
    ("npm_license", "placeholder", "not_applicable", "UNLICENSED", "conforms"),
    ("npm_license", "prose", "not_applicable", "Freeware", "violates"),
    (
        "cargo_license",
        "nonstandard_compound",
        "not_applicable",
        "MIT/Apache-2.0",
        "lenient",
    ),
    ("cargo_license", "nonstandard_compound", "not_applicable", "MIT, ISC", "violates"),
    (
        "cargo_license",
        "nonstandard_compound",
        "not_applicable",
        "MIT/ISC; X",
        "violates",
    ),
    ("cargo_license", "expression_shaped", "valid", "MIT OR ISC", "conforms"),
    ("url", "spdx_url", "valid", "https://spdx.org/licenses/MIT.html", "conforms"),
    ("url", "other_url", "noncanonical", "www.example.com/l", "lenient"),
    ("url", "nuget_license_url", "deprecated", "https://x.y/GPL-3.0", "violates"),
    ("url", "other_url", "malformed", "htp://x", "violates"),
    ("url", "prose", "not_applicable", "see site", "violates"),
    ("free_text", "prose", "not_applicable", "MIT", "not_applicable"),
    (
        "classifier",
        "trove_classifier",
        "not_applicable",
        "License :: X",
        "not_applicable",
    ),
    ("file_ref", "file_ref", "not_applicable", "LICENSE", "not_applicable"),
    ("hub_key", "id_shaped", "unknown_id", "qwen", "not_applicable"),
]


@pytest.mark.parametrize(
    ("contract", "fmt", "validity", "raw", "expected"),
    CASES,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)],
)
def test_conformance(
    contract: str, fmt: str, validity: str, raw: str, expected: str
) -> None:
    assert conformance(contract, fmt, validity, raw) == expected  # type: ignore[arg-type]


def test_every_contract_has_a_case() -> None:
    assert set(CONTRACTS) <= {c[0] for c in CASES}


@pytest.mark.parametrize(
    ("contract", "fmt", "raw"),
    [
        (None, "id_shaped", "MIT"),
        ("spdx_required", None, None),
        ("url", "other_url", 3),
    ],
    ids=["no-contract", "no-format", "not-a-string"],
)
def test_conformance_not_applicable(
    contract: str | None, fmt: str | None, raw: object
) -> None:
    assert conformance(contract, fmt, "valid", raw) == "not_applicable"  # type: ignore[arg-type]
