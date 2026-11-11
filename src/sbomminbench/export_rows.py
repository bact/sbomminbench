# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Flat table rows of the release files (``subjects.csv``, ``observations.csv``).

See also: ``export.py`` (writes the files), ``stats.py`` (aggregates the same
rows), ``export_review.py`` (the human overlay applied before this step).
"""

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator
from typing import Final, get_args

from sbomminbench.schema import (
    Element,
    Entry,
    ExpectedLicense,
    ExpectedName,
    ExpectedProvider,
    JsonValue,
    Observation,
    Subject,
)
from sbomminbench.stats_contract import conformance, field_contract

ELEMENTS: Final[tuple[str, ...]] = get_args(Element)
MAX_RAW_JSON = 1000
SUBJECT_COLUMNS: Final = [
    "id",
    "ecosystem",
    "stratum",
    "rank",
    "subject",
    "kind",
    "model_format",
    "element",
    "status",
    "auto_status",
    "independent_origins",
    "gold_json",
    "tags",
]
OBSERVATION_COLUMNS: Final = [
    "id",
    "layer",
    "subject",
    "source",
    "field_path",
    "element",
    "role",
    "codemeta",
    "present",
    "native_type",
    "semantic_format",
    "format_validity",
    "field_contract",
    "conformance",
    "has_email",
    "inherited_from",
    "raw_truncated",
    "raw_json",
]
_INDEX = re.compile(r"\[\d+\]")


def normalise_path(field_path: str) -> str:
    """Return ``field_path`` with list indexes replaced by ``[]`` (for counts)."""
    return _INDEX.sub("[]", field_path)


def compact_json(value: object) -> str:
    """Return the compact, key-sorted JSON text of ``value``."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def csv_text(header: list[str], rows: Iterable[list[str]]) -> str:
    """Return CSV text: UTF-8-ready, LF line endings, one header row."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def cell(value: object) -> str:
    """Return a CSV cell: ``None`` is empty, booleans are ``true``/``false``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def expected_of(
    subject: Subject, element: str
) -> ExpectedName | ExpectedProvider | ExpectedLicense:
    """Return the expected answer of ``subject`` for ``element``."""
    match element:
        case "name":
            return subject.expected.name
        case "provider":
            return subject.expected.provider
        case "license":
            return subject.expected.license
    raise ValueError(f"unknown element {element!r}")


def iter_observations(
    entry: Entry,
) -> Iterator[tuple[str, str, Observation, dict[str, JsonValue]]]:
    """Yield ``(layer, subject id, observation, siblings)`` for an entry.

    Platform observations belong to the root subject. ``siblings`` maps the
    observation keys of the same layer to their raw values.
    """
    platform = {o.key: o.raw for o in entry.platform.observations}
    for obs in entry.platform.observations:
        yield "platform", "root", obs, platform
    for subject in entry.subjects:
        manifest = {o.key: o.raw for o in subject.manifest_observations}
        for obs in subject.manifest_observations:
            yield "manifest", subject.id, obs, manifest


def observation_contract(
    obs: Observation, siblings: dict[str, JsonValue]
) -> tuple[str, str]:
    """Return ``(field_contract, conformance)``; empty strings when none applies.

    Only licence observations that are present have a contract.
    """
    if obs.element != "license" or not obs.present:
        return "", ""
    contract = field_contract(obs.source, obs.field_path, siblings)
    if contract is None:
        return "", ""
    verdict = conformance(contract, obs.semantic_format, obs.format_validity, obs.raw)
    return contract, verdict


def subject_rows(entry: Entry) -> Iterator[list[str]]:
    """Yield one row per subject and element (``SUBJECT_COLUMNS``)."""
    for subject in entry.subjects:
        for element in ELEMENTS:
            exp = expected_of(subject, element)
            gold = exp.value.model_dump(mode="json") if exp.value else None
            yield [
                cell(v)
                for v in (
                    entry.id,
                    entry.ecosystem,
                    entry.stratum,
                    entry.rank,
                    subject.id,
                    subject.kind,
                    subject.model_format,
                    element,
                    exp.status,
                    exp.auto_status or exp.status,
                    exp.independent_origins,
                    "" if gold is None else compact_json(gold),
                    ";".join(subject.challenge_tags),
                )
            ]


def observation_rows(entry: Entry) -> Iterator[list[str]]:
    """Yield one row per observation (``OBSERVATION_COLUMNS``)."""
    for layer, sid, obs, siblings in iter_observations(entry):
        contract, verdict = observation_contract(obs, siblings)
        raw_json = compact_json(obs.raw)
        cut = len(raw_json) > MAX_RAW_JSON
        yield [
            cell(v)
            for v in (
                entry.id,
                layer,
                sid,
                obs.source,
                obs.field_path,
                obs.element,
                obs.role,
                obs.codemeta,
                obs.present,
                obs.native_type,
                obs.semantic_format,
                obs.format_validity,
                contract,
                verdict,
                obs.has_email,
                obs.inherited_from,
                obs.raw_truncated or cut,
                raw_json[:MAX_RAW_JSON],
            )
        ]
