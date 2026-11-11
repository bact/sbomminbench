# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Ecosystem modules and their registry (``REGISTRY``: ecosystem -> module)."""

from typing import Protocol

from sbomminbench.eco import cargo, huggingface, maven, npm, nuget, pypi
from sbomminbench.eco._pkgfetch import Failure
from sbomminbench.eco.base import CacheStore, FetchIndex, Observed
from sbomminbench.http import HttpClient
from sbomminbench.schema import Ecosystem
from sbomminbench.seeds import Seed


class EcoModule(Protocol):
    """What every ecosystem module offers."""

    def fetch(self, seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
        """Network only: record one package; ``EcoError`` when layer 1 is lost."""
        ...

    def observe(self, index: FetchIndex, store: CacheStore) -> Observed:
        """Offline and pure: observations from the cache."""
        ...

    def observe_with_failures(
        self, index: FetchIndex, store: CacheStore
    ) -> tuple[Observed, list[Failure]]:
        """Like ``observe``, with the layer 2 files that could not be read."""
        ...


REGISTRY: dict[Ecosystem, EcoModule] = {
    "cargo": cargo,
    "huggingface": huggingface,
    "maven": maven,
    "npm": npm,
    "nuget": nuget,
    "pypi": pypi,
}

__all__ = ["REGISTRY", "EcoModule"]
