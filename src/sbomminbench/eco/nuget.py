# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""NuGet: fetch and observe the catalog entry and the nuspec.

Layer 1 is the catalog entry (``nuget_catalog``), reached through the
``registration5-gz-semver2`` leaf. The leaf and the catalog entry are
gzip-compressed on the server; ``requests`` inflates them (``Content-Encoding``),
and ``observe`` also inflates a body that was cached still compressed. Layer 2
is the nuspec from ``v3-flatcontainer`` (lower-case id and version in the URL).

The nupkg is never downloaded: the models come from ``packageEntries[].fullName``
and the digest is the catalog's ``packageHash`` as declared (base64 text).
Author strings are kept raw; splitting them belongs to the derive layer.
"""

import gzip
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
import zlib
from typing import Any, Final
from urllib.parse import urlsplit

from sbomminbench.eco._pkgfetch import EcoError, Failure
from sbomminbench.eco.base import (
    CacheMiss,
    CacheStore,
    FetchIndex,
    ModelMemberModel,
    Observed,
    XmlError,
    absent_obs,
    obs,
    obs_at,
    parse_json,
    strip_namespaces,
    xml_root,
)
from sbomminbench.http import HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.models_scan import scan_names
from sbomminbench.schema import Artifact, Observation, Platform, Role
from sbomminbench.seeds import Seed
from sbomminbench.tags import EMBEDDED_MODELS, NAME_CASE_DIFFERS, VERSION_NORMALIZED

LOG = logging.getLogger("sbomminbench")

API: Final = "https://api.nuget.org"
CATALOG: Final = "nuget_catalog"
NUSPEC: Final = "nuspec"
MAX_INFLATED = 8 * 1024 * 1024  # a catalog entry lists every file of the package
_VERSION = re.compile(r"(\d+(?:\.\d+)*)(-[0-9A-Za-z.-]+)?(\+.*)?")


def normalize_version(version: str) -> str:
    """The NuGet normalised version, lower case (also the URL form).

    Drops build metadata and a zero fourth part, and leading zeros of the
    numeric parts. A string that is not a NuGet version is only lower-cased.
    """
    m = _VERSION.fullmatch(version.strip())
    if m is None:
        return version.strip().lower()
    parts = [str(int(p)) for p in m.group(1).split(".")]
    while len(parts) < 3:
        parts.append("0")
    if len(parts) == 4 and parts[3] == "0":
        parts.pop()
    return (".".join(parts) + (m.group(2) or "")).lower()


def leaf_url(name: str, version: str) -> str:
    return (
        f"{API}/v3/registration5-gz-semver2/{name.lower()}/"
        f"{normalize_version(version)}.json"
    )


def nuspec_url(name: str, version: str) -> str:
    lid, lver = name.lower(), normalize_version(version)
    return f"{API}/v3-flatcontainer/{lid}/{lver}/{lid}.nuspec"


def _on_api(url: object) -> bool:
    """Is *url* an https URL on the NuGet API host (server text is untrusted)?"""
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "api.nuget.org"


def inflate(data: bytes) -> bytes:
    """Return *data* inflated when it is still gzip-compressed, else unchanged.

    Raises:
        ValueError: not valid gzip, or larger than ``MAX_INFLATED`` inflated.
    """
    if not data.startswith(b"\x1f\x8b"):
        return data
    try:
        out = zlib.decompressobj(31).decompress(data, MAX_INFLATED + 1)
    except (zlib.error, EOFError, gzip.BadGzipFile) as exc:
        raise ValueError("gzip body is corrupt") from exc
    if len(out) > MAX_INFLATED:
        raise ValueError("gzip body is too large")
    return out


def _json_object(body: bytes | None) -> dict[str, Any] | None:
    """Parse a (possibly still compressed) JSON object; ``None`` if not one."""
    if body is None:
        return None
    try:
        doc = parse_json(inflate(body))
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


# -- fetch -----------------------------------------------------------------------


def _get_object(http: HttpClient, url: str, stage: str) -> tuple[dict[str, Any], str]:
    resp = http.get(url)
    if resp.body is None:
        raise EcoError(stage, f"http_{resp.status}")
    doc = _json_object(resp.body)
    if doc is None:
        raise EcoError(stage, "malformed_json")
    return doc, resp.fetched_at


def _declared_digest(entry: dict[str, Any]) -> str | None:
    digest = entry.get("packageHash")
    if not isinstance(digest, str) or not digest:
        return None
    algo = entry.get("packageHashAlgorithm")
    return f"{algo.lower() if isinstance(algo, str) and algo else 'sha512'}:{digest}"


def _model_members(entry: dict[str, Any]) -> list[ModelMemberModel]:
    """Model files named in ``packageEntries`` (sizes as the catalog declares)."""
    listed = entry.get("packageEntries")
    files = [
        e
        for e in (listed if isinstance(listed, list) else [])
        if isinstance(e, dict) and isinstance(e.get("fullName"), str)
    ]
    sizes = {
        e["fullName"]: e["length"] for e in files if isinstance(e.get("length"), int)
    }
    return [
        ModelMemberModel.model_validate(
            {"path": name, "size": sizes.get(name, 0), "format": fmt}
        )
        for name, fmt in scan_names([e["fullName"] for e in files])
    ]


def fetch(seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
    """Fetch the leaf, the catalog entry and the nuspec of *seed* (network only).

    *max_bytes* bounds nothing here: no archive is downloaded.

    Raises:
        EcoError: the leaf or the catalog entry is missing or unusable.
        FetchFailed: the request failed after the retries.
    """
    leaf_at = leaf_url(seed.name, seed.version)
    leaf, _ = _get_object(http, leaf_at, "leaf")
    entry_at = leaf.get("catalogEntry")
    if not _on_api(entry_at):
        raise EcoError("leaf", "no_catalog_entry")
    entry, fetched_at = _get_object(http, str(entry_at), "catalog")
    version = entry.get("version")
    version = version if isinstance(version, str) and version else seed.version
    responses = {"leaf": leaf_at, "platform": str(entry_at)}
    spec_at = nuspec_url(seed.name, seed.version)
    if http.get(spec_at).body is not None:
        responses["nuspec"] = spec_at
    else:
        LOG.warning("nuget: no nuspec for %s %s", seed.name, seed.version)
    models = _model_members(entry)
    tags = [EMBEDDED_MODELS] if models else []
    if (
        entry.get("id") != seed.name
        and str(entry.get("id")).lower() == seed.name.lower()
    ):
        tags.append(NAME_CASE_DIFFERS)
    content = leaf.get("packageContent")
    return FetchIndex(
        ecosystem="nuget",
        name=seed.name,
        version=version,
        purl=make_purl("nuget", seed.name, version),
        stratum=seed.stratum,
        rank=seed.rank,
        responses=responses,
        artifact=Artifact(
            kind="nupkg",
            url=content if _on_api(content) else str(entry_at),
            filename=f"{seed.name.lower()}.{normalize_version(seed.version)}.nupkg",
            declared_digest=_declared_digest(entry),
            fetched=False,
            skip_reason="not_needed",
        ),
        members_dir=None,
        members={},
        model_members=models,
        fetched_at=fetched_at,
        tags=sorted(tags),
    )


# -- observe ---------------------------------------------------------------------


def _catalog_rows(entry: dict[str, Any]) -> list[Observation]:
    rows: list[Observation | None] = [
        obs_at("name", CATALOG, entry, "id"),
        obs_at("license", CATALOG, entry, "licenseExpression"),
        obs_at("license", CATALOG, entry, "licenseUrl", absent_row=False),
        obs_at("license", CATALOG, entry, "licenseFile", absent_row=False),
        obs_at("provider", CATALOG, entry, "authors", role="author"),
    ]
    return [r for r in rows if r is not None]


def _text(parent: ET.Element, tag: str) -> str | None:
    el = next((c for c in parent if c.tag == tag), None)
    return None if el is None else (el.text or "")


def _license_rows(metadata: ET.Element) -> list[Observation]:
    """Text and ``@type`` of ``<license>``; the form follows the type."""
    el = next((c for c in metadata if c.tag == "license"), None)
    if el is None:
        return [absent_obs("license", NUSPEC, "metadata.license")]
    kind = el.attrib.get("type")
    text = obs("license", NUSPEC, "metadata.license", el.text or "")
    rows = [text]
    if kind == "file" and isinstance(text.raw, str):
        text = text.model_copy(
            update={"semantic_format": "file_ref", "format_validity": "not_applicable"}
        )
        rows = [text]
    if kind is not None:
        # The qualifier is not a licence value: it carries no form of its own.
        type_row = obs("license", NUSPEC, "metadata.license@type", kind)
        rows.append(
            type_row.model_copy(
                update={"semantic_format": "empty", "format_validity": "not_applicable"}
            )
        )
    return rows


def _nuspec_rows(root: ET.Element) -> list[Observation]:
    metadata = next((c for c in root if c.tag == "metadata"), None)
    if metadata is None:
        raise XmlError("nuspec has no metadata")
    rows: list[Observation] = []
    ident = _text(metadata, "id")
    if ident is not None:
        rows.append(obs("name", NUSPEC, "metadata.id", ident))
    rows += _license_rows(metadata)
    url = _text(metadata, "licenseUrl")
    if url is not None:
        rows.append(obs("license", NUSPEC, "metadata.licenseUrl", url))
    people: tuple[tuple[str, Role], ...] = (("authors", "author"), ("owners", "owner"))
    for tag, role in people:
        value = _text(metadata, tag)
        path = f"metadata.{tag}"
        if value is not None:
            rows.append(obs("provider", NUSPEC, path, value, role=role))
        elif tag == "authors":
            rows.append(absent_obs("provider", NUSPEC, path, role="author"))
    return rows


def _parse_nuspec(data: bytes) -> ET.Element:
    root = strip_namespaces(xml_root(data))
    if root.tag != "package":
        raise XmlError("XML is not a nuspec")
    return root


def _nuspec_version(root: ET.Element) -> str | None:
    metadata = next((c for c in root if c.tag == "metadata"), None)
    return None if metadata is None else _text(metadata, "version")


def observe_with_failures(
    index: FetchIndex, store: CacheStore
) -> tuple[Observed, list[Failure]]:
    """Read the cached catalog entry and nuspec (offline, pure, deterministic).

    A nuspec that cannot be used gives a ``Failure`` and no ``nuspec`` rows.

    Raises:
        sbomminbench.eco.base.CacheMiss: the catalog entry is not cached.
        ValueError: the cached catalog entry is not a JSON object.
    """
    url = index.responses["platform"]
    meta = store.meta(url)
    body = store.body(url)
    entry = _json_object(body)
    if body is None or entry is None:
        raise CacheMiss(url)
    platform = Platform(
        ecosystem="nuget",
        source_url=url,
        html_url=f"https://www.nuget.org/packages/{index.name}/{index.version}",
        retrieved_at=meta.fetched_at,
        response_sha256=hashlib.sha256(body).hexdigest(),
        name=index.name,
        version=index.version,
        observations=sorted(_catalog_rows(entry), key=lambda o: o.field_path),
        artifacts=[index.artifact] if index.artifact else [],
    )
    manifest: list[Observation] = []
    files: list[str] = []
    failures: list[Failure] = []
    tags = set(index.tags)
    spec_url = index.responses.get("nuspec")
    data = store.body(spec_url) if spec_url else None
    if data is not None and spec_url is not None:
        try:
            root = _parse_nuspec(data)
            manifest = sorted(_nuspec_rows(root), key=lambda o: o.field_path)
        except XmlError:
            failures.append(Failure(NUSPEC, "xml_refused"))
        else:
            files.append(spec_url.rsplit("/", 1)[-1])
            if _nuspec_version(root) not in (None, index.version):
                tags.add(VERSION_NORMALIZED)
    elif spec_url is not None:
        failures.append(Failure(NUSPEC, "not_cached"))
    return Observed(platform, manifest, files, sorted(tags)), failures


def observe(index: FetchIndex, store: CacheStore) -> Observed:
    """``observe_with_failures`` without the failures."""
    return observe_with_failures(index, store)[0]
