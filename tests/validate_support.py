# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers of the ``validate`` tests: a data tree copied from the fixtures."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from sbomminbench import validate
from sbomminbench.jsonio import dumps
from sbomminbench.validate_findings import Finding

REPO = Path(__file__).parents[1]
EXPECTED = Path(__file__).parent / "fixtures" / "expected"
SCHEMA = REPO / "schema" / "entry.schema.json"
BOM = REPO / "vendor" / "bom"
PACKAGING = "pypi/_/packaging@26.3.json"
SLF4J = "maven/org.slf4j/slf4j-api@2.0.16.json"
GPT2 = "huggingface/openai-community/gpt2@607a30d783dfa663caf39e06633721c8d4cfcd7e.json"
BABEL = "npm/%40babel/core@7.26.0.json"
DEFAULT = (PACKAGING, SLF4J, GPT2, BABEL)

type Edit = Callable[[dict[str, object]], None]


def make_tree(tmp_path: Path, names: tuple[str, ...] = DEFAULT) -> Path:
    """Copy fixture entries to ``<tmp>/data/entries``; return the data directory."""
    data = tmp_path / "data"
    for name in names:
        dest = data / "entries" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXPECTED / name, dest)
    return data


def edit(data: Path, name: str, change: Edit) -> None:
    """Load an entry file, apply *change* to the dict, write it back canonically."""
    path = data / "entries" / name
    doc = json.loads(path.read_bytes())
    change(doc)
    path.write_bytes(dumps(doc).encode("utf-8"))


def run(data: Path, **kw: Path) -> validate.Report:
    """Run ``validate`` on *data* with the repo schema and BOM, and no cache."""
    return validate.run_validate(
        data,
        kw.get("cache", data.parent / "nocache"),
        kw.get("schema", SCHEMA),
        kw.get("bom", BOM),
    )


def hits(
    report: validate.Report, check: str, severity: str | None = None
) -> list[Finding]:
    """The findings of one check (optionally of one severity)."""
    return [
        f
        for f in report.findings
        if f.check == check and severity in (None, f.severity)
    ]


def subject_expected(doc: dict[str, object], element: str) -> dict[str, object]:
    """The ``expected.<element>`` dict of the root subject of an entry dict."""
    subjects: list[dict[str, dict[str, dict[str, object]]]] = doc["subjects"]  # type: ignore[assignment]
    return subjects[0]["expected"][element]
