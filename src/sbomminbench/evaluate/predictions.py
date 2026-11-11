# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Predictions and gold: reading, abstention and the self-test input."""

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sbomminbench.evaluate import csvio
from sbomminbench.schema import EMAIL_PLACEHOLDER, Element, Entry

ELEMENTS: tuple[Element, ...] = ("name", "provider", "license")
_ABSTAIN_STRINGS = frozenset({"", "NOASSERTION"})
_CASE_INSENSITIVE = frozenset({"pypi", "cargo", "nuget", "huggingface"})
_LICENSE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+:-]*")
_UTF8_BOM = b"\xef\xbb\xbf"


# -- predictions ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Predictions:
    """Parsed prediction records, keyed by ``(entry id, subject id)``.

    Each record maps an element to the raw JSON value as given; a missing key
    means the element was not predicted. The ``id`` of a line must equal the
    gold purl exactly (no normalisation); a line for another id matches no gold
    subject and is counted as unmatched.
    """

    records: dict[tuple[str, str], dict[str, object]]
    lines: int = 0
    malformed_lines: int = 0
    duplicates: int = 0


def parse_predictions(lines: Iterable[bytes | str]) -> Predictions:
    """Parse prediction JSONL.

    A line is ``{"id": <purl>, "subject": "root", "name": str|null,
    "provider": [str]|null, "license": str|null}``. ``subject`` defaults to
    ``root``; an element key may be missing. A line that is not a JSON object
    with a string ``id`` (and string ``subject``) is counted and skipped; a later
    line for the same subject replaces the earlier one.
    """
    records: dict[tuple[str, str], dict[str, object]] = {}
    total = bad = dup = 0
    for line in lines:
        data = line.encode() if isinstance(line, str) else line
        data = data.removeprefix(_UTF8_BOM).strip()
        if not data:
            continue
        total += 1
        try:
            obj = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            obj = None
        subject = obj.get("subject", "root") if isinstance(obj, dict) else None
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str):
            bad += 1
            continue
        if not isinstance(subject, str):
            bad += 1
            continue
        key = (obj["id"], subject)
        dup += key in records
        records[key] = {e: obj[e] for e in ELEMENTS if e in obj}
    return Predictions(records, total, bad, dup)


def _is_csv(path: Path) -> bool:
    return path.suffix.lower() == ".csv"


def load_predictions(path: Path) -> Predictions:
    """Read a prediction file: CSV by the ``.csv`` extension, else JSONL.

    A CSV means the same as the JSONL it would be written as (see
    :mod:`sbomminbench.evaluate.csvio`).
    """
    if _is_csv(path):
        header, rows = csvio.read_table(path)
        return Predictions(*csvio.parse_prediction_rows(header, rows))
    with path.open("rb") as handle:
        return parse_predictions(handle)


def is_abstention(element: Element, raw: object) -> bool:
    """Whether a *given* value is an explicit abstention.

    ``null``, ``""``, ``[]`` and ``NOASSERTION``; for a licence also ``NONE``.
    A key that is absent from the line is not a value: callers check coverage
    first, so an empty output never counts as abstaining.
    """
    if raw is None:
        return True
    if isinstance(raw, list):
        return not raw
    if not isinstance(raw, str):
        return False
    text = raw.strip().upper()
    return text in _ABSTAIN_STRINGS or (element == "license" and text == "NONE")


def is_malformed(element: Element, raw: object) -> bool:
    if raw is None:
        return False
    if element == "provider":
        return not isinstance(raw, list) or not all(isinstance(x, str) for x in raw)
    return not isinstance(raw, str)


def predictions_from_gold(entries: Iterable[Entry]) -> list[dict[str, object]]:
    """Prediction records that repeat the gold values (the self-test input)."""
    out: list[dict[str, object]] = []
    for entry in entries:
        for subject in entry.subjects:
            ex = subject.expected
            name = ex.name.value.name if ex.name.value else None
            provider = (
                [e.name for e in ex.provider.value.entities]
                if ex.provider.value
                else None
            )
            licence = ex.license.value.spdx_expression if ex.license.value else None
            out.append(
                {
                    "id": entry.id,
                    "subject": subject.id,
                    "name": name,
                    "provider": provider,
                    "license": licence,
                }
            )
    return out


def _respell_license(expr: str) -> str:
    """Reverse a flat ``OR`` list and lowercase every key but ``LicenseRef``s."""
    if not any(x in expr for x in ("(", " AND ", " WITH ")):
        expr = " OR ".join(reversed(expr.split(" OR ")))

    def lower(m: re.Match[str]) -> str:
        token = m.group(0)
        return token if "LicenseRef-" in token else token.lower()

    return _LICENSE_TOKEN.sub(lower, expr)


def _recased(text: str) -> str:
    """Upper-case ``text`` unless that changes its case-folded form (dotless i)."""
    upper = text.upper()
    return upper if upper.casefold() == text.casefold() else text


def respell_equivalent(
    records: Iterable[dict[str, object]], entries: Iterable[Entry]
) -> list[dict[str, object]]:
    """Predictions respelled in ways the strict rules treat as equal.

    Licence: ``OR`` operands reversed and keys lowercased. Name: upper-cased
    where the ecosystem ignores case. Provider: reversed, NFD, upper-cased, with a
    redacted e-mail appended. Upper-casing is skipped for a text whose case
    fold it would change (a dotless i). A scorer that falls below 1.0 on these
    compares spellings, not facts.
    """
    eco = {e.id: e.ecosystem for e in entries}
    out: list[dict[str, object]] = []
    for rec in records:
        new = dict(rec)
        name, provider, licence = rec["name"], rec["provider"], rec["license"]
        if isinstance(name, str) and eco.get(str(rec["id"])) in _CASE_INSENSITIVE:
            new["name"] = _recased(name)
        if isinstance(provider, list):
            new["provider"] = [
                f"{_recased(unicodedata.normalize('NFD', str(p)))} <{EMAIL_PLACEHOLDER}>"
                for p in reversed(provider)
            ]
        if isinstance(licence, str):
            new["license"] = _respell_license(licence)
        out.append(new)
    return out


# -- gold ----------------------------------------------------------------------------


def load_gold(path: Path) -> list[Entry]:
    """Read gold entries from a JSONL file, a directory or a ``.csv`` file."""
    if _is_csv(path):
        return csvio.load_gold_csv(path)
    if path.is_dir():
        files = sorted(
            path.rglob("*.json"), key=lambda p: p.relative_to(path).as_posix()
        )
        entries = [
            Entry.model_validate_json(f.read_bytes().removeprefix(_UTF8_BOM))
            for f in files
        ]
    else:
        with path.open("rb") as handle:
            entries = [
                Entry.model_validate_json(raw.removeprefix(_UTF8_BOM).strip())
                for raw in handle
                if raw.strip()
            ]
    return sorted(entries, key=lambda e: e.id)
