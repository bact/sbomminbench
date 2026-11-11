# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Evaluator core: matching rules, scores, slices and confidence intervals.

Independence: this module never imports ``licenseid``, ``pitloom``
or ``sbomminbench.derive``. Licence equivalence comes from ``license-expression``
and the name key is re-implemented here, so the tools that produced the silver
answers are never scored with themselves.

Gold is a JSONL file of release entries (``data/release/entries.jsonl``, review
overlay already applied by ``export``) or a directory of entry files. Predictions
are JSONL, one object per subject (see :func:`parse_predictions`).

See also: ``scoring.py`` (one score per gold row), ``intervals.py`` (Wilson and
bootstrap), ``caveats.py`` (per-standard notes), ``render.py`` (output).
"""

from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from sbomminbench.evaluate import intervals
from sbomminbench.evaluate.caveats import NOT_COMPLIANCE, caveat
from sbomminbench.evaluate.intervals import rate
from sbomminbench.evaluate.matching import (
    VERDICTS,
    license_equivalent,
    name_match,
    provider_prf,
)
from sbomminbench.evaluate.predictions import (
    ELEMENTS,
    Predictions,
    load_gold,
    load_predictions,
    predictions_from_gold,
)
from sbomminbench.evaluate.scoring import Score, score_rows
from sbomminbench.schema import Element, Entry
from sbomminbench.standards import STANDARD_ELEMENTS
from sbomminbench.wilson import wilson

DEFAULT_SEED = 20261107
DEFAULT_BOOT = 1000
RESULT_SCHEMA = "sbomminbench-evaluation-0.2"


# Standards the BOM ontology has no element for.
NO_ELEMENT: tuple[tuple[str, str, Element], ...] = (
    ("mof2024", "MOF 2024", "license"),
    ("mof2024", "MOF 2024", "name"),
    ("mof2024", "MOF 2024", "provider"),
    ("ntia2021", "NTIA 2021", "license"),
)


# -- blocks --------------------------------------------------------------------------


def _name_block(rows: Sequence[Score]) -> dict[str, Any]:
    n = len(rows)
    return {
        "n": n,
        "rates": {
            "strict_accuracy": rate(sum(r.name[0] for r in rows), n),
            "spelling_tolerant_accuracy": rate(sum(r.name[1] for r in rows), n),
        },
    }


def _provider_block(rows: Sequence[Score], seed: int, n_boot: int) -> dict[str, Any]:
    cis = intervals.bootstrap_set_metrics(rows, seed, n_boot)
    rates: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for side, label in enumerate(intervals.SIDES):
        tp, fp, fn = (sum(r.provider[side][i] for r in rows) for i in range(3))
        exact = sum(intervals.row_counts(r, side)[3] for r in rows)
        values = intervals.set_values(tp, fp, fn, exact, len(rows))
        ks: dict[str, tuple[int | None, int | None]] = {
            "precision": (tp, tp + fp),
            "recall": (tp, tp + fn),
            "f1": (None, None),
            "set_exact": (exact, len(rows)),
        }
        for metric in intervals.SET_METRICS:
            k, n = ks[metric]
            rates[f"{label}_{metric}"] = intervals.set_rate(
                k, n, values[metric], cis[f"{label}_{metric}"]
            )
        counts.update({f"{label}_tp": tp, f"{label}_fp": fp, f"{label}_fn": fn})
        counts[f"{label}_unmatched_under_group"] = sum(r.unmatched[side] for r in rows)
    return {"n": len(rows), "rates": rates, "counts": counts}


def _majority(rows: Sequence[Score]) -> dict[str, Any]:
    """The most common gold licence and its share: the constant-guess baseline."""
    counts = Counter(r.gold_license for r in rows)
    if not counts:
        return {"license": None, **rate(0, 0)}
    top, k = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"license": top, "k": k, "n": len(rows), "rate": k / len(rows)}


def _license_block(rows: Sequence[Score], *, sub_row: bool = False) -> dict[str, Any]:
    n = len(rows)
    verdicts = Counter(r.verdict for r in rows)
    right = verdicts["exact"] + verdicts["equivalent"]
    rates = {"accuracy": rate(right, n), "exact": rate(verdicts["exact"], n)}
    block: dict[str, Any] = {
        "n": n,
        "rates": rates,
        "counts": {v: verdicts[v] for v in VERDICTS},
    }
    if sub_row:
        # A match class of its own: some LicenseRef- was predicted, right or not.
        rates["licenseref_any"] = rate(sum(r.pred_licenseref for r in rows), n)
    else:
        block["majority_license"] = _majority(rows)
    return block


def _abstain_block(rows: Sequence[Score], element: str) -> dict[str, Any]:
    done = sum(r.abstained for r in rows)
    block: dict[str, Any] = {
        "n": len(rows),
        "rates": {"accuracy": rate(done, len(rows))},
        "counts": {"not_covered": sum(not r.covered for r in rows)},
    }
    if element == "license":
        none = sum(r.abstain_none for r in rows)
        block["shares"] = {"none_of_abstentions": rate(none, done)}
    return block


def element_block(
    rows: Sequence[Score], element: str, seed: int, n_boot: int
) -> dict[str, Any]:
    """The metric block ``element``: an element, ``license_non_spdx`` (a sub-row
    of ``license``: scored rows whose gold holds a ``LicenseRef-``) or
    ``abstain_<element>``."""
    if element.startswith("abstain_"):
        picked = [r for r in rows if r.state == "abstain" and r.element == element[8:]]
        return _abstain_block(picked, element[8:])
    base = "license" if element == "license_non_spdx" else element
    picked = [r for r in rows if r.state == "scored" and r.element == base]
    if element == "license_non_spdx":
        return _license_block([r for r in picked if r.non_spdx], sub_row=True)
    if element == "name":
        return _name_block(picked)
    if element == "provider":
        return _provider_block(picked, seed, n_boot)
    return _license_block(picked)


BLOCKS: tuple[str, ...] = (
    "name",
    "provider",
    "license",
    "license_non_spdx",
    "abstain_name",
    "abstain_provider",
    "abstain_license",
)


def coverage(rows: Sequence[Score]) -> dict[str, Any]:
    """Per element: gold rows, scored, abstention, unscored by status, not covered."""
    out: dict[str, Any] = {}
    for element in ELEMENTS:
        mine = [r for r in rows if r.element == element]
        unscored = Counter(r.status for r in mine if r.state == "unscored")
        out[element] = {
            "gold_rows": len(mine),
            "scored": sum(r.state == "scored" for r in mine),
            "abstention": sum(r.state == "abstain" for r in mine),
            "unscored": {k: unscored[k] for k in sorted(unscored)},
            "not_covered": sum(not r.covered for r in mine),
        }
    return out


def _origins_label(origins: int) -> str:
    return "2+" if origins >= 2 else str(origins)


_UNKNOWN_SLICES = frozenset(
    {("stratum", ""), ("origins", "-1"), ("auto_status", "unknown")}
)


def _slices(scores: Sequence[Score]) -> dict[tuple[str, str], list[Score]]:
    groups: dict[tuple[str, str], list[Score]] = {("all", "all"): list(scores)}
    for s in scores:
        keys = [
            ("gold", s.gold),
            ("ecosystem", s.ecosystem),
            ("stratum", s.stratum),
            ("kind", s.kind),
            ("status", s.status),
            ("auto_status", s.auto_status),
            ("origins", _origins_label(s.origins)),
        ]
        # A CSV gold has no stratum, origins or pre-review status (csvio.py).
        keys = [k for k in keys if k not in _UNKNOWN_SLICES]
        keys.extend(("tag", t) for t in s.tags)
        for key in keys:
            groups.setdefault(key, []).append(s)
    return groups


def standard_rows(scores: Sequence[Score], seed: int, n_boot: int) -> dict[str, Any]:
    """The per-standard view: the element's block over subjects of the row's kinds."""
    rows: list[dict[str, Any]] = []
    for row in STANDARD_ELEMENTS:
        subset = [s for s in scores if s.kind in row.kinds]
        rows.append(
            {
                "standard": row.standard,
                "standard_label": row.standard_label,
                "element": row.element,
                "element_id": row.element_id,
                "title": f"{row.standard_label} {row.label} ({row.notation})",
                "fit": row.fit,
                "caveat": caveat(row.element_id, row.element, row.kinds),
                "kinds": sorted(row.kinds),
                "metrics": element_block(subset, row.element, seed, n_boot),
            }
        )
    absent = [
        {
            "standard": s,
            "standard_label": label,
            "element": e,
            "title": f"{label}: no element",
        }
        for s, label, e in NO_ELEMENT
    ]
    return {"note": NOT_COMPLIANCE, "elements": rows, "no_element": absent}


def _summary(
    entries: Sequence[Entry],
    predictions: Predictions,
    scores: Sequence[Score],
    skipped: Counter[str],
) -> dict[str, Any]:
    known = {(e.id, s.id) for e in entries for s in e.subjects}
    judged = [s for s in scores if s.state != "unscored"]
    return {
        "entries": len(entries),
        "subjects": len(known),
        "rows_scored": sum(s.state == "scored" for s in scores),
        "rows_abstain": sum(s.state == "abstain" for s in scores),
        "rows_unscored": {k: skipped[k] for k in sorted(skipped)},
        "subjects_without_prediction": sum(k not in predictions.records for k in known),
        "rows_not_covered": {
            e: sum(not s.covered and s.element == e for s in judged) for e in ELEMENTS
        },
        "prediction_lines": predictions.lines,
        "prediction_malformed_lines": predictions.malformed_lines,
        "prediction_duplicates": predictions.duplicates,
        "prediction_unmatched": sum(k not in known for k in predictions.records),
        "malformed_values": {
            e: sum(s.malformed and s.element == e for s in judged) for e in ELEMENTS
        },
    }


def evaluate_entries(
    entries: Sequence[Entry],
    predictions: Predictions,
    *,
    seed: int = DEFAULT_SEED,
    n_boot: int = DEFAULT_BOOT,
    by_standard: bool = False,
) -> dict[str, Any]:
    """Score ``predictions`` against ``entries`` and return the result tree."""
    scores, skipped = score_rows(entries, predictions)
    groups = [
        {
            "group": dim,
            "value": value,
            "coverage": coverage(rows),
            "metrics": {b: element_block(rows, b, seed, n_boot) for b in BLOCKS},
        }
        for (dim, value), rows in sorted(_slices(scores).items())
    ]
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "seed": seed,
        "n_boot": n_boot,
        "summary": _summary(entries, predictions, scores, skipped),
        "groups": groups,
    }
    if by_standard:
        result["by_standard"] = standard_rows(
            [s for s in scores if s.state == "scored"], seed, n_boot
        )
    return result


def evaluate(
    gold_jsonl: Path,
    pred_jsonl: Path,
    *,
    seed: int = DEFAULT_SEED,
    n_boot: int = DEFAULT_BOOT,
    by_standard: bool = False,
) -> dict[str, Any]:
    """Read gold and predictions from files and evaluate."""
    return evaluate_entries(
        load_gold(gold_jsonl),
        load_predictions(pred_jsonl),
        seed=seed,
        n_boot=n_boot,
        by_standard=by_standard,
    )


def iter_rates(result: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(path, rate)`` for every rate in the result (for the self-test check)."""
    for g in result["groups"]:
        for block, data in g["metrics"].items():
            for metric, r in data["rates"].items():
                yield f"{g['group']}={g['value']}/{block}/{metric}", r


__all__ = [
    "ELEMENTS",
    "Predictions",
    "evaluate",
    "evaluate_entries",
    "license_equivalent",
    "name_match",
    "predictions_from_gold",
    "provider_prf",
    "score_rows",
    "wilson",
]
