# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.evaluate.metrics and predictions."""

import copy
import json
from typing import Any

import pytest
from helpers_evaluate import (
    BOOT,
    MAGIKA,
    QWEN,
    block,
    by_id,
    entries,
    load,
    many,
    perfect,
    rate,
    roots,
    run,
    set_expected,
)

from sbomminbench.evaluate import metrics
from sbomminbench.evaluate.matching import canonical_key
from sbomminbench.evaluate.predictions import (
    parse_predictions,
)
from sbomminbench.ids import canonical_name_key
from sbomminbench.schema import Entry
from sbomminbench.standards import STANDARD_ELEMENTS

# -- self-test -------------------------------------------------------------------------


def test_self_test_scores_one_everywhere() -> None:
    gold = entries()
    result = run(gold, perfect(gold), by_standard=True)
    rates = list(metrics.iter_rates(result))
    assert rates
    assert all(r["rate"] in (1.0, None) for _, r in rates)
    assert block(result, "name")["n"] == 2
    assert block(result, "provider")["n"] == 2
    assert block(result, "license")["n"] == 1


def test_self_test_covers_every_status_with_a_value() -> None:
    def mutate(m: dict[str, Any], q: dict[str, Any]) -> None:
        set_expected(
            m,
            "root",
            "license",
            "human_corrected",
            {"spdx_expression": "GPL-2.0+", "has_license_ref": False},
        )
        set_expected(
            q,
            "root",
            "license",
            "human_corrected",
            {"spdx_expression": "LicenseRef-qwen", "has_license_ref": True},
        )

    gold = entries(mutate)
    result = run(gold, perfect(gold))
    assert rate(result, "license", "accuracy") == 1.0
    assert rate(result, "license", "accuracy", group="gold", value="human") == 1.0
    assert block(result, "license_non_spdx")["n"] == 1
    assert block(result, "license", "gold", "silver")["n"] == 0


# -- per metric: perfect, wrong, absent, malformed ----------------------------------------

WRONG = {"name": "other", "provider": ["Nobody"], "license": "BSD-3-Clause"}
ABSENT_VALUE = {"name": None, "provider": None, "license": None}
MALFORMED = {"name": 7, "provider": "Magika Developers", "license": ["MIT"]}
STRICT = {"name": "strict_accuracy", "provider": "strict_recall", "license": "accuracy"}


@pytest.mark.parametrize("element", ["name", "provider", "license"])
@pytest.mark.parametrize(
    ("label", "wrong", "malformed"),
    [("wrong", WRONG, 0), ("null", ABSENT_VALUE, 0), ("malformed", MALFORMED, 1)],
)
def test_one_element_degrades_only_itself(
    element: str, label: str, wrong: dict[str, Any], malformed: int
) -> None:
    gold = entries()
    recs = perfect(gold)
    for r in recs:
        r[element] = wrong[element]
    result = run(gold, recs)
    for other in ("name", "provider", "license"):
        want = 0.0 if other == element else 1.0
        assert rate(result, other, STRICT[other]) == want, (label, other)
    n_scored = block(result, element)["n"]
    assert n_scored > 0
    assert result["summary"]["malformed_values"][element] == (
        n_scored if malformed else 0
    )


def test_absent_key_is_a_miss_for_a_scored_row() -> None:
    gold = entries()
    recs = [{"id": r["id"], "subject": r["subject"]} for r in perfect(gold)]
    result = run(gold, recs)
    assert rate(result, "name", "strict_accuracy") == 0.0
    assert block(result, "provider")["counts"]["strict_fn"] == 2
    assert block(result, "license")["counts"]["missing"] == 1


def test_missing_prediction_lines_score_zero() -> None:
    result = run(entries(), [])
    assert rate(result, "name", "strict_accuracy") == 0.0
    assert rate(result, "provider", "strict_precision") is None  # no predicted name


# -- unscored statuses ----------------------------------------------------------------


def test_unscored_statuses_are_excluded_and_change_the_denominator() -> None:
    def scored(m: dict[str, Any], q: dict[str, Any]) -> None:
        set_expected(
            q,
            "root",
            "license",
            "auto_unanimous",
            {"spdx_expression": "Apache-2.0", "has_license_ref": False},
        )

    base = entries()
    wide = entries(scored)
    recs = perfect(base)
    by_id(recs, QWEN)["license"] = (
        "MIT"  # wrong, but only visible once the row is scored
    )
    r_base, r_wide = run(base, recs), run(wide, recs)
    assert block(r_base, "license")["n"] == 1
    assert block(r_wide, "license")["n"] == 2
    assert rate(r_base, "license", "accuracy") == 1.0
    assert rate(r_wide, "license", "accuracy") == 0.5
    assert r_base["summary"]["rows_unscored"] == {
        "auto_unresolved": 1,
        "not_assessed": 3,
    }
    assert r_wide["summary"]["rows_unscored"] == {"not_assessed": 3}


@pytest.mark.parametrize(
    "status", ["auto_conflict", "auto_unresolved", "excluded", "not_assessed"]
)
def test_each_unscored_status_adds_no_scored_row(status: str) -> None:
    def mutate(m: dict[str, Any], q: dict[str, Any]) -> None:
        set_expected(m, "root", "name", status)

    result = run(entries(mutate), perfect(entries(mutate)))
    assert block(result, "name")["n"] == 1
    assert result["summary"]["rows_unscored"][status] >= 1


# -- abstention -----------------------------------------------------------------------


def abstain_gold(m: dict[str, Any], q: dict[str, Any]) -> None:
    set_expected(m, "root", "provider", "absent_in_source")
    set_expected(q, "root", "provider", "human_confirmed")


@pytest.mark.parametrize(
    ("pred", "want"),
    [(None, 1.0), ([], 1.0), (["Someone"], 0.0), ("NOASSERTION", 1.0)],
    ids=["null", "empty", "invented", "noassertion"],
)
def test_abstention(pred: Any, want: float) -> None:
    gold = entries(abstain_gold)
    recs = perfect(gold)
    for r in recs:
        r["provider"] = pred
    result = run(gold, recs)
    assert block(result, "abstain_provider")["n"] == 2
    assert rate(result, "abstain_provider", "accuracy") == want
    assert block(result, "provider")["n"] == 0  # abstention rows are not in P/R
    assert rate(result, "provider", "strict_recall") is None


def test_abstention_group_is_human_for_confirmed_and_silver_for_auto() -> None:
    result = run(entries(abstain_gold), [])
    assert block(result, "abstain_provider", "gold", "human")["n"] == 1
    assert block(result, "abstain_provider", "gold", "silver")["n"] == 1


# -- licence rows ---------------------------------------------------------------------


def test_license_verdict_counts_and_non_spdx_row() -> None:
    def mutate(m: dict[str, Any], q: dict[str, Any]) -> None:
        set_expected(
            q,
            "root",
            "license",
            "human_corrected",
            {"spdx_expression": "LicenseRef-qwen", "has_license_ref": True},
        )

    gold = entries(mutate)
    recs = perfect(gold)
    by_id(recs, MAGIKA)["license"] = "apache-2.0"
    by_id(recs, QWEN)["license"] = "LicenseRef-other"
    result = run(gold, recs)
    assert block(result, "license")["counts"] == {
        "exact": 0,
        "equivalent": 1,
        "different": 1,
        "invalid": 0,
        "missing": 0,
    }
    non_spdx = block(result, "license_non_spdx")
    assert (non_spdx["n"], non_spdx["rates"]["accuracy"]["rate"]) == (1, 0.0)
    by_id(recs, QWEN)["license"] = "LicenseRef-qwen"
    assert rate(run(gold, recs), "license_non_spdx", "accuracy") == 1.0


# -- name and provider details --------------------------------------------------------


def test_name_strict_vs_lenient_and_provider_lenient() -> None:
    gold = entries()
    recs = perfect(gold)
    by_id(recs, QWEN)["name"] = "Qwen2.5-72B Instruct"  # strict miss, lenient hit
    by_id(recs, QWEN)["provider"] = ["Qwen Inc."]
    result = run(gold, recs)
    assert rate(result, "name", "strict_accuracy") == 0.5
    assert rate(result, "name", "spelling_tolerant_accuracy") == 1.0
    assert rate(result, "provider", "strict_set_exact") == 0.5
    assert rate(result, "provider", "lenient_set_exact") == 1.0


def test_provider_extra_entity_lowers_precision_only() -> None:
    gold = entries()
    recs = perfect(gold)
    by_id(recs, MAGIKA)["provider"] = [
        "magika developers <email.redacted@example.com>",
        "Extra Org",
    ]
    result = run(gold, recs)
    assert rate(result, "provider", "strict_recall") == 1.0
    assert rate(result, "provider", "strict_precision") == pytest.approx(2 / 3)
    assert rate(result, "provider", "strict_f1") == pytest.approx(0.8)
    assert rate(result, "provider", "strict_set_exact") == 0.5


# -- slices ---------------------------------------------------------------------------


def test_slices_by_ecosystem_stratum_kind_and_tag() -> None:
    gold = entries()
    recs = perfect(gold)
    by_id(recs, QWEN)["name"] = "wrong"
    result = run(gold, recs)
    assert (
        rate(result, "name", "strict_accuracy", group="ecosystem", value="pypi") == 1.0
    )
    assert (
        rate(result, "name", "strict_accuracy", group="ecosystem", value="huggingface")
        == 0.0
    )
    assert rate(result, "name", "strict_accuracy", group="stratum", value="edge") == 0.5
    assert rate(result, "name", "strict_accuracy", group="kind", value="model") == 0.0
    assert (
        rate(result, "name", "strict_accuracy", group="tag", value="license_is_url")
        == 0.0
    )
    assert block(result, "name", "tag", "embedded_models")["n"] == 1
    keys = [(g["group"], g["value"]) for g in result["groups"]]
    assert keys == sorted(keys)


# -- empty and determinism ------------------------------------------------------------


def test_empty_gold_and_empty_predictions_do_not_divide_by_zero() -> None:
    result = metrics.evaluate_entries(
        [], parse_predictions([]), n_boot=BOOT, by_standard=True
    )
    assert result["summary"]["rows_scored"] == 0
    assert all(r["rate"] is None for _, r in metrics.iter_rates(result))
    assert all(row["metrics"]["n"] == 0 for row in result["by_standard"]["elements"])


def test_zero_bootstrap_draws_leave_f1_interval_empty() -> None:
    gold = many(30)
    result = metrics.evaluate_entries(
        gold, parse_predictions(json.dumps(r) for r in perfect(gold)), n_boot=0
    )
    f1 = block(result, "provider")["rates"]["strict_f1"]
    assert (f1["rate"], f1["low"], f1["high"]) == (1.0, None, None)


def test_determinism_and_seed_dependence() -> None:
    gold = many(30)
    recs = perfect(gold)
    for r in roots(recs)[::3]:
        r["provider"] = [*r["provider"], "Extra"]
    one = json.dumps(run(gold, recs, seed=1), sort_keys=True)
    two = json.dumps(run(gold, list(reversed(recs)), seed=1), sort_keys=True)
    assert one == two
    f1 = block(run(gold, recs, seed=1), "provider")["rates"]["strict_f1"]
    assert f1["low"] <= f1["rate"] <= f1["high"]
    other = block(run(gold, recs, seed=2), "provider")["rates"]["strict_f1"]
    assert (other["low"], other["high"]) != (f1["low"], f1["high"])


# -- predictions parsing --------------------------------------------------------------


def test_parse_predictions_counts_bad_lines_and_duplicates() -> None:
    lines: list[bytes | str] = [
        b"\xef\xbb\xbf" + json.dumps({"id": "a", "name": "x"}).encode(),
        "",
        "not json",
        "[1]",
        json.dumps({"id": 5}),
        json.dumps({"id": "a", "subject": 3}),
        json.dumps({"id": "a", "name": "y", "extra": 1}),
        b"\xff\xfe",
    ]
    p = parse_predictions(lines)
    assert (p.lines, p.malformed_lines, p.duplicates) == (7, 5, 1)
    assert p.records == {("a", "root"): {"name": "y"}}


def test_unmatched_prediction_is_counted() -> None:
    gold = entries()
    result = run(gold, [*perfect(gold), {"id": "pkg:pypi/none@1", "name": "x"}])
    assert result["summary"]["prediction_unmatched"] == 1


# -- by standard ----------------------------------------------------------------------


def test_by_standard_rows() -> None:
    gold = entries()
    recs = perfect(gold)
    by_id(recs, QWEN)["name"] = "wrong"
    result = run(gold, recs, by_standard=True)
    rows = {r["element_id"]: r for r in result["by_standard"]["elements"]}
    overall = block(result, "name")["rates"]["strict_accuracy"]
    cisa = rows["cisa2026:component-name"]
    assert cisa["metrics"]["rates"]["strict_accuracy"] == overall
    assert cisa["title"] == "CISA 2026 Component Name (BOM-CISA2026-COMP-007)"
    assert cisa["fit"] == "exact"
    assert rows["g7ai2026:model-name"]["metrics"]["n"] == 1  # the model subject only
    assert (
        rows["g7ai2026:model-name"]["metrics"]["rates"]["strict_accuracy"]["rate"]
        == 0.0
    )
    assert all(r["fit"] in ("exact", "close") for r in rows.values())
    assert "bsi2025:component-distribution-license" not in rows
    keys = [
        (r["standard"], r["element"], r["element_id"])
        for r in result["by_standard"]["elements"]
    ]
    assert keys == sorted(keys)
    assert {
        (r["standard"], r["element"]) for r in result["by_standard"]["no_element"]
    } >= {
        ("ntia2021", "license"),
        ("mof2024", "name"),
    }


@pytest.mark.parametrize(
    ("element_id", "needle"),
    [
        ("g7ai2026:model-producer", "hub owner"),
        ("certin2025:model-developer", "hub owner"),
        ("fsct2024:concluded-license", "declared licence"),
        ("ntia2021:supplier", "conflate creator and distributor"),
        ("fsct2024:supplier", "conflate creator and distributor"),
        ("cisa2026:component-producer", "one producer"),
        ("bsi2025:component-creator", "name only"),
        ("cisa2026:component-name", ""),
        ("cisa2026:component-license", ""),
    ],
)
def test_by_standard_caveats(element_id: str, needle: str) -> None:
    gold = entries()
    result = run(gold, perfect(gold), by_standard=True)
    assert result["by_standard"]["note"] == "relabelled element scores, not compliance"
    rows = {r["element_id"]: r for r in result["by_standard"]["elements"]}
    assert all("caveat" in r for r in rows.values())
    if needle:
        assert needle in rows[element_id]["caveat"]
    else:
        assert rows[element_id]["caveat"] == ""


def test_by_standard_model_rows_are_empty_for_a_package_only_gold() -> None:
    gold = [Entry.model_validate(load("magika"))]
    result = run(gold, perfect(gold), by_standard=True)
    rows = {r["element_id"]: r for r in result["by_standard"]["elements"]}
    assert rows["g7ai2026:model-producer"]["metrics"]["n"] == 0
    assert (
        rows["g7ai2026:model-producer"]["metrics"]["rates"]["strict_recall"]["rate"]
        is None
    )
    assert rows["cisa2026:component-producer"]["metrics"]["n"] == 1


def test_no_element_list_matches_standards_without_a_row() -> None:
    have = {(r.standard, r.element) for r in STANDARD_ELEMENTS}
    listed = {(s, e) for s, _, e in metrics.NO_ELEMENT}
    assert not have & listed
    for standard in {s for s, _ in have}:
        gaps = {e for e in ("name", "provider", "license") if (standard, e) not in have}
        assert gaps == {e for s, e in listed if s == standard}


# -- drift guards ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "eco", ["pypi", "npm", "cargo", "maven", "nuget", "huggingface"]
)
def test_canonical_key_agrees_with_ids(eco: Any) -> None:
    for name in ["Foo_Bar.baz", "Qwen/Qwen2.5", "group:Artifact", "A--b"]:
        assert canonical_key(eco, name) == canonical_name_key(eco, name)


def test_inputs_are_not_mutated() -> None:
    gold = entries()
    before = copy.deepcopy([g.model_dump() for g in gold])
    run(gold, perfect(gold), by_standard=True)
    assert [g.model_dump() for g in gold] == before
