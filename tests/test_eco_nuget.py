# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import gzip
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench.eco import nuget
from sbomminbench.eco._pkgfetch import EcoError, Failure
from sbomminbench.eco.base import (
    CacheMiss,
    CacheStore,
    FetchIndex,
    index_path,
    read_index,
)
from sbomminbench.http import HttpClient, url_key
from sbomminbench.ids import make_purl
from sbomminbench.schema import Observation
from sbomminbench.seeds import Seed, read_seeds

CACHE = Path(__file__).parent / "fixtures" / "cache"
SEEDS = Path(__file__).parent / "fixtures" / "seeds" / "nuget.tsv"
BIG = 10 * 1024 * 1024
NUSPEC_NS = "http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd"


def recorded(name: str, version: str) -> tuple[FetchIndex, CacheStore]:
    purl = make_purl("nuget", name, version)
    return read_index(index_path(CACHE, purl)), CacheStore(CACHE)


def by_path(rows: list[Observation]) -> dict[str, Observation]:
    return {o.field_path: o for o in rows}


# -- recorded fixtures (offline) -------------------------------------------------


def test_jquery_licence_url_only() -> None:
    index, store = recorded("jQuery", "3.7.1")
    observed = nuget.observe(index, store)
    cat = by_path(observed.platform.observations)
    present = [
        o.field_path for o in cat.values() if o.element == "license" and o.present
    ]
    assert present == ["licenseUrl"]
    url = cat["licenseUrl"]
    assert (url.semantic_format, url.format_validity) == ("other_url", "valid")
    assert cat["licenseExpression"].present is False  # absent row
    assert "licenseFile" not in cat  # no absent row for the optional fields
    authors = cat["authors"]
    assert (authors.raw, authors.role) == ("jQuery Foundation, Inc.", "author")
    spec = by_path(observed.manifest)
    assert spec["metadata.license"].present is False
    assert spec["metadata.owners"].role == "owner"
    assert observed.platform.source_url == index.responses["platform"]
    assert "version_normalized" not in observed.tags


def test_sqlite_four_part_version_is_normalised() -> None:
    index, store = recorded("System.Data.SQLite.Core", "1.0.119")
    observed = nuget.observe(index, store)
    assert "version_normalized" in observed.tags
    assert index.version == "1.0.119" and index.purl.endswith("@1.0.119")
    # The nuspec states the verbatim version; the id is read from it as well.
    assert by_path(observed.manifest)["metadata.id"].raw == "System.Data.SQLite.Core"
    assert "/1.0.119/" in index.responses["nuspec"]


def test_newtonsoft_licence_expression() -> None:
    index, store = recorded("Newtonsoft.Json", "13.0.3")
    observed = nuget.observe(index, store)
    spec = by_path(observed.manifest)
    assert spec["metadata.license@type"].raw == "expression"
    assert spec["metadata.license"].raw == "MIT"
    assert spec["metadata.license"].format_validity == "valid"
    cat = by_path(observed.platform.observations)
    assert cat["licenseUrl"].semantic_format == "nuget_license_url"
    assert cat["licenseExpression"].raw == "MIT"
    art = index.artifact
    assert art is not None and (art.kind, art.fetched, art.skip_reason) == (
        "nupkg",
        False,
        "not_needed",
    )
    assert art.declared_digest is not None
    assert art.declared_digest.startswith("sha512:mbJSvHfRxfX3")
    assert "version_normalized" not in observed.tags


def test_hangfire_licence_file_type() -> None:
    index, store = recorded("Hangfire.Core", "1.8.14")
    observed = nuget.observe(index, store)
    spec = by_path(observed.manifest)
    assert spec["metadata.license@type"].raw == "file"
    text = spec["metadata.license"]
    assert (text.raw, text.semantic_format) == ("LICENSE.md", "file_ref")
    assert by_path(observed.platform.observations)["licenseFile"].raw == "LICENSE.md"


def test_type_row_carries_no_licence_form() -> None:
    # "expression" or "file" would otherwise classify as an unknown SPDX id.
    index, store = recorded("log4net", "3.0.1")
    row = by_path(nuget.observe(index, store).manifest)["metadata.license@type"]
    assert (row.semantic_format, row.format_validity) == ("empty", "not_applicable")


def test_every_recorded_seed_is_sorted_and_deterministic() -> None:
    store = CacheStore(CACHE)
    seeds = read_seeds(SEEDS, "nuget")
    assert len(seeds) == 6
    for seed in seeds:
        index = read_index(
            index_path(CACHE, make_purl("nuget", seed.name, seed.version))
        )
        first, second = nuget.observe(index, store), nuget.observe(index, store)
        assert first.platform.model_dump() == second.platform.model_dump()
        assert [o.model_dump() for o in first.manifest] == [
            o.model_dump() for o in second.manifest
        ]
        keys = [(o.source, o.field_path) for o in first.manifest]
        assert keys == sorted(keys) and len(keys) == len(set(keys))


# -- fake transport --------------------------------------------------------------

API = nuget.API
LEAF = f"{API}/v3/registration5-gz-semver2/pkg.one/1.0.0.json"
CAT = f"{API}/v3/catalog0/data/2026.01.01.00.00.00/pkg.one.1.0.0.json"
SPEC = f"{API}/v3-flatcontainer/pkg.one/1.0.0/pkg.one.nuspec"


class Reply:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict({})
        self.url = ""

    def close(self) -> None:
        pass


class Session:
    def __init__(self, files: Mapping[str, bytes]) -> None:
        self.files = files
        self.asked: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Reply:
        self.asked.append(url)
        body = self.files.get(url)
        reply = Reply(404) if body is None else Reply(200, body)
        reply.url = url
        return reply


def js(doc: object, *, packed: bool = False) -> bytes:
    data = json.dumps(doc).encode()
    return gzip.compress(data, mtime=0) if packed else data


def nuspec(inner: str, version: str = "1.0.0", *, bom: bool = False) -> bytes:
    text = (
        f'<?xml version="1.0" encoding="utf-8"?><package xmlns="{NUSPEC_NS}">'
        f"<metadata><id>Pkg.One</id><version>{version}</version>{inner}"
        "</metadata></package>"
    )
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode()


def leaf(entry: Any = CAT, content: str | None = None) -> dict[str, Any]:
    return {
        "catalogEntry": entry,
        "packageContent": content or f"{API}/v3-flatcontainer/pkg.one/1.0.0/p.nupkg",
    }


def run(
    tmp_path: Path,
    files: Mapping[str, bytes],
    name: str = "Pkg.One",
    version: str = "1.0.0",
) -> tuple[FetchIndex, CacheStore, Session]:
    session = Session(files)
    http = HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
    )
    seed = Seed("nuget", name, version, "edge", None)
    index = nuget.fetch(seed, http, max_bytes=BIG)
    return index, CacheStore(tmp_path / "cache"), session


def test_fetch_inflates_compressed_bodies_and_records(tmp_path: Path) -> None:
    entry = {
        "id": "Pkg.One",
        "version": "1.0.0",
        "packageHash": "abc/+=",
        "packageHashAlgorithm": "SHA512",
        "licenseExpression": "MIT",
        "authors": "A. Author",
        "packageEntries": [
            {"fullName": "models/m.onnx", "length": 123},
            {"fullName": "lib/a.dll", "length": 5},
            {"fullName": "models/n.gguf"},
            {"length": 3},
            "junk",
        ],
    }
    files = {
        LEAF: js(leaf(), packed=True),
        CAT: js(entry, packed=True),
        SPEC: nuspec('<license type="expression">MIT</license>', bom=True),
    }
    index, store, _ = run(tmp_path, files)
    assert index.responses == {"leaf": LEAF, "platform": CAT, "nuspec": SPEC}
    assert index.purl == "pkg:nuget/Pkg.One@1.0.0"
    assert index.tags == ["embedded_models"]
    assert [(m.path, m.size, m.format) for m in index.model_members] == [
        ("models/m.onnx", 123, "onnx"),
        ("models/n.gguf", 0, "gguf"),
    ]
    art = index.artifact
    assert art is not None and art.declared_digest == "sha512:abc/+="
    assert art.url.endswith("/p.nupkg") and art.sha256 is None
    observed = nuget.observe(index, store)  # compressed body in the cache
    assert by_path(observed.platform.observations)["licenseExpression"].raw == "MIT"
    assert by_path(observed.manifest)["metadata.license"].raw == "MIT"  # BOM nuspec
    assert observed.manifest_files == ["pkg.one.nuspec"]
    assert observed.tags == ["embedded_models"]


def test_catalog_entry_without_licence_expression(tmp_path: Path) -> None:
    entry = {"id": "Pkg.One", "version": "1.0.0", "licenseUrl": "http://x.org/l"}
    files = {LEAF: js(leaf()), CAT: js(entry), SPEC: nuspec("")}
    index, store, _ = run(tmp_path, files)
    cat = by_path(nuget.observe(index, store).platform.observations)
    assert cat["licenseExpression"].present is False
    assert cat["authors"].present is False and cat["authors"].role == "author"
    assert cat["licenseUrl"].present is True and "licenseFile" not in cat
    assert index.artifact is not None and index.artifact.declared_digest is None
    spec = by_path(nuget.observe(index, store).manifest)
    assert spec["metadata.authors"].present is False
    assert spec["metadata.license"].present is False
    assert "metadata.owners" not in spec and "metadata.licenseUrl" not in spec


def test_version_comes_from_the_catalog_and_url_is_normalised(tmp_path: Path) -> None:
    files = {
        LEAF: js(leaf()),
        CAT: js({"id": "Pkg.One", "version": "1.0.0"}),
        SPEC: nuspec(""),
    }
    index, _, session = run(tmp_path, files, version="1.0.0.0")
    assert session.asked == [LEAF, CAT, SPEC] and index.version == "1.0.0"
    assert index.purl == "pkg:nuget/Pkg.One@1.0.0" and "nuspec" in index.responses


def test_name_case_differs_tag(tmp_path: Path) -> None:
    files = {LEAF: js(leaf()), CAT: js({"id": "Pkg.One", "version": "1.0.0"})}
    index, _, _ = run(tmp_path, files, name="pkg.ONE")
    assert index.tags == ["name_case_differs"]
    assert index.name == "pkg.ONE"  # the seed spelling; the catalog `id` is observed


def test_missing_nuspec_is_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    files = {LEAF: js(leaf()), CAT: js({"id": "Pkg.One", "version": "1.0.0"})}
    with caplog.at_level(logging.WARNING, logger="sbomminbench"):
        index, store, _ = run(tmp_path, files)
    assert "nuspec" not in index.responses and "no nuspec" in caplog.text
    observed = nuget.observe(index, store)
    assert observed.manifest == [] and observed.manifest_files == []


@pytest.mark.parametrize(
    ("files", "stage", "reason"),
    [
        ({}, "leaf", "http_404"),
        ({LEAF: b"not json"}, "leaf", "malformed_json"),
        ({LEAF: js([1])}, "leaf", "malformed_json"),
        ({LEAF: js({})}, "leaf", "no_catalog_entry"),
        ({LEAF: js(leaf("https://evil.example/c.json"))}, "leaf", "no_catalog_entry"),
        ({LEAF: js(leaf("http://api.nuget.org/c.json"))}, "leaf", "no_catalog_entry"),
        ({LEAF: js(leaf(7))}, "leaf", "no_catalog_entry"),
        ({LEAF: js(leaf())}, "catalog", "http_404"),
        ({LEAF: js(leaf()), CAT: b"\x1f\x8bbroken"}, "catalog", "malformed_json"),
    ],
    ids=[
        "no-leaf",
        "bad-json",
        "list",
        "no-entry",
        "foreign-host",
        "http",
        "not-str",
        "no-catalog",
        "bad-gzip",
    ],
)
def test_fetch_errors(
    tmp_path: Path, files: Mapping[str, bytes], stage: str, reason: str
) -> None:
    with pytest.raises(EcoError) as err:
        run(tmp_path, files)
    assert (err.value.stage, err.value.reason) == (stage, reason)


def test_foreign_catalog_host_is_never_requested(tmp_path: Path) -> None:
    session = Session({LEAF: js(leaf("https://evil.example/c.json"))})
    http = HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
    )
    with pytest.raises(EcoError):
        nuget.fetch(
            Seed("nuget", "Pkg.One", "1.0.0", "edge", None), http, max_bytes=BIG
        )
    assert session.asked == [LEAF]


# -- nuspec failures -------------------------------------------------------------


def corrupt_nuspec(tmp_path: Path, body: bytes | None) -> tuple[Any, Any]:
    files = {LEAF: js(leaf()), CAT: js({"id": "Pkg.One", "version": "1.0.0"})}
    files = {**files, SPEC: nuspec("")}
    index, store, _ = run(tmp_path, files)
    k = url_key(SPEC)
    path = tmp_path / "cache" / "http" / k[:2] / f"{k}.body"
    if body is None:
        path.unlink()
    else:
        path.write_bytes(body)
    return nuget.observe_with_failures(index, store)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b'<!DOCTYPE package [<!ENTITY x "y">]><package/>', "xml_refused"),
        (b"<package><metadata>", "xml_refused"),
        (b"<other/>", "xml_refused"),
        (b"<package/>", "xml_refused"),  # no <metadata>
        (None, "not_cached"),
    ],
    ids=["doctype", "malformed", "not-a-nuspec", "no-metadata", "not-cached"],
)
def test_unusable_nuspec_gives_failure_and_catalog_rows(
    tmp_path: Path, body: bytes | None, reason: str
) -> None:
    observed, failures = corrupt_nuspec(tmp_path, body)
    assert failures == [Failure("nuspec", reason)]
    assert observed.manifest == [] and observed.manifest_files == []
    assert observed.platform.observations  # layer 1 stays usable


def test_catalog_not_cached_raises(tmp_path: Path) -> None:
    files = {LEAF: js(leaf()), CAT: js({"id": "Pkg.One", "version": "1.0.0"})}
    index, _, _ = run(tmp_path, files)
    with pytest.raises(CacheMiss):
        nuget.observe(index, CacheStore(tmp_path / "empty"))


# -- helpers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.0.119", "1.0.119"),
        ("1.0.119.0", "1.0.119"),
        ("1.0.119.1", "1.0.119.1"),
        ("1.0", "1.0.0"),
        ("1", "1.0.0"),
        ("01.00.03", "1.0.3"),
        ("1.0.0-Beta.1+Build5", "1.0.0-beta.1"),
        ("1.0.0.0-RC", "1.0.0-rc"),
        ("v1", "v1"),
    ],
)
def test_normalize_version(version: str, expected: str) -> None:
    assert nuget.normalize_version(version) == expected


def test_urls_use_lowercase_id_and_version() -> None:
    assert nuget.leaf_url("Newtonsoft.Json", "13.0.3") == (
        f"{API}/v3/registration5-gz-semver2/newtonsoft.json/13.0.3.json"
    )
    assert nuget.nuspec_url("System.Data.SQLite.Core", "1.0.119.0") == (
        f"{API}/v3-flatcontainer/system.data.sqlite.core/1.0.119/"
        "system.data.sqlite.core.nuspec"
    )


def test_inflate() -> None:
    assert nuget.inflate(b"plain") == b"plain"
    assert nuget.inflate(gzip.compress(b"x" * 100)) == b"x" * 100
    with pytest.raises(ValueError, match="corrupt"):
        nuget.inflate(b"\x1f\x8bnot gzip")
    bomb = gzip.compress(b"\0" * (nuget.MAX_INFLATED + 1))
    assert len(bomb) < 100_000
    with pytest.raises(ValueError, match="too large"):
        nuget.inflate(bomb)
