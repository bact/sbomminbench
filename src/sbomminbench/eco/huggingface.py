# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Hugging Face Hub: ``fetch`` (network) and ``observe`` (offline) for a revision.

One response does for both layers: ``/api/models/{id}/revision/{sha}`` is layer 1
(``hf_api``: ``id``, ``author``, ``license:*`` tags) and its ``cardData`` is
layer 2 (``hf_card``). The request never carries a token, and gated models
still answer. No file is downloaded: the repository is an ``hf_repo`` artefact
that is ``not_needed``, and its model files are listed from ``siblings``.
"""

from typing import Any, Final
from urllib.parse import quote

from sbomminbench.eco._pkgfetch import (
    EcoError,
    Failure,
    assemble,
    fetch_platform,
    load_platform,
)
from sbomminbench.eco.base import (
    CacheStore,
    FetchIndex,
    Observed,
    obs,
    obs_at,
)
from sbomminbench.http import HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.models_scan import scan_names
from sbomminbench.schema import MAX_FILES, Artifact, Observation
from sbomminbench.seeds import Seed
from sbomminbench.tags import (
    HF_GATED,
    HF_LICENSE_OTHER,
    HF_RENAMED,
)

API_URL: Final = "https://huggingface.co/api/models/{id}/revision/{sha}"
HUB_URL: Final = "https://huggingface.co/{id}"
TREE_URL: Final = "https://huggingface.co/{id}/tree/{sha}"
LICENSE_TAG: Final = "license:"
_OTHER: Final = "other"


def api_url(model_id: str, sha: str) -> str:
    """The revision-pinned API URL; the ``owner/name`` slash stays literal."""
    return API_URL.format(id=_quote_id(model_id), sha=_quote_id(sha.lower()))


def _quote_id(value: str) -> str:
    return quote(value, safe="/")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def license_other(doc: dict[str, Any]) -> bool:
    """Whether the licence is the Hub's vague ``other`` (tag or card key)."""
    card = doc.get("cardData")
    declared = _strings(card.get("license")) if isinstance(card, dict) else []
    return f"{LICENSE_TAG}{_OTHER}" in _strings(doc.get("tags")) or _OTHER in declared


def _model_files(doc: dict[str, Any]) -> list[str]:
    """Names of the files listed in ``siblings``; items of the wrong shape skipped."""
    siblings = doc.get("siblings")
    names: list[str] = []
    for item in siblings if isinstance(siblings, list) else []:
        name = item.get("rfilename") if isinstance(item, dict) else None
        if isinstance(name, str):
            names.append(name)
    return [name for name, _ in scan_names(names)]


def _fetch_tags(doc: dict[str, Any], seed_name: str, model_id: str) -> list[str]:
    tags: set[str] = set()
    if model_id != seed_name:
        tags.add(HF_RENAMED)
    if "gated" in doc and doc["gated"] is not False:
        tags.add(HF_GATED)
    if license_other(doc):
        tags.add(HF_LICENSE_OTHER)
    return sorted(tags)


def fetch(seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
    """Fetch one pinned revision (network only; *max_bytes* is unused).

    The Hub id in the response replaces the seed name when they differ
    (tag ``hf_renamed``). A ``gated`` value other than ``false`` gives tag
    ``hf_gated``; a missing key gives none.

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: the API response is unavailable
            (network error, 404/410, or not a JSON object), or neither its
            ``id`` nor the seed name has the form ``owner/name``.
    """
    sha = seed.version.lower()
    url = api_url(seed.name, sha)
    resp, doc = fetch_platform(http, url)
    reported = doc.get("id")
    model_id = reported if isinstance(reported, str) and reported else seed.name
    try:
        purl = make_purl("huggingface", model_id, sha)
    except ValueError as exc:
        raise EcoError("platform", "bad_model_id") from exc
    files = _model_files(doc)
    return FetchIndex(
        ecosystem="huggingface",
        name=model_id,
        version=sha,
        purl=purl,
        stratum=seed.stratum,
        rank=seed.rank,
        responses={"platform": url},
        artifact=Artifact(
            kind="hf_repo",
            url=TREE_URL.format(id=_quote_id(model_id), sha=sha),
            fetched=False,
            skip_reason="not_needed",
        ),
        members_dir=None,
        members={},
        files=files[:MAX_FILES],
        files_count=len(files),
        fetched_at=resp.fetched_at,
        tags=_fetch_tags(doc, seed.name, model_id),
    )


# -- observe -------------------------------------------------------------------------


def _api_rows(doc: dict[str, Any]) -> list[Observation]:
    rows = [
        r
        for r in (
            obs_at("name", "hf_api", doc, "id", absent_row=False),
            obs_at("provider", "hf_api", doc, "author", role="owner"),
        )
        if r is not None
    ]
    tags = doc.get("tags")
    for i, tag in enumerate(tags if isinstance(tags, list) else []):
        if isinstance(tag, str) and tag.startswith(LICENSE_TAG):
            rows.append(obs("license", "hf_api", f"tags[{i}]", tag))
    return rows


def _card_rows(doc: dict[str, Any]) -> list[Observation]:
    paths = (
        ("cardData.license", True),
        ("cardData.license_name", False),
        ("cardData.license_link", False),
    )
    return [
        row
        for path, absent in paths
        if (row := obs_at("license", "hf_card", doc, path, absent_row=absent))
    ]


def observe_with_failures(
    index: FetchIndex, store: CacheStore
) -> tuple[Observed, list[Failure]]:
    """Like ``observe``; Hub data has no second document, so no failures arise.

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: the cached API response is missing
            or malformed.
    """
    meta, body, doc = load_platform(index, store)
    platform_rows = _api_rows(doc)
    card_rows = _card_rows(doc)
    tags = set(index.tags)
    if license_other(doc):
        tags.add(HF_LICENSE_OTHER)
    return assemble(
        index,
        meta,
        body,
        html_url=HUB_URL.format(id=_quote_id(index.name)),
        platform_obs=platform_rows,
        manifest_obs=card_rows,
        manifest_files=["hf_card"] if isinstance(doc.get("cardData"), dict) else [],
        tags=tags,
        failures=[],
    )


def observe(index: FetchIndex, store: CacheStore) -> Observed:
    """Turn the cached API response of *index* into observations.

    Offline and deterministic.

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: the cached API response is missing
            or malformed.
    """
    return observe_with_failures(index, store)[0]


__all__ = [
    "EcoError",
    "api_url",
    "fetch",
    "license_other",
    "observe",
    "observe_with_failures",
]
