# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""cargo: ``fetch`` (network) and ``observe`` (offline) for one crate version.

Layer 1 is the crates.io version document (``crates_api``); layer 2 is
``Cargo.toml`` and ``Cargo.toml.orig`` from the ``.crate`` file
(``Cargo.toml``, ``Cargo.toml.orig``). Spec 04.
"""

from typing import Any, Literal
from urllib.parse import quote

from sbomminbench.eco._pkgfetch import (
    LOG,
    EcoError,
    Failure,
    assemble,
    fetch_archive,
    fetch_platform,
    load_member,
    load_platform,
    new_index,
    parse_toml,
)
from sbomminbench.eco.base import CacheStore, FetchIndex, Observed, obs_at
from sbomminbench.http import HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.schema import Observation, Source
from sbomminbench.seeds import Seed
from sbomminbench.tags import WORKSPACE_INHERITED

API = "https://crates.io/api/v1/crates"
STATIC = "https://static.crates.io/crates"
MANIFESTS: tuple[Literal["Cargo.toml", "Cargo.toml.orig"], ...] = (
    "Cargo.toml",
    "Cargo.toml.orig",
)


def api_url(name: str, version: str) -> str:
    """Return the crates.io version document URL."""
    return f"{API}/{quote(name, safe='')}/{quote(version, safe='')}"


def crate_url(name: str, version: str) -> str:
    """Return the static download URL of the ``.crate`` file."""
    n, v = quote(name, safe=""), quote(version, safe="")
    return f"{STATIC}/{n}/{n}-{v}.crate"


def html_url(name: str, version: str) -> str:
    """Return the crates.io page of the version."""
    return f"https://crates.io/crates/{quote(name, safe='')}/{quote(version, safe='')}"


def _checksum(doc: dict[str, Any]) -> str | None:
    version = doc.get("version")
    checksum = version.get("checksum") if isinstance(version, dict) else None
    return f"sha256:{checksum}" if isinstance(checksum, str) and checksum else None


def fetch(seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
    """Fetch the crates.io document and the ``.crate`` of *seed* (network only).

    Raises:
        EcoError: the API document is unavailable, not a JSON object, or has
            no ``version`` object.
    """
    url = api_url(seed.name, seed.version)
    resp, doc = fetch_platform(http, url)
    if not isinstance(doc.get("version"), dict):
        raise EcoError("platform", "malformed_json")
    declared = _checksum(doc)
    archive = fetch_archive(
        http,
        crate_url(seed.name, seed.version),
        kind="crate",
        declared_digest=declared,
        max_bytes=max_bytes,
        manifests={m: m for m in MANIFESTS},
    )
    art = archive.artifact
    if art.sha256 and declared and declared != f"sha256:{art.sha256}":
        LOG.warning("crate: measured sha256 differs from the declared checksum")
    purl = make_purl("cargo", seed.name, seed.version)
    return new_index(seed, purl, url, resp, archive)


def _observe_api(doc: dict[str, Any]) -> list[Observation]:
    rows = [
        obs_at("name", "crates_api", doc, "version.crate", absent_row=False),
        obs_at("license", "crates_api", doc, "version.license"),
        obs_at(
            "provider",
            "crates_api",
            doc,
            "version.published_by",
            role="publisher",
            absent_row=False,
        ),
    ]
    return [r for r in rows if r is not None]


def _observe_toml(source: Source, doc: dict[str, Any]) -> list[Observation]:
    absent = source == "Cargo.toml"
    rows = [
        obs_at("name", source, doc, "package.name", absent_row=False),
        obs_at("license", source, doc, "package.license", absent_row=absent),
        obs_at("license", source, doc, "package.license-file", absent_row=False),
        obs_at(
            "provider",
            source,
            doc,
            "package.authors",
            role="author",
            absent_row=absent,
        ),
    ]
    return [r for r in rows if r is not None]


def _inherits(rows: list[Observation]) -> bool:
    return any(isinstance(o.raw, dict) and o.raw.get("workspace") is True for o in rows)


def observe_with_failures(
    index: FetchIndex, store: CacheStore
) -> tuple[Observed, list[Failure]]:
    """Like ``observe``, also returning the members that could not be read.

    Raises:
        EcoError: the cached API document is missing or malformed.
    """
    meta, body, doc = load_platform(index, store)
    if not isinstance(doc.get("version"), dict):
        raise EcoError("platform", "malformed_json")
    manifest_obs: list[Observation] = []
    files: list[str] = []
    failures: list[Failure] = []
    for role in MANIFESTS:
        parsed, failure = load_member(index, store, role, parse_toml)
        if failure:
            failures.append(failure)
        if parsed is not None:
            manifest_obs += _observe_toml(role, parsed)
            files.append(index.members[role])
    tags = [WORKSPACE_INHERITED] if _inherits(manifest_obs) else []
    return assemble(
        index,
        meta,
        body,
        html_url=html_url(index.name, index.version),
        platform_obs=_observe_api(doc),
        manifest_obs=manifest_obs,
        manifest_files=files,
        tags=tags,
        failures=failures,
    )


def observe(index: FetchIndex, store: CacheStore) -> Observed:
    """Turn the cached responses and members of *index* into observations.

    Offline and deterministic. A malformed manifest gives no rows for that
    file (use ``observe_with_failures`` to learn why).

    Raises:
        EcoError: the cached API document is missing or malformed.
    """
    return observe_with_failures(index, store)[0]


__all__ = [
    "EcoError",
    "api_url",
    "crate_url",
    "fetch",
    "html_url",
    "observe",
    "observe_with_failures",
]
