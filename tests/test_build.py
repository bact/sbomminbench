# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.build and sbomminbench.failures (offline)."""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from sbomminbench import build, console, jsonio
from sbomminbench.classify import SPDX_LIST_VERSION
from sbomminbench.eco.base import CacheStore, FetchIndex, index_path, read_index
from sbomminbench.failures import record_once
from sbomminbench.http import HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.redact import PLACEHOLDER, find_emails
from sbomminbench.schema import Ecosystem, Entry
from sbomminbench.tags import ALL_TAGS

FIXTURES = Path(__file__).parent / "fixtures"
CACHE = FIXTURES / "cache"
EXPECTED = FIXTURES / "expected"
ALL_ECOS: list[Ecosystem] = ["cargo", "huggingface", "maven", "npm", "nuget", "pypi"]
INDEXES = sorted(
    p.relative_to(CACHE / "index").as_posix() for p in (CACHE / "index").rglob("*.json")
)


def build_tree(data_dir: Path) -> dict[str, bytes]:
    """Build every fixture index into *data_dir*; return relpath -> bytes."""
    summary = build.run_build(CACHE, data_dir, ALL_ECOS, [])
    assert summary.failed == 0
    root = data_dir / "entries"
    return {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.json")
    }


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    return build_tree(tmp_path_factory.mktemp("built"))


def strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from strings(v)


# -- determinism and golden snapshots ---------------------------------------------------


def test_every_index_builds_and_twice_gives_identical_bytes(
    built: dict[str, bytes], tmp_path: Path
) -> None:
    assert sorted(built) == INDEXES and len(INDEXES) >= 30
    assert build_tree(tmp_path) == built


def test_golden_entries_match_byte_for_byte(built: dict[str, bytes]) -> None:
    if os.environ.get("UPDATE_GOLDEN") == "1":
        console.info(f"UPDATE_GOLDEN=1: regenerating {len(built)} golden entries")
        shutil.rmtree(EXPECTED, ignore_errors=True)
        for rel, data in built.items():
            (EXPECTED / rel).parent.mkdir(parents=True, exist_ok=True)
            (EXPECTED / rel).write_bytes(data)
    golden = {
        p.relative_to(EXPECTED).as_posix(): p.read_bytes()
        for p in EXPECTED.rglob("*.json")
    }
    assert sorted(golden) == sorted(built), "golden set differs; UPDATE_GOLDEN=1"
    assert [r for r in built if golden[r] != built[r]] == []


# -- every entry is valid and clean -----------------------------------------------------


@pytest.mark.parametrize("rel", INDEXES, ids=[Path(r).stem for r in INDEXES])
def test_entry_is_valid_round_trips_and_has_no_address(
    rel: str, built: dict[str, bytes]
) -> None:
    data = built[rel]
    assert b"\r" not in data and data.endswith(b"}\n")
    entry = Entry.model_validate_json(data)
    assert jsonio.dumps(entry.model_dump(mode="json")).encode() == data
    leaked = [
        m
        for s in strings(json.loads(data))
        if not s.startswith("pkg:")
        for m in find_emails(s)
        if m != PLACEHOLDER
    ]
    assert leaked == []
    root = entry.subjects[0]
    assert set(root.challenge_tags) <= ALL_TAGS
    index = read_index(CACHE / "index" / rel)
    assert [s.locator for s in entry.subjects[1:]] == sorted(
        m.path for m in index.model_members
    )
    assert ("embedded_models" in root.challenge_tags) == bool(index.model_members)


# -- spot checks of the assembly ----------------------------------------------------------


def entry_of(eco: Ecosystem, name: str, version: str) -> Entry:
    index = read_index(index_path(CACHE, make_purl(eco, name, version)))
    return build.build_entry(index, CacheStore(CACHE), {**build.collected_versions()})


def test_collected_versions_keys_and_list_version() -> None:
    v = build.collected_versions()
    assert sorted(v) == [
        "license_list",
        "license_list_format",
        "licenseid",
        "pitloom",
        "sbomminbench",
    ]
    assert v["license_list_format"] == SPDX_LIST_VERSION == "3.27.0"
    assert v["licenseid"] == "0.8.4" and v["pitloom"] == "0.24.0"


def test_model_subjects_sorted_with_unassessed_expected() -> None:
    entry = entry_of("pypi", "silero-vad", "5.1.2")
    models = entry.subjects[1:]
    assert [s.id for s in models] == ["model:1", "model:2"]
    assert [s.locator for s in models] == sorted(s.locator or "" for s in models)
    for s in models:
        assert s.kind == "model" and s.model_format is not None
        e = s.expected
        assert {e.name.status, e.provider.status, e.license.status} == {"not_assessed"}
    assert entry.subjects[0].kind == "package"


def test_hub_entry_root_is_a_model_and_name_comes_from_the_api_id() -> None:
    entry = entry_of(
        "huggingface",
        "openai-community/gpt2",
        "607a30d783dfa663caf39e06633721c8d4cfcd7e",
    )
    root = entry.subjects[0]
    assert root.kind == "model"
    assert "hf_renamed" in root.challenge_tags
    assert root.expected.name.value is not None
    assert root.expected.name.value.canonical_key == "openai-community/gpt2"
    assert root.model_format is None  # safetensors and onnx files: no single format


@pytest.mark.parametrize(
    ("files", "count", "expected"),
    [
        (["a.safetensors", "b/c.safetensors"], 2, "safetensors"),
        (["a.safetensors", "b.onnx"], 2, None),
        (["a.safetensors"], 40, None),
        ([], 0, None),
    ],
    ids=["one-format", "mixed", "capped-list", "no-model-files"],
)
def test_hub_root_model_format_needs_one_complete_format(
    files: list[str], count: int, expected: str | None
) -> None:
    name, sha = "meta-llama/Llama-3.1-8B", "d04e592bb4f6aa9cfee91e2e20afa771667e1d4b"
    index = read_index(index_path(CACHE, make_purl("huggingface", name, sha)))
    changed = index.model_copy(update={"files": files, "files_count": count})
    assert build.hub_model_format(changed) == expected
    assert (
        build.build_entry(changed, CacheStore(CACHE), build.collected_versions())
        .subjects[0]
        .model_format
        == expected
    )


def test_retrieved_at_is_the_cached_fetch_time() -> None:
    entry = entry_of("cargo", "serde", "1.0.210")
    index = read_index(index_path(CACHE, entry.id))
    meta = CacheStore(CACHE).meta(index.responses["platform"])
    assert entry.platform.retrieved_at == meta.fetched_at


# -- run_build: filters, writes, failures -------------------------------------------------


def test_filters_by_ecosystem_and_name_the_ecosystem_way(tmp_path: Path) -> None:
    only_cargo = build.run_build(CACHE, tmp_path / "a", ["cargo"], [])
    assert only_cargo.written == 5
    assert {p.name for p in (tmp_path / "a/entries").iterdir()} == {"cargo"}
    # PEP 503: "Flask" and "flask" name one package; the Hub id is the index name.
    names = [
        i.name for i in build.iter_indexes(CACHE, ["pypi", "huggingface"], ["flask"])
    ]
    assert names == ["Flask"] or names == ["flask"]
    hub = [
        i.name
        for i in build.iter_indexes(CACHE, ["huggingface"], ["OPENAI-community/GPT2"])
    ]
    assert hub == ["openai-community/gpt2"]


def test_writes_only_changes_and_removes_nothing(tmp_path: Path) -> None:
    first = build.run_build(CACHE, tmp_path, ["npm"], [])
    stale = tmp_path / "entries" / "npm" / "_" / "gone@1.0.json"
    stale.write_text("{}\n")
    second = build.run_build(CACHE, tmp_path, ["npm"], [])
    assert (first.written, first.unchanged) == (5, 0)
    assert (second.written, second.unchanged) == (0, 5)
    assert stale.is_file()


def test_unusable_layer_one_is_logged_once_and_the_run_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    for rel in ("pypi/_/flask@3.0.3.json", "pypi/_/magika@0.6.1.json"):
        shutil.copy(CACHE / "index" / rel, _ensure(cache / "index" / rel))
    shutil.copytree(CACHE / "http", cache / "http")
    shutil.copytree(CACHE / "members", cache / "members")
    index = read_index(cache / "index" / "pypi/_/flask@3.0.3.json")
    url = index.responses["platform"]
    for f in (cache / "http").rglob("*"):  # drop flask's layer 1 record only
        if f.is_file() and f.name.endswith(".meta.json") and url in f.read_text():
            f.unlink()
    data = tmp_path / "data"
    summary = build.run_build(cache, data, ["pypi"], [])
    again = build.run_build(cache, data, ["pypi"], [])
    assert (summary.written, summary.failed, summary.failure_rows) == (1, 1, 1)
    assert (again.failed, again.failure_rows) == (1, 0)
    rows = (data / "seeds" / "failed.tsv").read_text().splitlines()
    assert rows[1:] == ["pypi\tFlask\t3.0.3\tplatform\tcache_miss\t"]
    out = capsys.readouterr().out
    assert "RESULT=failed STAGE=platform REASON=cache_miss" in out


def _ensure(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_layer_two_failure_is_recorded_but_the_entry_is_written(
    tmp_path: Path,
) -> None:
    summary = build.run_build(CACHE, tmp_path, ["maven"], [])
    assert (summary.written, summary.failed, summary.failure_rows) == (6, 0, 1)
    rows = (tmp_path / "seeds" / "failed.tsv").read_text().splitlines()[1:]
    assert rows == ["maven\texample.synthetic:doctype-pom\t1.0\tpom\txml_refused\t"]


def test_record_once_matches_the_flattened_row(tmp_path: Path) -> None:
    path = tmp_path / "failed.tsv"
    args = (path, "npm", "x", "1", "platform", "bad\tthing\nsecond line")
    assert [record_once(*args), record_once(*args)] == [True, False]
    assert record_once(path, "npm", "x", "1", "platform", "other") is True
    assert len(path.read_text().splitlines()) == 3


# -- offline guarantee ------------------------------------------------------------------


def test_build_never_creates_or_uses_an_http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("build must not use HttpClient")

    for name in ("__init__", "get", "download", "_request"):
        monkeypatch.setattr(HttpClient, name, refuse)
    assert build.run_build(CACHE, tmp_path, ALL_ECOS, []).written == len(INDEXES)


def test_cli_build_runs_with_sockets_blocked_in_a_subprocess(tmp_path: Path) -> None:
    code = (
        "import runpy, socket, sys\n"
        "def deny(*a, **k): raise OSError('network disabled')\n"
        "socket.socket.connect = deny\n"
        "socket.create_connection = deny\n"
        "socket.getaddrinfo = deny\n"
        "sys.argv = ['sbomminbench', 'build', '--cache-dir', sys.argv[1],"
        " '--data-dir', sys.argv[2]]\n"
        "runpy.run_module('sbomminbench', run_name='__main__')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(CACHE), str(tmp_path)],
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode()
    lines = done.stdout.decode().splitlines()
    assert len(lines) == len(INDEXES) and all("RESULT=written" in ln for ln in lines)
    assert done.stderr.decode().startswith("INFO: build: ")
    assert FetchIndex  # the index model is the offline input
