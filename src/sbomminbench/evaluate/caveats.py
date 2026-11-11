# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Caveats of the per-standard view, one line per row.

The view relabels element scores; it does not measure compliance. Data only: no
import of anything that needs ``pitloom`` or ``licenseid``.
"""

from typing import Final

NOT_COMPLIANCE: Final = "relabelled element scores, not compliance"

_HUB_OWNER = "model provider is the hub owner, not evidence of the producer"
_SUPPLIER = (
    "supplier is scored against authors; NTIA and FSCT conflate creator and distributor"
)

# element id -> caveat for every row that has one
_ROW: Final[dict[str, str]] = {
    "ntia2021:supplier": _SUPPLIER,
    "fsct2024:supplier": _SUPPLIER,
    "fsct2024:concluded-license": (
        "concluded licence is scored against the declared licence"
    ),
    "cisa2026:component-producer": (
        "CISA asks for one producer; a list of authors is scored"
    ),
    "bsi2025:component-creator": "BSI asks for an e-mail address; scored on the name only",
    "gpaimdf2025:provider-legal-name": (
        "asks for a legal name; a declared author string is scored"
    ),
}


def caveat(element_id: str, element: str, kinds: frozenset[str]) -> str:
    """The caveat of one standard row, ``""`` when it has none."""
    parts = [_ROW.get(element_id, "")]
    if element == "provider" and "model" in kinds:
        parts.append(_HUB_OWNER)
    return "; ".join(p for p in parts if p)
