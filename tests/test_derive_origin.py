# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

from typing import get_args

from sbomminbench.derive import license as L
from sbomminbench.derive.origin import ORIGIN
from sbomminbench.schema import Source


def test_origin_covers_every_source() -> None:
    assert set(ORIGIN) == set(get_args(Source))


def test_copies_share_an_origin() -> None:
    assert ORIGIN["pypi_json"] == ORIGIN["METADATA"] == ORIGIN["PKG-INFO"]
    assert ORIGIN["nuget_catalog"] == ORIGIN["nuspec"]
    assert ORIGIN["pyproject.toml"] != ORIGIN["METADATA"]
    assert L.ORIGIN is ORIGIN
