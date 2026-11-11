# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Row scoring: one :class:`Score` per gold ``(entry, subject, element)``.

See also: ``metrics.py`` (aggregation), ``matching.py`` (the match rules).
A gold row is *scored* (has a value), an *abstention* (the source states
nothing) or *unscored* (conflict, unresolved, excluded, not assessed). Unscored
rows get a ``Score`` too, so the coverage of every slice can be reported.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from sbomminbench.evaluate.gold_state import State, gold_class
from sbomminbench.evaluate.matching import (
    Counts,
    Verdict,
    license_equivalent,
    name_match,
    provider_counts,
)
from sbomminbench.evaluate.predictions import (
    ELEMENTS,
    Predictions,
    is_abstention,
    is_malformed,
)
from sbomminbench.schema import Ecosystem, Element, Entry, Subject

NO_COUNTS: tuple[Counts, Counts] = ((0, 0, 0), (0, 0, 0))


@dataclass(frozen=True, slots=True)
class Score:
    """The outcome for one gold ``(entry, subject, element)``."""

    element: Element
    state: State
    status: str  # final gold status
    auto_status: str  # status before human review (the status when unreviewed)
    origins: int  # independent origins behind the gold answer
    gold: str  # "human" (reviewed) | "silver"
    ecosystem: str
    stratum: str
    kind: str
    tags: tuple[str, ...]
    covered: bool = True  # the prediction line has this element's key
    malformed: bool = False
    name: tuple[bool, bool] = (False, False)  # strict, spelling-tolerant
    provider: tuple[Counts, Counts] = NO_COUNTS  # strict, lenient
    unmatched: tuple[int, int] = (0, 0)  # unmatched_under_group: strict, lenient
    verdict: Verdict = "missing"
    non_spdx: bool = False
    gold_license: str = ""
    pred_licenseref: bool = False
    abstained: bool = False  # abstention rows: an explicit abstention was given
    abstain_none: bool = False  # ... and it was the string NONE


def _score_value(
    element: Element, eco: Ecosystem, value: Any, raw: object
) -> dict[str, Any]:
    """Score fields for a scored row; a malformed value counts as a miss."""
    bad = is_malformed(element, raw)
    if element == "name":
        pred = raw if isinstance(raw, str) and not bad else None
        return {"name": name_match(eco, pred, value), "malformed": bad}
    if element == "provider":
        names = cast(list[str], raw) if isinstance(raw, list) and not bad else None
        strict = provider_counts(names, value, lenient=False)
        lenient = provider_counts(names, value, lenient=True)
        return {
            "provider": (strict[0], lenient[0]),
            "unmatched": (strict[1], lenient[1]),
            "malformed": bad,
        }
    text = raw if isinstance(raw, str) and not bad else None
    verdict: Verdict = (
        "invalid" if bad else license_equivalent(text, value.spdx_expression)
    )
    return {
        "verdict": verdict,
        "non_spdx": value.has_license_ref,
        "gold_license": value.spdx_expression,
        "pred_licenseref": text is not None and "LicenseRef-" in text,
        "malformed": bad,
    }


def _prediction_fields(
    state: State, entry: Entry, element: Element, value: Any, record: Any
) -> dict[str, Any]:
    if state == "unscored":
        return {}
    covered = record is not None and element in record
    raw = record.get(element) if covered else None
    if state == "abstain":
        return {
            "covered": covered,
            "abstained": covered and is_abstention(element, raw),
            "abstain_none": covered
            and isinstance(raw, str)
            and raw.strip().upper() == "NONE",
            "malformed": covered and is_malformed(element, raw),
        }
    return {
        "covered": covered,
        **_score_value(element, entry.ecosystem, value, raw),
    }


def score_row(
    entry: Entry,
    subject: Subject,
    element: Element,
    record: dict[str, object] | None,
) -> Score:
    """Score one gold element. ``record`` is ``None`` without a prediction line."""
    expected = getattr(subject.expected, element)
    state = gold_class(expected.status, expected.value)
    return Score(
        element=element,
        state=state,
        status=expected.status,
        auto_status=expected.auto_status or expected.status,
        origins=expected.independent_origins,
        gold="human" if expected.auto_status is not None else "silver",
        ecosystem=entry.ecosystem,
        stratum=entry.stratum,
        kind=subject.kind,
        tags=tuple(subject.challenge_tags),
        **_prediction_fields(state, entry, element, expected.value, record),
    )


def score_rows(
    entries: Iterable[Entry], predictions: Predictions
) -> tuple[list[Score], Counter[str]]:
    """Return a score for every gold row and a count of unscored rows by status."""
    scores: list[Score] = []
    skipped: Counter[str] = Counter()
    for entry in entries:
        for subject in entry.subjects:
            record = predictions.records.get((entry.id, subject.id))
            for element in ELEMENTS:
                score = score_row(entry, subject, element, record)
                scores.append(score)
                if score.state == "unscored":
                    skipped[score.status] += 1
    return scores, skipped
