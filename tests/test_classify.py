# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys
from typing import get_args

import pytest

from sbomminbench import classify
from sbomminbench.classify import (
    PLACEHOLDERS,
    format_validity,
    license_format,
    native_type_of,
    spdx_validity,
    worst,
)
from sbomminbench.schema import FormatValidity, SemanticFormat

NUMPY_TEXT = "BSD License text line\n" * 2000  # stands in for numpy's 46 KB text

# (input, form, validity) -- every row of the classifier table.
TABLE: list[tuple[str, SemanticFormat, FormatValidity]] = [
    ("Apache-2.0", "id_shaped", "valid"),
    ("mit", "id_shaped", "noncanonical"),
    ("apache-2.0", "id_shaped", "noncanonical"),
    ("cc-by-nc-4.0", "id_shaped", "noncanonical"),
    ("GPL-3.0", "id_shaped", "deprecated"),
    ("AGPL-3.0", "id_shaped", "deprecated"),
    ("GPL-3.0+", "id_shaped", "deprecated"),
    ("GPL-2.0+", "id_shaped", "deprecated"),
    ("GPLv3", "id_shaped", "unknown_id"),
    ("qwen", "id_shaped", "unknown_id"),
    ("llama3.1", "id_shaped", "unknown_id"),
    ("Classpath-exception-2.0", "id_shaped", "unknown_id"),
    ("OpenMDW-1.0", "id_shaped", "unknown_id"),
    ("LicenseRef-foo", "id_shaped", "valid"),
    ("DocumentRef-x:LicenseRef-y", "id_shaped", "valid"),
    ("LicenseRef-", "id_shaped", "malformed"),
    ("MIT\u200b", "id_shaped", "noncanonical"),
    ("MIT OR Apache-2.0", "expression_shaped", "valid"),
    ("(MIT OR Apache-2.0)", "expression_shaped", "valid"),
    ("(MIT AND BSD-3-Clause)", "expression_shaped", "valid"),
    ("MIT or Apache-2.0", "expression_shaped", "noncanonical"),
    (
        "LGPL-2.1-or-later WITH Classpath-exception-2.0",
        "expression_shaped",
        "valid",
    ),
    ("GPL-2.0-only WITH Foo-exception", "expression_shaped", "unknown_id"),
    ("MIT AND", "expression_shaped", "malformed"),
    ("(MIT", "expression_shaped", "malformed"),
    ("Apache-2.0 OR", "expression_shaped", "malformed"),
    ("MIT OR Apache 2.0", "expression_shaped", "malformed"),
    ("MIT/Apache-2.0", "nonstandard_compound", "not_applicable"),
    ("MIT; Apache-2.0", "nonstandard_compound", "not_applicable"),
    ("MIT, BSD-3-Clause", "nonstandard_compound", "not_applicable"),
    ("https://spdx.org/licenses/Apache-2.0.html", "spdx_url", "valid"),
    ("https://spdx.org/licenses/GPL-3.0.html", "spdx_url", "deprecated"),
    ("https://spdx.org/licenses/Nope-1.0.html", "spdx_url", "unknown_id"),
    ("https://opensource.org/license/apache-2.0", "osi_url", "valid"),
    ("https://licenses.nuget.org/MIT", "nuget_license_url", "valid"),
    ("licenses.nuget.org/MIT", "nuget_license_url", "noncanonical"),
    ("https://www.apache.org/licenses/LICENSE-2.0", "other_url", "valid"),
    ("www.example.com/license", "other_url", "noncanonical"),
    ("htp://x", "other_url", "malformed"),
    ("http://", "other_url", "malformed"),
    ("License :: OSI Approved :: MIT License", "trove_classifier", "not_applicable"),
    ("Apache, Version 2.0", "prose", "not_applicable"),
    ("Apache License 2.0", "prose", "not_applicable"),
    ("Dual License", "prose", "not_applicable"),
    ("SEE LICENSE IN LICENSE.md", "file_ref", "not_applicable"),
    ("UNKNOWN", "placeholder", "not_applicable"),
    ("non-standard", "placeholder", "not_applicable"),
    (NUMPY_TEXT, "license_text", "not_applicable"),
    ("", "empty", "not_applicable"),
]
IDS = [f"{i}-{t[0][:20]!r}" for i, t in enumerate(TABLE)]


@pytest.mark.parametrize(("value", "form", "validity"), TABLE, ids=IDS)
def test_table(value: str, form: SemanticFormat, validity: FormatValidity) -> None:
    assert license_format(value) == form
    assert format_validity(value, form) == validity


@pytest.mark.parametrize(
    ("value", "form"),
    [
        ("Unlicense", "id_shaped"),
        ("unlicense-libtelnet", "id_shaped"),
        ("BSD-3-Clause-No-Military-License", "id_shaped"),
        ("BSD-3-Clause-No-Nuclear-License-2014", "id_shaped"),
        ("Latex2e-translated-notice", "id_shaped"),
        ("MIT-License", "id_shaped"),
        ("BSD-License", "id_shaped"),
        ("Apache_License_2.0", "prose"),
        ("minimax-h3-community-license-agreement", "id_shaped"),
        ("llama3.1-community-license", "id_shaped"),
        ("LICENSE-MIT", "file_ref"),
        ("COPYING.txt", "file_ref"),
        ("NOTICE", "file_ref"),
        ("licenses/MIT-License", "file_ref"),
        ("MIT-License.txt", "file_ref"),
        ("file: MIT-License", "file_ref"),
    ],
    ids=lambda p: str(p)[:24],
)
def test_file_ref_needs_file_evidence(value: str, form: SemanticFormat) -> None:
    assert license_format(value) == form


@pytest.mark.parametrize(
    ("value", "form"),
    [
        ("https://opensource.org/licenses/MIT", "osi_url"),
        ("https://www.gnu.org/licenses/gpl-3.0.html", "other_url"),
        ("<https://spdx.org/licenses/MIT>", "spdx_url"),
        ("Apache License (2.0)", "prose"),
        ("GPL 2.0 or later version", "prose"),
        ("  MIT  ", "id_shaped"),
        ("\u200b\u200b", "empty"),
        ("LICENSE", "file_ref"),
        ("other", "placeholder"),
        ("MIT & ISC", "nonstandard_compound"),
        ("x" * 201, "license_text"),
    ],
    ids=lambda p: str(p)[:24],
)
def test_form_extras(value: str, form: SemanticFormat) -> None:
    assert license_format(value) == form


@pytest.mark.parametrize(
    ("value", "form", "validity"),
    [
        ("<https://spdx.org/licenses/MIT>", "spdx_url", "noncanonical"),
        ("https://spdx.org/licenses/MIT.json", "spdx_url", "valid"),
        ("https://spdx.org/licenses/", "spdx_url", "malformed"),
        (
            "https://licenses.nuget.org/MIT%20OR%20Apache-2.0",
            "nuget_license_url",
            "valid",
        ),
        ("https://licenses.nuget.org/", "nuget_license_url", "malformed"),
        ("ftp://example.com/license", "other_url", "malformed"),
        ("MIT\u200b", "id_shaped", "noncanonical"),
        ("\u200bGPL-3.0", "id_shaped", "deprecated"),
        ("\u200bGPLv3", "id_shaped", "unknown_id"),
        ("MIT", "prose", "not_applicable"),
    ],
    ids=lambda p: str(p)[:24],
)
def test_validity_extras(
    value: str, form: SemanticFormat, validity: FormatValidity
) -> None:
    assert format_validity(value, form) == validity


def test_hidden_char_only_raises_to_noncanonical() -> None:
    assert format_validity("MIT", "id_shaped") == "valid"
    assert format_validity("MIT\u200b", "id_shaped") == "noncanonical"
    assert format_validity(" MIT ", "id_shaped") == "valid"  # plain space: not hidden


def test_worst_precedence() -> None:
    order = ["valid", "noncanonical", "deprecated", "unknown_id", "malformed"]
    for i, a in enumerate(order):
        for b in order[i:]:
            assert worst(a, b) == b == worst(b, a)  # type: ignore[arg-type]


def test_all_forms_and_validities_reachable_types() -> None:
    assert set(get_args(FormatValidity)) >= {v for _, _, v in TABLE}
    assert {f for _, f, _ in TABLE} == set(get_args(SemanticFormat))


def test_spdx_list_version_pinned() -> None:
    assert classify.SPDX_LIST_VERSION == "3.27.0"
    assert "unknown" in PLACEHOLDERS
    assert spdx_validity("MIT") == "valid"


def test_private_tables_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classify, "_LICENSES", {})
    monkeypatch.setattr(classify, "_EXCEPTIONS", {})
    assert spdx_validity("GPL-3.0") == "valid"  # no deprecation data, no crash


def test_native_type_reexported() -> None:
    assert native_type_of(None) == "null"


def test_independent_of_licenseid_and_pitloom() -> None:
    code = (
        "import sys, sbomminbench.classify, sbomminbench.tags, sbomminbench.codemeta;"
        "bad = [m for m in sys.modules if m.split('.')[0] in {'licenseid', 'pitloom'}];"
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
