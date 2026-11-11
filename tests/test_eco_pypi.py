# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import gzip
import hashlib
import io
import json
import logging
import tarfile
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench.eco import pypi
from sbomminbench.eco._pkgfetch import EcoError, Failure
from sbomminbench.eco.base import (
    CacheStore,
    FetchIndex,
    Observed,
    index_path,
    read_index,
)
from sbomminbench.http import HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.redact import PLACEHOLDER
from sbomminbench.schema import Observation
from sbomminbench.seeds import Seed
from sbomminbench.tags import structural_tags

CACHE = Path(__file__).parent / "fixtures" / "cache"
SEEDS = [
    ("requests", "2.32.3"),
    ("python-dateutil", "2.9.0.post0"),
    ("pytest", "8.3.3"),
    ("numpy", "2.1.2"),
    ("silero-vad", "5.1.2"),
    ("Flask", "3.0.3"),
    ("packaging", "26.3"),
    ("magika", "0.6.1"),
    ("torch", "2.5.0"),
]
CAP = 50 * 1024 * 1024


def recorded(name: str, version: str) -> tuple[FetchIndex, CacheStore]:
    index = read_index(index_path(CACHE, make_purl("pypi", name, version)))
    return index, CacheStore(CACHE)


def observed(name: str, version: str) -> Observed:
    return pypi.observe(*recorded(name, version))


def row(o: Observed, key: str) -> Observation:
    found = [r for r in [*o.platform.observations, *o.manifest] if r.key == key]
    assert len(found) == 1, key
    return found[0]


def keys(rows: list[Observation]) -> list[tuple[str, str]]:
    return [(r.source, r.field_path) for r in rows]


# -- recorded fixtures --


def test_requests_license_and_author_email() -> None:
    o = observed("requests", "2.32.3")
    lic = row(o, "pypi_json:info.license")
    assert (lic.raw, lic.semantic_format, lic.format_validity) == (
        "Apache-2.0",
        "id_shaped",
        "valid",
    )
    mail = row(o, "pypi_json:info.author_email")
    assert (mail.raw, mail.has_email, mail.role) == (PLACEHOLDER, True, "author")
    assert row(o, "pypi_json:info.classifiers[3]").semantic_format == "trove_classifier"
    assert o.manifest_files == ["PKG-INFO", "pyproject.toml"]


def test_numpy_licence_text_is_truncated() -> None:
    lic = row(observed("numpy", "2.1.2"), "pypi_json:info.license")
    assert lic.raw_truncated and isinstance(lic.raw, str) and len(lic.raw) == 4096
    # The length is of the redacted text: the licence holds one address, and the
    # placeholder is 5 characters longer (46566 before redaction).
    assert lic.raw_length == 46571
    assert lic.semantic_format == "license_text"
    assert lic.raw_sha256 is not None and len(lic.raw_sha256) == 64


def test_silero_vad_models_and_no_jit() -> None:
    index, store = recorded("silero-vad", "5.1.2")
    paths = [m.path for m in index.model_members]
    # The sdist is chosen, so paths carry the sdist prefix.
    assert paths == [
        "silero_vad-5.1.2/src/silero_vad/data/silero_vad.onnx",
        "silero_vad-5.1.2/src/silero_vad/data/silero_vad_half.onnx",
    ]
    assert {m.format for m in index.model_members} == {"onnx"}
    assert not any(p.endswith(".jit") for p in paths)
    assert "embedded_models" in pypi.observe(index, store).tags


def test_flask_has_no_author_value() -> None:
    o = observed("Flask", "3.0.3")
    rows = [*o.platform.observations, *o.manifest]
    authors = [r for r in rows if r.role == "author"]
    assert authors and all(not r.present or r.raw is None for r in authors)
    assert row(o, "PKG-INFO:Author").present is False
    assert row(o, "pyproject.toml:project.authors").present is False
    maint = row(o, "pypi_json:info.maintainer_email")
    assert maint.role == "maintainer" and maint.has_email
    assert "author_absent_maintainer_only" in structural_tags(
        o.platform.observations, o.manifest, "pypi"
    )


def test_torch_is_wheel_metadata_only() -> None:
    index, store = recorded("torch", "2.5.0")
    art = index.artifact
    assert art is not None
    assert (art.kind, art.fetched, art.skip_reason) == (
        "wheel_metadata",
        False,
        "too_large",
    )
    assert art.declared_digest is not None and art.declared_digest.startswith("sha256:")
    assert (index.members_dir, index.members) == (None, {})
    o = pypi.observe(index, store)
    assert row(o, "METADATA:Author").raw == "PyTorch Team"
    assert "artifact_too_large" in o.tags
    assert o.manifest_files == ["METADATA"]
    assert o.platform.artifacts == [art]


def test_packaging_expression_and_null_license() -> None:
    o = observed("packaging", "26.3")
    expr = row(o, "pypi_json:info.license_expression")
    assert (expr.raw, expr.semantic_format, expr.format_validity) == (
        "Apache-2.0 OR BSD-2-Clause",
        "expression_shaped",
        "valid",
    )
    null = row(o, "pypi_json:info.license")
    assert (null.present, null.raw, null.native_type) == (True, None, "null")
    assert (null.semantic_format, null.format_validity) == (None, None)
    assert row(o, "PKG-INFO:License").present is False


def test_magika_pyproject_table_and_classifier_index() -> None:
    o = observed("magika", "0.6.1")
    assert row(o, "pyproject.toml:project.license").raw == {"text": "Apache-2.0"}
    assert row(o, "pyproject.toml:project.license").native_type == "object"
    # info.classifiers[5] and Classifier[5] are the index in the full list.
    assert row(o, "pypi_json:info.classifiers[5]").raw.startswith("License ::")
    assert (
        row(o, "PKG-INFO:Classifier[5]").raw
        == row(o, "pypi_json:info.classifiers[5]").raw
    )
    assert row(o, "PKG-INFO:Author").present is False
    assert [m.format for m in recorded("magika", "0.6.1")[0].model_members] == ["onnx"]


@pytest.mark.parametrize(("name", "version"), SEEDS, ids=[n for n, _ in SEEDS])
def test_every_fixture_observes_sorted_and_deterministic(
    name: str, version: str
) -> None:
    index, store = recorded(name, version)
    first, second = pypi.observe(index, store), pypi.observe(index, store)
    assert first == second
    for rows in (first.platform.observations, first.manifest):
        assert keys(rows) == sorted(set(keys(rows)))
    assert first.tags == sorted(set(first.tags))
    assert first.platform.source_url == f"https://pypi.org/pypi/{name}/{version}/json"
    assert first.platform.retrieved_at == index.fetched_at
    assert pypi.observe_with_failures(index, store)[1] == []
    assert index.purl == make_purl("pypi", name, version)


# -- synthetic cache for observe boundaries ----------------------------------------------


class FakeResponse:
    def __init__(
        self, status: int, body: bytes = b"", length: str | None = None
    ) -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict(
            {"Content-Length": length if length is not None else str(len(body))}
        )
        self.url = ""

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def close(self) -> None:
        pass


class FakeSession:
    """Serves ``routes`` (URL -> response); an unknown URL is a 404."""

    def __init__(self, routes: Mapping[str, FakeResponse] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        resp = self.routes.get(url, FakeResponse(404))
        resp.url = url
        return resp


def http_client(tmp_path: Path, session: FakeSession) -> HttpClient:
    return HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )


def synth(
    tmp_path: Path,
    doc: Any,
    members: Mapping[str, bytes] | None = None,
    wheel_metadata: bytes | None = None,
) -> tuple[FetchIndex, CacheStore]:
    """A cache holding one release JSON, layer 2 members and PEP 658 metadata."""
    url = pypi.release_url("demo", "1.0")
    body = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
    routes = {url: FakeResponse(200, body)}
    responses = {"platform": url}
    if wheel_metadata is not None:
        meta_url = "https://files.example/demo-1.0-py3-none-any.whl.metadata"
        routes[meta_url] = FakeResponse(200, wheel_metadata)
        responses["wheel_metadata"] = meta_url
    client = http_client(tmp_path, FakeSession(routes))
    fetched_at = ""
    for u in responses.values():
        fetched_at = client.get(u).fetched_at
    paths = {
        "PKG-INFO": "demo-1.0/PKG-INFO",
        "pyproject.toml": "demo-1.0/pyproject.toml",
    }
    chosen = {role: paths[role] for role in members or {}}
    for role, data in (members or {}).items():
        target = client.cache_dir / "members" / "abc" / paths[role]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    index = FetchIndex(
        ecosystem="pypi",
        name="demo",
        version="1.0",
        purl="pkg:pypi/demo@1.0",
        stratum="edge",
        rank=None,
        responses=responses,
        artifact=None,
        members_dir="members/abc" if chosen else None,
        members=chosen,
        fetched_at=fetched_at,
    )
    return index, CacheStore(client.cache_dir)


def info(**fields: Any) -> dict[str, Any]:
    return {"info": {"name": "demo", **fields}}


def test_author_present_empty_null_and_missing(tmp_path: Path) -> None:
    cases = {"": ("", "string"), None: (None, "null")}
    for i, (value, (raw, native)) in enumerate(cases.items()):
        o = pypi.observe(*synth(tmp_path / str(i), info(author=value)))
        r = row(o, "pypi_json:info.author")
        assert (r.present, r.raw, r.native_type) == (True, raw, native)
    o = pypi.observe(*synth(tmp_path / "missing", info()))
    gone = row(o, "pypi_json:info.author")
    assert (gone.present, gone.raw, gone.native_type) == (False, None, "absent")
    assert row(o, "pypi_json:info.author_email").present is False
    assert row(o, "pypi_json:info.license").present is False
    assert row(o, "pypi_json:info.license_expression").present is False
    # Maintainers and the name have no absent row.
    assert {r.field_path for r in o.platform.observations} == {
        "info.name",
        "info.author",
        "info.author_email",
        "info.license",
        "info.license_expression",
    }


def test_json_classifier_index_is_position_in_full_list(tmp_path: Path) -> None:
    classifiers = [
        "Programming Language :: Python :: 3",
        7,
        "License :: OSI Approved :: MIT License",
        "Topic :: Utilities",
        "License :: Public Domain",
    ]
    o = pypi.observe(*synth(tmp_path, info(classifiers=classifiers)))
    rows = [r for r in o.platform.observations if "classifiers" in r.field_path]
    assert [(r.field_path, r.raw) for r in rows] == [
        ("info.classifiers[2]", "License :: OSI Approved :: MIT License"),
        ("info.classifiers[4]", "License :: Public Domain"),
    ]
    assert all(r.element == "license" and r.role is None for r in rows)
    bad = pypi.observe(*synth(tmp_path / "b", info(classifiers="License :: X")))
    assert not [r for r in bad.platform.observations if "classifiers" in r.field_path]


def test_platform_json_with_bom_and_wrong_shapes(tmp_path: Path) -> None:
    body = b"\xef\xbb\xbf" + json.dumps(info(license="MIT")).encode()
    assert (
        row(pypi.observe(*synth(tmp_path / "bom", body)), "pypi_json:info.license").raw
        == "MIT"
    )
    for i, bad in enumerate([b"[]", b"not json", b'"x"']):
        with pytest.raises(EcoError):
            pypi.observe(*synth(tmp_path / f"bad{i}", bad))
    index, store = synth(tmp_path / "ok", info())
    index.responses["platform"] = "https://pypi.org/pypi/none/1/json"
    with pytest.raises(EcoError):
        pypi.observe(index, store)
    # info of the wrong type: every key is simply absent.
    o = pypi.observe(*synth(tmp_path / "wrong", {"info": ["x"]}))
    assert not any(r.present for r in o.platform.observations)


def test_pkg_info_headers(tmp_path: Path) -> None:
    text = (
        "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n"
        "License: Line one\n        line two\n"
        "Classifier: Topic :: X\nClassifier: License :: OSI Approved :: MIT License\n"
        "Author: A\nAuthor: B\nMaintainer-email: M <m@example.org>\n\nbody: ignored\n"
    )
    o = pypi.observe(
        *synth(tmp_path, info(), {"PKG-INFO": b"\xef\xbb\xbf" + text.encode()})
    )
    assert row(o, "PKG-INFO:Name").raw == "demo"
    assert "line two" in str(row(o, "PKG-INFO:License").raw)
    assert row(o, "PKG-INFO:Classifier[1]").semantic_format == "trove_classifier"
    assert not [r for r in o.manifest if r.field_path == "Classifier[0]"]
    repeated = row(o, "PKG-INFO:Author")
    assert (repeated.raw, repeated.native_type) == (["A", "B"], "list_of_strings")
    mail = row(o, "PKG-INFO:Maintainer-email")
    assert (mail.role, mail.has_email, mail.raw) == (
        "maintainer",
        True,
        "M <" + PLACEHOLDER + ">",
    )
    assert row(o, "PKG-INFO:Author-email").present is False
    assert row(o, "PKG-INFO:License-Expression").present is False
    assert o.manifest_files == ["PKG-INFO"]


@pytest.mark.parametrize(
    "data",
    [b"", b"just some words\nno headers", b"\xff\xfe\x00bad", b"\x80Name: x\n"],
    ids=str,
)
def test_malformed_pkg_info_is_a_failure(tmp_path: Path, data: bytes) -> None:
    index, store = synth(tmp_path, info(), {"PKG-INFO": data})
    o, failures = pypi.observe_with_failures(index, store)
    assert failures == [Failure("PKG-INFO", "malformed")]
    assert o.manifest == [] and o.manifest_files == []
    assert pypi.observe(index, store).manifest == []


def test_wheel_metadata_response_is_metadata(tmp_path: Path) -> None:
    index, store = synth(tmp_path, info(), wheel_metadata=b"Name: demo\nAuthor: Zed\n")
    o = pypi.observe(index, store)
    assert row(o, "METADATA:Author").raw == "Zed"
    assert o.manifest_files == ["METADATA"]


def pyproject(text: str) -> bytes:
    return text.encode()


def test_pyproject_rows_and_native_types(tmp_path: Path) -> None:
    toml = """
[project]
name = "demo"
license = "MIT"
authors = "Alice, Bob"
maintainers = [{name = "M"}]
classifiers = ["License :: OSI Approved :: MIT License", "X"]
[tool.poetry]
name = "demo-poetry"
license = "Apache-2.0"
authors = ["P <p@example.org>"]
"""
    o = pypi.observe(*synth(tmp_path, info(), {"pyproject.toml": pyproject(toml)}))
    lic = row(o, "pyproject.toml:project.license")
    assert (lic.raw, lic.semantic_format) == ("MIT", "id_shaped")
    auth = row(o, "pyproject.toml:project.authors")
    assert (auth.raw, auth.native_type, auth.role) == ("Alice, Bob", "string", "author")
    assert row(o, "pyproject.toml:project.maintainers").role == "maintainer"
    assert row(o, "pyproject.toml:project.classifiers[0]").element == "license"
    poetry = row(o, "pyproject.toml:tool.poetry.authors")
    assert (poetry.raw, poetry.has_email) == (["P <" + PLACEHOLDER + ">"], True)
    assert row(o, "pyproject.toml:tool.poetry.license").raw == "Apache-2.0"
    assert row(o, "pyproject.toml:tool.poetry.name").raw == "demo-poetry"
    assert o.manifest_files == ["pyproject.toml"]
    assert "dynamic_field" not in o.tags


@pytest.mark.parametrize(
    ("toml", "absent", "dynamic"),
    [
        ('[project]\nname = "d"\ndynamic = ["license", "authors"]\n', True, True),
        ('[project]\nname = "d"\ndynamic = ["version"]\n', True, False),
        ('[project]\nname = "d"\ndynamic = "license"\n', True, False),
        ("[build-system]\nrequires = []\n", False, False),
        ('project = "not a table"\n', False, False),
        ('[tool.poetry]\nname = "d"\n', False, False),
    ],
    ids=[
        "dynamic",
        "other-dynamic",
        "dynamic-not-list",
        "no-project",
        "project-scalar",
        "poetry",
    ],
)
def test_pyproject_absent_rows_and_dynamic_field(
    tmp_path: Path, toml: str, absent: bool, dynamic: bool
) -> None:
    o = pypi.observe(*synth(tmp_path, info(), {"pyproject.toml": pyproject(toml)}))
    lic = [r for r in o.manifest if r.field_path == "project.license"]
    auth = [r for r in o.manifest if r.field_path == "project.authors"]
    assert bool(lic) is absent and bool(auth) is absent
    assert all(not r.present and r.native_type == "absent" for r in lic + auth)
    assert ("dynamic_field" in o.tags) is dynamic


def test_pyproject_dynamic_but_present_has_no_tag(tmp_path: Path) -> None:
    toml = '[project]\nlicense = "MIT"\ndynamic = ["license"]\n'
    o = pypi.observe(*synth(tmp_path, info(), {"pyproject.toml": pyproject(toml)}))
    assert row(o, "pyproject.toml:project.license").present
    assert "dynamic_field" not in o.tags


def test_pyproject_bom_dates_and_odd_values(tmp_path: Path) -> None:
    toml = (
        '﻿[project]\nname = "d"\nlicense = {text = "MIT"}\n'
        'authors = [{name = "A", extra = 1979-05-27T07:32:00Z}, 3]\n'
        "classifiers = 5\n"
    )
    o = pypi.observe(*synth(tmp_path, info(), {"pyproject.toml": toml.encode()}))
    authors = row(o, "pyproject.toml:project.authors")
    assert authors.native_type == "list_mixed"
    assert authors.raw == [{"name": "A", "extra": "1979-05-27T07:32:00+00:00"}, 3]
    assert not [r for r in o.manifest if "classifiers" in r.field_path]


@pytest.mark.parametrize(
    "data",
    [b"[project\nname = ", b"\xff\xfe", b'a = "unterminated', b"[a]\n[a]\n"],
    ids=["syntax", "not-utf8", "string", "duplicate-table"],
)
def test_malformed_pyproject_is_a_failure_not_a_crash(
    tmp_path: Path, data: bytes
) -> None:
    index, store = synth(
        tmp_path,
        info(),
        {"PKG-INFO": b"Name: demo\nAuthor: A\n", "pyproject.toml": data},
    )
    o, failures = pypi.observe_with_failures(index, store)
    assert failures == [Failure("pyproject.toml", "malformed")]
    assert o.manifest_files == ["PKG-INFO"]
    assert not [r for r in o.manifest if r.source == "pyproject.toml"]
    assert row(o, "PKG-INFO:Author").raw == "A"


# -- fetch against a fake transport -----------------------------------------------------


def deterministic_gz(entries: Mapping[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for name, data in entries.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def wheel_zip(entries: Mapping[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in entries.items():
            zf.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), data)
    return out.getvalue()


class Dist:
    """One file of a fake release: served at ``https://files.example/<name>``."""

    def __init__(self, name: str, body: bytes, *, size: int | None = None) -> None:
        self.name, self.body = name, body
        self.size = len(body) if size is None else size
        self.url = f"https://files.example/{name}"

    def entry(self) -> dict[str, Any]:
        return {
            "packagetype": "sdist" if self.name.endswith(".tar.gz") else "bdist_wheel",
            "filename": self.name,
            "url": self.url,
            "size": self.size,
            "digests": {"sha256": hashlib.sha256(self.body).hexdigest()},
        }


SDIST_FILES = {
    "demo-1.0/PKG-INFO": b"Metadata-Version: 2.1\nName: demo\nAuthor: Sdist\n",
    "demo-1.0/pyproject.toml": b'[project]\nname = "demo"\n',
    "demo-1.0/nested/pkg/PKG-INFO": b"Name: nested\n",
    "demo-1.0/src/data/model.onnx": b"not a model",
}
WHEEL_FILES = {
    "demo-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: demo\nAuthor: Wheel\n",
    "demo/__init__.py": b"",
}


def release_fake(
    tmp_path: Path,
    dists: list[Dist],
    *,
    overrides: Mapping[str, FakeResponse] | None = None,
) -> tuple[HttpClient, FakeSession]:
    url = pypi.release_url("demo", "1.0")
    routes: dict[str, FakeResponse] = {
        url: FakeResponse(
            200, json.dumps({"info": {}, "urls": [d.entry() for d in dists]}).encode()
        )
    }
    routes.update({d.url: FakeResponse(200, d.body) for d in dists})
    routes.update(overrides or {})
    session = FakeSession(routes)
    return http_client(tmp_path, session), session


SEED = Seed("pypi", "demo", "1.0", "edge", 7)


def test_fetch_prefers_sdist_and_extracts_depth_one_members(tmp_path: Path) -> None:
    sdist = Dist("demo-1.0.tar.gz", deterministic_gz(SDIST_FILES))
    wheel = Dist("demo-1.0-py3-none-any.whl", wheel_zip(WHEEL_FILES))
    client, session = release_fake(tmp_path, [wheel, sdist])
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    art = index.artifact
    assert art is not None and art.kind == "sdist" and art.fetched
    assert art.sha256 == hashlib.sha256(sdist.body).hexdigest()
    assert art.declared_digest == f"sha256:{art.sha256}"
    assert (art.size_bytes, art.filename) == (len(sdist.body), "demo-1.0.tar.gz")
    assert index.members == {
        "PKG-INFO": "demo-1.0/PKG-INFO",
        "pyproject.toml": "demo-1.0/pyproject.toml",
    }
    assert index.members_dir == f"members/{art.sha256}"
    assert wheel.url not in session.calls
    assert (index.purl, index.stratum, index.rank) == ("pkg:pypi/demo@1.0", "edge", 7)
    o = pypi.observe(index, CacheStore(client.cache_dir))
    assert row(o, "PKG-INFO:Author").raw == "Sdist"
    # Offline replay of the same cache gives the same index (clock from the cache).
    assert pypi.fetch(SEED, client, max_bytes=CAP) == index


def test_fetch_wheel_choice_order(tmp_path: Path) -> None:
    big = Dist(
        "demo-1.0-py3-none-any.whl", wheel_zip({**WHEEL_FILES, "demo/x.py": b"x" * 500})
    )
    small_platform = Dist("demo-1.0-cp312-linux_x86_64.whl", wheel_zip(WHEEL_FILES))
    other = Dist("demo-1.0-cp311-linux_x86_64.whl", wheel_zip(WHEEL_FILES))
    client, _ = release_fake(tmp_path / "a", [big, small_platform, other])
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    assert index.artifact is not None and index.artifact.filename == big.name
    assert index.members == {"METADATA": "demo-1.0.dist-info/METADATA"}
    # Only a platform wheel fits the cap: the smallest, filename breaks the tie.
    client, _ = release_fake(tmp_path / "b", [big, small_platform, other])
    index = pypi.fetch(SEED, client, max_bytes=len(small_platform.body) + 1)
    assert index.artifact is not None and index.artifact.filename == other.name
    assert index.artifact.kind == "wheel" and index.model_members == []


def test_fetch_sdist_over_cap_falls_back_to_wheel(tmp_path: Path) -> None:
    sdist = Dist("demo-1.0.tar.gz", deterministic_gz(SDIST_FILES), size=10_000)
    wheel = Dist("demo-1.0-py3-none-any.whl", wheel_zip(WHEEL_FILES))
    client, session = release_fake(tmp_path, [sdist, wheel])
    index = pypi.fetch(SEED, client, max_bytes=len(wheel.body) + 1)
    assert index.artifact is not None and index.artifact.kind == "wheel"
    assert sdist.url not in session.calls and index.tags == []


def test_fetch_only_huge_wheels_uses_pep_658_metadata(tmp_path: Path) -> None:
    a = Dist("demo-1.0-cp312-linux_x86_64.whl", b"w", size=900_000_000)
    b = Dist("demo-1.0-cp311-linux_x86_64.whl", b"w", size=800_000_000)
    meta_url = f"{b.url}.metadata"
    client, session = release_fake(
        tmp_path,
        [a, b],
        overrides={meta_url: FakeResponse(200, b"Name: demo\nAuthor: Meta\n")},
    )
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    art = index.artifact
    assert art is not None
    assert (art.kind, art.fetched, art.skip_reason, art.url) == (
        "wheel_metadata",
        False,
        "too_large",
        b.url,
    )
    assert art.size_bytes == 800_000_000
    assert index.responses["wheel_metadata"] == meta_url
    assert index.tags == ["artifact_too_large"]
    assert a.url not in session.calls and b.url not in session.calls
    o = pypi.observe(index, CacheStore(client.cache_dir))
    assert row(o, "METADATA:Author").raw == "Meta"


def test_fetch_wheel_metadata_missing_keeps_record(tmp_path: Path) -> None:
    a = Dist("demo-1.0-cp312-linux_x86_64.whl", b"w", size=900_000_000)
    client, _ = release_fake(tmp_path, [a])
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    assert index.artifact is not None and index.artifact.kind == "wheel_metadata"
    assert "wheel_metadata" not in index.responses
    assert pypi.observe(index, CacheStore(client.cache_dir)).manifest == []


def test_fetch_declared_size_is_not_trusted(tmp_path: Path) -> None:
    """The listing says the sdist is small, the download says otherwise."""
    sdist = Dist("demo-1.0.tar.gz", deterministic_gz(SDIST_FILES), size=10)
    wheel = Dist("demo-1.0-py3-none-any.whl", wheel_zip(WHEEL_FILES))
    liar = FakeResponse(200, sdist.body, length=str(CAP + 1))
    client, _ = release_fake(tmp_path, [sdist, wheel], overrides={sdist.url: liar})
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    assert index.artifact is not None and index.artifact.kind == "wheel"


def test_fetch_sdist_only_and_too_large(tmp_path: Path) -> None:
    sdist = Dist("demo-1.0.tar.gz", b"x", size=CAP + 1)
    client, _ = release_fake(tmp_path, [sdist])
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    art = index.artifact
    assert art is not None
    assert (art.kind, art.fetched, art.skip_reason) == ("sdist", False, "too_large")
    assert index.tags == ["artifact_too_large"]


def test_fetch_no_files_and_download_errors(tmp_path: Path) -> None:
    client, _ = release_fake(tmp_path / "none", [])
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    assert (index.artifact, index.tags, index.members) == (None, [], {})
    sdist = Dist("demo-1.0.tar.gz", b"x")
    client, _ = release_fake(
        tmp_path / "gone", [sdist], overrides={sdist.url: FakeResponse(404)}
    )
    art = pypi.fetch(SEED, client, max_bytes=CAP).artifact
    assert art is not None and (art.fetched, art.skip_reason) == (False, "http_404")
    client, _ = release_fake(
        tmp_path / "err", [sdist], overrides={sdist.url: FakeResponse(403)}
    )
    art = pypi.fetch(SEED, client, max_bytes=CAP).artifact
    assert art is not None and (art.fetched, art.skip_reason) == (False, "http_error")


def test_fetch_unreadable_archive_and_oversize_member(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = Dist("demo-1.0.tar.gz", b"this is not a gzip file")
    client, _ = release_fake(tmp_path / "bad", [bad])
    with caplog.at_level(logging.WARNING, logger="sbomminbench"):
        art = pypi.fetch(SEED, client, max_bytes=CAP).artifact
    assert art is not None and (art.fetched, art.skip_reason) == (
        False,
        "archive_error",
    )
    assert art.sha256 == hashlib.sha256(bad.body).hexdigest()
    huge = Dist(
        "demo-1.0.tar.gz",
        deterministic_gz({**SDIST_FILES, "demo-1.0/PKG-INFO": b"x" * (3 << 20)}),
    )
    client, _ = release_fake(tmp_path / "huge", [huge])
    index = pypi.fetch(SEED, client, max_bytes=CAP)
    assert index.members == {"pyproject.toml": "demo-1.0/pyproject.toml"}
    assert index.artifact is not None and index.artifact.fetched


def test_fetch_release_missing_or_malformed_raises(tmp_path: Path) -> None:
    url = pypi.release_url("demo", "1.0")
    for i, resp in enumerate(
        [FakeResponse(404), FakeResponse(200, b"[1]"), FakeResponse(200, b"{")]
    ):
        client, _ = release_fake(tmp_path / str(i), [], overrides={url: resp})
        with pytest.raises(EcoError):
            pypi.fetch(SEED, client, max_bytes=CAP)


def test_release_url_quotes_name_and_version() -> None:
    assert (
        pypi.release_url("Flask", "3.0.3") == "https://pypi.org/pypi/Flask/3.0.3/json"
    )
    assert pypi.release_url("a/b", "1+x") == "https://pypi.org/pypi/a%2Fb/1%2Bx/json"


def test_fetch_ignores_hostile_file_entries(tmp_path: Path) -> None:
    url = pypi.release_url("demo", "1.0")
    doc = {
        "urls": [
            "x",
            {"packagetype": "bdist_egg", "filename": "a", "url": "u"},
            {"packagetype": "sdist", "filename": 1, "url": "u"},
            {"packagetype": "sdist", "filename": "a.tar.gz", "url": None},
            {
                "packagetype": "sdist",
                "filename": "b.tar.gz",
                "url": "https://files.example/b",
                "size": True,
                "digests": 5,
            },
        ]
    }
    resp = FakeResponse(404)
    client, _ = release_fake(
        tmp_path,
        [],
        overrides={
            url: FakeResponse(200, json.dumps(doc).encode()),
            "https://files.example/b": resp,
        },
    )
    art = pypi.fetch(SEED, client, max_bytes=CAP).artifact
    assert art is not None and art.filename == "b.tar.gz"
    assert (art.declared_digest, art.skip_reason) == (None, "http_404")
