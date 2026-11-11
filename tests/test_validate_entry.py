# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.validate_entry: V01-V07, V09-V11 and V19."""

import json
from pathlib import Path

import pytest
from validate_support import (
    BABEL,
    GPT2,
    PACKAGING,
    SLF4J,
    Edit,
    edit,
    hits,
    make_tree,
    run,
    subject_expected,
)

from sbomminbench import validate_entry
from sbomminbench.schema import EMAIL_PLACEHOLDER, NameValue


def _obs(doc: dict[str, object], key: str) -> dict[str, object]:
    platform: dict[str, list[dict[str, object]]] = doc["platform"]  # type: ignore[assignment]
    return next(
        o for o in platform["observations"] if f"{o['source']}:{o['field_path']}" == key
    )


def _set(element: str, field: str, value: object) -> Edit:
    def change(doc: dict[str, object]) -> None:
        subject_expected(doc, element)[field] = value

    return change


def _set_value(element: str, field: str, value: object) -> Edit:
    def change(doc: dict[str, object]) -> None:
        inner: dict[str, object] = subject_expected(doc, element)["value"]  # type: ignore[assignment]
        inner[field] = value

    return change


def _first_subject(doc: dict[str, object]) -> dict[str, object]:
    subjects: list[dict[str, object]] = doc["subjects"]  # type: ignore[assignment]
    return subjects[0]


# (check, mutation) pairs: each mutation breaks exactly the rule of its check.
MUTATIONS: list[tuple[str, Edit]] = [
    ("V01", lambda d: d.update(unknown_field=1)),
    ("V01", lambda d: d.update(stratum="gold")),
    ("V03", lambda d: _first_subject(d).update(id="rootx")),
    ("V03", lambda d: _first_subject(d).update(locator="a.onnx")),
    ("V03", lambda d: _first_subject(d).update(challenge_tags=["b", "a"])),
    ("V04", _set("name", "value", None)),
    ("V04", _set("name", "basis", ["pypi_json:info.license_expression"])),
    ("V04", _set("name", "status", "auto_conflict")),
    (
        "V04",
        lambda d: subject_expected(d, "license").update(
            status="human_confirmed", auto_status="auto_unanimous", reviewer="x"
        ),
    ),
    ("V05", _set_value("license", "spdx_expression", "Apache 2.0")),
    ("V05", _set_value("license", "spdx_expression", "MIT AND")),
    ("V05", _set_value("license", "spdx_expression", "")),
    ("V06", _set_value("name", "canonical_key", "other")),
    ("V06", _set_value("name", "accepted", ["x"])),
    ("V07", lambda d: _provider(d, [" "])),
    ("V07", lambda d: _provider(d, [EMAIL_PLACEHOLDER])),
    ("V07", lambda d: _provider(d, ["Ann", "ann"])),
    (
        "V09",
        lambda d: _obs(d, "pypi_json:info.license_expression").update(
            native_type="number"
        ),
    ),
    ("V09", lambda d: _obs(d, "pypi_json:info.license").update(raw="x")),
    ("V09", lambda d: d["platform"]["observations"].reverse()),  # type: ignore[index]
    ("V10", lambda d: d["platform"].update(version="9.9")),  # type: ignore[attr-defined]
    ("V10", lambda d: d.update(ecosystem="npm")),
    ("V11", lambda d: d["platform"]["artifacts"][0].update(sha256=None)),  # type: ignore[index]
    (
        "V11",
        lambda d: d["platform"]["artifacts"][0].update(fetched=False, skip_reason=None),  # type: ignore[index]
    ),
    ("V14", lambda d: d["collected_with"].pop("licenseid")),  # type: ignore[attr-defined]
]


def _provider(doc: dict[str, object], names: list[str]) -> None:
    value = {"entities": [{"name": n, "role": "author"} for n in names]}
    expected = subject_expected(doc, "provider")
    expected.update(status="auto_unanimous", value=value, candidates=[])


@pytest.mark.parametrize(
    ("check", "change"),
    MUTATIONS,
    ids=[f"{c}-{i}" for i, (c, _) in enumerate(MUTATIONS)],
)
def test_check_fires_on_its_mutation(tmp_path: Path, check: str, change: Edit) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    assert not hits(run(data), check)
    edit(data, PACKAGING, change)
    report = run(data)
    assert hits(report, check, "ERROR"), report.findings


def test_plain_schema_error_is_v01_and_names_the_location(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, lambda d: d.update(unknown_field=1))
    (found,) = hits(run(data), "V01")
    assert found.target == "pkg:pypi/packaging@26.3"
    assert "unknown_field" in found.detail


def test_unreadable_json_is_v01(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    (data / "entries" / PACKAGING).write_bytes(b"{not json")
    (found,) = hits(run(data), "V01")
    assert found.target == PACKAGING


# -- V02 -----------------------------------------------------------------------------


def test_v02_file_path_must_be_the_entry_path(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    src = data / "entries" / PACKAGING
    dest = data / "entries" / "pypi" / "_" / "wrong.json"
    src.rename(dest)
    (found,) = hits(run(data), "V02")
    assert found.target == "pkg:pypi/packaging@26.3"
    assert "packaging@26.3.json" in found.detail


def test_v02_path_is_checked_even_when_the_entry_is_invalid(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, lambda d: d.update(unknown_field=1))
    src = data / "entries" / PACKAGING
    src.rename(src.with_name("wrong.json"))
    assert hits(run(data), "V02") and hits(run(data), "V01")


def test_v02_noncanonical_id_is_reported(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, lambda d: d.update(id="pkg:pypi/Packaging@26.3"))
    assert hits(run(data), "V02")


def test_v02_duplicate_id_in_two_files(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    src = data / "entries" / PACKAGING
    src.with_name("copy.json").write_bytes(src.read_bytes())
    details = [f.detail for f in hits(run(data), "V02")]
    assert any(d.startswith("duplicate id") for d in details)


def test_v02_casefold_clash_between_paths() -> None:
    clash = validate_entry.check_unique_paths(
        [("pkg:a/Foo@1", "a/_/Foo@1.json"), ("pkg:a/foo@1", "a/_/foo@1.json")]
    )
    assert [f.check for f in clash] == ["V02"]
    assert not validate_entry.check_unique_paths(
        [("pkg:a/foo@1", "a/_/foo@1.json"), ("pkg:a/bar@1", "a/_/bar@1.json")]
    )


# -- V04 to V07: boundaries ----------------------------------------------------------


def test_v04_basis_must_name_an_observation_of_the_same_element(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, _set("name", "basis", ["no:such.key"]))
    assert hits(run(data), "V04")


def test_v04_human_status_is_refused_under_entries_not_the_value(
    tmp_path: Path,
) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(
        data,
        PACKAGING,
        lambda d: subject_expected(d, "license").update(
            status="human_corrected", auto_status="auto_unanimous", reviewer="x"
        ),
    )
    (found,) = hits(run(data), "V04")
    assert "human_corrected" in found.detail


@pytest.mark.parametrize(
    ("expression", "ref"),
    [
        ("LicenseRef-foo", True),
        ("MIT OR LicenseRef-foo", True),
        ("DocumentRef-x:LicenseRef-foo", True),
        ("mit or apache-2.0", False),
        ("GPL-2.0-only WITH Classpath-exception-2.0", False),
    ],
    ids=["ref", "mixed", "docref", "lowercase", "with"],
)
def test_v05_accepts_license_refs_and_parsable_expressions(
    tmp_path: Path, expression: str, ref: bool
) -> None:
    data = make_tree(tmp_path, (PACKAGING,))

    def change(doc: dict[str, object]) -> None:
        _set_value("license", "spdx_expression", expression)(doc)
        _set_value("license", "has_license_ref", ref)(doc)

    edit(data, PACKAGING, change)
    assert not hits(run(data), "V05")


@pytest.mark.parametrize(
    ("expression", "message"),
    [("Foo-1.0", "unknown key"), ("(MIT", "does not parse"), ("  ", "empty")],
    ids=["unknown", "parens", "blank"],
)
def test_license_problem_wording(expression: str, message: str) -> None:
    problem = validate_entry.license_problem(expression)
    assert problem is not None and message in problem
    assert validate_entry.license_problem("MIT") is None


def test_v06_key_may_come_from_an_accepted_spelling(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (SLF4J, GPT2))
    assert not hits(run(data), "V06")
    name = NameValue(
        name="slf4j-api",
        canonical_key="org.slf4j:slf4j-api",
        accepted=["slf4j-api", "org.slf4j:slf4j-api"],
    )
    assert validate_entry.name_key_problem("maven", name) is None
    bad = name.model_copy(update={"canonical_key": "slf4j-API"})
    assert validate_entry.name_key_problem("maven", bad)


def test_v06_pypi_key_follows_pep_503(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, _set_value("name", "canonical_key", "Packaging"))
    assert hits(run(data), "V06")


def test_v07_only_the_bad_name_fails(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, lambda d: _provider(d, ["Ann", "Bob"]))
    assert not hits(run(data), "V07")


# -- V09 -----------------------------------------------------------------------------


def test_v09_native_type_must_match_raw_when_schema_order_is_fine(
    tmp_path: Path,
) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(
        data,
        PACKAGING,
        lambda d: _obs(d, "pypi_json:info.license_expression").update(
            native_type="object"
        ),
    )
    (found,) = hits(run(data), "V09")
    assert "native_type object" in found.detail


# -- V10 -----------------------------------------------------------------------------


def test_v10_hub_revision_case_and_pitloom_agree(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (GPT2, BABEL, SLF4J))
    assert not hits(run(data), "V10")


def test_v10_drift_guard_reports_a_disagreeing_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert validate_entry.drift_findings() == []
    monkeypatch.setattr(validate_entry, "_pitloom_purl", lambda e, n, v: "pkg:x/y@1")
    found = validate_entry.drift_findings()
    assert [f.check for f in found] == ["V10", "V10"]


def test_v10_name_and_version_must_build_the_id(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, lambda d: d["platform"].update(name="other"))  # type: ignore[attr-defined]
    details = [f.detail for f in hits(run(data), "V10")]
    assert any("name and version give" in d for d in details)


# -- V19 -----------------------------------------------------------------------------

KEY = "pypi_json:info.license_expression"


def _trail(doc: dict[str, object]) -> dict[str, object]:
    rows: list[dict[str, object]] = subject_expected(doc, "license")["licenseid"]  # type: ignore[assignment]
    return next(r for r in rows if r["key"] == KEY)


def test_v19_openmdw_is_a_warning_not_an_error(tmp_path: Path) -> None:
    """The classifier's SPDX list lacks OpenMDW-1.0; LicenseID resolves it."""
    data = make_tree(tmp_path, (PACKAGING,))

    def change(doc: dict[str, object]) -> None:
        _obs(doc, KEY).update(
            raw="OpenMDW-1.0", semantic_format="id_shaped", format_validity="unknown_id"
        )
        _trail(doc).update(
            input="OpenMDW-1.0", expression="OpenMDW-1.0", category="exact"
        )

    edit(data, PACKAGING, change)
    report = run(data)
    (found,) = hits(report, "V19")
    assert found.severity == "WARN" and "unknown_id" in found.detail
    assert report.errors == 0


def test_v19_valid_form_with_a_blocking_category(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, lambda d: _trail(d).update(category="unresolved"))
    (found,) = hits(run(data), "V19", "WARN")
    assert "unresolved" in found.detail


@pytest.mark.parametrize(
    "change",
    [
        lambda d: _obs(d, KEY).update(format_validity="deprecated"),
        lambda d: _obs(d, KEY).update(semantic_format="prose"),
        lambda d: _trail(d).update(input="something else", category="unresolved"),
    ],
    ids=["deprecated", "prose", "no-join"],
)
def test_v19_stays_quiet_outside_its_scope(tmp_path: Path, change: Edit) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    edit(data, PACKAGING, change)
    assert not hits(run(data), "V19")


def test_load_entry_drops_a_byte_order_mark(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    path = data / "entries" / PACKAGING
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    entry, problems = validate_entry.load_entry(path, PACKAGING)
    assert entry is not None and not problems
    assert json.loads(path.read_bytes().removeprefix(b"\xef\xbb\xbf"))["id"] == entry.id
