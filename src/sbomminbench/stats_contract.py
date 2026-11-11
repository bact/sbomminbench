# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Field contracts and licence conformance.

A contract says what a field promises about its value. Conformance is a pure
function of ``(contract, semantic_format, format_validity, raw)``, derived at
export time and never stored in entries, so a fix needs no rebuild. The table
is read like ``codemeta.TABLE``: the first full match wins.
"""

import re
from collections.abc import Mapping
from typing import Final, Literal

from sbomminbench.schema import FormatValidity, JsonValue, SemanticFormat

type Conformance = Literal[
    "conforms", "lenient", "violates", "not_spdx", "not_applicable"
]

CONTRACTS: Final[tuple[str, ...]] = (
    "spdx_required",  # PEP 639, Cargo reference
    "spdx_required_nuget",  # NuGet licenseExpression: no LicenseRef-
    "spdx_recommended",  # Poetry, POM
    "npm_license",  # SPDX recommended; "SEE LICENSE IN <f>" and UNLICENSED conform
    "cargo_license",  # SPDX required; "A/B" is the legacy form (lenient)
    "free_text",
    "classifier",
    "file_ref",
    "url",
    "hub_key",
)
_SPDX_CONTRACTS = frozenset(
    {
        "spdx_required",
        "spdx_required_nuget",
        "spdx_recommended",
        "npm_license",
        "cargo_license",
    }
)
_SPDX_FORMS = frozenset({"id_shaped", "expression_shaped"})
_URL_FORMS = frozenset({"spdx_url", "osi_url", "nuget_license_url", "other_url"})
_SLASH_ONLY = re.compile(r"[^\s/;,&]+(?:/[^\s/;,&]+)+")
_NUSPEC_LICENSE = "nuspec_license"  # resolved by the sibling "@type" observation
_NUSPEC_TYPE_KEY = "nuspec:metadata.license@type"
_POM = r"(?:parent\[\d+\]\.)?"
_TXT = r"METADATA|PKG-INFO"
_NPM = r"npm_registry|package\.json"
_CARGO = r"Cargo\.toml(?:\.orig)?"

# (source pattern, field_path pattern, contract); first full match wins.
FIELD_CONTRACT: Final[list[tuple[re.Pattern[str], re.Pattern[str], str]]] = [
    (re.compile(a), re.compile(b), c)
    for a, b, c in [
        (_TXT, r"License-Expression", "spdx_required"),
        ("pypi_json", r"info\.license_expression", "spdx_required"),
        ("pyproject.toml", r"project\.license", "spdx_required"),
        (_TXT, r"License", "free_text"),
        ("pypi_json", r"info\.license", "free_text"),
        ("pyproject.toml", r"tool\.poetry\.license", "spdx_recommended"),
        (r".*", r"(?:(?:project|info)\.classifiers|Classifier)\[\d+\]", "classifier"),
        (_NPM, r"license", "npm_license"),
        (_NPM, r"licenses", "free_text"),
        (_CARGO, r"package\.license", "cargo_license"),
        ("crates_api", r"version\.license", "cargo_license"),
        (_CARGO, r"package\.license-file", "file_ref"),
        ("nuget_catalog", r"licenseFile", "file_ref"),
        ("nuget_catalog", r"licenseExpression", "spdx_required_nuget"),
        ("nuspec", r"metadata\.license", _NUSPEC_LICENSE),
        ("nuspec", r"metadata\.license@type", "free_text"),
        ("pom.xml", rf"{_POM}licenses\.license\[\d+\]\.name", "spdx_recommended"),
        ("pom.xml", rf"{_POM}licenses\.license\[\d+\]\.url", "url"),
        ("pom.xml", rf"{_POM}licenses\.license", "free_text"),
        ("nuget_catalog", r"licenseUrl", "url"),
        ("nuspec", r"metadata\.licenseUrl", "url"),
        ("hf_card", r"cardData\.license_link", "url"),
        ("hf_card", r"cardData\.license_name", "free_text"),
        ("hf_card", r"cardData\.license", "hub_key"),
        ("hf_api", r"tags\[\d+\]", "hub_key"),
    ]
]


def field_contract(
    source: str, field_path: str, siblings: Mapping[str, JsonValue] | None = None
) -> str | None:
    """Return the contract of a licence field, or ``None`` when it has none.

    ``siblings`` maps observation keys to raw values from the same record; the
    NuGet ``metadata.license`` contract depends on its ``@type`` sibling.
    """
    for src, path, contract in FIELD_CONTRACT:
        if not (src.fullmatch(source) and path.fullmatch(field_path)):
            continue
        if contract != _NUSPEC_LICENSE:
            return contract
        kind = (siblings or {}).get(_NUSPEC_TYPE_KEY)
        if kind == "file":
            return "file_ref"
        return "spdx_required_nuget" if kind == "expression" else "free_text"
    return None


def _spdx_conformance(
    contract: str,
    fmt: SemanticFormat,
    validity: FormatValidity | None,
    raw: str,
) -> Conformance:
    text = raw.strip()
    if contract == "npm_license" and (
        text.startswith("SEE LICENSE IN") or text == "UNLICENSED"
    ):
        return "conforms"
    if (
        contract == "cargo_license"
        and fmt == "nonstandard_compound"
        and _SLASH_ONLY.fullmatch(text)
    ):
        return "lenient"
    if fmt not in _SPDX_FORMS or (
        contract == "spdx_required_nuget" and "LicenseRef-" in raw
    ):
        return "violates"
    if validity == "valid":
        return "conforms"
    return "lenient" if validity in ("noncanonical", "deprecated") else "violates"


def conformance(
    contract: str | None,
    fmt: SemanticFormat | None,
    validity: FormatValidity | None,
    raw: JsonValue,
) -> Conformance:
    """Return how a value meets its field contract.

    A value without a semantic format (not a string) and a field without a
    contract are ``not_applicable``. Callers skip absent observations.
    """
    if contract is None or fmt is None or not isinstance(raw, str):
        return "not_applicable"
    if contract in _SPDX_CONTRACTS:
        verdict = _spdx_conformance(contract, fmt, validity, raw)
        # SPDX is only recommended there: a non-SPDX value breaks no rule.
        if contract == "spdx_recommended" and verdict == "violates":
            return "not_spdx"
        return verdict
    if contract == "url":
        if fmt not in _URL_FORMS:
            return "violates"
        if validity == "valid":
            return "conforms"
        return "lenient" if validity == "noncanonical" else "violates"
    return "not_applicable"
