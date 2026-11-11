# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Maven Central: fetch and observe one POM and its parent chain.

Only the POM is read; the jar is never fetched (tag ``artifact_not_scanned``).
Layer 1 is empty: Central has no metadata API, so ``platform.source_url`` is the
POM URL. Layer 2 is ``pom.xml``.

Parents (``<parent>``, at most ``MAX_PARENTS`` levels, Central only,
``relativePath`` ignored) are walked so that each field group (``licenses``,
``developers``, ``contributors``, ``organization``) comes from the nearest POM
that defines it, as Maven inherits them. Rows from a parent carry the prefix
``parent[n].`` (n = 1 for the direct parent) and ``inherited_from`` = the
parent's purl.

Property placeholders such as ``${project.groupId}`` stay raw: nothing is
interpolated. A ``<parent>`` whose coordinates hold a placeholder ends the walk.
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, NamedTuple

from sbomminbench.eco._pkgfetch import EcoError, Failure
from sbomminbench.eco.base import (
    CacheStore,
    FetchIndex,
    Observed,
    XmlError,
    absent_obs,
    obs,
    strip_namespaces,
    xml_root,
)
from sbomminbench.http import FetchFailed, HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.schema import Artifact, Observation, Platform, Role
from sbomminbench.seeds import Seed
from sbomminbench.tags import ARTIFACT_NOT_SCANNED, MAVEN_INHERITED

LOG = logging.getLogger("sbomminbench")

CENTRAL = "https://repo1.maven.org/maven2"
MAX_PARENTS = 4
SOURCE: Final = "pom.xml"
_COORD = re.compile(r"[A-Za-z0-9_.+-]+")
_COORD_TAGS = ("groupId", "artifactId", "version")
_GROUPS = ("licenses", "developers", "contributors", "organization")
_ABSENT_ROWS = frozenset({"licenses", "developers"})


class Coord(NamedTuple):
    group: str
    artifact: str
    version: str

    @property
    def name(self) -> str:
        return f"{self.group}:{self.artifact}"

    @property
    def url(self) -> str:
        """The URL of the POM on Central."""
        stem = f"{self.artifact}-{self.version}"
        base = f"{CENTRAL}/{self.group.replace('.', '/')}/{self.artifact}"
        return f"{base}/{self.version}/{stem}.pom"

    @property
    def purl(self) -> str:
        return make_purl("maven", self.name, self.version)


def _valid(coord: Coord) -> bool:
    parts = (coord.group, coord.artifact, coord.version)
    return all(_COORD.fullmatch(p) and p.strip(".") for p in parts)


def seed_coord(seed: Seed) -> Coord:
    """Split ``group:artifact`` and add the version.

    Raises:
        EcoError: the name or version is not a plain Maven coordinate.
    """
    group, _, artifact = seed.name.partition(":")
    coord = Coord(group, artifact, seed.version)
    if not _valid(coord):
        raise EcoError("seed", "bad_coordinates")
    return coord


# -- XML helpers -----------------------------------------------------------------


def parse_pom(data: bytes) -> ET.Element:
    """Parse a POM (no DTD or entity, namespaces stripped).

    Raises:
        XmlError: refused, not well-formed, or the root is not ``<project>``.
    """
    root = strip_namespaces(xml_root(data))
    if root.tag != "project":
        raise XmlError("XML is not a POM")
    return root


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if c.tag == name), None)


def _text(el: ET.Element | None) -> str | None:
    """The raw text of *el* (``""`` for an empty element), ``None`` if missing."""
    return None if el is None else (el.text or "")


def parent_coord(root: ET.Element) -> Coord | None:
    """The ``<parent>`` coordinates, or ``None`` (none, incomplete or unusable)."""
    parent = _child(root, "parent")
    if parent is None:
        return None
    parts = [(_text(_child(parent, n)) or "").strip() for n in _COORD_TAGS]
    coord = Coord(*parts)
    return coord if _valid(coord) else None


# -- fetch -----------------------------------------------------------------------


@dataclass
class _Pom:
    url: str
    body: bytes
    root: ET.Element | None  # None when over the size cap
    fetched_at: str


def _get_pom(http: HttpClient, url: str, max_bytes: int) -> _Pom | None:
    """GET a POM; ``None`` on a negative response. Raises ``XmlError``."""
    resp = http.get(url)
    if resp.body is None:
        return None
    if len(resp.body) > max_bytes:
        return _Pom(url, resp.body, None, resp.fetched_at)
    return _Pom(url, resp.body, parse_pom(resp.body), resp.fetched_at)


def _walk_parents(
    http: HttpClient, first: Coord, root: ET.Element, max_bytes: int
) -> dict[str, str]:
    """Fetch up to ``MAX_PARENTS`` parents; return role -> URL."""
    responses: dict[str, str] = {}
    seen = {first}
    for n in range(1, MAX_PARENTS + 1):
        coord = parent_coord(root)
        if coord is None:
            break
        if coord in seen:
            LOG.warning("maven: parent cycle at %s; walk stopped", coord.name)
            break
        seen.add(coord)
        try:
            pom = _get_pom(http, coord.url, max_bytes)
        except XmlError as exc:
            LOG.warning("maven: parent %s refused (%s); walk stopped", coord.name, exc)
            break
        if pom is None or pom.root is None:
            LOG.warning("maven: parent %s unavailable; walk stopped", coord.name)
            break
        responses[f"pom_parent{n}"] = coord.url
        root = pom.root
    return responses


def _pom_artifact(coord: Coord, pom: _Pom) -> Artifact:
    if pom.root is None:
        return Artifact(
            kind="pom",
            url=coord.url,
            filename=f"{coord.artifact}-{coord.version}.pom",
            size_bytes=len(pom.body),
            fetched=False,
            skip_reason="too_large",
        )
    return Artifact(
        kind="pom",
        url=coord.url,
        filename=f"{coord.artifact}-{coord.version}.pom",
        size_bytes=len(pom.body),
        sha256=hashlib.sha256(pom.body).hexdigest(),
        fetched=True,
    )


def fetch(seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
    """Fetch the POM and its parents of *seed* (network only).

    A POM over *max_bytes* is recorded as ``too_large`` and not parsed.

    Raises:
        EcoError: bad seed, or the POM is refused XML or not a POM.
        FetchFailed: the POM is missing (404/410) or the request failed.
    """
    coord = seed_coord(seed)
    try:
        pom = _get_pom(http, coord.url, max_bytes)
    except XmlError as exc:
        raise EcoError("pom", "xml_refused") from exc
    if pom is None:
        raise FetchFailed(f"{coord.url}: not found")
    responses = {"pom": coord.url}
    if pom.root is not None:
        responses.update(_walk_parents(http, coord, pom.root, max_bytes))
    return FetchIndex(
        ecosystem="maven",
        name=seed.name,
        version=seed.version,
        purl=coord.purl,
        stratum=seed.stratum,
        rank=seed.rank,
        responses=responses,
        artifact=_pom_artifact(coord, pom),
        members_dir=None,
        members={},
        fetched_at=pom.fetched_at,
        tags=[ARTIFACT_NOT_SCANNED],
    )


# -- observe ---------------------------------------------------------------------


@dataclass
class _Link:
    """One POM of the chain: depth 0 is the package itself."""

    depth: int
    root: ET.Element
    purl: str | None  # the declared parent's purl; None at depth 0

    @property
    def prefix(self) -> str:
        return f"parent[{self.depth}]." if self.depth else ""


def _rebuild_chain(
    index: FetchIndex, store: CacheStore
) -> tuple[list[_Link], list[Failure]]:
    """Parse the cached POMs in order; stop at the first one that is unusable."""
    chain: list[_Link] = []
    purl: str | None = None
    for depth in range(MAX_PARENTS + 1):
        url = index.responses.get("pom" if depth == 0 else f"pom_parent{depth}")
        if url is None:
            return chain, []
        body = store.body(url)
        if body is None:
            return chain, [Failure(_stage(depth), "not_cached")]
        try:
            root = parse_pom(body)
        except XmlError:
            return chain, [Failure(_stage(depth), "xml_refused")]
        chain.append(_Link(depth, root, purl))
        declared = parent_coord(root)
        purl = declared.purl if declared else None
    return chain, []


def _stage(depth: int) -> str:
    return "pom" if depth == 0 else f"pom_parent{depth}"


def _with(row: Observation, link: _Link) -> Observation:
    if link.depth == 0:
        return row
    return row.model_copy(update={"inherited_from": link.purl})


def _items(parent: ET.Element, item: str) -> list[tuple[int, ET.Element]]:
    return list(enumerate(x for x in parent if x.tag == item))


def _field(
    link: _Link,
    path: str,
    el: ET.Element,
    tag: str,
    build: Callable[[str, str], Observation],
) -> list[Observation]:
    value = _text(_child(el, tag))
    if value is None:
        return []
    return [_with(build(f"{link.prefix}{path}", value), link)]


def _license_rows(group_el: ET.Element, link: _Link) -> list[Observation]:
    rows: list[Observation] = []
    entries = _items(group_el, "license")
    if not entries:
        path = f"{link.prefix}licenses.license"
        return [_with(obs("license", SOURCE, path, []), link)]

    def lic(path: str, value: str) -> Observation:
        return obs("license", SOURCE, path, value)

    for i, el in entries:
        name = f"licenses.license[{i}].name"
        if _child(el, "name") is None:
            rows.append(_with(absent_obs("license", SOURCE, link.prefix + name), link))
        rows += _field(link, name, el, "name", lic)
        rows += _field(link, f"licenses.license[{i}].url", el, "url", lic)
    return rows


def _people_rows(
    group: str, item: str, role: Role, group_el: ET.Element, link: _Link
) -> list[Observation]:
    entries = _items(group_el, item)
    if not entries:
        path = f"{link.prefix}{group}.{item}"
        return [_with(obs("provider", SOURCE, path, [], role=role), link)]

    def person(path: str, value: str) -> Observation:
        return obs("provider", SOURCE, path, value, role=role)

    def organisation(path: str, value: str) -> Observation:
        return obs("provider", SOURCE, path, value, role="organization")

    rows: list[Observation] = []
    for i, el in entries:
        base = f"{group}.{item}[{i}]"
        rows += _field(link, f"{base}.name", el, "name", person)
        if group == "developers":
            rows += _field(
                link, f"{base}.organization", el, "organization", organisation
            )
    return rows


def _organization_rows(group_el: ET.Element, link: _Link) -> list[Observation]:
    def org(path: str, value: str) -> Observation:
        return obs("provider", SOURCE, path, value, role="organization")

    return _field(link, "organization.name", group_el, "name", org)


def _group_rows(group: str, group_el: ET.Element, link: _Link) -> list[Observation]:
    match group:
        case "licenses":
            return _license_rows(group_el, link)
        case "developers":
            return _people_rows(group, "developer", "author", group_el, link)
        case "contributors":
            return _people_rows(group, "contributor", "contributor", group_el, link)
        case _:
            return _organization_rows(group_el, link)


def _absent_row(group: str) -> Observation:
    if group == "licenses":
        return absent_obs("license", SOURCE, "licenses.license")
    return absent_obs("provider", SOURCE, "developers.developer", role="author")


def _own_rows(root: ET.Element) -> list[Observation]:
    """Display name rows; the package's own POM only, never inherited."""
    rows = []
    artifact = _text(_child(root, "artifactId"))
    if artifact is None:
        rows.append(absent_obs("name", SOURCE, "artifactId"))
    else:
        rows.append(obs("name", SOURCE, "artifactId", artifact))
    name = _text(_child(root, "name"))
    if name is not None:
        rows.append(obs("name", SOURCE, "name", name))
    return rows


def _manifest(chain: list[_Link], complete: bool) -> list[Observation]:
    rows = _own_rows(chain[0].root)
    for group in _GROUPS:
        for link in chain:
            group_el = _child(link.root, group)
            if group_el is not None:
                rows += _group_rows(group, group_el, link)
                break
        else:
            if complete and group in _ABSENT_ROWS:
                rows.append(_absent_row(group))
    return sorted(rows, key=lambda o: (o.source, o.field_path))


def _platform(index: FetchIndex, store: CacheStore) -> Platform:
    url = index.responses["pom"]
    meta = store.meta(url)  # CacheMiss when the POM was never cached
    body = store.body(url) or b""
    group, _, artifact = index.name.partition(":")
    return Platform(
        ecosystem="maven",
        source_url=url,
        html_url=f"https://central.sonatype.com/artifact/{group}/{artifact}/{index.version}",
        retrieved_at=meta.fetched_at,
        response_sha256=hashlib.sha256(body).hexdigest(),
        name=index.name,
        version=index.version,
        artifacts=[index.artifact] if index.artifact else [],
    )


def observe_with_failures(
    index: FetchIndex, store: CacheStore
) -> tuple[Observed, list[Failure]]:
    """Read the cached POMs of *index* (offline, pure, deterministic).

    A POM that cannot be used gives a ``Failure`` and no observations; a parent
    that cannot be used cuts the walk and suppresses the absent rows, since
    absence is not established.

    Raises:
        sbomminbench.eco.base.CacheMiss: the package's own POM is not cached.
    """
    platform = _platform(index, store)
    artifact = index.artifact
    if artifact is not None and not artifact.fetched:
        return Observed(platform, [], [], sorted(index.tags)), []
    chain, failures = _rebuild_chain(index, store)
    if not chain:
        return Observed(platform, [], [], sorted(index.tags)), failures
    manifest = _manifest(chain, complete=not failures)
    tags = set(index.tags)
    if any(o.inherited_from is not None for o in manifest):
        tags.add(MAVEN_INHERITED)
    files = [index.responses[_stage(link.depth)].rsplit("/", 1)[-1] for link in chain]
    return Observed(platform, manifest, sorted(files), sorted(tags)), failures


def observe(index: FetchIndex, store: CacheStore) -> Observed:
    """``observe_with_failures`` without the failures."""
    return observe_with_failures(index, store)[0]
