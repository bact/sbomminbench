# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""PyPI: ``fetch`` (network) and ``observe`` (offline) for one pinned release.

Layer 1 is ``https://pypi.org/pypi/{name}/{version}/json`` (``pypi_json``).
Layer 2 is one of ``PKG-INFO`` (sdist) / ``METADATA`` (wheel or the PEP 658
``<wheel-url>.metadata``) plus, for an sdist, ``pyproject.toml``.

Artefact choice: the smallest sdist within the cap; else the smallest
``py3-none-any`` wheel within the cap; else the smallest wheel within the cap;
else only the PEP 658 metadata of a wheel (``wheel_metadata``, tag
``artifact_too_large``). The caps are checked on the declared size to choose
and again on the bytes read by the download.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import quote

from sbomminbench.archive import (
    ArchiveError,
    Member,
    MemberTooLarge,
    extract_members,
    list_members,
)
from sbomminbench.eco._pkgfetch import (
    EcoError,
    Failure,
    assemble,
    fetch_platform,
    load_platform,
    parse_toml,
)
from sbomminbench.eco.base import (
    CacheStore,
    FetchIndex,
    ModelMemberModel,
    Observed,
    absent_obs,
    get_path,
    obs,
    obs_at,
    parse_core_metadata,
)
from sbomminbench.http import (
    NEGATIVE_STATUS,
    Download,
    FetchFailed,
    HttpClient,
)
from sbomminbench.ids import make_purl
from sbomminbench.models_scan import scan_archive
from sbomminbench.schema import Artifact, Element, JsonValue, Observation, Role, Source
from sbomminbench.seeds import Seed
from sbomminbench.tags import (
    ARTIFACT_TOO_LARGE,
    DYNAMIC_FIELD,
    EMBEDDED_MODELS,
)

LOG = logging.getLogger("sbomminbench")

JSON_URL: Final = "https://pypi.org/pypi/{name}/{version}/json"
HTML_URL: Final = "https://pypi.org/project/{name}/{version}/"
WHEEL_METADATA: Final = "wheel_metadata"
TROVE_LICENSE: Final = "License ::"
_UNIVERSAL_WHEEL: Final = "-py3-none-any.whl"
_FILE_KINDS: Final[dict[str, Literal["sdist", "wheel"]]] = {
    "sdist": "sdist",
    "bdist_wheel": "wheel",
}
ArtifactSkip = Literal["too_large", "http_404", "http_error", "archive_error"]


# -- fetch -------------------------------------------------------------------------


@dataclass(frozen=True)
class _File:
    """One distribution file listed in the ``urls`` array of the release JSON."""

    kind: Literal["sdist", "wheel"]
    url: str
    filename: str
    size: int | None
    sha256: str | None

    @property
    def declared_digest(self) -> str | None:
        return f"sha256:{self.sha256}" if self.sha256 else None

    @property
    def universal(self) -> bool:
        return self.filename.endswith(_UNIVERSAL_WHEEL)

    def order(self) -> tuple[bool, int, str]:
        """Smallest first; an unknown size sorts last; filename breaks ties."""
        return (self.size is None, self.size or 0, self.filename)


def _release_files(doc: dict[str, Any]) -> list[_File]:
    """Read the ``urls`` array defensively; items of the wrong shape are skipped."""
    urls = doc.get("urls")
    files: list[_File] = []
    for item in urls if isinstance(urls, list) else []:
        if not isinstance(item, dict):
            continue
        ptype, url, name = (
            item.get("packagetype"),
            item.get("url"),
            item.get("filename"),
        )
        kind = _FILE_KINDS.get(ptype) if isinstance(ptype, str) else None
        if kind is None or not isinstance(url, str) or not isinstance(name, str):
            continue
        size, digests = item.get("size"), item.get("digests")
        sha = digests.get("sha256") if isinstance(digests, dict) else None
        files.append(
            _File(
                kind=kind,
                url=url,
                filename=name,
                size=size
                if isinstance(size, int) and not isinstance(size, bool)
                else None,
                sha256=sha if isinstance(sha, str) else None,
            )
        )
    return files


def _wheel_order(wheel: _File) -> tuple[bool, bool, int, str]:
    return (not wheel.universal, *wheel.order())


def _candidates(files: list[_File], max_bytes: int) -> list[_File]:
    """Files to try, best first: sdists, universal wheels, other wheels."""
    fit = [f for f in files if f.size is None or f.size <= max_bytes]
    sdists = sorted((f for f in fit if f.kind == "sdist"), key=_File.order)
    wheels = sorted((f for f in fit if f.kind == "wheel"), key=_wheel_order)
    return [*sdists, *wheels]


def _artifact(
    f: _File,
    *,
    kind: Literal["sdist", "wheel", "wheel_metadata"] | None = None,
    dl: Download | None = None,
    skip: ArtifactSkip | None = None,
) -> Artifact:
    """Artifact record; *dl* (a finished download) supplies the measured fields."""
    return Artifact(
        kind=kind or f.kind,
        url=f.url,
        filename=f.filename,
        size_bytes=dl.size if dl is not None else f.size,
        sha256=dl.sha256 if dl is not None else None,
        declared_digest=f.declared_digest,
        fetched=skip is None,
        skip_reason=skip,
    )


@dataclass
class _Acquired:
    """What choosing and reading the artefact produced."""

    artifact: Artifact | None = None
    responses: dict[str, str] = field(default_factory=dict)
    members_dir: str | None = None
    members: dict[str, str] = field(default_factory=dict)
    model_members: list[ModelMemberModel] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)


def _metadata_roles(kind: str, members: Iterable[Member]) -> dict[str, str]:
    """Pick the layer 2 files at depth one: role -> member path.

    An sdist gives ``<top>/PKG-INFO`` and ``<top>/pyproject.toml`` (one ``top``,
    the first by name); a wheel gives ``<name>.dist-info/METADATA``.
    """
    wanted = {"PKG-INFO", "pyproject.toml"} if kind == "sdist" else {"METADATA"}
    found: dict[str, list[str]] = {}
    for m in members:
        parts = m.name.split("/")
        if not m.is_file or len(parts) != 2 or parts[1] not in wanted:
            continue
        if kind == "wheel" and not parts[0].endswith(".dist-info"):
            continue
        found.setdefault(parts[1], []).append(m.name)
    firsts = {role: min(names) for role, names in found.items()}
    top = min((n.split("/")[0] for n in firsts.values()), default="")
    return {r: n for r, n in sorted(firsts.items()) if n.split("/")[0] == top}


def _extract(path: Path, dest: Path, roles: dict[str, str]) -> dict[str, str]:
    """Extract the chosen members (each <= 2 MiB); a failure skips that member."""
    out: dict[str, str] = {}
    for role, name in roles.items():
        try:
            done = extract_members(path, [name], dest)
        except MemberTooLarge:
            LOG.warning("pypi: %s is over the member read cap; not extracted", role)
            continue
        if name in done:
            out[role] = name
    return out


def _read_archive(f: _File, dl: Download, cache_dir: Path) -> _Acquired:
    """List, model-scan and extract the metadata members of a downloaded file."""
    if dl.path is None or dl.sha256 is None:
        return _Acquired(_artifact(f, skip="http_error"))
    try:
        listing = list_members(dl.path)
        models, scan_tags = scan_archive(dl.path, listing)
        members_dir = f"members/{dl.sha256}"
        members = _extract(
            dl.path, cache_dir / members_dir, _metadata_roles(f.kind, listing)
        )
    except ArchiveError as exc:
        LOG.warning("pypi: %s: archive unreadable (%s)", f.kind, exc)
        return _Acquired(
            _artifact(f, dl=dl, skip="archive_error"),
        )
    tags = set(scan_tags) | ({EMBEDDED_MODELS} if models else set())
    return _Acquired(
        _artifact(f, dl=dl),
        members_dir=members_dir if members else None,
        members=members,
        model_members=[ModelMemberModel.from_member(m) for m in models],
        tags=tags,
    )


def _try_download(
    http: HttpClient, f: _File, max_bytes: int
) -> tuple[Download | None, ArtifactSkip | None]:
    """Download *f*; on failure return the skip reason instead."""
    try:
        dl = http.download(f.url, max_bytes=max_bytes)
    except FetchFailed as exc:
        LOG.warning("pypi: download failed (%s)", exc)
        return None, "http_error"
    if dl.too_large:
        return None, "too_large"
    if dl.path is None:
        return None, "http_404" if dl.status in NEGATIVE_STATUS else "http_error"
    return dl, None


def _wheel_metadata(wheel: _File, http: HttpClient) -> _Acquired:
    """No wheel fits the cap: take only the PEP 658 ``<wheel-url>.metadata``."""
    url = f"{wheel.url}.metadata"
    acquired = _Acquired(
        _artifact(wheel, kind=WHEEL_METADATA, skip="too_large"),
        tags={ARTIFACT_TOO_LARGE},
    )
    try:
        resp = http.get(url)
    except FetchFailed as exc:
        LOG.warning("pypi: wheel metadata not fetched (%s)", exc)
        return acquired
    if resp.body is not None:
        acquired.responses[WHEEL_METADATA] = url
    return acquired


def _fallback(
    files: list[_File], failure: tuple[_File, ArtifactSkip] | None, http: HttpClient
) -> _Acquired:
    """Nothing could be read: PEP 658 metadata, an unfetched record, or nothing."""
    reason: ArtifactSkip = failure[1] if failure else "too_large"
    wheels = sorted((f for f in files if f.kind == "wheel"), key=_wheel_order)
    if wheels and reason == "too_large":
        return _wheel_metadata(wheels[0], http)
    target = failure[0] if failure else min(files, key=_File.order, default=None)
    if target is None:
        return _Acquired()
    tags = {ARTIFACT_TOO_LARGE} if reason == "too_large" else set()
    return _Acquired(_artifact(target, skip=reason), tags=tags)


def _acquire(files: list[_File], http: HttpClient, max_bytes: int) -> _Acquired:
    """Apply the artefact rules; the first candidate that downloads wins."""
    failure: tuple[_File, ArtifactSkip] | None = None
    for cand in _candidates(files, max_bytes):
        dl, skip = _try_download(http, cand, max_bytes)
        if dl is not None:
            return _read_archive(cand, dl, http.cache_dir)
        if failure is None and skip is not None:
            failure = (cand, skip)
    return _fallback(files, failure, http)


def release_url(name: str, version: str) -> str:
    """The layer 1 URL of a release (``name`` as seeded, percent-encoded)."""
    return JSON_URL.format(name=quote(name, safe=""), version=quote(version, safe=""))


def fetch(seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
    """Fetch one pinned release (network only) and record what was read.

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: the release JSON is unavailable
            (network error, 404/410, or not a JSON object).
    """
    url = release_url(seed.name, seed.version)
    resp, doc = fetch_platform(http, url)
    got = _acquire(_release_files(doc), http, max_bytes)
    return FetchIndex(
        ecosystem="pypi",
        name=seed.name,
        version=seed.version,
        purl=make_purl("pypi", seed.name, seed.version),
        stratum=seed.stratum,
        rank=seed.rank,
        responses={"platform": url, **got.responses},
        artifact=got.artifact,
        members_dir=got.members_dir,
        members=got.members,
        model_members=got.model_members,
        fetched_at=resp.fetched_at,
        tags=sorted(got.tags),
    )


# -- observe -----------------------------------------------------------------------


def _is_license_classifier(value: object) -> bool:
    return isinstance(value, str) and value.startswith(TROVE_LICENSE)


def _classifier_rows(source: Source, values: object, prefix: str) -> list[Observation]:
    """Licence classifiers only; the index is the position in the full list."""
    if not isinstance(values, list):
        return []
    return [
        obs("license", source, f"{prefix}[{i}]", v)
        for i, v in enumerate(values)
        if _is_license_classifier(v)
    ]


_JSON_ROWS: Final[
    tuple[tuple[Element, str, Role | None, bool], ...]
] = (  # (element, path, role, absent row when the key is missing)
    ("name", "info.name", None, False),
    ("license", "info.license_expression", None, True),
    ("license", "info.license", None, True),
    ("provider", "info.author", "author", True),
    ("provider", "info.author_email", "author", True),
    ("provider", "info.maintainer", "maintainer", False),
    ("provider", "info.maintainer_email", "maintainer", False),
)


def _json_rows(doc: dict[str, Any]) -> list[Observation]:
    rows = [
        row
        for element, path, role, absent in _JSON_ROWS
        if (
            row := obs_at(element, "pypi_json", doc, path, role=role, absent_row=absent)
        )
    ]
    _, classifiers = get_path(doc, "info.classifiers")
    return [*rows, *_classifier_rows("pypi_json", classifiers, "info.classifiers")]


_HEADER_ROWS: Final[tuple[tuple[Element, str, Role | None, bool], ...]] = (
    ("name", "Name", None, False),
    ("license", "License-Expression", None, True),
    ("license", "License", None, True),
    ("provider", "Author", "author", True),
    ("provider", "Author-email", "author", True),
    ("provider", "Maintainer", "maintainer", False),
    ("provider", "Maintainer-email", "maintainer", False),
)


def _header_value(headers: list[str]) -> JsonValue:
    """One header is its string; a repeated header is the list of its values."""
    return headers[0] if len(headers) == 1 else list(headers)


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _core_rows(source: Source, data: bytes) -> list[Observation] | None:
    """Rows of ``PKG-INFO`` / ``METADATA``; ``None`` when it is not core metadata."""
    text = _decode(data)
    if text is None:
        return None
    msg = parse_core_metadata(text)
    if "Metadata-Version" not in msg and "Name" not in msg:
        return None
    rows: list[Observation] = []
    for element, key, role, absent in _HEADER_ROWS:
        values = [str(v) for v in msg.get_all(key) or []]
        if values:
            rows.append(obs(element, source, key, _header_value(values), role=role))
        elif absent:
            rows.append(absent_obs(element, source, key, role=role))
    classifiers = [str(v) for v in msg.get_all("Classifier") or []]
    return [*rows, *_classifier_rows(source, classifiers, "Classifier")]


_PYPROJECT_ROWS: Final[tuple[tuple[Element, str, Role | None, bool], ...]] = (
    ("name", "project.name", None, False),
    ("name", "tool.poetry.name", None, False),
    ("license", "project.license", None, True),
    ("license", "tool.poetry.license", None, False),
    ("provider", "project.authors", "author", True),
    ("provider", "tool.poetry.authors", "author", False),
    ("provider", "project.maintainers", "maintainer", False),
    ("provider", "tool.poetry.maintainers", "maintainer", False),
)


def _dynamic_keys(project: dict[str, Any]) -> set[str]:
    dynamic = project.get("dynamic")
    if not isinstance(dynamic, list):
        return set()
    return {d for d in dynamic if isinstance(d, str)}


def _pyproject_rows(doc: dict[str, Any]) -> tuple[list[Observation], set[str]]:
    """Rows of ``pyproject.toml`` plus the tags they imply.

    A ``[project]`` table makes ``project.license`` and ``project.authors``
    expected: when missing they give an absent row, and ``dynamic_field`` when
    ``project.dynamic`` lists the key.
    """
    project = doc.get("project")
    has_project = isinstance(project, dict)
    dynamic = _dynamic_keys(project) if isinstance(project, dict) else set()
    rows: list[Observation] = []
    tags: set[str] = set()
    for element, path, role, absent in _PYPROJECT_ROWS:
        present, _ = get_path(doc, path)
        key = path.rsplit(".", 1)[-1]
        if not present and path.startswith("project.") and key in dynamic:
            tags.add(DYNAMIC_FIELD)
        want_absent = absent and has_project
        row = obs_at(
            element, "pyproject.toml", doc, path, role=role, absent_row=want_absent
        )
        if row is not None:
            rows.append(row)
    _, classifiers = get_path(doc, "project.classifiers")
    rows += _classifier_rows("pyproject.toml", classifiers, "project.classifiers")
    return rows, tags


def _layer2(
    index: FetchIndex, store: CacheStore
) -> tuple[list[Observation], list[str], set[str], list[Failure]]:
    """Observations, files read, tags and failures of the layer 2 documents."""
    rows: list[Observation] = []
    files: list[str] = []
    tags: set[str] = set()
    failures: list[Failure] = []
    sources: list[tuple[Source, bytes | None]] = [
        ("PKG-INFO", store.member(index, "PKG-INFO")),
        ("METADATA", _metadata_bytes(index, store)),
    ]
    for source, data in sources:
        if data is None:
            continue
        core = _core_rows(source, data)
        if core is None:
            failures.append(Failure(source, "malformed"))
            continue
        rows += core
        files.append(source)
    pyproject = store.member(index, "pyproject.toml")
    if pyproject is not None:
        try:
            doc = parse_toml(pyproject)
        except ValueError:
            failures.append(Failure("pyproject.toml", "malformed"))
        else:
            toml_rows, toml_tags = _pyproject_rows(doc)
            rows += toml_rows
            tags |= toml_tags
            files.append("pyproject.toml")
    return rows, files, tags, failures


def _metadata_bytes(index: FetchIndex, store: CacheStore) -> bytes | None:
    """``METADATA`` from a wheel member, else from the PEP 658 response."""
    data = store.member(index, "METADATA")
    url = index.responses.get(WHEEL_METADATA)
    if data is None and url is not None:
        data = store.body(url)
    return data


def observe_with_failures(
    index: FetchIndex, store: CacheStore
) -> tuple[Observed, list[Failure]]:
    """Like ``observe``, also returning the members that could not be read.

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: the cached release JSON is missing
            or malformed.
    """
    meta, body, doc = load_platform(index, store)
    platform_rows = _json_rows(doc)
    manifest_rows, files, tags, failures = _layer2(index, store)
    all_tags = {*index.tags, *tags}
    return assemble(
        index,
        meta,
        body,
        html_url=HTML_URL.format(
            name=quote(index.name, safe=""), version=quote(index.version, safe="")
        ),
        platform_obs=platform_rows,
        manifest_obs=manifest_rows,
        manifest_files=files,
        tags=all_tags,
        failures=failures,
    )


def observe(index: FetchIndex, store: CacheStore) -> Observed:
    """Turn the cached responses and members of *index* into observations.

    Offline and deterministic. A malformed layer 2 file gives no rows from it
    (use ``observe_with_failures`` to learn which).

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: the cached release JSON is missing
            or malformed.
    """
    return observe_with_failures(index, store)[0]


__all__ = [
    "EcoError",
    "fetch",
    "observe",
    "observe_with_failures",
    "release_url",
]
