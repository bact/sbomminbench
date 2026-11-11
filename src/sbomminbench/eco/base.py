# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Shared base of the per-ecosystem fetchers.

* ``FetchIndex``: what ``fetch`` records about one package (``index/``).
* ``CacheStore``: offline reader over the cache that ``HttpClient`` writes.
* ``Observed``, ``obs()``, ``get_path()``: how ``observe`` builds observations.
* ``xml_root()``, ``parse_core_metadata()``: safe parsers for POM, nuspec,
  ``PKG-INFO`` and ``METADATA``.

Nothing here touches the network.
"""

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.message import Message
from email.parser import Parser
from email.policy import compat32
from pathlib import Path
from typing import Any, Self

from pydantic import Field

from sbomminbench import jsonio
from sbomminbench.archive import is_safe_name
from sbomminbench.classify import (
    HF_LICENSE_PREFIX,
    format_validity,
    license_format,
    native_type_of,
)
from sbomminbench.codemeta import codemeta_label
from sbomminbench.http import url_key
from sbomminbench.ids import entry_relpath
from sbomminbench.models_scan import ModelMember
from sbomminbench.redact import redact_json
from sbomminbench.schema import (
    MAX_RAW_CHARS,
    UTC_PATTERN,
    Artifact,
    Base,
    Ecosystem,
    Element,
    JsonValue,
    ModelFormat,
    Observation,
    Platform,
    Role,
    Source,
    Stratum,
)

# -- fetch index ---------------------------------------------------------------


class ModelMemberModel(Base):
    """A model file found in the artefact (pydantic mirror of ``ModelMember``)."""

    path: str
    size: int
    format: ModelFormat

    @classmethod
    def from_member(cls, member: ModelMember) -> Self:
        return cls.model_validate(
            {"path": member.path, "size": member.size, "format": member.format}
        )


class FetchIndex(Base):
    """Internal record of one fetch (``.cache/index/...``); never released."""

    ecosystem: Ecosystem
    name: str
    version: str
    purl: str
    stratum: Stratum
    rank: int | None
    responses: dict[str, str]  # role -> URL
    artifact: Artifact | None
    members_dir: str | None  # "members/<sha256>", relative to the cache dir
    members: dict[str, str]  # role -> member path inside members_dir
    model_members: list[ModelMemberModel] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    files_count: int = 0
    fetched_at: str = Field(pattern=UTC_PATTERN)
    tags: list[str] = Field(default_factory=list)


def index_path(cache_dir: Path, purl: str) -> Path:
    """Return ``<cache_dir>/index/<entry relpath>`` for *purl*."""
    return cache_dir / "index" / entry_relpath(purl)


def write_index(index: FetchIndex, cache_dir: Path) -> Path:
    """Write *index* as canonical JSON (only when changed); return its path."""
    path = index_path(cache_dir, index.purl)
    jsonio.write_if_changed(path, jsonio.dumps(index.model_dump(mode="json")))
    return path


def read_index(path: Path) -> FetchIndex:
    """Read one ``FetchIndex``; a leading UTF-8 BOM is dropped by the parser."""
    return FetchIndex.model_validate_json(path.read_bytes())


# -- offline cache reader --------------------------------------------------------


class CacheMiss(LookupError):
    """The URL has no cache record."""


@dataclass(frozen=True)
class ResponseMeta:
    """The ``<k>.meta.json`` record that ``HttpClient`` writes for a URL."""

    url: str
    final_url: str
    status: int
    has_body: bool
    content_type: str | None
    fetched_at: str

    @classmethod
    def from_json(cls, data: object) -> Self:
        """Build from the parsed meta file; ``ValueError`` when malformed."""
        try:
            if not isinstance(data, dict):
                raise TypeError("not an object")
            ctype = data["content_type"]
            return cls(
                url=str(data["url"]),
                final_url=str(data["final_url"]),
                status=int(data["status"]),
                has_body=data["has_body"] is True,
                content_type=ctype if isinstance(ctype, str) else None,
                fetched_at=str(data["fetched_at"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("cache meta is malformed") from exc


def _reject_constant(name: str) -> Any:
    raise ValueError(f"JSON constant {name} is not allowed")


def parse_json(data: bytes) -> Any:
    """Parse JSON bytes (a UTF-8 BOM is accepted; NaN and Infinity are refused).

    Raises:
        ValueError: not valid JSON.
    """
    try:
        return json.loads(data, parse_constant=_reject_constant)
    except RecursionError as exc:
        raise ValueError("JSON nested too deeply") from exc


class CacheStore:
    """Read-only view of a cache directory.

    A URL that was never cached and a negative (404, 410) result both give
    ``None`` from ``body`` and ``json``; ``meta`` tells them apart.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def _record(self, url: str) -> Path:
        k = url_key(url)
        return self.cache_dir / "http" / k[:2] / k

    def meta(self, url: str) -> ResponseMeta:
        """Return the meta record of *url*.

        Raises:
            CacheMiss: *url* is not cached.
            ValueError: the meta file is malformed.
        """
        path = self._record(url).with_suffix(".meta.json")
        if not os.path.isfile(path):
            raise CacheMiss(url)
        return ResponseMeta.from_json(json.loads(path.read_bytes()))

    def body(self, url: str) -> bytes | None:
        """Return the cached body, or ``None`` (not cached or no body)."""
        try:
            meta = self.meta(url)
        except CacheMiss:
            return None
        path = self._record(url).with_suffix(".body")
        if not meta.has_body or not os.path.isfile(path):
            return None
        return path.read_bytes()

    def json(self, url: str) -> Any | None:
        """Return the parsed JSON body, or ``None`` (no body or not JSON).

        The raw bytes are parsed (a UTF-8 BOM is accepted; NaN and Infinity
        are refused, see ``parse_json``).
        """
        data = self.body(url)
        if data is None:
            return None
        try:
            return parse_json(data)
        except ValueError:
            return None

    def member(self, index: FetchIndex, role: str) -> bytes | None:
        """Return the extracted archive member recorded under *role*."""
        name = index.members.get(role)
        if index.members_dir is None or name is None:
            return None
        if not (is_safe_name(index.members_dir) and is_safe_name(name)):
            return None
        path = self.cache_dir / index.members_dir / name
        return path.read_bytes() if os.path.isfile(path) else None


# -- observations ----------------------------------------------------------------


@dataclass
class Observed:
    """Result of ``observe``: layer 1 platform, layer 2 manifest rows, tags."""

    platform: Platform
    manifest: list[Observation]
    manifest_files: list[str]
    tags: list[str]


def truncate(raw: JsonValue) -> tuple[JsonValue, bool, int | None, str | None]:
    """Cut a string longer than ``MAX_RAW_CHARS`` characters.

    Returns ``(raw, truncated, full length, sha256 of the full UTF-8 text)``;
    the last two are ``None`` unless truncated. Other values pass unchanged.
    """
    if not isinstance(raw, str) or len(raw) <= MAX_RAW_CHARS:
        return raw, False, None, None
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    return raw[:MAX_RAW_CHARS], True, len(raw), digest


def obs(
    element: Element,
    source: Source,
    field_path: str,
    value: JsonValue,
    *,
    present: bool = True,
    role: Role | None = None,
) -> Observation:
    """Build one observation.

    E-mail addresses are redacted, then the licence form and validity are
    computed on the redacted, untruncated string (for ``hf_api`` after the
    ``license:`` prefix), then a long string is cut. ``value`` is ignored when
    ``present`` is false.
    """
    raw, has_email = redact_json(value) if present else (None, False)
    text = raw
    if source == "hf_api" and isinstance(raw, str):
        text = raw.removeprefix(HF_LICENSE_PREFIX)
    fmt = validity = None
    if element == "license" and isinstance(text, str):
        fmt = license_format(text)
        validity = format_validity(text, fmt)
    cut, truncated, length, sha = truncate(raw)
    return Observation(
        element=element,
        source=source,
        field_path=field_path,
        present=present,
        raw=cut,
        raw_truncated=truncated,
        raw_length=length,
        raw_sha256=sha,
        native_type=native_type_of(value if present else None, present),
        semantic_format=fmt,
        format_validity=validity,
        role=role,
        codemeta=codemeta_label(source, field_path),
        has_email=has_email,
    )


def absent_obs(
    element: Element, source: Source, field_path: str, *, role: Role | None = None
) -> Observation:
    """An observation of a key the source lacks (``present=False``)."""
    return obs(element, source, field_path, None, present=False, role=role)


_STEP = re.compile(r"\.?([^.\[\]]+)|\[(\d+)\]")


def _steps(dotted: str) -> list[int | str]:
    out: list[int | str] = []
    pos = 0
    while pos < len(dotted):
        m = _STEP.match(dotted, pos)
        if m is None or (pos == 0 and dotted[0] == "."):
            raise ValueError(f"bad path {dotted!r}")
        out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    if not out:
        raise ValueError("empty path")
    return out


def get_path(doc: Any, dotted: str) -> tuple[bool, Any]:
    """Look up ``"a.b[2].c"`` in parsed JSON or TOML: ``(present, value)``.

    A key present with value ``None`` gives ``(True, None)``. A missing key,
    an index out of range or a step into a non-container gives
    ``(False, None)``. Keys that contain ``.``, ``[`` or ``]`` cannot be
    addressed.

    Raises:
        ValueError: *dotted* is empty or malformed.
    """
    node = doc
    for step in _steps(dotted):
        if isinstance(step, int):
            if not isinstance(node, list) or step >= len(node):
                return False, None
            node = node[step]
        else:
            if not isinstance(node, dict) or step not in node:
                return False, None
            node = node[step]
    return True, node


def obs_at(
    element: Element,
    source: Source,
    doc: Any,
    path: str,
    *,
    role: Role | None = None,
    absent_row: bool = True,
) -> Observation | None:
    """Observe ``get_path(doc, path)``; ``field_path`` is *path*.

    A missing key gives an absent row, or ``None`` when ``absent_row`` is
    false.
    """
    present, value = get_path(doc, path)
    if present:
        return obs(element, source, path, value, role=role)
    return absent_obs(element, source, path, role=role) if absent_row else None


# -- parsers ---------------------------------------------------------------------


class XmlError(ValueError):
    """The XML document was refused or is not well-formed."""


# Adapted from licenseid tools/licence_sample/channels.py (Apache-2.0, same
# author): an entity can expand without bound, so a DTD is refused unread.
_XML_DECLARATIONS = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
_XML_ENCODING = re.compile(rb"\A\s*<\?xml[^>]*?\bencoding\s*=\s*[\"']([^\"']+)[\"']")
# Encodings expat reads itself; the scan above is only sound for these.
_ASCII_COMPATIBLE = frozenset({"utf-8", "us-ascii", "iso-8859-1"})


def xml_root(data: bytes) -> ET.Element:
    """Parse *data*; refuse any ``<!DOCTYPE`` or ``<!ENTITY``.

    Documents in UTF-16/32 (NUL bytes) or in an encoding other than UTF-8,
    US-ASCII and ISO-8859-1 are refused too, since the byte scan would miss a
    declaration written in them.

    Raises:
        XmlError: refused or not well-formed (the message never echoes data).
    """
    if _XML_DECLARATIONS.search(data):
        raise XmlError("XML declares a DTD or an entity; refused")
    if b"\x00" in data:
        raise XmlError("XML is not in an ASCII-compatible encoding; refused")
    declared = _XML_ENCODING.match(data.removeprefix(b"\xef\xbb\xbf"))
    if declared and declared.group(1).decode("ascii", "replace").lower() not in (
        _ASCII_COMPATIBLE
    ):
        raise XmlError("XML declares an unsupported encoding; refused")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XmlError("XML is not well-formed") from exc


def local_name(tag: str) -> str:
    """Return *tag* without its ``{namespace}`` prefix."""
    return tag.rsplit("}", 1)[-1]


def strip_namespaces(root: ET.Element) -> ET.Element:
    """Reduce every tag and attribute name under *root* to its local name.

    Works in place and returns *root*. A document with two attributes that
    differ only by namespace keeps the later one.
    """
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = local_name(el.tag)
        if any(k.startswith("{") for k in el.attrib):
            el.attrib = {local_name(k): v for k, v in el.attrib.items()}
    return root


def parse_core_metadata(text: str) -> "Message[str, str]":
    """Parse ``PKG-INFO`` or ``METADATA`` headers (the body is ignored).

    Uses the ``compat32`` policy, so a folded ``License`` stays raw.
    """
    return Parser(policy=compat32).parsestr(text, headersonly=True)
