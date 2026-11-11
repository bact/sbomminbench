# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for abstention coverage, slices, intervals and the self-test controls."""

from typing import Any

import pytest
from helpers_evaluate import (
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
    variant,
    variants,
)

from sbomminbench.evaluate import intervals
from sbomminbench.evaluate.predictions import respell_equivalent
from sbomminbench.evaluate.scoring import Score
from sbomminbench.schema import Entry


def abstain_gold(m: dict[str, Any], q: dict[str, Any]) -> None:
    set_expected(m, "root", "provider", "absent_in_source")
    set_expected(q, "root", "provider", "human_confirmed")


# -- coverage: an empty output must not look like abstaining -----------------------------


def test_empty_predictions_do_not_earn_abstention_credit() -> None:
    result = run(entries(abstain_gold), [])
    assert block(result, "abstain_provider")["n"] == 2
    assert rate(result, "abstain_provider", "accuracy") == 0.0
    s = result["summary"]
    assert s["subjects_without_prediction"] == 3
    assert s["rows_not_covered"] == {"name": 2, "provider": 2, "license": 1}
    assert block(result, "abstain_provider")["counts"] == {"not_covered": 2}


@pytest.mark.parametrize(
    ("record", "want"),
    [
        ({}, 0.0),  # key absent inside an existing line: not covered
        ({"provider": None}, 1.0),
        ({"provider": []}, 1.0),
        ({"provider": "NOASSERTION"}, 1.0),
        ({"provider": ["Someone"]}, 0.0),
    ],
    ids=["absent-key", "null", "empty", "noassertion", "invented"],
)
def test_only_an_explicit_abstention_is_credited(
    record: dict[str, Any], want: float
) -> None:
    gold = entries(abstain_gold)
    recs = [{"id": r["id"], "subject": r["subject"], **record} for r in perfect(gold)]
    result = run(gold, recs)
    assert rate(result, "abstain_provider", "accuracy") == want
    assert result["summary"]["subjects_without_prediction"] == 0


def test_license_abstention_accepts_none_and_reports_the_share() -> None:
    def licence_absent(m: dict[str, Any], q: dict[str, Any]) -> None:
        set_expected(m, "root", "license", "absent_in_source")
        set_expected(q, "root", "license", "absent_in_source")

    gold = entries(licence_absent)
    recs = perfect(gold)
    by_id(recs, MAGIKA)["license"] = "NONE"
    by_id(recs, QWEN)["license"] = " noassertion "
    result = run(gold, recs)
    assert rate(result, "abstain_license", "accuracy") == 1.0
    share = block(result, "abstain_license")["shares"]["none_of_abstentions"]
    assert (share["k"], share["n"]) == (1, 2)
    by_id(recs, QWEN)["license"] = None
    assert rate(run(gold, recs), "abstain_license", "accuracy") == 1.0


def test_none_is_still_a_miss_on_a_scored_licence_row() -> None:
    gold = entries()
    recs = perfect(gold)
    by_id(recs, MAGIKA)["license"] = "NONE"
    result = run(gold, recs)
    assert rate(result, "license", "accuracy") == 0.0
    assert block(result, "license")["counts"]["missing"] == 1
    # NONE is no abstention for the other elements
    assert block(result, "abstain_provider")["n"] == 0


# -- coverage block and slices ------------------------------------------------------------


def test_coverage_block_and_status_and_origin_slices() -> None:
    gold = entries()
    result = run(gold, perfect(gold))
    groups = {(g["group"], g["value"]): g for g in result["groups"]}
    cov = groups[("all", "all")]["coverage"]["license"]
    assert cov == {
        "gold_rows": 3,
        "scored": 1,
        "abstention": 0,
        "unscored": {"auto_unresolved": 1, "not_assessed": 1},
        "not_covered": 0,
    }
    assert groups[("status", "auto_unanimous")]["coverage"]["name"]["scored"] == 2
    for key in (("auto_status", "auto_unanimous"), ("origins", "1"), ("origins", "2+")):
        assert key in groups
    assert block(result, "name", "origins", "1")["n"] == 1
    assert block(result, "name", "origins", "2+")["n"] == 1

    # human_corrected rows form their own slice
    def mutate(m: dict[str, Any], q: dict[str, Any]) -> None:
        set_expected(
            m,
            "root",
            "license",
            "human_corrected",
            {"spdx_expression": "GPL-2.0+", "has_license_ref": False},
        )

    again = run(entries(mutate), perfect(entries(mutate)))
    assert block(again, "license", "status", "human_corrected")["n"] == 1
    assert block(again, "license", "auto_status", "auto_conflict")["n"] == 1


def test_unmatched_under_group_is_reported_not_penalised() -> None:
    d = load("magika")
    d["subjects"][0]["expected"]["provider"]["value"]["has_unnamed_group"] = True
    gold = [Entry.model_validate(d)]
    recs = perfect(gold)
    root = roots(recs)[0]
    root["provider"] = [*root["provider"], "Zed", "et al."]
    result = run(gold, recs)
    assert rate(result, "provider", "strict_precision") == 1.0
    counts = block(result, "provider")["counts"]
    assert (counts["strict_fp"], counts["strict_unmatched_under_group"]) == (0, 1)


# -- licence extras -------------------------------------------------------------------------


def test_licenseref_any_and_case_sensitivity() -> None:
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
    by_id(recs, QWEN)["license"] = "MIT OR LicenseRef-other"
    result = run(gold, recs)
    assert rate(result, "license_non_spdx", "accuracy") == 0.0
    assert rate(result, "license_non_spdx", "licenseref_any") == 1.0
    by_id(recs, QWEN)["license"] = (
        "licenseref-qwen"  # LicenseRef ids are case-sensitive
    )
    result = run(gold, recs)
    assert rate(result, "license_non_spdx", "accuracy") == 0.0
    assert rate(result, "license_non_spdx", "licenseref_any") == 0.0


def test_majority_license_share() -> None:
    gold = [variant(n, ["A"], lic) for n, lic in (("aa", "MIT"), ("bb", "MIT"))]
    gold += [variant("cc", ["A"], "ISC"), variant("dd", ["A"], "Apache-2.0")]
    major = block(run(gold, []), "license")["majority_license"]
    assert (major["license"], major["k"], major["n"]) == ("MIT", 2, 4)
    assert major["rate"] == 0.5


# -- intervals ----------------------------------------------------------------------------


def test_set_metric_intervals_need_thirty_rows() -> None:
    def provider_ci(n: int) -> dict[str, Any]:
        gold = many(n)
        recs = perfect(gold)
        for r in roots(recs)[::2]:
            r["provider"] = [*r["provider"], "Extra"]
        return block(run(gold, recs), "provider")["rates"]

    small, big = provider_ci(29), provider_ci(30)
    for metric, r in small.items():
        assert (r["low"], r["high"]) == (None, None), metric
    for label in ("strict", "lenient"):
        for metric in intervals.SET_METRICS:
            r = big[f"{label}_{metric}"]
            assert r["low"] <= r["rate"] <= r["high"], (label, metric)
    assert big["strict_precision"]["high"] < 1.0
    assert big["strict_recall"]["low"] == big["strict_recall"]["high"] == 1.0


def test_bootstrap_drops_zero_denominator_resamples() -> None:
    def row(counts: tuple[int, int, int]) -> Score:
        return Score(
            element="provider",
            state="scored",
            status="auto_unanimous",
            auto_status="auto_unanimous",
            origins=1,
            gold="silver",
            ecosystem="pypi",
            stratum="s",
            kind="package",
            tags=(),
            provider=(counts, counts),
        )

    rows = [row((1, 0, 0)), *[row((0, 0, 1)) for _ in range(29)]]
    cis = intervals.bootstrap_set_metrics(rows, seed=1, n_boot=200)
    # a resample without the one predicting row has no precision: dropped, not 0.0
    assert cis["strict_precision"] == (1.0, 1.0)
    assert cis["strict_f1"] is not None
    assert intervals.bootstrap_set_metrics(rows, 1, 0)["strict_f1"] is None


# -- the self-test must not be vacuous ---------------------------------------------------------


def spelled(gold: list[Entry]) -> list[dict[str, Any]]:
    return respell_equivalent(perfect(gold), gold)


def test_equivalent_respelling_keeps_every_headline_at_one() -> None:
    gold = [*entries(), *variants()]
    base, mutated = perfect(gold), spelled(gold)
    assert mutated != base  # the input changed
    assert by_id(mutated, QWEN)["name"] == "QWEN2.5-72B-INSTRUCT"
    result = run(gold, mutated)
    assert rate(result, "license", "accuracy") == 1.0
    assert rate(result, "license", "exact") < 1.0
    for metric in ("precision", "recall", "f1", "set_exact"):
        assert rate(result, "provider", f"strict_{metric}") == 1.0, metric
    assert rate(result, "name", "strict_accuracy") == 1.0


def test_case_sensitive_ecosystems_keep_their_names() -> None:
    npm = entries()[0].model_copy(update={"ecosystem": "npm"})
    base = perfect([npm])
    mutated = respell_equivalent(base, [npm])
    assert by_id(mutated, MAGIKA)["name"] == by_id(base, MAGIKA)["name"]


def test_deranged_gold_control_scores_low_with_pinned_numbers() -> None:
    gold = variants()
    recs = roots(perfect(gold))
    keep = {"id", "subject"}
    values = [{k: v for k, v in r.items() if k not in keep} for r in recs]
    rotated = [
        {"id": r["id"], "subject": r["subject"], **values[(i + 1) % len(recs)]}
        for i, r in enumerate(recs)
    ]
    result = run(gold, rotated)
    assert rate(result, "name", "strict_accuracy") == 0.0
    assert rate(result, "license", "accuracy") == 0.0
    counts = block(result, "provider")["counts"]
    assert (counts["strict_tp"], counts["strict_fp"], counts["strict_fn"]) == (1, 5, 5)
    assert rate(result, "provider", "strict_f1") == pytest.approx(1 / 6)
    assert rate(result, "provider", "strict_set_exact") == 0.0


def test_superset_control_keeps_recall_and_loses_precision() -> None:
    gold = variants()
    recs = roots(perfect(gold))
    for r in recs:
        r["provider"] = [*r["provider"], "Extra Org"]
    result = run(gold, recs)
    assert rate(result, "provider", "strict_recall") == 1.0
    assert rate(result, "provider", "strict_precision") == pytest.approx(0.6)
    assert rate(result, "provider", "strict_set_exact") == 0.0
