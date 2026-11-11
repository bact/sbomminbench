# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers of the evaluator tests (gold from the two example entries)."""

import json
from pathlib import Path
from typing import Any

from sbomminbench.evaluate import metrics
from sbomminbench.evaluate.predictions import parse_predictions, predictions_from_gold
from sbomminbench.schema import Entry

EXAMPLES = Path(__file__).parent / "fixtures" / "examples"
MAGIKA = "pkg:pypi/magika@0.6.1"
QWEN = (
    "pkg:huggingface/Qwen/Qwen2.5-72B-Instruct@495f39366efef23836d0cfae4fbe635880d2be31"
)
BOOT = 20


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text("utf-8"))
    return data


def set_expected(
    d: dict[str, Any], sid: str, element: str, status: str, value: Any = None
) -> None:
    """Set one expected element, adding the review fields a reviewed status needs."""
    sub = next(s for s in d["subjects"] if s["id"] == sid)
    ex = sub["expected"][element]
    ex["status"], ex["value"] = status, value
    ex["candidates"] = ["a", "b"] if status == "auto_conflict" else []
    reviewed = status.startswith("human_") or status == "excluded"
    ex["auto_status"] = "auto_conflict" if reviewed else None
    ex["reviewer"] = "r" if reviewed else None
    if status == "human_confirmed" and value is None:
        ex["auto_status"] = "absent_in_source"


def entries(mutate: Any = None) -> list[Entry]:
    magika, qwen = load("magika"), load("qwen")
    if mutate:
        mutate(magika, qwen)
    return [Entry.model_validate(magika), Entry.model_validate(qwen)]


def run(gold: list[Entry], recs: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    preds = parse_predictions(json.dumps(r) for r in recs)
    return metrics.evaluate_entries(gold, preds, n_boot=BOOT, **kw)


def block(
    result: dict[str, Any], name: str, group: str = "all", value: str = "all"
) -> dict[str, Any]:
    g = next(g for g in result["groups"] if (g["group"], g["value"]) == (group, value))
    out: dict[str, Any] = g["metrics"][name]
    return out


def rate(result: dict[str, Any], name: str, metric: str, **kw: str) -> Any:
    return block(result, name, **kw)["rates"][metric]["rate"]


def perfect(gold: list[Entry]) -> list[dict[str, Any]]:
    return [dict(r) for r in predictions_from_gold(gold)]


def by_id(recs: list[dict[str, Any]], i: str) -> dict[str, Any]:
    return next(r for r in recs if r["id"] == i and r["subject"] == "root")


def many(n: int) -> list[Entry]:
    """``n`` copies of the magika entry under distinct ids (for bootstrap sizes)."""
    out = []
    for i in range(n):
        d = load("magika")
        d["id"] = f"pkg:pypi/magika{i:03d}@0.6.1"
        out.append(Entry.model_validate(d))
    return out


def variant(name: str, providers: list[str], licence: str) -> Entry:
    """A magika-shaped entry with its own root name, authors and licence."""
    d = load("magika")
    d["id"] = f"pkg:pypi/{name}@0.6.1"
    root = d["subjects"][0]["expected"]
    root["name"]["value"] = {"name": name, "canonical_key": name, "accepted": [name]}
    root["provider"]["value"] = {
        "entities": [{"name": p, "role": "author"} for p in providers],
        "has_unnamed_group": False,
    }
    root["license"]["value"] = {"spdx_expression": licence, "has_license_ref": False}
    return Entry.model_validate(d)


def variants() -> list[Entry]:
    """Four entries with distinct names, overlapping authors and licences."""
    return [
        variant("aa", ["Ann"], "MIT"),
        variant("bb", ["Bob", "Ann"], "Apache-2.0 OR MIT"),
        variant("cc", ["Cy"], "BSD-3-Clause"),
        variant("dd", ["Di", "Bob"], "ISC"),
    ]


def roots(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in recs if r["subject"] == "root"]
