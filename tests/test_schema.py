# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.schema."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from sbomminbench.schema import (
    MAX_RAW_CHARS,
    Element,
    Entry,
    JsonValue,
    Observation,
    ReviewLine,
    native_type_of,
)

ROOT = Path(__file__).parent.parent
EXAMPLES = Path(__file__).parent / "fixtures" / "examples"
NAMES = ["magika", "qwen"]


def dumps(data: Any) -> str:
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_bytes())
    return data


def obs(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "element": "name",
        "source": "pypi_json",
        "field_path": "info.name",
        "native_type": "string",
        "raw": "x",
    }
    return base | kw


def lic(**kw: Any) -> dict[str, Any]:
    base = obs(
        element="license",
        field_path="info.license",
        semantic_format="id_shaped",
        format_validity="valid",
    )
    return base | kw


@pytest.mark.parametrize("name", NAMES)
def test_examples_round_trip(name: str) -> None:
    text = (EXAMPLES / f"{name}.json").read_bytes().decode("utf-8")
    assert "\r" not in text
    entry = Entry.model_validate_json(text)
    assert dumps(entry.model_dump(mode="json")) == text


def test_json_value_and_element_exported() -> None:
    assert JsonValue is not None
    assert Element.__args__ == ("name", "provider", "license")  # type: ignore[attr-defined]


def test_observation_valid_cases() -> None:
    Observation.model_validate(obs())
    Observation.model_validate(lic())
    Observation.model_validate(obs(present=False, raw=None, native_type="absent"))


@pytest.mark.parametrize(
    "kw",
    [
        obs(present=False, raw="x", native_type="absent"),  # only raw wrong
        obs(present=False, raw=None, native_type="string"),  # only type wrong
        obs(native_type="absent"),  # present but absent type
        obs(semantic_format="prose", format_validity="valid"),  # non-licence
        lic(semantic_format=None, format_validity=None),  # licence str, no format
        lic(raw=None, native_type="null"),  # format set, raw not str
        lic(format_validity=None),  # only validity missing
        lic(semantic_format=None),  # only format missing
        obs(element="provider"),  # provider needs role
        obs(role="author"),  # role on non-provider
        obs(raw_truncated=True, raw="x" * 10, raw_length=9000, raw_sha256="0" * 64),
        obs(
            raw_truncated=True,
            raw="x" * MAX_RAW_CHARS,
            raw_length=MAX_RAW_CHARS,
            raw_sha256="0" * 64,
        ),
        obs(
            raw_truncated=True,
            raw="x" * MAX_RAW_CHARS,
            raw_length=9000,
            raw_sha256="zz",
        ),
        obs(
            raw_truncated=True,
            raw=None,
            native_type="null",
            raw_length=9000,
            raw_sha256="0" * 64,
        ),
        obs(raw_length=5),
        obs(raw_sha256="0" * 64),
        obs(unknown_field=1),
    ],
    ids=[
        "absent-raw",
        "absent-type",
        "present-absent-type",
        "format-on-name",
        "lic-no-format",
        "lic-format-nonstr",
        "no-validity",
        "no-format",
        "provider-no-role",
        "role-on-name",
        "trunc-short",
        "trunc-not-longer",
        "trunc-bad-sha",
        "trunc-nonstr",
        "length-untruncated",
        "sha-untruncated",
        "extra-forbidden",
    ],
)
def test_observation_invalid(kw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(kw)


def test_truncated_observation_valid() -> None:
    Observation.model_validate(
        obs(
            raw_truncated=True,
            raw="x" * MAX_RAW_CHARS,
            raw_length=9000,
            raw_sha256="0" * 64,
        )
    )


def mutate(name: str, fn: Any) -> dict[str, Any]:
    d = copy.deepcopy(load(name))
    fn(d)
    return d


def test_unfetched_artifact_needs_reason_and_fetched_needs_sha() -> None:
    d = mutate("magika", lambda d: d["platform"]["artifacts"][0].update(fetched=False))
    with pytest.raises(ValidationError):
        Entry.model_validate(d)  # only skip_reason missing
    ok = mutate(
        "magika",
        lambda d: d["platform"]["artifacts"][0].update(
            fetched=False, skip_reason="http_404"
        ),
    )
    Entry.model_validate(ok)
    bad = mutate("magika", lambda d: d["platform"]["artifacts"][0].update(sha256=None))
    with pytest.raises(ValidationError):
        Entry.model_validate(bad)  # only sha missing


def exp(d: dict[str, Any], el: str = "license") -> dict[str, Any]:
    out: dict[str, Any] = d["subjects"][0]["expected"][el]
    return out


def test_auto_unanimous_without_value_raises() -> None:
    d = mutate("magika", lambda d: exp(d).update(value=None))
    with pytest.raises(ValidationError):
        Entry.model_validate(d)


def test_present_false_with_raw_raises_in_entry() -> None:
    def f(d: dict[str, Any]) -> None:
        d["platform"]["observations"][5].update(present=False, raw="x")

    with pytest.raises(ValidationError):
        Entry.model_validate(mutate("magika", f))


@pytest.mark.parametrize(
    ("status", "extra", "ok"),
    [
        ("auto_conflict", {"candidates": ["a"]}, False),
        ("auto_conflict", {"candidates": ["a", "b"]}, True),
        ("auto_unresolved", {}, True),
        ("excluded", {"auto_status": "auto_unresolved", "reviewer": "me"}, True),
        ("excluded", {}, False),  # only reviewer fields missing
        ("auto_unresolved", {"reviewer": "me"}, False),  # only reviewer set
        (
            "auto_unresolved",
            {"auto_status": "auto_unresolved"},
            False,
        ),  # only auto_status
        (
            "human_confirmed",
            {"auto_status": "auto_unresolved", "reviewer": "me"},
            False,
        ),
        (
            "human_confirmed",
            {"auto_status": "absent_in_source", "reviewer": "me"},
            True,
        ),
        ("human_corrected", {"auto_status": "auto_conflict", "reviewer": "me"}, False),
        (
            "auto_unresolved",
            {"basis": ["pypi_json:info.license", "PKG-INFO:License"]},
            False,
        ),
    ],
    ids=[
        "conflict-1",
        "conflict-2",
        "unresolved",
        "excluded",
        "excluded-no-reviewer",
        "reviewer-only",
        "auto-status-only",
        "confirm-no-value",
        "confirm-absent",
        "corrected-no-value",
        "basis-unsorted",
    ],
)
def test_expected_status_rules(status: str, extra: dict[str, Any], ok: bool) -> None:
    def f(d: dict[str, Any]) -> None:
        e = exp(d)
        e.update(status=status, value=None, licenseid=[], independent_origins=0)
        e["basis"] = []
        e["reviewer"] = None
        e["auto_status"] = None
        e.update(extra)
        if "basis" in extra:
            e["basis"] = extra["basis"]

    d = mutate("magika", f)
    if ok:
        Entry.model_validate(d)
    else:
        with pytest.raises(ValidationError):
            Entry.model_validate(d)


def test_unanimous_with_value_ok_but_conflict_with_value_raises() -> None:
    def f(d: dict[str, Any]) -> None:
        exp(d).update(status="auto_conflict", candidates=["a", "b"])

    with pytest.raises(ValidationError):  # value still set
        Entry.model_validate(mutate("magika", f))


def test_basis_key_must_exist_and_match_element() -> None:
    with pytest.raises(ValidationError):
        Entry.model_validate(
            mutate("magika", lambda d: exp(d).update(basis=["pypi_json:info.nope"]))
        )
    # key exists but belongs to another element
    with pytest.raises(ValidationError):
        Entry.model_validate(
            mutate("magika", lambda d: exp(d).update(basis=["pypi_json:info.name"]))
        )


def test_subject_ids_must_be_contiguous() -> None:
    def f(d: dict[str, Any]) -> None:
        d["subjects"][1]["id"] = "model:2"

    with pytest.raises(ValidationError):
        Entry.model_validate(mutate("magika", f))


def test_subject_rules() -> None:
    def root_locator(d: dict[str, Any]) -> None:
        d["subjects"][0]["locator"] = "a/b"

    def no_locator(d: dict[str, Any]) -> None:
        d["subjects"][1]["locator"] = None

    def no_format(d: dict[str, Any]) -> None:
        d["subjects"][1]["model_format"] = None

    def bad_kind(d: dict[str, Any]) -> None:
        d["subjects"][1]["kind"] = "package"

    def platform_src(d: dict[str, Any]) -> None:
        d["subjects"][1]["manifest_observations"].append(obs())

    def bad_tags(d: dict[str, Any]) -> None:
        d["subjects"][1]["challenge_tags"] = ["b", "a"]

    def too_many_files(d: dict[str, Any]) -> None:
        d["subjects"][1]["files"] = [str(i) for i in range(21)]

    def bad_id(d: dict[str, Any]) -> None:
        d["subjects"][1]["id"] = "model:0"

    for fn in (
        root_locator,
        no_locator,
        no_format,
        bad_kind,
        platform_src,
        bad_tags,
        too_many_files,
        bad_id,
    ):
        with pytest.raises(ValidationError):
            Entry.model_validate(mutate("magika", fn))


def test_observations_must_be_sorted_unique() -> None:
    def swap(d: dict[str, Any]) -> None:
        o = d["platform"]["observations"]
        o[0], o[1] = o[1], o[0]

    def dup(d: dict[str, Any]) -> None:
        o = d["platform"]["observations"]
        o[1] = copy.deepcopy(o[0])

    def src(d: dict[str, Any]) -> None:
        d["platform"]["observations"][0]["source"] = "METADATA"

    for fn in (swap, dup, src):
        with pytest.raises(ValidationError):
            Entry.model_validate(mutate("magika", fn))


@pytest.mark.parametrize(
    "bad",
    ["pkg:pypi/Magika@0.6.1", "pkg:pypi/magika@0.6.1?x=1", "pkg:pypi/magika@0.6.1#sub"],
    ids=["noncanonical", "qualifier", "subpath"],
)
def test_id_must_be_canonical_purl(bad: str) -> None:
    with pytest.raises(ValidationError):
        Entry.model_validate(mutate("magika", lambda d: d.update(id=bad)))


def test_id_ecosystem_and_version_must_match() -> None:
    with pytest.raises(ValidationError):  # only purl type differs
        Entry.model_validate(
            mutate("magika", lambda d: d.update(id="pkg:npm/magika@0.6.1"))
        )
    with pytest.raises(ValidationError):  # only platform ecosystem differs
        Entry.model_validate(
            mutate("magika", lambda d: d["platform"].update(ecosystem="npm"))
        )
    with pytest.raises(ValidationError):  # only version differs
        Entry.model_validate(
            mutate("magika", lambda d: d["platform"].update(version="0.6.2"))
        )


def test_root_kind_follows_ecosystem() -> None:
    with pytest.raises(ValidationError):
        Entry.model_validate(
            mutate("magika", lambda d: d["subjects"][0].update(kind="model"))
        )
    with pytest.raises(ValidationError):
        Entry.model_validate(
            mutate("qwen", lambda d: d["subjects"][0].update(kind="package"))
        )


@pytest.mark.parametrize(
    "key",
    ["license_list_format", "license_list", "licenseid", "pitloom", "sbomminbench"],
)
def test_collected_with_requires_each_key(key: str) -> None:
    with pytest.raises(ValidationError):
        Entry.model_validate(mutate("magika", lambda d: d["collected_with"].pop(key)))


def test_no_subjects_and_bad_timestamp() -> None:
    with pytest.raises(ValidationError):
        Entry.model_validate(mutate("magika", lambda d: d.update(subjects=[])))
    with pytest.raises(ValidationError):
        Entry.model_validate(
            mutate("magika", lambda d: d["platform"].update(retrieved_at="2026-11-09"))
        )


def test_value_models() -> None:
    def license_ref(v: bool, e: str) -> None:
        d = mutate(
            "magika",
            lambda d: exp(d)["value"].update(spdx_expression=e, has_license_ref=v),
        )
        Entry.model_validate(d)

    license_ref(True, "LicenseRef-x")
    for v, e in ((False, "LicenseRef-x"), (True, "MIT")):  # each side alone
        with pytest.raises(ValidationError):
            license_ref(v, e)
    with pytest.raises(ValidationError):  # name not in accepted
        Entry.model_validate(
            mutate(
                "magika", lambda d: exp(d, "name")["value"].update(accepted=["other"])
            )
        )
    with pytest.raises(ValidationError):  # empty accepted
        Entry.model_validate(
            mutate("magika", lambda d: exp(d, "name")["value"].update(accepted=[]))
        )


def provider_with(entities: list[dict[str, str]]) -> dict[str, Any]:
    def f(d: dict[str, Any]) -> None:
        exp(d, "provider").update(
            status="auto_unanimous",
            value={"entities": entities, "has_unnamed_group": False},
        )

    return mutate("magika", f)


def test_provider_value_rules() -> None:
    Entry.model_validate(provider_with([{"name": "A"}, {"name": "B"}]))
    with pytest.raises(ValidationError):  # duplicate only
        Entry.model_validate(provider_with([{"name": "A"}, {"name": "a"}]))
    with pytest.raises(ValidationError):  # e-mail placeholder only
        Entry.model_validate(provider_with([{"name": "A email.redacted@example.com"}]))


def test_review_line() -> None:
    base: dict[str, Any] = {
        "id": "pkg:pypi/magika@0.6.1",
        "subject": "root",
        "element": "license",
        "decision": "confirm",
        "reviewer": "arthit",
        "at": "2026-11-09T02:10:44Z",
        "auto_status": "auto_unresolved",
        "auto_value_at_review": None,
    }
    ReviewLine.model_validate(base)
    for gone in ("auto_status", "auto_value_at_review"):
        with pytest.raises(ValidationError, match="needs " + gone):
            ReviewLine.model_validate({k: v for k, v in base.items() if k != gone})
    typed = ReviewLine.model_validate(
        base
        | {
            "auto_status": "auto_unanimous",
            "auto_value_at_review": {"spdx_expression": "MIT"},
        }
    )
    assert typed.auto_value_at_review is not None
    value = {"spdx_expression": "MIT"}
    corrected = ReviewLine.model_validate(
        base | {"decision": "correct", "value": value}
    )
    assert corrected.value is not None
    with pytest.raises(ValidationError):  # correct without value
        ReviewLine.model_validate(base | {"decision": "correct"})
    with pytest.raises(ValidationError):  # value without correct
        ReviewLine.model_validate(base | {"value": value})
    with pytest.raises(ValidationError):  # value shaped for another element
        ReviewLine.model_validate(
            base | {"decision": "correct", "element": "name", "value": value}
        )
    with pytest.raises(ValidationError):
        ReviewLine.model_validate(base | {"reviewer": "Bad Name"})
    with pytest.raises(ValidationError):  # not a mapping
        ReviewLine.model_validate("x")


@pytest.mark.parametrize(
    ("value", "present", "expected"),
    [
        (None, False, "absent"),
        (None, True, "null"),
        (True, True, "bool"),
        (1, True, "number"),
        (1.5, True, "number"),
        ("s", True, "string"),
        ({}, True, "object"),
        ([], True, "list_empty"),
        (["a"], True, "list_of_strings"),
        ([{}], True, "list_of_objects"),
        (["a", {}], True, "list_mixed"),
        (["a", 1], True, "list_mixed"),
    ],
)
def test_native_type_of(value: object, present: bool, expected: str) -> None:
    assert native_type_of(value, present) == expected


def test_native_type_of_rejects_non_json() -> None:
    with pytest.raises(TypeError):
        native_type_of(object())


def test_schema_is_deterministic_and_committed() -> None:
    first = dumps(Entry.model_json_schema())
    assert dumps(Entry.model_json_schema()) == first
    assert (ROOT / "schema" / "entry.schema.json").read_bytes().decode("utf-8") == first
