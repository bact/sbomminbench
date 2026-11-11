# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the export and stats tests: a data dir from fixtures."""

import json
import shutil
from pathlib import Path

EXPECTED = Path(__file__).parent / "fixtures" / "expected"
FIXTURES = (
    "pypi/_/magika@0.6.1.json",
    "pypi/_/requests@2.32.3.json",
    "npm/_/express@4.21.1.json",
    "cargo/_/ring@0.16.20.json",
    "maven/org.slf4j/slf4j-api@2.0.16.json",
    "nuget/_/Newtonsoft.Json@13.0.3.json",
    "huggingface/openai-community/gpt2@607a30d783dfa663caf39e06633721c8d4cfcd7e.json",
)
REQUESTS = "pkg:pypi/requests@2.32.3"


def make_data_dir(tmp_path: Path, names: tuple[str, ...] = FIXTURES) -> Path:
    """Copy fixture entries into ``tmp_path/data/entries`` and return ``data``."""
    data = tmp_path / "data"
    for name in names:
        dest = data / "entries" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXPECTED / name, dest)
    return data


def write_review(data: Path, reviewer: str, *lines: dict[str, object]) -> Path:
    """Write review lines to ``data/reviews/<reviewer>.jsonl``."""
    path = data / "reviews" / f"{reviewer}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(line) + "\n" for line in lines)
    path.write_bytes(text.encode("utf-8"))
    return path


def review(
    decision: str,
    element: str = "license",
    at: str = "2026-11-10T01:00:00Z",
    **extra: object,
) -> dict[str, object]:
    """Return a review line for the requests root subject.

    The auto answer it was made against is the one in the fixture entry.
    """
    auto = json.loads((EXPECTED / FIXTURES[1]).read_bytes())["subjects"][0]["expected"][
        element
    ]
    return {
        "auto_status": auto["status"],
        "auto_value_at_review": auto["value"],
        "id": REQUESTS,
        "subject": "root",
        "element": element,
        "decision": decision,
        "reviewer": "alice",
        "at": at,
        **extra,
    }
