# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Confirm the crosswalk table against the vendored BOM files (offline)."""

import csv
import hashlib
import json
from pathlib import Path

import pytest

from sbomminbench.standards import (
    BOM_NAMESPACE,
    BOM_RELEASE,
    BOM_RETRIEVED,
    STANDARD_ELEMENTS,
    StandardElement,
    elements_for,
    release_metadata,
)

BOM = Path(__file__).resolve().parent.parent / "vendor" / "bom"
IDS = [r.element_id for r in STANDARD_ELEMENTS]


def _ttl(standard: str) -> str:
    return (BOM / "req" / standard / f"{standard}.ttl").read_text(encoding="utf-8")


def _block(ttl: str, element_id: str) -> str:
    lines = ttl.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == element_id)
    out: list[str] = []
    for ln in lines[start:]:
        if not ln.strip():
            break
        out.append(ln)
    return "\n".join(out)


def _sssom(standard: str, target: str) -> list[dict[str, str]]:
    path = BOM / "mapping" / f"{standard}-to-{target}.sssom.tsv"
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def _dump(obj: object) -> str:
    return (
        json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def test_checksums() -> None:
    entries = (BOM / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(entries) == 27
    for line in entries:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((BOM / rel).read_bytes()).hexdigest() == digest, rel


def test_source_json() -> None:
    src = json.loads((BOM / "SOURCE.json").read_text(encoding="utf-8"))
    assert src["bom_release"] == BOM_RELEASE
    assert src["retrieved"] == BOM_RETRIEVED
    assert src["namespace"] == BOM_NAMESPACE


@pytest.mark.parametrize("row", STANDARD_ELEMENTS, ids=IDS)
def test_ttl_block(row: StandardElement) -> None:
    ttl = _ttl(row.standard)
    local = row.element_id.split(":", 1)[1]
    assert row.iri == f"{BOM_NAMESPACE}req/{row.standard}/{local}"
    ns = row.iri[: -len(local)]
    assert f'vann:preferredNamespaceUri "{ns}"' in ttl
    block = _block(ttl, row.element_id)
    assert f'skos:prefLabel "{row.label}"@en' in block
    assert f'skos:notation "{row.notation}"^^bom:SarifRuleId' in block
    if row.code is not None:
        assert f'skos:notation "{row.code}"^^bom:ElementCode' in block
    collapsed = {" ".join(ln.split()) for ln in ttl.splitlines()}
    assert f"{row.element_id} bom:provisionType bom:{row.obligation} ." in collapsed


@pytest.mark.parametrize("row", STANDARD_ELEMENTS, ids=IDS)
@pytest.mark.parametrize("target", ["spdx30", "spdx31"])
def test_sssom_target(row: StandardElement, target: str) -> None:
    hits = [
        r
        for r in _sssom(row.standard, target)
        if r["subject_id"] == row.element_id and r["object_id"] == row.spdx_target
    ]
    assert len(hits) == 1
    assert hits[0]["predicate_id"] == "skos:exactMatch"


_BRIDGE = {
    "name": ("bom:component-name",),
    "provider": ("bom:component-producer",),
    "license": ("bom:component-license", "bom:component-declared-license"),
}


@pytest.mark.parametrize(
    "row",
    [r for r in STANDARD_ELEMENTS if r.fit == "exact"],
    ids=lambda r: r.element_id,
)
def test_exact_fit_has_bridge_match(row: StandardElement) -> None:
    block = _block(_ttl(row.standard), row.element_id)
    assert any(f"skos:exactMatch {b}" in block for b in _BRIDGE[row.element]), block


@pytest.mark.parametrize(
    "row",
    [r for r in STANDARD_ELEMENTS if r.fit == "close"],
    ids=lambda r: r.element_id,
)
def test_close_fit_has_no_exact_bridge_match(row: StandardElement) -> None:
    block = _block(_ttl(row.standard), row.element_id)
    for bridge in ("bom:component-producer", "bom:component-declared-license"):
        assert f"skos:exactMatch {bridge}" not in block


def test_absences() -> None:
    pairs = {(r.standard, r.element) for r in STANDARD_ELEMENTS}
    assert ("ntia2021", "license") not in pairs
    assert not any(s == "mof2024" for s, _ in pairs)
    assert "certin2025:component-supplier" not in IDS
    assert "bsi2025:component-distribution-license" not in IDS


def test_release_metadata_and_kinds() -> None:
    meta = release_metadata()
    assert _dump(meta) == _dump(release_metadata())
    elements = meta["elements"]
    assert isinstance(elements, list)
    assert len(elements) == 22
    assert len(IDS) == len(set(IDS))
    ai_only = {r.element_id for r in STANDARD_ELEMENTS if r.kinds == {"model"}}
    assert ai_only
    got = {r.element_id for r in elements_for("name", "package")}
    assert got and not got & ai_only
