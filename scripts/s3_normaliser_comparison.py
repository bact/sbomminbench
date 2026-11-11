# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Experimental S3: three ways to read one declared licence value.

Input:    root subjects with a scored licence: the first declared licence
          string (platform first, a ``license:`` hub prefix removed).
Method:   read the string three ways and compare each reading with the
          expected expression (equivalence):
          ``passthrough``: the string as it is;
          ``license-expression``: SPDX validation, no answer when it fails;
          ``licenseid``: ``resolve()`` on the packaged list, no answer unless
          the category is one the dataset accepts.
Output:   ``experimental/s3-normaliser-comparison.json``.
Network:  none.
Voting:   none. ``build``, ``validate`` and ``export`` never read the output.
Caveat:   the automatic labels were derived with LicenseID, so its rate on
          ``auto_unanimous`` rows is not an independent measurement.
Usage:    ``uv run python scripts/s3_normaliser_comparison.py``
"""

import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from license_expression import get_spdx_licensing
from licenseid import resolve

from sbomminbench.derive.license import OK_CATEGORIES

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "experimental" / "s3-normaliser-comparison.json"
SCORED = ("auto_unanimous", "human_corrected")
LICENSING = get_spdx_licensing()


def _license_expression(raw: str) -> str | None:
    try:
        info = LICENSING.validate(raw, strict=True)
    except Exception:  # noqa: BLE001 - odd input is "no answer"
        return None
    return None if info.errors else info.normalized_expression


def _licenseid(raw: str) -> str | None:
    try:
        res: Any = resolve(raw, source="packaged")
    except Exception:  # noqa: BLE001 - a probe records, never aborts
        return None
    expression = res.get("expression")
    return expression if res["category"] in OK_CATEGORIES and expression else None


READERS: dict[str, Callable[[str], str | None]] = {
    "passthrough": lambda raw: raw,
    "license-expression": _license_expression,
    "licenseid": _licenseid,
}


def _equivalent(answer: str, gold: str) -> bool:
    try:
        return bool(LICENSING.is_equivalent(answer, gold))
    except Exception:  # noqa: BLE001 - unparseable text compares as text
        return answer.strip().casefold() == gold.strip().casefold()


def _first_raw(entry: dict[str, Any]) -> str | None:
    subject = entry["subjects"][0]
    for obs in entry["platform"]["observations"] + subject["manifest_observations"]:
        raw = obs["raw"]
        if obs["element"] != "license" or not obs["present"]:
            continue
        if isinstance(raw, str) and raw:
            return raw.removeprefix("license:")
    return None


def main() -> int:
    path = ROOT / "data" / "release" / "entries.jsonl"
    count: Counter[tuple[str, str, str]] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        expected = entry["subjects"][0]["expected"]["license"]
        if expected["status"] not in SCORED:
            continue
        gold = expected["value"]["spdx_expression"]
        raw = _first_raw(entry)
        for reader, read in READERS.items():
            answer = read(raw) if raw else None
            correct = answer is not None and _equivalent(answer, gold)
            for group in (entry["ecosystem"], expected["status"], "all"):
                count[(reader, group, "subjects")] += 1
                count[(reader, group, "answered")] += answer is not None
                count[(reader, group, "correct")] += correct
    result: dict[str, dict[str, dict[str, int]]] = {}
    for (reader, group, key), n in sorted(count.items()):
        result.setdefault(reader, {}).setdefault(group, {})[key] = n
    OUT.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for reader, groups in result.items():
        row = groups["all"]
        print(
            f"READER={reader} SUBJECTS={row['subjects']} "
            f"ANSWERED={row['answered']} CORRECT={row['correct']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
