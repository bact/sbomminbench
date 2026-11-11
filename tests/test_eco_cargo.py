# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from test_eco_npm import (
    MB,
    fake_http,
    jbytes,
    load,
    make_tgz,
    rows,
)

from sbomminbench.eco import cargo
from sbomminbench.eco._pkgfetch import EcoError, parse_toml
from sbomminbench.eco.base import CacheStore, FetchIndex, Observed
from sbomminbench.redact import PLACEHOLDER
from sbomminbench.seeds import Seed


def cargo_load(name: str, version: str) -> tuple[FetchIndex, Observed]:
    return load(cargo, "cargo", name, version)


# -- cargo: recorded fixtures --


def test_ring_non_standard_platform_license_and_license_file() -> None:
    _, o = cargo_load("ring", "0.16.20")
    api = rows(o.platform.observations)["version.license"]
    assert api.raw == "non-standard" and api.semantic_format == "placeholder"
    assert api.codemeta == "license"
    toml = rows([r for r in o.manifest if r.source == "Cargo.toml"])
    assert toml["package.license-file"].present is True
    assert toml["package.license-file"].raw == "LICENSE"
    assert toml["package.license-file"].semantic_format == "file_ref"
    assert toml["package.license"].present is False  # absent row, Cargo.toml only
    orig = rows([r for r in o.manifest if r.source == "Cargo.toml.orig"])
    assert "package.license" not in orig and "package.authors" in orig


def test_winapi_slash_compound_is_not_applicable() -> None:
    _, o = cargo_load("winapi", "0.3.9")
    for row in (
        rows(o.platform.observations)["version.license"],
        *(r for r in o.manifest if r.field_path == "package.license"),
    ):
        assert row.raw == "MIT/Apache-2.0"
        assert row.semantic_format == "nonstandard_compound"
        assert row.format_validity == "not_applicable"


def test_serde_reordered_expression_and_authors() -> None:
    _, o = cargo_load("serde", "1.0.210")
    lic = rows(o.platform.observations)["version.license"]
    assert (lic.semantic_format, lic.format_validity) == ("expression_shaped", "valid")
    authors = rows(o.manifest)  # last source wins in the dict: Cargo.toml.orig
    assert authors["package.authors"].native_type == "list_of_strings"
    assert authors["package.authors"].role == "author"
    art = o.platform.artifacts[0]
    assert art.kind == "crate" and art.fetched and art.sha256
    assert art.declared_digest and art.declared_digest.startswith("sha256:")
    assert art.declared_digest == f"sha256:{art.sha256}"


def test_clap_workspace_inheritance_in_orig() -> None:
    """clap 4.5.20 inherits the licence."""
    _, o = cargo_load("clap", "4.5.20")
    orig = rows([r for r in o.manifest if r.source == "Cargo.toml.orig"])
    resolved = rows([r for r in o.manifest if r.source == "Cargo.toml"])
    assert orig["package.license"].raw == {"workspace": True}
    assert orig["package.license"].native_type == "object"
    assert orig["package.license"].semantic_format is None
    assert resolved["package.license"].raw == "MIT OR Apache-2.0"
    assert "workspace_inherited" in o.tags
    assert resolved["package.authors"].present is False  # clap sets no authors


def test_tokio_authors_and_publisher() -> None:
    _, o = cargo_load("tokio", "1.41.0")
    auth = rows([r for r in o.manifest if r.source == "Cargo.toml"])["package.authors"]
    assert auth.raw == [f"Tokio Contributors <{PLACEHOLDER}>"]
    pub = rows(o.platform.observations)["version.published_by"]
    assert pub.role == "publisher" and pub.codemeta is None
    assert isinstance(pub.raw, dict) and "login" in pub.raw


def test_recorded_observe_is_sorted_and_deterministic() -> None:
    index, first = cargo_load("serde", "1.0.210")
    second = cargo.observe(index, CacheStore(Path(__file__).parent / "fixtures/cache"))
    assert first == second
    assert first.manifest_files == [
        "serde-1.0.210/Cargo.toml",
        "serde-1.0.210/Cargo.toml.orig",
    ]
    for layer in (first.platform.observations, first.manifest):
        keys = [(o.source, o.field_path) for o in layer]
        assert keys == sorted(keys)
    assert first.platform.html_url == "https://crates.io/crates/serde/1.0.210"


# -- synthetic caches ----------------------------------------------------------------

API = cargo.api_url("pkg", "1.0.0")
CRATE = cargo.crate_url("pkg", "1.0.0")
SEED = Seed("cargo", "pkg", "1.0.0", "edge", None)


def api_doc(**version: Any) -> bytes:
    return jbytes({"version": {"crate": "pkg", "license": "MIT"} | version})


def test_urls() -> None:
    assert API == "https://crates.io/api/v1/crates/pkg/1.0.0"
    assert CRATE == "https://static.crates.io/crates/pkg/pkg-1.0.0.crate"


def fetched(
    tmp_path: Path, manifests: dict[str, bytes], **version: Any
) -> tuple[FetchIndex, CacheStore]:
    crate = make_tgz(manifests)
    http, _ = fake_http(
        tmp_path,
        {API: (200, api_doc(**version), {}), CRATE: (200, crate, {})},
    )
    index = cargo.fetch(SEED, http, max_bytes=MB)
    return index, CacheStore(tmp_path / "cache")


def test_fetch_reads_both_manifests_and_checksum(tmp_path: Path) -> None:
    toml = b'[package]\nname = "pkg"\nlicense = "MIT"\n'
    crate = make_tgz(
        {
            "pkg-1.0.0/Cargo.toml": toml,
            "pkg-1.0.0/Cargo.toml.orig": b'[package]\nname = "pkg"\nlicense.workspace = true\n',
            "pkg-1.0.0/sub/Cargo.toml": b"[package]\n",
            "pkg-1.0.0/m.safetensors": bytes(16),
        }
    )
    sha = hashlib.sha256(crate).hexdigest()
    http, session = fake_http(
        tmp_path,
        {API: (200, api_doc(checksum=sha), {}), CRATE: (200, crate, {})},
    )
    index = cargo.fetch(SEED, http, max_bytes=MB)
    assert session.calls == [API, CRATE]
    assert index.members == {
        "Cargo.toml": "pkg-1.0.0/Cargo.toml",
        "Cargo.toml.orig": "pkg-1.0.0/Cargo.toml.orig",
    }
    assert index.members_dir == f"members/{sha}" and index.purl == "pkg:cargo/pkg@1.0.0"
    assert index.artifact is not None
    assert index.artifact.declared_digest == f"sha256:{sha}"
    assert index.artifact.kind == "crate" and index.artifact.fetched
    observed = cargo.observe(index, CacheStore(tmp_path / "cache"))
    assert observed.tags == ["workspace_inherited"]
    assert {o.source for o in observed.manifest} == {"Cargo.toml", "Cargo.toml.orig"}


def test_fetch_checksum_mismatch_is_warned_not_hidden(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="sbomminbench"):
        index, _ = fetched(tmp_path, {"pkg-1.0.0/Cargo.toml": b""}, checksum="0" * 64)
    assert "differs from the declared checksum" in caplog.text
    assert index.artifact is not None
    assert index.artifact.declared_digest == "sha256:" + "0" * 64
    assert index.artifact.sha256 != "0" * 64  # measured stays measured


@pytest.mark.parametrize(
    "checksum", [None, 5, "", ["a"]], ids=["null", "int", "empty", "list"]
)
def test_fetch_declared_digest_needs_a_string(tmp_path: Path, checksum: Any) -> None:
    index, _ = fetched(tmp_path, {"pkg-1.0.0/Cargo.toml": b""}, checksum=checksum)
    assert index.artifact is not None and index.artifact.declared_digest is None


def test_fetch_crate_404_and_too_large(tmp_path: Path) -> None:
    http, _ = fake_http(tmp_path / "a", {API: (200, api_doc(), {})})
    index = cargo.fetch(SEED, http, max_bytes=MB)
    assert index.artifact is not None and index.artifact.skip_reason == "http_404"
    http, _ = fake_http(
        tmp_path / "b", {API: (200, api_doc(), {}), CRATE: (200, bytes(9000), {})}
    )
    index = cargo.fetch(SEED, http, max_bytes=4096)
    assert index.artifact is not None and index.artifact.skip_reason == "too_large"
    assert index.tags == ["artifact_too_large"]
    observed = cargo.observe(index, CacheStore(tmp_path / "b" / "cache"))
    assert rows(observed.platform.observations)["version.license"].raw == "MIT"


@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        (b'{"errors": [{"detail": "x"}]}', "malformed_json"),
        (b'{"version": []}', "malformed_json"),
        (b'{"version": null}', "malformed_json"),
        (b"nope", "malformed_json"),
    ],
    ids=["no-version", "list", "null", "syntax"],
)
def test_fetch_bad_api_document(tmp_path: Path, doc: bytes, reason: str) -> None:
    http, session = fake_http(tmp_path, {API: (200, doc, {})})
    with pytest.raises(EcoError) as info:
        cargo.fetch(SEED, http, max_bytes=MB)
    assert info.value.reason == reason and session.calls == [API]


def test_fetch_api_404(tmp_path: Path) -> None:
    http, _ = fake_http(tmp_path, {})
    with pytest.raises(EcoError, match="http_404"):
        cargo.fetch(SEED, http, max_bytes=MB)


def test_observe_rejects_a_cached_document_without_version(tmp_path: Path) -> None:
    index, store = fetched(tmp_path, {"pkg-1.0.0/Cargo.toml": b""})
    k = _body_path(tmp_path, API)
    k.write_bytes(b'{"version": 1}')
    with pytest.raises(EcoError, match="malformed_json"):
        cargo.observe(index, store)


def _body_path(tmp_path: Path, url: str) -> Path:
    from sbomminbench.http import url_key

    k = url_key(url)
    return tmp_path / "cache" / "http" / k[:2] / f"{k}.body"


# -- boundary cases -------------------------------------------------------------------


def toml_rows(tmp_path: Path, toml: bytes) -> Observed:
    index, store = fetched(tmp_path, {"pkg-1.0.0/Cargo.toml": toml})
    return cargo.observe(index, store)


def test_authors_present_empty_vs_missing(tmp_path: Path) -> None:
    o = toml_rows(tmp_path / "a", b'[package]\nname = "p"\nauthors = []\n')
    r = rows(o.manifest)["package.authors"]
    assert (r.present, r.raw, r.native_type) == (True, [], "list_empty")
    o = toml_rows(tmp_path / "b", b'[package]\nname = "p"\n')
    r = rows(o.manifest)["package.authors"]
    assert (r.present, r.native_type) == (False, "absent")


def test_license_empty_string_and_both_license_fields(tmp_path: Path) -> None:
    o = toml_rows(tmp_path, b'[package]\nlicense = ""\nlicense-file = "COPYING"\n')
    r = rows(o.manifest)
    assert r["package.license"].raw == "" and r["package.license"].present
    assert r["package.license"].semantic_format == "empty"
    assert r["package.license-file"].raw == "COPYING"
    assert "package.name" not in r  # no absent row for the name


def test_workspace_inheritance_forms_and_non_true_value(tmp_path: Path) -> None:
    dotted = b"[package]\nlicense.workspace = true\nauthors = { workspace = true }\n"
    o = toml_rows(tmp_path / "a", dotted)
    r = rows(o.manifest)
    assert r["package.license"].raw == {"workspace": True}
    assert r["package.authors"].raw == {"workspace": True}
    assert r["package.authors"].role == "author" and "workspace_inherited" in o.tags
    o = toml_rows(tmp_path / "b", b"[package]\nlicense = { workspace = false }\n")
    assert "workspace_inherited" not in o.tags


def test_toml_email_redacted_and_dates_nan_made_json_safe(tmp_path: Path) -> None:
    toml = (
        b'[package]\nauthors = ["Jo <jo@corp.example.org>"]\n'
        b"license = 2026-11-09\nname = nan\n"
    )
    o = toml_rows(tmp_path, toml)
    r = rows(o.manifest)
    assert r["package.authors"].raw == [f"Jo <{PLACEHOLDER}>"]
    assert r["package.authors"].has_email
    assert r["package.license"].raw == "2026-11-09"
    assert r["package.name"].raw == "nan"
    json.dumps(o.platform.model_dump(), allow_nan=False)
    assert parse_toml(b"x = inf\ny = -inf\n") == {"x": "inf", "y": "-inf"}


@pytest.mark.parametrize(
    "bad",
    [b"[package\nname = 1", b"\xff\xfe\x00", b"a = 1\na = 2\n"],
    ids=["syntax", "binary", "duplicate-key"],
)
def test_malformed_toml_is_a_failure_not_a_crash(tmp_path: Path, bad: bytes) -> None:
    index, store = fetched(
        tmp_path,
        {
            "pkg-1.0.0/Cargo.toml": bad,
            "pkg-1.0.0/Cargo.toml.orig": b'[package]\nname = "p"\n',
        },
    )
    observed, failures = cargo.observe_with_failures(index, store)
    assert [(f.stage, f.reason) for f in failures] == [("Cargo.toml", "malformed")]
    assert {o.source for o in observed.manifest} == {"Cargo.toml.orig"}
    assert observed.manifest_files == ["pkg-1.0.0/Cargo.toml.orig"]
    assert observed.tags == ["schema_type_violation"]
    assert cargo.observe(index, store) == observed


def test_observe_twice_identical(tmp_path: Path) -> None:
    index, store = fetched(
        tmp_path, {"pkg-1.0.0/Cargo.toml": b'[package]\nauthors = ["a"]\n'}
    )
    a, b = cargo.observe(index, store), cargo.observe(index, store)
    assert a == b and a.platform.model_dump_json() == b.platform.model_dump_json()
