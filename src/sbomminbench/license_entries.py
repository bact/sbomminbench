# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Which licence entry of which declaration a licence string belongs to.

A *declaration* is one place a source lists licences: a Maven `<licenses>`
block (with its `parent[n].` prefix), or one observation (a list-valued field
or a single value). An *entry* is one licence inside it: a Maven `<license>`
(its name and url are one entry), one item of a list, or the whole value.
Shared by `sbomminbench.derive.license` and `sbomminbench.tags` so the two
cannot disagree about "several licences in one declaration".
"""

import re
from collections.abc import Mapping

from sbomminbench.schema import Observation

DICT_NAME_KEYS = ("text", "type", "name")
_MAVEN_ENTRY = re.compile(
    r"(?P<decl>(?:parent\[\d+\]\.)*licenses\.license)\[(?P<i>\d+)\]\.\w+"
)


def dict_name(d: Mapping[str, object]) -> str | None:
    """The first string among `text`, `type`, `name` of a licence object."""
    return next((v for k in DICT_NAME_KEYS if isinstance(v := d.get(k), str)), None)


def entry_ref(o: Observation, index: int | None = None) -> tuple[str, str]:
    """`(declaration, entry)` ids of a string stated by *o*.

    *index* is the position in a list-valued field (None for a scalar).
    """
    m = _MAVEN_ENTRY.fullmatch(o.field_path)
    if m is not None:
        decl = f"{o.source}:{m.group('decl')}"
        return decl, f"{decl}[{m.group('i')}]"
    return o.key, o.key if index is None else f"{o.key}#{index}"
