# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Plumbing shared by the archive ecosystems that read JSON or TOML (npm, cargo).

Failure convention for every ecosystem module (``Failure`` and ``EcoError``
are defined here and shared by pypi, huggingface, npm, cargo, maven, nuget):

* ``fetch`` raises ``EcoError(stage, reason)`` when layer 1 is unavailable or
  unusable (no package record can be written). A network failure after the
  retries stays an ``http.FetchFailed``, of which ``EcoError`` is a subclass.
* ``observe_with_failures`` returns ``(Observed, [Failure])``: a layer 2 file
  that cannot be used gives a ``Failure(stage, reason)`` and no rows, never an
  exception. ``observe`` returns the ``Observed`` alone.
* The build step catches ``EcoError`` and calls
  ``seeds.record_failure(stage=exc.stage, reason=exc.reason)``; each
  ``Failure`` of ``observe_with_failures`` is recorded the same way.

Fetch side: the layer 1 document, the size-capped archive download, and the
extraction of the manifests (and the model scan) into ``members/<sha256>``.
Observe side: loading the cached layer 1 document and parsing a member, where
a malformed file gives a ``Failure`` and no observations, never an exception.
"""

import hashlib
import logging
import math
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import unquote, urlsplit

from sbomminbench.archive import (
    ArchiveError,
    MemberTooLarge,
    extract_members,
    list_members,
)
from sbomminbench.eco.base import (
    CacheMiss,
    CacheStore,
    FetchIndex,
    ModelMemberModel,
    Observed,
    ResponseMeta,
    parse_json,
)
from sbomminbench.http import NEGATIVE_STATUS, FetchFailed, HttpClient, Response
from sbomminbench.models_scan import scan_archive
from sbomminbench.schema import Artifact, Observation, Platform
from sbomminbench.seeds import Seed
from sbomminbench.tags import ARTIFACT_TOO_LARGE, SCHEMA_TYPE_VIOLATION

LOG = logging.getLogger("sbomminbench")

ArtifactKind = Literal["npm_tarball", "crate"]
SkipReason = Literal["http_404", "http_error", "too_large", "archive_error"]


class Failure(NamedTuple):
    """A stage that failed on one package, for ``seeds.record_failure``."""

    stage: str
    reason: str


class EcoError(FetchFailed):
    """Layer 1 is unavailable or unusable (no package record)."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


# -- parsing ---------------------------------------------------------------------


def _toml_safe(value: Any) -> Any:
    """Turn TOML-only values (dates, non-finite floats) into JSON values."""
    if isinstance(value, dict):
        return {k: _toml_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_toml_safe(v) for v in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def parse_toml(data: bytes) -> dict[str, Any]:
    """Parse TOML bytes into JSON-compatible values.

    Raises:
        ValueError: not valid UTF-8 or TOML.
    """
    try:
        doc: dict[str, Any] = _toml_safe(tomllib.loads(data.decode("utf-8-sig")))
    except RecursionError as exc:
        raise ValueError("TOML nested too deeply") from exc
    return doc


def shallowest_member(names: Iterable[str], basename: str) -> str | None:
    """Return ``<top>/<basename>`` (exactly one ``/``), first by name, or ``None``."""
    found = sorted(
        n for n in names if n.count("/") == 1 and n.rsplit("/", 1)[1] == basename
    )
    return found[0] if found and found[0].split("/")[0] else None


# -- fetch -----------------------------------------------------------------------


def fetch_platform(http: HttpClient, url: str) -> tuple[Response, dict[str, Any]]:
    """GET the layer 1 JSON object.

    Raises:
        EcoError: network failure, 404/410, or a body that is not a JSON object.
    """
    try:
        resp = http.get(url)
    except FetchFailed as exc:
        raise EcoError("platform", "http_error") from exc
    if resp.body is None:
        raise EcoError("platform", f"http_{resp.status}")
    try:
        doc = parse_json(resp.body)
    except ValueError as exc:
        raise EcoError("platform", "malformed_json") from exc
    if not isinstance(doc, dict):
        raise EcoError("platform", "malformed_json")
    return resp, doc


def skipped_artifact(
    kind: ArtifactKind,
    url: str,
    reason: SkipReason,
    declared_digest: str | None,
) -> Artifact:
    """An artefact record for an archive that was not read."""
    return Artifact(
        kind=kind,
        url=url,
        filename=unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or None,
        declared_digest=declared_digest,
        fetched=False,
        skip_reason=reason,
    )


@dataclass(frozen=True)
class ArchiveFetch:
    """What reading one archive produced, ready for a ``FetchIndex``."""

    artifact: Artifact
    members_dir: str | None = None
    members: dict[str, str] = field(default_factory=dict)
    model_members: list[ModelMemberModel] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def fetch_archive(
    http: HttpClient,
    url: str,
    *,
    kind: ArtifactKind,
    declared_digest: str | None,
    max_bytes: int,
    manifests: Mapping[str, str],
) -> ArchiveFetch:
    """Download *url* (capped), extract the manifests and scan for models.

    *manifests* maps a role to the basename sought at depth one (one ``/``).
    A download or archive problem gives an unfetched ``Artifact``, never an
    exception: layer 1 stays usable. ``OfflineMiss`` is not caught.
    """
    try:
        dl = http.download(url, max_bytes=max_bytes)
    except FetchFailed as exc:
        LOG.warning("%s: download failed (%s)", kind, exc)
        return ArchiveFetch(skipped_artifact(kind, url, "http_error", declared_digest))
    if dl.too_large:
        art = skipped_artifact(kind, url, "too_large", declared_digest)
        return ArchiveFetch(art, tags=[ARTIFACT_TOO_LARGE])
    if dl.path is None or dl.sha256 is None:
        reason: SkipReason = (
            "http_404" if dl.status in NEGATIVE_STATUS else "http_error"
        )
        return ArchiveFetch(skipped_artifact(kind, url, reason, declared_digest))
    base = skipped_artifact(kind, url, "archive_error", declared_digest)
    try:
        listing = list_members(dl.path)
        models, tags = scan_archive(dl.path, listing)
        files = [m.name for m in listing if m.is_file]
        chosen = {
            role: name
            for role, basename in manifests.items()
            if (name := shallowest_member(files, basename)) is not None
        }
        members_dir = f"members/{dl.sha256}"
        members: dict[str, str] = {}
        for role, name in sorted(chosen.items()):
            members.update(_extract_one(http, dl.path, members_dir, role, name))
    except ArchiveError as exc:
        LOG.warning("%s: archive unreadable (%s)", kind, exc)
        art = base.model_copy(
            update={"size_bytes": dl.size, "sha256": dl.sha256},
        )
        return ArchiveFetch(art)
    art = base.model_copy(
        update={
            "size_bytes": dl.size,
            "sha256": dl.sha256,
            "fetched": True,
            "skip_reason": None,
        }
    )
    return ArchiveFetch(
        art,
        members_dir=members_dir if members else None,
        members=members,
        model_members=[ModelMemberModel.from_member(m) for m in models],
        tags=tags,
    )


def _extract_one(
    http: HttpClient, archive: Path, members_dir: str, role: str, name: str
) -> dict[str, str]:
    """Extract one member (<= 2 MiB); an oversize member is skipped with a warning."""
    try:
        done = extract_members(archive, [name], http.cache_dir / members_dir)
    except MemberTooLarge:
        LOG.warning("%s: member over the read cap; not extracted", role)
        return {}
    return {role: name} if name in done else {}


def new_index(
    seed: Seed,
    purl: str,
    platform_url: str,
    resp: Response,
    archive: ArchiveFetch | None,
) -> FetchIndex:
    """Assemble the ``FetchIndex`` of one fetch."""
    return FetchIndex(
        ecosystem=seed.eco,
        name=seed.name,
        version=seed.version,
        purl=purl,
        stratum=seed.stratum,
        rank=seed.rank,
        responses={"platform": platform_url},
        artifact=archive.artifact if archive else None,
        members_dir=archive.members_dir if archive else None,
        members=archive.members if archive else {},
        model_members=archive.model_members if archive else [],
        fetched_at=resp.fetched_at,
        tags=sorted(set(archive.tags)) if archive else [],
    )


# -- observe ---------------------------------------------------------------------


def load_platform(
    index: FetchIndex, store: CacheStore
) -> tuple[ResponseMeta, bytes, dict[str, Any]]:
    """Read the cached layer 1 JSON object of *index*.

    Raises:
        EcoError: not cached, no body, or not a JSON object.
    """
    url = index.responses.get("platform")
    if url is None:
        raise EcoError("platform", "no_platform_url")
    try:
        meta = store.meta(url)
    except (CacheMiss, ValueError) as exc:
        raise EcoError("platform", "cache_miss") from exc
    body = store.body(url)
    if body is None:
        raise EcoError("platform", f"http_{meta.status}")
    try:
        doc = parse_json(body)
    except ValueError as exc:
        raise EcoError("platform", "malformed_json") from exc
    if not isinstance(doc, dict):
        raise EcoError("platform", "malformed_json")
    return meta, body, doc


def load_member(
    index: FetchIndex,
    store: CacheStore,
    role: str,
    parse: Callable[[bytes], Any],
) -> tuple[dict[str, Any] | None, Failure | None]:
    """Parse the member recorded under *role*.

    No member gives ``(None, None)``; a file that does not parse, or whose root
    is not an object, gives ``(None, Failure(role, ...))``.
    """
    data = store.member(index, role)
    if data is None:
        return None, None
    try:
        doc = parse(data)
    except ValueError:
        return None, Failure(role, "malformed")
    if not isinstance(doc, dict):
        return None, Failure(role, "not_an_object")
    return doc, None


def sort_observations(rows: Iterable[Observation]) -> list[Observation]:
    """Sort by ``(source, field_path)``."""
    return sorted(rows, key=lambda o: (o.source, o.field_path))


def assemble(
    index: FetchIndex,
    meta: ResponseMeta,
    body: bytes,
    *,
    html_url: str,
    platform_obs: Iterable[Observation],
    manifest_obs: Iterable[Observation],
    manifest_files: Iterable[str],
    tags: Iterable[str],
    failures: list[Failure],
) -> tuple[Observed, list[Failure]]:
    """Build the ``Observed`` result (and pass the failures through)."""
    platform = Platform(
        ecosystem=index.ecosystem,
        source_url=index.responses["platform"],
        html_url=html_url,
        retrieved_at=meta.fetched_at,
        response_sha256=hashlib.sha256(body).hexdigest(),
        name=index.name,
        version=index.version,
        observations=sort_observations(platform_obs),
        artifacts=[index.artifact] if index.artifact else [],
    )
    all_tags = set(tags)
    if failures:
        all_tags.add(SCHEMA_TYPE_VIOLATION)
    observed = Observed(
        platform=platform,
        manifest=sort_observations(manifest_obs),
        manifest_files=sorted(manifest_files),
        tags=sorted(all_tags),
    )
    return observed, failures
