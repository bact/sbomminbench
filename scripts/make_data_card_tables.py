# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Rewrite the generated tables of ``docs/data-card.md`` from the release files.

Each table sits between ``<!-- generated:NAME:start -->`` and
``<!-- generated:NAME:end -->``. The numbers come from
``data/release/entries.jsonl``, ``stats.json`` and ``standard-elements.json``,
so no count in those tables is typed by hand.

Usage: ``uv run python scripts/make_data_card_tables.py [--check]``
(``--check`` exits 1 when the file is not up to date and changes nothing).
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "docs" / "data-card.md"
RELEASE = ROOT / "data" / "release"
ECOS = ["pypi", "npm", "cargo", "maven", "nuget", "huggingface"]
LABEL = {
    "pypi": "PyPI",
    "npm": "npm",
    "cargo": "Cargo",
    "maven": "Maven",
    "nuget": "NuGet",
    "huggingface": "Hugging Face",
}
STRATA = ["top", "mid", "edge"]
ELEMENTS = ["name", "provider", "license"]
STATUS_COLUMNS = [
    ("auto_unanimous", "Unanimous"),
    ("human_confirmed", "Confirmed"),
    ("human_corrected", "Corrected"),
    ("absent_in_source", "Absent in source"),
    ("auto_conflict", "Conflict"),
    ("auto_unresolved", "Unresolved"),
    ("excluded", "Excluded"),
    ("not_assessed", "Not assessed"),
]


def table(header: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def entries_table(entries: list[dict[str, Any]]) -> str:
    count = Counter((e["ecosystem"], e["stratum"]) for e in entries)
    subjects = Counter()
    for entry in entries:
        subjects[entry["ecosystem"]] += len(entry["subjects"])
    rows: list[list[object]] = []
    for eco in ECOS:
        per = [count[(eco, s)] for s in STRATA]
        rows.append([LABEL[eco], *per, sum(per), subjects[eco]])
    totals = [sum(count[(e, s)] for e in ECOS) for s in STRATA]
    rows.append(["All", *totals, sum(totals), sum(subjects.values())])
    return table(["Channel", *STRATA, "Entries", "Subjects"], rows)


def status_table(stats: dict[str, Any]) -> str:
    total: Counter[tuple[str, str]] = Counter()
    for per_element in stats["status_counts"].values():
        for element, counts in per_element.items():
            for status, n in counts.items():
                total[(element, status)] += n
    used = [
        (k, label)
        for k, label in STATUS_COLUMNS
        if any(total[(e, k)] for e in ELEMENTS)
    ]
    unknown = {s for _e, s in total} - {k for k, _ in STATUS_COLUMNS}
    if unknown:
        raise SystemExit(f"ERROR: status without a column: {sorted(unknown)}")
    rows = [
        [
            element,
            *[total[(element, k)] for k, _ in used],
            sum(total[(element, k)] for k, _ in used),
        ]
        for element in ELEMENTS
    ]
    return table(["Element", *[label for _, label in used], "Subjects"], rows)


def versions_table(stats: dict[str, Any]) -> str:
    with_ = stats["collected_with"]
    rows = [
        ["sbomminbench", ", ".join(with_["sbomminbench"])],
        ["Pitloom", ", ".join(with_["pitloom"])],
        ["LicenseID", ", ".join(with_["licenseid"])],
        [
            "SPDX licence list used to derive licences (LicenseID)",
            ", ".join(with_["license_list"]),
        ],
        [
            "SPDX licence list used to classify forms (`packaging`)",
            ", ".join(with_["license_list_format"]),
        ],
        [
            "Ranking read on",
            ", ".join(sorted({d for v in stats["frame_dates"].values() for d in v})),
        ],
    ]
    return table(["Item", "Value"], rows)


def standards_table(doc: dict[str, Any]) -> str:
    rows = [
        [
            e["standard_label"],
            e["element"],
            e["label"],
            f"`{e['notation']}`",
            e["obligation"],
            e["fit"],
            ", ".join(e["kinds"]),
        ]
        for e in sorted(
            doc["elements"],
            key=lambda e: (
                e["standard_label"],
                ELEMENTS.index(e["element"]),
                e["notation"],
            ),
        )
    ]
    return table(
        [
            "Standard",
            "Element",
            "Label in the standard",
            "BOM notation",
            "Obligation",
            "Fit",
            "Applies to",
        ],
        rows,
    )


def render(text: str, blocks: dict[str, str]) -> str:
    for name, body in blocks.items():
        pattern = re.compile(
            rf"(<!-- generated:{name}:start -->\n).*?(<!-- generated:{name}:end -->)",
            re.DOTALL,
        )
        if not pattern.search(text):
            raise SystemExit(f"ERROR: marker generated:{name} not found in {CARD.name}")
        text = pattern.sub(
            lambda m, body=body: f"{m.group(1)}\n{body}\n\n{m.group(2)}", text
        )
    return text


def main() -> int:
    lines = (RELEASE / "entries.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    stats = json.loads((RELEASE / "stats.json").read_text(encoding="utf-8"))
    standards = json.loads(
        (RELEASE / "standard-elements.json").read_text(encoding="utf-8")
    )
    old = CARD.read_text(encoding="utf-8")
    new = render(
        old,
        {
            "entries": entries_table(entries),
            "status": status_table(stats),
            "versions": versions_table(stats),
            "standards": standards_table(standards),
        },
    )
    if "--check" in sys.argv[1:]:
        if new != old:
            print(
                f"ERROR: {CARD.name} tables are out of date; run {Path(__file__).name}",
                file=sys.stderr,
            )
            return 1
        return 0
    CARD.write_text(new, encoding="utf-8", newline="\n")
    print(f"FILE={CARD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
