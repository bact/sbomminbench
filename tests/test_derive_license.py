# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import pytest

from sbomminbench import tags as T
from sbomminbench.classify import format_validity, license_format
from sbomminbench.derive import license as L
from sbomminbench.derive.license import (
    BLOCK_CATEGORIES,
    OK_CATEGORIES,
    Candidate,
    candidates,
    derive_license,
)
from sbomminbench.schema import (
    MAX_RAW_CHARS,
    ExpectedLicense,
    JsonValue,
    Observation,
    native_type_of,
)

TROVE_APACHE = "License :: OSI Approved :: Apache Software License"
TROVE_BSD = "License :: OSI Approved :: BSD License"
TROVE_MIT = "License :: OSI Approved :: MIT License"


def ob(source: str, path: str, raw: JsonValue, **kw: object) -> Observation:
    fmt = license_format(raw) if isinstance(raw, str) else None
    args: dict[str, object] = {
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
    return Observation.model_validate(args | kw)


def run(*obs: Observation) -> tuple[ExpectedLicense, list[str]]:
    return derive_license(list(obs), [])


def outcome(*obs: Observation) -> tuple[str, str | None, list[str]]:
    e, tags = run(*obs)
    return e.status, (e.value.spdx_expression if e.value else None), tags


# -- the 16 rule tests ---------------------------------------------------------


def test_rule1_requests() -> None:
    e, tags = run(
        ob("pypi_json", "info.license", "Apache-2.0"),
        ob("METADATA", "License", "Apache-2.0"),
        ob("pypi_json", "info.classifiers[0]", TROVE_APACHE),
        ob("METADATA", "Classifier[0]", TROVE_APACHE),
    )
    assert e.status == "auto_unanimous"
    assert e.value is not None and e.value.spdx_expression == "Apache-2.0"
    assert e.independent_origins == 1
    assert e.basis == ["METADATA:License", "pypi_json:info.license"]
    assert T.LICENSE_TROVE_AMBIGUOUS in tags


def test_rule2_dateutil_dual_license() -> None:
    e, tags = run(
        ob("pypi_json", "info.license", "Dual License"),
        ob("pypi_json", "info.classifiers[0]", TROVE_BSD),
        ob("pypi_json", "info.classifiers[1]", TROVE_APACHE),
    )
    assert e.status == "auto_unresolved" and e.value is None
    assert T.LICENSE_TROVE_MULTIPLE in tags and T.LICENSE_UNRESOLVED_VALUE in tags
    assert e.note is not None and "'Dual License': unresolved" in e.note


def test_rule3_silero_trove_only() -> None:
    status, expr, _ = outcome(
        ob("pypi_json", "info.classifiers[0]", TROVE_MIT),
        ob("METADATA", "Classifier[0]", TROVE_MIT),
    )
    assert (status, expr) == ("auto_unanimous", "MIT")


def test_rule4_winapi_slash_compound_is_read_as_or() -> None:
    status, expr, tags = outcome(
        ob("crates_api", "version.license", "MIT/Apache-2.0"),
        ob("Cargo.toml", "package.license", "MIT/Apache-2.0"),
    )
    assert (status, expr) == ("auto_unanimous", "Apache-2.0 OR MIT")
    assert T.LICENSE_UNRESOLVED_VALUE not in tags


def test_rule4_semicolon_compound_stays_unresolved() -> None:
    status, expr, tags = outcome(
        ob("crates_api", "version.license", "MIT; Apache-2.0"),
        ob("Cargo.toml", "package.license", "MIT; Apache-2.0"),
    )
    assert (status, expr) == ("auto_unresolved", None)
    assert T.LICENSE_UNRESOLVED_VALUE in tags


def test_rule5_serde_expression() -> None:
    e, _ = run(
        ob("crates_api", "version.license", "MIT OR Apache-2.0"),
        ob("Cargo.toml", "package.license", "MIT OR Apache-2.0"),
        ob("Cargo.toml.orig", "package.license", "MIT OR Apache-2.0"),
    )
    assert e.status == "auto_unanimous"
    assert e.value is not None and e.value.spdx_expression == "Apache-2.0 OR MIT"
    assert e.independent_origins == 1 and len(e.basis) == 3


def test_rule6_gpl3_assumed_only() -> None:
    e, tags = run(ob("pypi_json", "info.license", "GPL-3.0"))
    assert e.status == "auto_unresolved" and e.value is None
    assert e.note is not None and "GPL-3.0-only, GPL-3.0-or-later" in e.note
    assert T.LICENSE_ASSUMED_ONLY in tags and T.LICENSE_REF_NEEDED not in tags


def test_rule7_conflict() -> None:
    e, tags = run(
        ob("pypi_json", "info.license", "MIT"),
        ob("pyproject.toml", "project.license", "BSD-3-Clause"),
    )
    assert e.status == "auto_conflict" and e.value is None
    assert e.candidates == ["BSD-3-Clause", "MIT"]
    assert e.independent_origins == 2
    assert T.LICENSE_MULTI_SOURCE_DISAGREE in tags


def test_rule8_qwen() -> None:
    e, tags = run(
        ob("hf_card", "cardData.license", "other"),
        ob("hf_card", "cardData.license_name", "qwen"),
        ob(
            "hf_card",
            "cardData.license_link",
            "https://huggingface.co/Qwen/Qwen2.5-72B-Instruct/blob/main/LICENSE",
        ),
        ob("hf_api", "tags[4]", "license:other"),
    )
    assert e.status == "auto_unresolved" and e.value is None
    assert {T.HF_LICENSE_OTHER, T.LICENSE_REF_NEEDED, T.LICENSE_IS_URL} <= set(tags)
    assert [t.key for t in e.licenseid] == [
        "hf_card:cardData.license_link",
        "hf_card:cardData.license_name",
    ]


def test_rule9_jquery_url_unresolved() -> None:
    e, tags = run(
        ob("nuget_catalog", "licenseUrl", "http://jquery.org/license"),
        ob("nuspec", "metadata.licenseUrl", "http://jquery.org/license"),
    )
    assert e.status == "auto_unresolved" and e.basis == []
    assert T.LICENSE_IS_URL in tags


def test_rule10_newtonsoft() -> None:
    status, expr, _ = outcome(
        ob("nuget_catalog", "licenseExpression", "MIT"),
        ob("nuspec", "metadata.license", "MIT"),
        ob("nuspec", "metadata.license@type", "expression"),
        ob("nuget_catalog", "licenseUrl", "https://licenses.nuget.org/MIT"),
        ob("nuspec", "metadata.licenseUrl", "https://licenses.nuget.org/MIT"),
    )
    assert (status, expr) == ("auto_unanimous", "MIT")


def test_rule11_ring_file_only() -> None:
    status, _, tags = outcome(
        ob("crates_api", "version.license", "non-standard"),
        ob("Cargo.toml", "package.license-file", "LICENSE"),
    )
    assert status == "auto_unresolved"
    assert {T.LICENSE_FILE_ONLY, T.LICENSE_PLACEHOLDER} <= set(tags)


def test_rule12_no_observations() -> None:
    e, tags = derive_license([], [])
    assert e.status == "absent_in_source" and e.value is None and tags == []
    assert e.basis == [] and e.independent_origins == 0 and e.licenseid == []


def test_rule13_jackson_prose_plus_url() -> None:
    status, expr, tags = outcome(
        ob(
            "pom.xml",
            "licenses.license[0].name",
            "The Apache Software License, Version 2.0",
        ),
        ob(
            "pom.xml",
            "licenses.license[0].url",
            "https://www.apache.org/licenses/LICENSE-2.0.txt",
        ),
    )
    assert (status, expr) == ("auto_unanimous", "Apache-2.0")
    assert T.LICENSE_IS_URL in tags


def test_rule14_apache_url_only() -> None:
    e, tags = run(
        ob(
            "pom.xml",
            "licenses.license[0].url",
            "https://www.apache.org/licenses/LICENSE-2.0",
        )
    )
    assert e.status == "auto_unresolved" and e.value is None
    assert e.basis == ["pom.xml:licenses.license[0].url"]  # a weak vote
    assert e.licenseid[0].trust == "resolver_rule"
    assert T.LICENSE_IS_URL in tags


def test_rule15_gnu_url_does_not_block() -> None:
    e, _ = run(
        ob("pypi_json", "info.license", "GPL-3.0-only"),
        ob(
            "pypi_json", "info.license_url", "https://www.gnu.org/licenses/gpl-3.0.html"
        ),
    )
    assert e.status == "auto_unanimous"
    assert e.value is not None and e.value.spdx_expression == "GPL-3.0-only"
    assert {t.category for t in e.licenseid} == {"exact", "assumed_only"}


def test_rule16_apache_version_prose() -> None:
    assert outcome(ob("pypi_json", "info.license", "Apache, Version 2.0"))[:2] == (
        "auto_unanimous",
        "Apache-2.0",
    )


# -- boundaries: A and B rules ---------------------------------------------------


def test_rule4_weak_only_needs_strong_trust() -> None:
    # Only the trust differs: trove list_data passes, URL resolver_rule fails.
    ok = outcome(ob("pypi_json", "info.classifiers[0]", TROVE_MIT))
    weak_url = outcome(
        ob("pypi_json", "info.license", "https://opensource.org/licenses/MIT")
    )
    assert ok[:2] == ("auto_unanimous", "MIT")
    assert weak_url[:2] == ("auto_unresolved", None)


def test_rule4_weak_votes_with_one_non_strong_fail() -> None:
    status, _, _ = outcome(
        ob("pypi_json", "info.classifiers[0]", TROVE_MIT),
        ob("pypi_json", "info.license_url", "https://opensource.org/licenses/MIT"),
    )
    assert status == "auto_unresolved"  # "any weak vote's trust is not strong"


def test_weak_vote_next_to_strong_vote_counts_in_conflict() -> None:
    e, _ = run(
        ob("pypi_json", "info.license", "Apache-2.0"),
        ob("pypi_json", "info.license_url", "https://opensource.org/licenses/MIT"),
    )
    assert e.status == "auto_conflict" and e.candidates == ["Apache-2.0", "MIT"]


def test_trove_multiple_vs_single() -> None:
    two = run(
        ob("pypi_json", "info.classifiers[0]", TROVE_MIT),
        ob("pypi_json", "info.classifiers[1]", TROVE_BSD),
    )
    one = run(ob("pypi_json", "info.classifiers[0]", TROVE_MIT))
    assert two[0].status == "auto_unresolved" and two[0].basis == []
    assert T.LICENSE_TROVE_MULTIPLE in two[1] and two[0].licenseid == []
    assert one[0].status == "auto_unanimous" and T.LICENSE_TROVE_MULTIPLE not in one[1]


def test_same_trove_in_two_sources_is_single() -> None:
    assert (
        outcome(
            ob("pypi_json", "info.classifiers[0]", TROVE_MIT),
            ob("METADATA", "Classifier[0]", TROVE_MIT),
        )[0]
        == "auto_unanimous"
    )


def test_unresolved_trove_never_blocks() -> None:
    status, expr, tags = outcome(
        ob("pypi_json", "info.license", "MIT"),
        ob("pypi_json", "info.classifiers[0]", TROVE_BSD),
    )
    assert (status, expr) == ("auto_unanimous", "MIT")
    assert T.LICENSE_TROVE_AMBIGUOUS in tags


def test_blocking_value_blocks_even_with_vote() -> None:
    e, _ = run(
        ob("pypi_json", "info.license", "MIT"),
        ob("METADATA", "License", "ASL 2.0"),
    )
    assert e.status == "auto_unresolved" and e.basis == ["pypi_json:info.license"]


# -- absent vs empty vs placeholder ----------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["", "UNKNOWN", [], None, 5, True, [""], ["N/A", ""]],
    ids=[
        "empty",
        "placeholder",
        "list_empty",
        "null",
        "number",
        "bool",
        "list_blank",
        "list_ph",
    ],
)
def test_nothing_usable_is_absent_in_source(raw: JsonValue) -> None:
    e, _ = run(ob("pypi_json", "info.license", raw))
    assert e.status == "absent_in_source" and e.value is None


def test_absent_row_is_absent_in_source() -> None:
    gone = Observation(
        element="license",
        source="pypi_json",
        field_path="info.license",
        present=False,
        native_type="absent",
    )
    assert run(gone)[0].status == "absent_in_source"


def test_placeholder_does_not_vote_and_later_source_wins() -> None:
    e, tags = run(
        ob("pypi_json", "info.license", "UNKNOWN"),
        ob("METADATA", "License-Expression", "MIT"),
    )
    assert e.status == "auto_unanimous" and e.basis == ["METADATA:License-Expression"]
    assert T.LICENSE_PLACEHOLDER in tags


def test_hf_other_gets_hf_tag_non_hf_other_gets_placeholder_tag() -> None:
    hf = run(ob("hf_card", "cardData.license", "other"))[1]
    py = run(ob("pypi_json", "info.license", "other"))[1]
    assert hf == [T.HF_LICENSE_OTHER] and py == [T.LICENSE_PLACEHOLDER]


# -- candidates(): shapes ---------------------------------------------------------


def cands(o: Observation, *more: Observation) -> list[Candidate]:
    return candidates(o, {x.key: x for x in (o, *more)})


def test_list_valued_license_yields_each_item() -> None:
    o = ob("package.json", "licenses", ["MIT", 3, {"type": "ISC"}, "Apache-2.0"])
    assert [c.value for c in cands(o)] == ["MIT", "ISC", "Apache-2.0"]
    status, _, tags = outcome(o)
    assert (status, T.LICENSE_MULTIPLE_ENTRIES in tags) == ("auto_unresolved", True)


def test_npm_license_object() -> None:
    o = ob(
        "npm_registry",
        "license",
        {
            "type": "MIT",
            "url": "https://opensource.org/licenses/MIT",
            "file": "LICENSE",
        },
    )
    got = cands(o)
    assert [(c.value, c.forced) for c in got] == [
        ("MIT", None),
        ("LICENSE", "file_ref"),
        ("https://opensource.org/licenses/MIT", None),
    ]
    assert outcome(o)[:2] == ("auto_unanimous", "MIT")


@pytest.mark.parametrize(
    ("obj", "values"),
    [
        ({"name": "ISC", "type": 5}, ["ISC"]),
        ({"text": "MIT", "type": "ISC"}, ["MIT"]),
        ({"type": None, "url": 7}, []),
        ({}, []),
    ],
    ids=["non_str_type", "text_first", "wrong_types", "empty"],
)
def test_dict_name_key_order_and_types(obj: JsonValue, values: list[str]) -> None:
    assert [c.value for c in cands(ob("npm_registry", "license", obj))] == values


def test_hf_api_prefix_stripped_only_for_hf_api() -> None:
    assert cands(ob("hf_api", "tags[0]", "license:apache-2.0"))[0].value == "apache-2.0"
    assert cands(ob("hf_card", "cardData.license", "license:x"))[0].value == "license:x"
    assert outcome(ob("hf_api", "tags[0]", "license:apache-2.0"))[:2] == (
        "auto_unanimous",
        "Apache-2.0",
    )


def test_nuspec_file_type_sibling_forces_file_ref() -> None:
    text = ob("nuspec", "metadata.license", "LICENSE.txt")
    kind = ob("nuspec", "metadata.license@type", "file")
    expr = ob("nuspec", "metadata.license", "MIT")
    assert cands(text, kind)[0].forced == "file_ref"
    assert cands(expr)[0].forced is None
    assert (
        cands(expr, ob("nuspec", "metadata.license@type", "expression"))[0].forced
        is None
    )
    assert cands(kind)[0].forced == "file_ref"  # "*@type" rows are never votes


def test_license_file_fields_are_file_refs() -> None:
    assert (
        cands(ob("Cargo.toml", "package.license-file", "MIT"))[0].forced == "file_ref"
    )
    assert cands(ob("nuget_catalog", "licenseFile", "MIT"))[0].forced == "file_ref"
    status, _, tags = outcome(ob("Cargo.toml", "package.license-file", "MIT"))
    assert status == "auto_unresolved" and T.LICENSE_FILE_ONLY in tags


def test_truncated_raw_is_forced_license_text() -> None:
    raw = "MIT" * (MAX_RAW_CHARS // 3 + 1)
    raw = raw[:MAX_RAW_CHARS]
    o = ob(
        "pypi_json",
        "info.license",
        raw,
        raw_truncated=True,
        raw_length=MAX_RAW_CHARS + 1,
        raw_sha256="0" * 64,
    )
    assert cands(o)[0].forced == "license_text"
    status, _, tags = outcome(o)
    assert status == "auto_unresolved" and T.LICENSE_TEXT_IN_FIELD in tags


def test_licence_text_in_field_is_non_voting() -> None:
    status, expr, tags = outcome(
        ob("pypi_json", "info.license", "Permission is hereby granted\n" * 3),
        ob("METADATA", "License-Expression", "MIT"),
    )
    assert (status, expr) == ("auto_unanimous", "MIT")
    assert T.LICENSE_TEXT_IN_FIELD in tags


# -- URL forms -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://licenses.nuget.org/MIT%20OR%20Apache-2.0", "Apache-2.0 OR MIT"),
        ("licenses.nuget.org/MIT", "MIT"),
        ("https://spdx.org/licenses/Apache-2.0.html", "Apache-2.0"),
    ],
    ids=["nuget_encoded", "nuget_no_scheme", "spdx"],
)
def test_url_votes(url: str, expected: str) -> None:
    status, expr, _ = outcome(ob("nuget_catalog", "licenseUrl", url))
    assert (status, expr) == ("auto_unanimous", expected)


def test_url_forms_that_do_not_resolve_never_block() -> None:
    assert outcome(
        ob("pypi_json", "info.license", "MIT"),
        ob("pypi_json", "info.a", "https://spdx.org/licenses/Nope-1.0.html"),
        ob("pypi_json", "info.b", "https://licenses.nuget.org/"),
        ob("pypi_json", "info.c", "http://jquery.org/license"),
    )[:2] == ("auto_unanimous", "MIT")


def test_nuget_unquote_is_applied_to_resolver_input() -> None:
    e, _ = run(
        ob(
            "nuget_catalog",
            "licenseUrl",
            "https://licenses.nuget.org/MIT%20OR%20Apache-2.0",
        )
    )
    assert e.licenseid[0].input == "MIT OR Apache-2.0"


# -- determinism and shape ------------------------------------------------------


def test_reversed_observation_order_gives_identical_output() -> None:
    obs = [
        ob("pypi_json", "info.license", "MIT"),
        ob("METADATA", "License", "MIT OR Apache-2.0"),
        ob("pypi_json", "info.classifiers[0]", TROVE_BSD),
        ob("pypi_json", "info.classifiers[1]", TROVE_MIT),
        ob("pypi_json", "info.license_url", "https://opensource.org/licenses/MIT"),
        ob("pypi_json", "info.x", "GPL-3.0"),
    ]
    a = derive_license(obs, [])
    b = derive_license(obs[::-1], [])
    assert obs != obs[::-1]
    assert a[0].model_dump_json() == b[0].model_dump_json() and a[1] == b[1]
    assert a[0].basis == sorted(a[0].basis)
    keys = [(t.key, t.input) for t in a[0].licenseid]
    assert keys == sorted(keys) and len(keys) >= 4
    assert a[1] == sorted(set(a[1]))


def test_platform_and_manifest_are_merged() -> None:
    e, _ = derive_license(
        [ob("pypi_json", "info.license", "MIT")],
        [ob("pyproject.toml", "project.license", "MIT")],
    )
    assert e.status == "auto_unanimous" and e.independent_origins == 2


def test_non_licence_observations_are_ignored() -> None:
    other = Observation(
        element="name",
        source="pypi_json",
        field_path="info.name",
        raw="MIT",
        native_type="string",
    )
    assert derive_license([other], [])[0].status == "absent_in_source"


def test_licence_ref_flag() -> None:
    e, _ = run(ob("pypi_json", "info.license", "LicenseRef-foo"))
    assert e.value is not None and e.value.has_license_ref


def test_trail_input_is_cut_at_500() -> None:
    e, _ = run(ob("pypi_json", "info.license", "Dual " + "x " * 90))  # prose, <= 200
    assert all(len(t.input) <= 500 for t in e.licenseid)


def test_category_sets_cover_licenseid_vocabulary() -> None:
    assert not OK_CATEGORIES & BLOCK_CATEGORIES
    assert len(OK_CATEGORIES | BLOCK_CATEGORIES) == 17
    assert L.STRONG_TRUST == {"identity", "list_data"}


# -- golden table (real licenseid; guards the pin) ----------------------------------

GOLDEN: list[tuple[str, str | None, str]] = [
    ("Apache-2.0", "Apache-2.0", "exact"),
    ("Apache License, Version 2.0", "Apache-2.0", "prose_form"),
    ("Apache License 2.0", "Apache-2.0", "listed_name"),
    ("Apache 2.0", "Apache-2.0", "separator"),
    ("mit", "MIT", "lexical"),
    ("MIT License", "MIT", "listed_name"),
    ("License :: OSI Approved :: MIT License", "MIT", "listed_name"),
    ("MIT OR Apache-2.0", "Apache-2.0 OR MIT", "lexical"),
    ("(MIT OR Apache-2.0)", "Apache-2.0 OR MIT", "lexical"),
    ("GPL-3.0+", "GPL-3.0-or-later", "plus_to_or_later"),
    ("GPL 2.0 or later version", "GPL-2.0-or-later", "separator"),
    ("BSD 3-Clause License", "BSD-3-Clause", "prose_form"),
    ("cc-by-nc-4.0", "CC-BY-NC-4.0", "lexical"),
    ("MPL 2.0", "MPL-2.0", "separator"),
    ("GPL-3.0", "GPL-3.0", "assumed_only"),
    ("GPLv3", "GPL-3.0", "assumed_only"),
    ("MIT/Apache-2.0", "Apache-2.0 OR MIT", "separator"),
    ("MIT / Apache-2.0", "Apache-2.0 OR MIT", "separator"),
    ("Apache-2.0/MIT/BSD-3-Clause", "Apache-2.0 OR BSD-3-Clause OR MIT", "separator"),
    ("MIT/Apache2", None, "unresolved"),
    ("MIT/X11", None, "unresolved"),
    ("TCL/TK", None, "unresolved"),
    ("BSD (3 clause)", "BSD-3-Clause", "alias_name"),
    ("Apache, Version 2.0", "Apache-2.0", "prose_form"),
    ("License :: OSI Approved :: BSD License", None, "unresolved"),
    ("https://spdx.org/licenses/Apache-2.0.html", "Apache-2.0", "license_url"),
    ("MIT; Apache-2.0", None, "unresolved"),
    ("https://www.apache.org/licenses/LICENSE-2.0", "Apache-2.0", "license_url"),
    ("https://licenses.nuget.org/MIT", "MIT", "license_url"),
    ("https://www.gnu.org/licenses/gpl-3.0.html", "GPL-3.0", "assumed_only"),
    ("http://jquery.org/license", None, "unresolved"),
    ("ASL 2.0", None, "unresolved"),
    ("", None, "unresolved"),
]


@pytest.mark.parametrize(
    ("value", "expression", "category"),
    GOLDEN,
    ids=[f"{i:02d}" for i in range(len(GOLDEN))],
)
def test_golden_licenseid_0_8_4(
    value: str, expression: str | None, category: str
) -> None:
    res = L._resolve(value)
    assert (res.expression, res.category) == (expression, category)
    assert res.category in OK_CATEGORIES | BLOCK_CATEGORIES


def test_golden_pin_and_list_version() -> None:
    from licenseid import resolve

    r = resolve("MIT", source="packaged")
    assert r["resolver"]["version"] == "0.8.4"
    assert r["lists"][0]["version"] == "3.29.0"
    assert resolve("Apache-2.0", source="packaged")["trust"] == "identity"
    assert L._resolve("https://opensource.org/licenses/MIT").trust == "resolver_rule"
    assert L._resolve(TROVE_MIT).trust == "list_data"


def test_block_note_deduplicates_identical_clauses() -> None:
    e, _ = run(
        ob("crates_api", "version.license", "MIT; Apache-2.0"),
        ob("Cargo.toml", "package.license", "MIT; Apache-2.0"),
        ob("Cargo.toml.orig", "package.license", "MIT; Apache-2.0"),
    )
    assert e.note is not None
    assert e.note.count("'MIT; Apache-2.0'") == 1
