# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import gzip
import hashlib
import io
import json
import logging
import tarfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench.eco import npm
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
from sbomminbench.seeds import Seed, record_failure

FIXTURES = Path(__file__).parent / "fixtures" / "cache"
MB = 1024 * 1024


# -- helpers shared with test_eco_cargo ------------------------------------------


class FakeResponse:
    def __init__(
        self, status: int = 200, body: bytes = b"", headers: Mapping[str, str] = {}
    ) -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict(headers)
        self.url = ""

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def close(self) -> None:
        pass


class FakeSession:
    """Serves ``routes`` (URL -> (status, body, headers)); an unknown URL is a 404."""

    def __init__(self, routes: Mapping[str, tuple[int, bytes, Mapping[str, str]]]):
        self.routes = dict(routes)
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        status, body, headers = self.routes.get(url, (404, b"", {}))
        resp = FakeResponse(status, body, headers)
        resp.url = url
        return resp


def fake_http(
    tmp_path: Path, routes: Mapping[str, tuple[int, bytes, Mapping[str, str]]]
) -> tuple[HttpClient, FakeSession]:
    session = FakeSession(routes)
    http = HttpClient(
        tmp_path / "cache",
        artifact_dir=tmp_path / "artifacts",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
        clock=lambda: 0.0,
        retries=0,
    )
    return http, session


def make_tgz(files: Mapping[str, bytes]) -> bytes:
    """A reproducible .tgz (fixed mtimes, no gzip timestamp)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


def jbytes(doc: object) -> bytes:
    return json.dumps(doc).encode()


def rows(items: list[Observation]) -> dict[str, Observation]:
    return {o.field_path: o for o in items}


def load(module: Any, eco: str, name: str, version: str) -> tuple[FetchIndex, Observed]:
    index = read_index(index_path(FIXTURES, make_purl(eco, name, version)))
    return index, module.observe(index, CacheStore(FIXTURES))


def npm_load(name: str, version: str) -> tuple[FetchIndex, Observed]:
    return load(npm, "npm", name, version)


# -- npm: recorded fixtures --


def test_express_author_object_in_registry_string_in_tarball() -> None:
    """The tarball's author is a string."""
    _, o = npm_load("express", "4.21.1")
    reg, pkg = rows(o.platform.observations), rows(o.manifest)
    assert reg["author"].native_type == "object"
    assert reg["author"].role == "author" and reg["author"].codemeta == "author"
    assert isinstance(reg["author"].raw, dict)
    assert reg["author"].raw["name"] == "TJ Holowaychuk"
    assert pkg["author"].native_type == "string"
    assert pkg["author"].raw == f"TJ Holowaychuk <{PLACEHOLDER}>"
    # The registry body keeps a stand-in address, the extracted member the
    # placeholder, so only the registry rows report an address.
    assert reg["author"].has_email and reg["contributors"].has_email
    assert reg["_npmUser"].has_email and reg["maintainers"].has_email
    assert not pkg["author"].has_email
    assert reg["contributors"].role == "contributor"
    assert (
        reg["maintainers"].role == "maintainer" and reg["maintainers"].codemeta is None
    )
    assert reg["_npmUser"].role == "publisher"
    assert pkg["license"].raw == "MIT" and pkg["license"].format_validity == "valid"
    assert o.manifest_files == ["package/package.json"]


def test_json_schema_licenses_list_of_objects() -> None:
    _, o = npm_load("json-schema", "0.2.3")
    for layer in (o.platform.observations, o.manifest):
        r = rows(layer)
        assert r["licenses"].native_type == "list_of_objects"
        assert isinstance(r["licenses"].raw, list)
        assert [x["type"] for x in r["licenses"].raw if isinstance(x, dict)] == [
            "AFLv2.1",
            "BSD",
        ]
        assert r["licenses"].semantic_format is None
        # `license` is missing (not null): an absent row beside the present list.
        assert r["license"].present is False and r["license"].native_type == "absent"


def test_scoped_name_url_and_purl() -> None:
    index, o = npm_load("@babel/core", "7.26.0")
    assert "@babel%2Fcore" in index.responses["platform"]
    assert o.platform.source_url == index.responses["platform"]
    assert index.purl == "pkg:npm/%40babel/core@7.26.0"
    assert npm.registry_url("@babel/core", "7.26.0") == (
        "https://registry.npmjs.org/@babel%2Fcore/7.26.0"
    )
    assert npm.registry_url("sha.js", "2.4.11").endswith("/sha.js/2.4.11")
    assert o.platform.html_url == "https://www.npmjs.com/package/@babel/core/v/7.26.0"
    author = rows(o.manifest)["author"]
    assert author.native_type == "string" and "babel.dev/team" in str(author.raw)


def test_types_node_has_absent_author_rows() -> None:
    _, o = npm_load("@types/node", "22.9.0")
    for layer in (o.platform.observations, o.manifest):
        r = rows(layer)
        assert r["author"].present is False and r["author"].raw is None
        assert r["author"].role == "author" and r["author"].codemeta == "author"
        assert r["contributors"].present is True
        assert r["license"].raw == "MIT"


def test_sha_js_expression_and_artifact() -> None:
    index, o = npm_load("sha.js", "2.4.11")
    lic = rows(o.platform.observations)["license"]
    assert lic.raw == "(MIT AND BSD-3-Clause)"
    assert (lic.semantic_format, lic.format_validity) == ("expression_shaped", "valid")
    art = o.platform.artifacts[0]
    assert art.kind == "npm_tarball" and art.fetched and art.sha256
    assert art.declared_digest and art.declared_digest.startswith("sha512-")
    assert art.filename == "sha.js-2.4.11.tgz" and index.members_dir == (
        f"members/{art.sha256}"
    )


def test_recorded_observe_is_sorted_and_deterministic() -> None:
    index = read_index(index_path(FIXTURES, make_purl("npm", "express", "4.21.1")))
    store = CacheStore(FIXTURES)
    first, second = npm.observe(index, store), npm.observe(index, store)
    assert first == second
    assert first.platform.model_dump_json() == second.platform.model_dump_json()
    for layer in (first.platform.observations, first.manifest):
        keys = [(o.source, o.field_path) for o in layer]
        assert keys == sorted(keys)
    assert first.platform.retrieved_at == index.fetched_at


# -- observe on synthetic caches ---------------------------------------------------

REG_URL = npm.registry_url("pkg", "1.0.0")


def synth(
    tmp_path: Path,
    registry: object,
    package_json: bytes | None = None,
    *,
    registry_raw: bytes | None = None,
) -> tuple[FetchIndex, CacheStore]:
    """A cache with one registry body and (optionally) one package.json member."""
    http, _ = fake_http(
        tmp_path, {REG_URL: (200, registry_raw or jbytes(registry), {})}
    )
    resp = http.get(REG_URL)
    sha = "1" * 64
    members: dict[str, str] = {}
    if package_json is not None:
        target = tmp_path / "cache" / "members" / sha / "package" / "package.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(package_json)
        members = {"package.json": "package/package.json"}
    index = FetchIndex.model_validate(
        {
            "ecosystem": "npm",
            "name": "pkg",
            "version": "1.0.0",
            "purl": "pkg:npm/pkg@1.0.0",
            "stratum": "edge",
            "rank": None,
            "responses": {"platform": REG_URL},
            "artifact": None,
            "members_dir": f"members/{sha}" if members else None,
            "members": members,
            "fetched_at": resp.fetched_at,
        }
    )
    return index, CacheStore(tmp_path / "cache")


def test_author_present_but_empty_differs_from_missing(tmp_path: Path) -> None:
    index, store = synth(tmp_path, {"name": "pkg", "author": "", "license": "MIT"})
    r = rows(npm.observe(index, store).platform.observations)
    assert r["author"].present is True and r["author"].raw == ""
    assert r["author"].native_type == "string"
    index, store = synth(tmp_path / "b", {"name": "pkg", "license": "MIT"})
    r = rows(npm.observe(index, store).platform.observations)
    assert r["author"].present is False and r["author"].native_type == "absent"


def test_license_null_differs_from_missing(tmp_path: Path) -> None:
    index, store = synth(tmp_path, {"name": "pkg", "license": None})
    r = rows(npm.observe(index, store).platform.observations)["license"]
    assert (r.present, r.raw, r.native_type) == (True, None, "null")
    assert r.semantic_format is None
    index, store = synth(tmp_path / "b", {"name": "pkg"})
    r = rows(npm.observe(index, store).platform.observations)["license"]
    assert (r.present, r.native_type) == (False, "absent")


@pytest.mark.parametrize(
    ("license_value", "native"),
    [
        ({"type": "MIT", "url": "https://example.org/l"}, "object"),
        (["MIT", "ISC"], "list_of_strings"),
        ([{"type": "MIT"}, "ISC"], "list_mixed"),
        ([], "list_empty"),
        (7, "number"),
        ("", "string"),
    ],
    ids=["object", "strings", "mixed", "empty", "number", "empty-string"],
)
def test_odd_license_shapes_are_kept_raw(
    tmp_path: Path, license_value: object, native: str
) -> None:
    index, store = synth(tmp_path, {"name": "pkg", "license": license_value})
    r = rows(npm.observe(index, store).platform.observations)["license"]
    assert r.raw == license_value and r.native_type == native
    assert (r.semantic_format is None) == (not isinstance(license_value, str))


def test_author_object_email_is_redacted(tmp_path: Path) -> None:
    doc = {
        "name": "pkg",
        "author": {"name": "Jane", "email": "jane@corp.example.org"},
        "_npmUser": {"name": "jane", "email": "jane@corp.example.org"},
        "maintainers": [{"name": "j", "email": "j@corp.example.org"}],
    }
    index, store = synth(tmp_path, doc)
    r = rows(npm.observe(index, store).platform.observations)
    assert r["author"].raw == {"name": "Jane", "email": PLACEHOLDER}
    assert r["author"].has_email and r["_npmUser"].has_email
    assert r["maintainers"].raw == [{"name": "j", "email": PLACEHOLDER}]


def test_json_nan_and_non_object_root_are_failures_not_crashes(tmp_path: Path) -> None:
    for i, bad in enumerate([b'{"license": NaN}', b"[1, 2]", b"{nope", b"\xff\xfe"]):
        index, store = synth(tmp_path / str(i), {"name": "pkg"}, bad)
        observed, failures = npm.observe_with_failures(index, store)
        assert observed.manifest == [] and observed.manifest_files == []
        assert [f.stage for f in failures] == ["package.json"]
        assert observed.tags == ["schema_type_violation"]
        assert rows(observed.platform.observations)["name"].raw == "pkg"
    log = tmp_path / "failed.tsv"
    record_failure(log, "npm", "pkg", "1.0.0", failures[0].stage, failures[0].reason)
    assert log.read_text().splitlines()[1].startswith("npm\tpkg\t1.0.0\tpackage.json\t")


def test_package_json_bom_is_accepted(tmp_path: Path) -> None:
    index, store = synth(
        tmp_path, {"name": "pkg"}, b"\xef\xbb\xbf" + jbytes({"name": "p"})
    )
    assert rows(npm.observe(index, store).manifest)["name"].raw == "p"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [(b"{nope", "malformed_json"), (b"[]", "malformed_json")],
    ids=["syntax", "not-object"],
)
def test_bad_registry_document_raises_eco_error(
    tmp_path: Path, raw: bytes, reason: str
) -> None:
    index, store = synth(tmp_path, None, registry_raw=raw)
    with pytest.raises(EcoError) as info:
        npm.observe(index, store)
    assert (info.value.stage, info.value.reason) == ("platform", reason)


def test_uncached_registry_document_raises(tmp_path: Path) -> None:
    index, _ = synth(tmp_path, {"name": "pkg"})
    with pytest.raises(EcoError, match="cache_miss"):
        npm.observe(index, CacheStore(tmp_path / "empty"))


def test_observe_twice_identical(tmp_path: Path) -> None:
    doc = {"name": "pkg", "author": "A <a@b.example>", "licenses": [{"type": "MIT"}]}
    index, store = synth(tmp_path, doc, jbytes({"name": "pkg", "license": "MIT"}))
    a, b = npm.observe(index, store), npm.observe(index, store)
    assert a == b and a.platform.model_dump_json() == b.platform.model_dump_json()


# -- fetch against a fake transport ------------------------------------------------

TARBALL = "https://registry.npmjs.org/pkg/-/pkg-1.0.0.tgz"
SEED = Seed("npm", "pkg", "1.0.0", "edge", None)


def registry_doc(**dist: Any) -> bytes:
    base = {"tarball": TARBALL, "integrity": "sha512-abc"}
    return jbytes({"name": "pkg", "license": "MIT", "dist": base | dist})


def routes(tgz: bytes | None, doc: bytes | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {REG_URL: (200, doc or registry_doc(), {})}
    if tgz is not None:
        out[TARBALL] = (200, tgz, {})
    return out


def test_fetch_reads_shallowest_manifest_and_models(tmp_path: Path) -> None:
    tgz = make_tgz(
        {
            "package/node_modules/dep/package.json": jbytes({"name": "dep"}),
            "package/package.json": jbytes({"name": "pkg", "license": "ISC"}),
            "package/data/m.gguf": b"GGUF\x03\x00\x00\x00" + bytes(40),
            "package/data/w.npy": b"\x93NUMPY" + bytes(40),
        }
    )
    http, session = fake_http(tmp_path, routes(tgz))
    index = npm.fetch(SEED, http, max_bytes=MB)
    assert session.calls == [REG_URL, TARBALL]
    sha = hashlib.sha256(tgz).hexdigest()
    assert index.members == {"package.json": "package/package.json"}
    assert index.members_dir == f"members/{sha}"
    assert [(m.path, m.format) for m in index.model_members] == [
        ("package/data/m.gguf", "gguf")
    ]
    assert index.artifact is not None
    assert (index.artifact.sha256, index.artifact.size_bytes) == (sha, len(tgz))
    assert index.artifact.declared_digest == "sha512-abc" and index.artifact.fetched
    assert index.purl == "pkg:npm/pkg@1.0.0" and index.tags == [
        "array_data_not_scanned"
    ]
    observed = npm.observe(index, CacheStore(tmp_path / "cache"))
    assert rows(observed.manifest)["license"].raw == "ISC"
    assert not (
        tmp_path / "cache" / "members" / sha / "package" / "node_modules"
    ).exists()


def test_fetch_without_manifest_in_tarball(tmp_path: Path) -> None:
    tgz = make_tgz({"package/lib/package.json": b"{}", "x/y/package.json": b"{}"})
    http, _ = fake_http(tmp_path, routes(tgz))
    index = npm.fetch(SEED, http, max_bytes=MB)
    assert index.members == {} and index.members_dir is None
    assert index.artifact is not None and index.artifact.fetched
    assert npm.observe(index, CacheStore(tmp_path / "cache")).manifest == []


def test_fetch_too_large_by_header_and_by_stream(tmp_path: Path) -> None:
    for i, headers in enumerate([{"Content-Length": "999999"}, {}]):
        r = routes(None)
        r[TARBALL] = (200, bytes(5000), headers)
        http, _ = fake_http(tmp_path / str(i), r)
        index = npm.fetch(SEED, http, max_bytes=4096)
        art = index.artifact
        assert art is not None and art.fetched is False
        assert art.skip_reason == "too_large" and art.sha256 is None
        assert art.declared_digest == "sha512-abc"
        assert index.tags == ["artifact_too_large"] and index.members == {}


@pytest.mark.parametrize(
    ("tarball_route", "reason"),
    [
        (None, "http_404"),
        ((200, b"this is not a tarball", {}), "archive_error"),
        ((200, gzip.compress(b"x" * 600)[:40], {}), "archive_error"),
    ],
    ids=["404", "garbage", "truncated"],
)
def test_fetch_artifact_problems_keep_layer_one(
    tmp_path: Path, tarball_route: Any, reason: str
) -> None:
    r = routes(None)
    if tarball_route is not None:
        r[TARBALL] = tarball_route
    http, _ = fake_http(tmp_path, r)
    index = npm.fetch(SEED, http, max_bytes=MB)
    art = index.artifact
    assert art is not None and art.fetched is False and art.skip_reason == reason
    observed = npm.observe(index, CacheStore(tmp_path / "cache"))
    assert rows(observed.platform.observations)["license"].raw == "MIT"
    assert observed.platform.artifacts == [art]


def test_fetch_member_over_read_cap_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    big = b" " * (2 * MB + 1)
    http, _ = fake_http(tmp_path, routes(make_tgz({"package/package.json": big})))
    with caplog.at_level(logging.WARNING, logger="sbomminbench"):
        index = npm.fetch(SEED, http, max_bytes=10 * MB)
    assert index.members == {} and "over the read cap" in caplog.text
    assert index.artifact is not None and index.artifact.fetched


def test_fetch_refuses_tarball_off_the_registry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    evil = "https://evil.example.org/pkg-1.0.0.tgz"
    doc = registry_doc(tarball=evil)
    http, session = fake_http(tmp_path, {REG_URL: (200, doc, {})})
    with caplog.at_level(logging.WARNING, logger="sbomminbench"):
        index = npm.fetch(SEED, http, max_bytes=MB)
    assert session.calls == [REG_URL]
    assert index.artifact is not None and index.artifact.url == evil
    assert index.artifact.skip_reason == "http_error" and "not on" in caplog.text


@pytest.mark.parametrize(
    "dist",
    [None, "x", {}, {"tarball": 3}, {"tarball": ""}, {"tarball": None}],
    ids=["none", "str", "empty", "int", "blank", "null"],
)
def test_fetch_without_usable_dist_has_no_artifact(tmp_path: Path, dist: Any) -> None:
    doc = jbytes({"name": "pkg", "dist": dist})
    http, session = fake_http(tmp_path, {REG_URL: (200, doc, {})})
    index = npm.fetch(SEED, http, max_bytes=MB)
    assert index.artifact is None and session.calls == [REG_URL]


def test_fetch_integrity_must_be_a_string(tmp_path: Path) -> None:
    doc = jbytes({"dist": {"tarball": TARBALL, "integrity": ["sha512-x"]}})
    http, _ = fake_http(tmp_path, {REG_URL: (200, doc, {}), TARBALL: (404, b"", {})})
    index = npm.fetch(SEED, http, max_bytes=MB)
    assert index.artifact is not None and index.artifact.declared_digest is None


@pytest.mark.parametrize(
    ("route", "reason"),
    [
        ((404, b"", {}), "http_404"),
        ((200, b"<html>", {}), "malformed_json"),
        ((200, b"[]", {}), "malformed_json"),
        ((200, b'{"a": Infinity}', {}), "malformed_json"),
        ((500, b"", {}), "http_error"),
    ],
    ids=["404", "html", "list", "infinity", "500"],
)
def test_fetch_platform_failure_raises_eco_error(
    tmp_path: Path, route: Any, reason: str
) -> None:
    http, _ = fake_http(tmp_path, {REG_URL: route})
    with pytest.raises(EcoError) as info:
        npm.fetch(SEED, http, max_bytes=MB)
    assert (info.value.stage, info.value.reason) == ("platform", reason)
    assert Failure(info.value.stage, info.value.reason).reason == reason


def test_fetch_scoped_package_requests_encoded_url(tmp_path: Path) -> None:
    seed = Seed("npm", "@s/p", "2.0.0", "edge", None)
    url = "https://registry.npmjs.org/@s%2Fp/2.0.0"
    http, session = fake_http(tmp_path, {url: (200, jbytes({"name": "@s/p"}), {})})
    index = npm.fetch(seed, http, max_bytes=MB)
    assert session.calls == [url] and index.purl == "pkg:npm/%40s/p@2.0.0"
    assert index.fetched_at == CacheStore(tmp_path / "cache").meta(url).fetched_at
