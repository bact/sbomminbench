# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import requests
from pydantic import ValidationError

from sbomminbench import http
from sbomminbench.eco.base import (
    CacheMiss,
    CacheStore,
    FetchIndex,
    ModelMemberModel,
    ResponseMeta,
    XmlError,
    absent_obs,
    get_path,
    index_path,
    local_name,
    obs,
    obs_at,
    parse_core_metadata,
    read_index,
    strip_namespaces,
    truncate,
    write_index,
    xml_root,
)
from sbomminbench.http import HttpClient
from sbomminbench.models_scan import ModelMember
from sbomminbench.redact import PLACEHOLDER

URL = "https://example.org/a.json"


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict(
            {"Content-Type": "application/json"}
        )
        self.url = ""

    def close(self) -> None:
        pass


class FakeSession:
    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith("/missing"):
            return FakeResponse(404)
        resp = FakeResponse(200, b"\xef\xbb\xbf" + json.dumps({"a": [1]}).encode())
        resp.url = url
        return resp


def client(cache: Path) -> HttpClient:
    return HttpClient(
        cache,
        session=FakeSession(),  # type: ignore[arg-type]
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )


# -- cache drift guard ---------------------------------------------------------


def test_store_reads_what_httpclient_writes(tmp_path: Path) -> None:
    written = client(tmp_path).get(URL)
    store = CacheStore(tmp_path)
    assert store.body(URL) == written.body
    assert store.json(URL) == {"a": [1]}  # BOM accepted
    meta = store.meta(URL)
    assert (meta.status, meta.has_body, meta.fetched_at) == (
        200,
        True,
        written.fetched_at,
    )
    assert meta.content_type == "application/json" and meta.url == URL
    k = http.url_key(URL)
    raw = json.loads((tmp_path / "http" / k[:2] / f"{k}.meta.json").read_bytes())
    assert set(raw) == {f.name for f in dataclasses.fields(ResponseMeta)}


def test_store_negative_and_uncached(tmp_path: Path) -> None:
    missing = "https://example.org/missing"
    client(tmp_path).get(missing)
    store = CacheStore(tmp_path)
    assert (store.body(missing), store.json(missing)) == (None, None)
    assert (store.meta(missing).status, store.meta(missing).has_body) == (404, False)
    other = "https://example.org/never"
    assert (store.body(other), store.json(other)) == (None, None)
    with pytest.raises(CacheMiss):
        store.meta(other)


def test_store_bad_json_and_bad_meta(tmp_path: Path) -> None:
    c = client(tmp_path)
    c.get(URL)
    k = http.url_key(URL)
    base = tmp_path / "http" / k[:2]
    (base / f"{k}.body").write_bytes(b"{nope")
    store = CacheStore(tmp_path)
    assert store.json(URL) is None
    (base / f"{k}.meta.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="malformed"):
        store.meta(URL)
    (base / f"{k}.meta.json").write_bytes(b'{"url": "x"}')
    with pytest.raises(ValueError, match="malformed"):
        store.meta(URL)


@pytest.mark.parametrize(
    "body",
    [b'{"a": NaN}', b'{"a": [Infinity]}', b'{"a": -Infinity}', b"[" * 100000],
    ids=["nan", "infinity", "minus_infinity", "too_deep"],
)
def test_store_json_rejects_non_finite_constants(tmp_path: Path, body: bytes) -> None:
    c = client(tmp_path)
    c.get(URL)
    k = http.url_key(URL)
    store = CacheStore(tmp_path)
    assert store.json(URL) == {"a": [1]}
    (tmp_path / "http" / k[:2] / f"{k}.body").write_bytes(body)
    assert store.json(URL) is None


# -- index and members -----------------------------------------------------------


def make_index(**kw: Any) -> FetchIndex:
    data: dict[str, Any] = {
        "ecosystem": "pypi",
        "name": "magika",
        "version": "0.6.1",
        "purl": "pkg:pypi/magika@0.6.1",
        "stratum": "top",
        "rank": 3,
        "responses": {"platform": URL},
        "artifact": None,
        "members_dir": "members/" + "0" * 64,
        "members": {"pyproject.toml": "magika-0.6.1/pyproject.toml"},
        "fetched_at": "2026-11-07T00:00:00Z",
    }
    return FetchIndex.model_validate(data | kw)


def test_index_roundtrip_is_canonical(tmp_path: Path) -> None:
    mm = ModelMemberModel.from_member(ModelMember("a/x.onnx", 5, "onnx"))
    index = make_index(model_members=[mm], tags=["embedded_models"])
    path = write_index(index, tmp_path)
    assert path == index_path(tmp_path, index.purl)
    assert path.relative_to(tmp_path).as_posix() == "index/pypi/_/magika@0.6.1.json"
    first = path.read_bytes()
    assert first.endswith(b"}\n") and b"\r" not in first
    assert write_index(index, tmp_path) == path and path.read_bytes() == first
    assert read_index(path) == index
    assert json.loads(first) == index.model_dump(mode="json")


@pytest.mark.parametrize(
    "bad",
    [{"extra": 1}, {"fetched_at": "yesterday"}, {"ecosystem": "pip"}],
    ids=["extra", "date", "eco"],
)
def test_index_rejects(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_index(**bad)


def test_model_member_format_validated() -> None:
    with pytest.raises(ValidationError):
        ModelMemberModel.from_member(ModelMember("x.bin", 1, "mystery"))


def test_member_reads_and_guards(tmp_path: Path) -> None:
    index = make_index()
    target = tmp_path / "members" / ("0" * 64) / "magika-0.6.1" / "pyproject.toml"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"[project]\r\n")
    store = CacheStore(tmp_path)
    assert store.member(index, "pyproject.toml") == b"[project]\r\n"
    assert store.member(index, "PKG-INFO") is None  # role not recorded
    for bad in (
        make_index(members_dir=None),
        make_index(members={"pyproject.toml": "../../../x"}),
        make_index(members_dir="/etc"),
        make_index(members={"pyproject.toml": "gone.toml"}),
    ):
        assert store.member(bad, "pyproject.toml") is None


# -- get_path ----------------------------------------------------------------------

DOC = {"a": {"b": [{"c": 1}, {"c": None}, "s"], "n": None, "e": []}, "t": "text"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.b[0].c", (True, 1)),
        ("a.b[1].c", (True, None)),
        ("a.n", (True, None)),
        ("a.e", (True, [])),
        ("a.b[2]", (True, "s")),
        ("a.missing", (False, None)),
        ("a.b[3]", (False, None)),
        ("a.b[0].c.d", (False, None)),
        ("t.x", (False, None)),
        ("t[0]", (False, None)),
        ("a[0]", (False, None)),
        ("a.b.c", (False, None)),
        ("a.b[2].c", (False, None)),
    ],
    ids=lambda v: str(v),
)
def test_get_path(path: str, expected: tuple[bool, Any]) -> None:
    assert get_path(DOC, path) == expected


def test_get_path_list_root_and_license_at() -> None:
    assert get_path([[7]], "[0][0]") == (True, 7)
    assert get_path(None, "a") == (False, None)
    assert get_path({"license@type": "x"}, "license@type") == (True, "x")


@pytest.mark.parametrize("bad", ["", "a..b", ".a", "a.", "a[x]", "a[-1]", "a[0"])
def test_get_path_malformed(bad: str) -> None:
    with pytest.raises(ValueError, match="path"):
        get_path(DOC, bad)


# -- obs ---------------------------------------------------------------------------


def test_obs_redacts_email() -> None:
    o = obs("provider", "pypi_json", "info.author_email", "A <a@b.com>", role="author")
    assert o.raw == f"A <{PLACEHOLDER}>" and o.has_email
    assert o.native_type == "string" and o.semantic_format is None
    assert o.codemeta == "author" and o.role == "author"
    plain = obs("provider", "pypi_json", "info.author", "A", role="author")
    assert not plain.has_email


def test_obs_truncates_long_licence() -> None:
    text = ("BSD licence line\n" * 3000)[:46566]
    o = obs("license", "pypi_json", "info.license", text)
    assert o.raw == text[:4096] and o.raw_truncated
    assert (o.raw_length, o.raw_sha256) == (
        46566,
        hashlib.sha256(text.encode()).hexdigest(),
    )
    assert (o.semantic_format, o.format_validity) == ("license_text", "not_applicable")


@pytest.mark.parametrize(("n", "cut"), [(4096, False), (4097, True)])
def test_truncate_boundary(n: int, cut: bool) -> None:
    raw, truncated, length, sha = truncate("é" * n)
    assert truncated is cut and len(raw) == min(n, 4096)  # type: ignore[arg-type]
    assert (length, sha is not None) == ((n, True) if cut else (None, False))


def test_truncate_leaves_non_strings() -> None:
    assert truncate({"k": "x" * 5000}) == ({"k": "x" * 5000}, False, None, None)


@pytest.mark.parametrize(
    ("source", "raw", "fmt", "validity"),
    [
        ("pypi_json", "Apache-2.0", "id_shaped", "valid"),
        ("hf_api", "license:apache-2.0", "id_shaped", "noncanonical"),
        ("hf_api", "license:Apache-2.0", "id_shaped", "valid"),
        ("hf_card", "llama3.1", "id_shaped", "unknown_id"),
        ("hf_card", "license:apache-2.0", "prose", "not_applicable"),
        ("crates_api", "non-standard", "placeholder", "not_applicable"),
    ],
    ids=["valid", "hf_prefix", "hf_exact", "hf_card", "card_keeps_prefix", "place"],
)
def test_obs_licence_format(source: Any, raw: str, fmt: str, validity: str) -> None:
    o = obs("license", source, "license", raw)
    assert (o.raw, o.semantic_format, o.format_validity) == (raw, fmt, validity)


def test_obs_absent_null_and_non_strings() -> None:
    gone = absent_obs("license", "pypi_json", "info.license_expression")
    assert (gone.present, gone.raw, gone.native_type) == (False, None, "absent")
    assert gone.semantic_format is None
    forced = obs("license", "pypi_json", "info.license", "MIT", present=False)
    assert (forced.raw, forced.native_type) == (None, "absent")
    null = obs("license", "pypi_json", "info.license", None)
    assert (null.present, null.native_type, null.semantic_format) == (
        True,
        "null",
        None,
    )
    lst = obs("license", "hf_card", "cardData.license", ["mit", "x"])
    assert (lst.native_type, lst.semantic_format) == ("list_of_strings", None)
    nm = obs("name", "pypi_json", "info.name", "Apache-2.0")
    assert nm.semantic_format is None and nm.codemeta == "name"
    with pytest.raises(ValidationError):  # provider without a role
        obs("provider", "pypi_json", "info.author", "x")


def test_obs_at() -> None:
    doc = {"info": {"license": None, "authors": [{"name": "A"}]}}
    kept = obs_at("license", "pypi_json", doc, "info.license")
    assert kept is not None and kept.present and kept.raw is None
    assert obs_at("license", "pypi_json", doc, "info.nope") == absent_obs(
        "license", "pypi_json", "info.nope"
    )
    assert obs_at("license", "pypi_json", doc, "info.nope", absent_row=False) is None
    assert obs_at(
        "provider", "pypi_json", doc, "info.authors[0].name", role="author"
    ) == obs("provider", "pypi_json", "info.authors[0].name", "A", role="author")


# -- xml and core metadata -----------------------------------------------------------

POM = b'<?xml version="1.0"?><project xmlns="http://maven.apache.org/POM/4.0.0"><name>n</name></project>'


def test_xml_root_and_namespaces() -> None:
    root = xml_root(POM)
    assert root.tag.startswith("{") and local_name(root.tag) == "project"
    strip_namespaces(root)
    assert root.tag == "project" and root[0].tag == "name"
    attr = xml_root(b'<a xmlns:x="u" x:type="e" plain="p"/>')
    assert strip_namespaces(attr).attrib == {"type": "e", "plain": "p"}
    assert xml_root(b"\xef\xbb\xbf" + POM).tag.endswith("project")
    latin = '<?xml version="1.0" encoding="ISO-8859-1"?><a>é</a>'.encode("latin-1")
    assert xml_root(latin).text == "é"


@pytest.mark.parametrize(
    "data",
    [
        b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>',
        b"<!doctype a><a/>",
        b"<a><!ENTITY x 'y'></a>",
        "<!DOCTYPE a><a/>".encode("utf-16"),
        '<?xml version="1.0" encoding="cp037"?><a/>'.encode("cp037"),
        b'<?xml version="1.0" encoding="UTF-16"?><a/>',
        b"<a>",
        b"",
    ],
    ids=["dtd", "lower", "entity", "utf16", "ebcdic", "decl16", "broken", "empty"],
)
def test_xml_root_refuses(data: bytes) -> None:
    with pytest.raises(XmlError):
        xml_root(data)


def test_core_metadata() -> None:
    text = (
        "Metadata-Version: 2.4\nName: x\nLicense: Line one\n"
        "        line two\nClassifier: A\nClassifier: License :: B\n"
        "Author-email: Someone <a@b.com>\n\nName: body-not-a-header\n"
    )
    msg = parse_core_metadata(text)
    assert msg.get_all("Classifier") == ["A", "License :: B"]
    assert msg["License"] == "Line one\n        line two"  # folded, raw
    assert msg["Author-email"] == "Someone <a@b.com>"  # not redacted here
    assert msg.get_all("Name") == ["x"]  # headers only
    assert msg["Author"] is None
