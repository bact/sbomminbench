# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Output of ``evaluate``: ``KEY=VALUE`` lines and Markdown tables.

See also: ``cli.py`` (the command), ``metrics.py`` (the result tree).
"""

from collections.abc import Iterator
from typing import Any

from sbomminbench.evaluate.caveats import NOT_COMPLIANCE

NA = "NA"
RATE_KEYS = ("GROUP", "SLICE", "ELEMENT", "METRIC", "N", "K", "RATE", "LOW", "HIGH")
COVERAGE_KEYS = (
    "GROUP",
    "SLICE",
    "ELEMENT",
    "GOLD_ROWS",
    "SCORED",
    "ABSTENTION",
    "UNSCORED",
    "NOT_COVERED",
    "UNSCORED_BY",
)


NUMERIC = frozenset(
    {"N", "K", "RATE", "LOW", "HIGH", "GOLD_ROWS", "SCORED", "ABSTENTION"}
    | {"UNSCORED", "NOT_COVERED"}
)


def fmt(value: object) -> str:
    """One output value: ``NA`` for ``None``, six decimals for a float."""
    if value is None:
        return NA
    return f"{value:.6f}" if isinstance(value, float) else str(value)


def _rate_fields(r: dict[str, Any]) -> dict[str, str]:
    return {
        "N": fmt(r["n"]),
        "K": fmt(r["k"]),
        "RATE": fmt(r["rate"]),
        "LOW": fmt(r["low"]),
        "HIGH": fmt(r["high"]),
    }


def _group_records(result: dict[str, Any]) -> Iterator[dict[str, str]]:
    for g in result["groups"]:
        for block, data in g["metrics"].items():
            if data["n"] == 0:
                continue
            base = {"GROUP": g["group"], "SLICE": g["value"], "ELEMENT": block}
            for metric, r in data["rates"].items():
                yield {**base, "METRIC": metric, **_rate_fields(r)}
            for metric, r in data.get("shares", {}).items():
                yield {**base, "METRIC": metric, **_rate_fields(r)}
            major = data.get("majority_license")
            if major and major["n"]:
                yield {
                    **base,
                    "METRIC": "majority_license_share",
                    **_rate_fields({**major, "low": None, "high": None}),
                    "LICENSE": major["license"],
                }


def _standard_records(result: dict[str, Any]) -> Iterator[dict[str, str]]:
    standards = result.get("by_standard")
    if standards is None:
        return
    for row in standards["elements"]:
        first = True
        for metric, r in row["metrics"]["rates"].items():
            rec = {
                "GROUP": "standard",
                "SLICE": row["element_id"],
                "ELEMENT": row["element"],
                "METRIC": metric,
                "FIT": row["fit"],
                **_rate_fields({**r, "n": row["metrics"]["n"]}),
                "TITLE": row["title"],
            }
            if first and row["caveat"]:
                rec["CAVEAT"] = row["caveat"]
            first = False
            yield rec


def _coverage_records(result: dict[str, Any]) -> Iterator[dict[str, str]]:
    for g in result["groups"]:
        for element, c in g["coverage"].items():
            if c["gold_rows"] == 0:
                continue
            by = ",".join(f"{k}:{v}" for k, v in c["unscored"].items())
            yield {
                "GROUP": g["group"],
                "SLICE": g["value"],
                "ELEMENT": element,
                "GOLD_ROWS": str(c["gold_rows"]),
                "SCORED": str(c["scored"]),
                "ABSTENTION": str(c["abstention"]),
                "UNSCORED": str(sum(c["unscored"].values())),
                "NOT_COVERED": str(c["not_covered"]),
                "UNSCORED_BY": by or "none",
            }


def kv_lines(result: dict[str, Any]) -> Iterator[list[tuple[str, str]]]:
    """One data point per line; a value that may hold a space goes last."""
    for rec in _group_records(result):
        license_last = [("LICENSE", rec["LICENSE"])] if "LICENSE" in rec else []
        yield [(k, rec[k]) for k in RATE_KEYS] + license_last
    for rec in _coverage_records(result):
        yield [
            *[(k, rec[k]) for k in COVERAGE_KEYS[:3]],
            ("METRIC", "coverage"),
            *[(k, rec[k]) for k in COVERAGE_KEYS[3:]],
        ]
    for rec in _standard_records(result):
        pairs = [(k, rec[k]) for k in RATE_KEYS[:4] + ("FIT",) + RATE_KEYS[4:]]
        if "CAVEAT" in rec:
            yield [*pairs[:3], ("METRIC", "caveat"), ("CAVEAT", rec["CAVEAT"])]
        yield [*pairs, ("TITLE", rec["TITLE"])]
    standards = result.get("by_standard")
    for gap in standards["no_element"] if standards else []:
        yield [
            ("GROUP", "standard"),
            ("SLICE", gap["standard"]),
            ("ELEMENT", gap["element"]),
            ("METRIC", "no_element"),
            ("TITLE", gap["title"]),
        ]


def _table(header: tuple[str, ...], rows: list[list[str]]) -> list[str]:
    """A Markdown table; numeric columns are right-aligned."""
    align = ["---:" if h in NUMERIC else ":---" for h in header]
    out = ["| " + " | ".join(h.lower() for h in header) + " |"]
    out.append("| " + " | ".join(align) + " |")
    out.extend("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows)
    return out


def markdown(result: dict[str, Any]) -> str:
    """Rates (with ``fit`` and ``caveat`` for standards) and coverage as tables."""
    records = [*_group_records(result), *_standard_records(result)]
    keys = (*RATE_KEYS, "FIT", "CAVEAT")
    out = _table(keys, [[rec.get(k, "") for k in keys] for rec in records])
    cov = [[c[k] for k in COVERAGE_KEYS] for c in _coverage_records(result)]
    out += ["", *_table(COVERAGE_KEYS, cov)]
    if "by_standard" in result:
        out += ["", f"Per-standard rows are {NOT_COMPLIANCE}."]
    return "\n".join(out) + "\n"
