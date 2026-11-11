# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Crosswalk from the dataset elements to minimum-elements standard ids.

The only reference is the BOM ontology v0.2.0 (vendored in ``vendor/bom/``).
Ids, labels, notations, obligations and SPDX targets are copied from it;
``tests/test_standards.py`` confirms every row against the vendored files. Nothing here reads the network or the vendored files at run time.
"""

from dataclasses import asdict, dataclass
from typing import Literal

from sbomminbench.schema import Element, JsonValue

type Fit = Literal["exact", "close"]
type Kind = Literal["package", "model"]
type Obligation = Literal["Requirement", "ConditionalRequirement"]

BOM_RELEASE = "0.2.0"
BOM_RETRIEVED = "2026-11-09"
BOM_NAMESPACE = "https://w3id.org/bom/"

_ALL: frozenset[Kind] = frozenset({"package", "model"})
_PACKAGE: frozenset[Kind] = frozenset({"package"})
_MODEL: frozenset[Kind] = frozenset({"model"})
_DECLARED = "spdx3-core:RelationshipType/hasDeclaredLicense"


@dataclass(frozen=True, slots=True)
class StandardElement:
    """One standard element that one dataset element instantiates."""

    standard: str  # BOM vocabulary name, "cisa2026"
    standard_label: str  # "CISA 2026"
    element: Element  # ours: "name" | "provider" | "license"
    element_id: str  # CURIE, "cisa2026:component-producer"
    iri: str  # BOM_NAMESPACE + "req/cisa2026/component-producer"
    label: str  # skos:prefLabel
    notation: str  # BOM-* skos:notation (bom:SarifRuleId)
    code: str | None  # bom:ElementCode notation when present
    obligation: Obligation
    fit: Fit
    kinds: frozenset[Kind]
    spdx_target: str  # exactMatch object in -to-spdx30 and -to-spdx31


def _row(
    standard: str,
    standard_label: str,
    element: Element,
    local: str,
    label: str,
    notation: str,
    *,
    code: str | None = None,
    obligation: Obligation = "Requirement",
    fit: Fit = "exact",
    kinds: frozenset[Kind] = _ALL,
    spdx_target: str,
) -> StandardElement:
    return StandardElement(
        standard=standard,
        standard_label=standard_label,
        element=element,
        element_id=f"{standard}:{local}",
        iri=f"{BOM_NAMESPACE}req/{standard}/{local}",
        label=label,
        notation=notation,
        code=code,
        obligation=obligation,
        fit=fit,
        kinds=kinds,
        spdx_target=spdx_target,
    )


_NAME = "spdx3-core:name"
_ORIG = "spdx3-core:originatedBy"
_SUPP = "spdx3-core:suppliedBy"

_ROWS: tuple[StandardElement, ...] = (
    _row(
        "ntia2021",
        "NTIA 2021",
        "name",
        "name",
        "Component Name",
        "BOM-NTIA2021-DF-002",
        spdx_target=_NAME,
    ),
    _row(
        "ntia2021",
        "NTIA 2021",
        "provider",
        "supplier",
        "Supplier Name",
        "BOM-NTIA2021-DF-001",
        fit="close",
        spdx_target=_SUPP,
    ),
    _row(
        "fsct2024",
        "FSCT 2024",
        "name",
        "name",
        "Component Name",
        "BOM-FSCT2024-COMP-001",
        spdx_target=_NAME,
    ),
    _row(
        "fsct2024",
        "FSCT 2024",
        "provider",
        "supplier",
        "Supplier",
        "BOM-FSCT2024-COMP-003",
        fit="close",
        spdx_target=_SUPP,
    ),
    _row(
        "fsct2024",
        "FSCT 2024",
        "license",
        "concluded-license",
        "Concluded License",
        "BOM-FSCT2024-COMP-007",
        fit="close",
        spdx_target="spdx3-core:RelationshipType/hasConcludedLicense",
    ),
    _row(
        "cisa2026",
        "CISA 2026",
        "name",
        "component-name",
        "Component Name",
        "BOM-CISA2026-COMP-007",
        spdx_target=_NAME,
    ),
    _row(
        "cisa2026",
        "CISA 2026",
        "provider",
        "component-producer",
        "Component Producer",
        "BOM-CISA2026-COMP-001",
        spdx_target=_ORIG,
    ),
    _row(
        "cisa2026",
        "CISA 2026",
        "license",
        "component-license",
        "Component License",
        "BOM-CISA2026-COMP-006",
        spdx_target=_DECLARED,
    ),
    _row(
        "g7ai2026",
        "G7 AI 2026",
        "name",
        "model-name",
        "Model name",
        "BOM-G7AI2026-MDL-001",
        code="G7AI-MDL-001",
        kinds=_MODEL,
        spdx_target=_NAME,
    ),
    _row(
        "g7ai2026",
        "G7 AI 2026",
        "provider",
        "model-producer",
        "Model producer",
        "BOM-G7AI2026-MDL-005",
        code="G7AI-MDL-005",
        kinds=_MODEL,
        spdx_target=_ORIG,
    ),
    _row(
        "g7ai2026",
        "G7 AI 2026",
        "license",
        "model-license",
        "Model license",
        "BOM-G7AI2026-MDL-012",
        code="G7AI-MDL-012",
        kinds=_MODEL,
        spdx_target=_DECLARED,
    ),
    _row(
        "bsi2025",
        "BSI TR-03183-2",
        "name",
        "component-name",
        "Component name",
        "BOM-BSI2025-COMP-002",
        spdx_target=_NAME,
    ),
    _row(
        "bsi2025",
        "BSI TR-03183-2",
        "provider",
        "component-creator",
        "Component creator",
        "BOM-BSI2025-COMP-001",
        spdx_target=_ORIG,
    ),
    _row(
        "bsi2025",
        "BSI TR-03183-2",
        "license",
        "component-original-license",
        "Original licences",
        "BOM-BSI2025-COMP-014",
        obligation="ConditionalRequirement",
        spdx_target=_DECLARED,
    ),
    _row(
        "certin2025",
        "CERT-In 2025",
        "name",
        "component-name",
        "Component Name",
        "BOM-CERTIN2025-SBOM-001",
        kinds=_PACKAGE,
        spdx_target=_NAME,
    ),
    _row(
        "certin2025",
        "CERT-In 2025",
        "name",
        "model-name",
        "Model Name",
        "BOM-CERTIN2025-AIBOM-001",
        kinds=_MODEL,
        spdx_target=_NAME,
    ),
    _row(
        "certin2025",
        "CERT-In 2025",
        "provider",
        "model-developer",
        "Model Developer",
        "BOM-CERTIN2025-AIBOM-004",
        kinds=_MODEL,
        spdx_target=_ORIG,
    ),
    _row(
        "certin2025",
        "CERT-In 2025",
        "license",
        "component-license",
        "Component License",
        "BOM-CERTIN2025-SBOM-005",
        kinds=_PACKAGE,
        spdx_target=_DECLARED,
    ),
    _row(
        "certin2025",
        "CERT-In 2025",
        "license",
        "model-licensing-information",
        "Model Licensing Information",
        "BOM-CERTIN2025-AIBOM-005",
        kinds=_MODEL,
        spdx_target=_DECLARED,
    ),
    _row(
        "gpaimdf2025",
        "GPAI MDF 2025",
        "name",
        "model-name",
        "Model name",
        "BOM-GPAIMDF2025-004",
        code="MDF-004",
        kinds=_MODEL,
        spdx_target=_NAME,
    ),
    _row(
        "gpaimdf2025",
        "GPAI MDF 2025",
        "provider",
        "provider-legal-name",
        "Legal name for the model provider",
        "BOM-GPAIMDF2025-003",
        code="MDF-003",
        kinds=_MODEL,
        spdx_target=_ORIG,
    ),
    _row(
        "gpaimdf2025",
        "GPAI MDF 2025",
        "license",
        "licence-link",
        "License",
        "BOM-GPAIMDF2025-016",
        code="MDF-016",
        kinds=_MODEL,
        spdx_target=_DECLARED,
    ),
)

STANDARD_ELEMENTS: tuple[StandardElement, ...] = tuple(
    sorted(_ROWS, key=lambda r: (r.standard, r.element, r.element_id))
)


def elements_for(element: Element, kind: Kind) -> tuple[StandardElement, ...]:
    """Return the standard elements for a dataset element and subject kind."""
    return tuple(
        r for r in STANDARD_ELEMENTS if r.element == element and kind in r.kinds
    )


def release_metadata() -> dict[str, JsonValue]:
    """Return the crosswalk as written to ``standard-elements.json``."""
    rows: list[JsonValue] = []
    for r in STANDARD_ELEMENTS:
        d: dict[str, JsonValue] = {k: v for k, v in asdict(r).items() if k != "kinds"}
        d["kinds"] = [str(k) for k in sorted(r.kinds)]
        rows.append(d)
    return {
        "bom_release": BOM_RELEASE,
        "retrieved": BOM_RETRIEVED,
        "namespace": BOM_NAMESPACE,
        "elements": rows,
    }
