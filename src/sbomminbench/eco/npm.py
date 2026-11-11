# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""npm: ``fetch`` (network) and ``observe`` (offline) for one package version.

Layer 1 is the registry version document (``npm_registry``); layer 2 is the
shallowest ``<top>/package.json`` of the tarball (``package.json``). Spec 04.
"""

from typing import Any, Literal
from urllib.parse import quote, urlsplit

from sbomminbench.eco._pkgfetch import (
    LOG,
    ArchiveFetch,
    EcoError,
    Failure,
    assemble,
    fetch_archive,
    fetch_platform,
    load_member,
    load_platform,
    new_index,
    skipped_artifact,
)
from sbomminbench.eco.base import (
    CacheStore,
    FetchIndex,
    Observed,
    obs_at,
    parse_json,
)
from sbomminbench.http import HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.schema import Element, Observation, Role, Source
from sbomminbench.seeds import Seed

REGISTRY_HOST = "registry.npmjs.org"
MANIFEST: Literal["package.json"] = "package.json"

# (field, element, role); a field absent from the document gets an absent row
# only when listed in _ABSENT_ROWS.
_FIELDS: tuple[tuple[str, Element, Role | None], ...] = (
    ("name", "name", None),
    ("license", "license", None),
    ("licenses", "license", None),
    ("author", "provider", "author"),
    ("contributors", "provider", "contributor"),
    ("maintainers", "provider", "maintainer"),
)
_ABSENT_ROWS = frozenset({"license", "author"})


def registry_url(name: str, version: str) -> str:
    """Return the version document URL; ``@scope/name`` becomes ``@scope%2Fname``."""
    return f"https://{REGISTRY_HOST}/{quote(name, safe='@')}/{quote(version, safe='')}"


def html_url(name: str, version: str) -> str:
    """Return the npmjs.com page of the version."""
    return f"https://www.npmjs.com/package/{name}/v/{quote(version, safe='')}"


def _is_registry_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == REGISTRY_HOST


def _dist(doc: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(tarball URL, integrity)``; each is ``None`` unless a string."""
    dist = doc.get("dist")
    if not isinstance(dist, dict):
        return None, None
    tarball, integrity = dist.get("tarball"), dist.get("integrity")
    return (
        tarball if isinstance(tarball, str) and tarball else None,
        integrity if isinstance(integrity, str) and integrity else None,
    )


def fetch(seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
    """Fetch the registry document and the tarball of *seed* (network only).

    Raises:
        EcoError: the registry document is unavailable or not a JSON object.
    """
    url = registry_url(seed.name, seed.version)
    resp, doc = fetch_platform(http, url)
    tarball, integrity = _dist(doc)
    archive: ArchiveFetch | None = None
    if tarball is not None and _is_registry_url(tarball):
        archive = fetch_archive(
            http,
            tarball,
            kind="npm_tarball",
            declared_digest=integrity,
            max_bytes=max_bytes,
            manifests={MANIFEST: MANIFEST},
        )
    elif tarball is not None:
        LOG.warning("npm_tarball: URL is not on %s; not downloaded", REGISTRY_HOST)
        art = skipped_artifact("npm_tarball", tarball, "http_error", integrity)
        archive = ArchiveFetch(art)
    purl = make_purl("npm", seed.name, seed.version)
    return new_index(seed, purl, url, resp, archive)


def _observe_doc(
    source: Source, doc: dict[str, Any], *, registry: bool
) -> list[Observation]:
    rows: list[Observation] = []
    for field_name, element, role in _FIELDS:
        row = obs_at(
            element,
            source,
            doc,
            field_name,
            role=role,
            absent_row=field_name in _ABSENT_ROWS,
        )
        if row is not None:
            rows.append(row)
    if registry:
        row = obs_at(
            "provider", source, doc, "_npmUser", role="publisher", absent_row=False
        )
        if row is not None:
            rows.append(row)
    return rows


def observe_with_failures(
    index: FetchIndex, store: CacheStore
) -> tuple[Observed, list[Failure]]:
    """Like ``observe``, also returning the members that could not be read.

    Raises:
        EcoError: the cached registry document is missing or malformed.
    """
    meta, body, doc = load_platform(index, store)
    manifest, failure = load_member(index, store, MANIFEST, parse_json)
    manifest_obs: list[Observation] = []
    files: list[str] = []
    if manifest is not None:
        manifest_obs = _observe_doc(MANIFEST, manifest, registry=False)
        files = [index.members[MANIFEST]]
    return assemble(
        index,
        meta,
        body,
        html_url=html_url(index.name, index.version),
        platform_obs=_observe_doc("npm_registry", doc, registry=True),
        manifest_obs=manifest_obs,
        manifest_files=files,
        tags=(),
        failures=[failure] if failure else [],
    )


def observe(index: FetchIndex, store: CacheStore) -> Observed:
    """Turn the cached responses and members of *index* into observations.

    Offline and deterministic. A malformed ``package.json`` gives no layer 2
    rows (use ``observe_with_failures`` to learn why).

    Raises:
        EcoError: the cached registry document is missing or malformed.
    """
    return observe_with_failures(index, store)[0]


__all__ = [
    "EcoError",
    "fetch",
    "html_url",
    "observe",
    "observe_with_failures",
    "registry_url",
]
