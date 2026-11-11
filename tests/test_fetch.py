# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.fetch on a fake transport (no network)."""

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench import fetch
from sbomminbench.__main__ import main
from sbomminbench.eco import REGISTRY, huggingface
from sbomminbench.eco._pkgfetch import EcoError
from sbomminbench.eco.base import FetchIndex, index_path, read_index
from sbomminbench.http import FetchFailed, HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.seeds import Seed

SHA = "a" * 40
NAMES = ["o/one", "o/two", "o/missing", "o/three"]


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict({})
        self.url = ""

    def close(self) -> None:
        pass


class FakeSession:
    """Serves the Hub API for NAMES except ``o/missing`` (404)."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(url)
        for name in NAMES:
            if url == huggingface.api_url(name, SHA) and name != "o/missing":
                doc = {
                    "id": name,
                    "author": "o",
                    "tags": ["license:mit"],
                    "siblings": [],
                }
                resp = FakeResponse(200, json.dumps(doc).encode())
                resp.url = url
                return resp
        return FakeResponse(404)


def client(tmp_path: Path, session: FakeSession, **kw: Any) -> HttpClient:
    return HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
        clock=lambda: 0.0,
        **kw,
    )


def seeds_file(data: Path, names: list[str] = NAMES) -> None:
    rows = "".join(f"{n}\t{SHA}\tedge\t\t\n" for n in names)
    (data / "seeds").mkdir(parents=True)
    (data / "seeds" / "huggingface.tsv").write_text(
        "name\tversion\tstratum\trank\tnote\n" + rows
    )


def run(
    tmp_path: Path, http: HttpClient, only: list[str] | None = None, jobs: int = 4
) -> fetch.FetchSummary:
    return fetch.run_fetch(
        http,
        tmp_path / "data",
        tmp_path / "cache",
        ["huggingface"],
        only or [],
        jobs=jobs,
    )


def test_failing_seed_is_logged_and_the_run_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seeds_file(tmp_path / "data")
    session = FakeSession()
    summary = run(tmp_path, client(tmp_path, session))
    assert (summary.fetched, summary.failed) == (3, 1)
    for name in ("o/one", "o/two", "o/three"):
        idx = read_index(
            index_path(tmp_path / "cache", make_purl("huggingface", name, SHA))
        )
        assert idx.name == name
    failed = (tmp_path / "data/seeds/failed.tsv").read_text().splitlines()
    assert failed[1:] == [f"huggingface\to/missing\t{SHA}\tplatform\thttp_404\t"]
    # Results come in seed order whatever order the threads finish in.
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split()[1] for ln in lines] == [f"NAME={n}" for n in NAMES]
    assert lines[2].endswith("RESULT=failed STAGE=platform REASON=http_404")
    # A rerun adds no second failure row.
    run(tmp_path, client(tmp_path, FakeSession()))
    assert len((tmp_path / "data/seeds/failed.tsv").read_text().splitlines()) == 2


def test_only_filters_by_name_the_ecosystem_way(tmp_path: Path) -> None:
    seeds_file(tmp_path / "data")
    session = FakeSession()
    summary = run(tmp_path, client(tmp_path, session), only=["O/TWO"])
    assert summary.fetched == 1
    assert session.requests == [huggingface.api_url("o/two", SHA)]


def test_cache_is_reused_and_refresh_requests_again(tmp_path: Path) -> None:
    seeds_file(tmp_path / "data", ["o/one"])
    session = FakeSession()
    run(tmp_path, client(tmp_path, session))
    run(tmp_path, client(tmp_path, session))
    assert len(session.requests) == 1
    run(tmp_path, client(tmp_path, session, refresh=True))
    assert len(session.requests) == 2


def test_missing_seed_file_fetches_nothing(tmp_path: Path) -> None:
    session = FakeSession()
    assert run(tmp_path, client(tmp_path, session)).fetched == 0
    assert session.requests == []


class StubModule:
    """An ecosystem module whose fetch raises *exc* and records its arguments."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls: list[tuple[Seed, int]] = []

    def fetch(self, seed: Seed, http: HttpClient, *, max_bytes: int) -> FetchIndex:
        self.calls.append((seed, max_bytes))
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "stage", "reason"),
    [
        (EcoError("pom", "xml_refused"), "pom", "xml_refused"),
        (FetchFailed("boom after 4 attempts"), "http", "boom after 4 attempts"),
    ],
    ids=["eco-error", "network"],
)
def test_fetch_one_turns_expected_failures_into_results(
    exc: BaseException,
    stage: str,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubModule(exc)
    monkeypatch.setitem(REGISTRY, "huggingface", stub)
    seed = Seed("huggingface", "o/m", SHA, "edge", None)
    res = fetch.fetch_one(seed, client(tmp_path, FakeSession()), tmp_path, 123)
    assert (res.index, res.stage, res.reason) == (None, stage, reason)
    assert stub.calls == [(seed, 123)]


def test_cli_options_reach_the_driver_and_an_internal_error_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeds_file(tmp_path / "data", ["o/one"])
    stub = StubModule(RuntimeError("bug\nsecond line"))
    monkeypatch.setitem(REGISTRY, "huggingface", stub)
    seen: dict[str, Any] = {}

    def make_client(cache: Path, **kw: Any) -> HttpClient:
        seen.update(kw, cache=cache)
        return client(tmp_path, FakeSession())

    monkeypatch.setattr(fetch, "HttpClient", make_client)
    argv = [
        "fetch", "--eco", "huggingface", "--only", "o/one", "-j", "2", "--max-mb", "7",
        "--refresh", "--artifact-dir", str(tmp_path / "art"),
        "--data-dir", str(tmp_path / "data"), "--cache-dir", str(tmp_path / "cache"),
    ]  # fmt: skip
    assert main(argv) == 1
    assert stub.calls[0][1] == 7 * 1024 * 1024
    assert seen == {
        "cache": tmp_path / "cache",
        "artifact_dir": tmp_path / "art",
        "refresh": True,
    }
    err = capsys.readouterr().err.splitlines()
    assert err == ["ERROR: fetch: RuntimeError: bug\\nsecond line"]


def test_cli_exit_zero_with_failures_and_summary_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeds_file(tmp_path / "data")
    monkeypatch.setattr(
        fetch, "HttpClient", lambda cache, **kw: client(tmp_path, FakeSession())
    )
    argv = ["fetch", "--eco", "huggingface", "-j", "1"]
    argv += [
        "--data-dir",
        str(tmp_path / "data"),
        "--cache-dir",
        str(tmp_path / "cache"),
    ]
    assert main(argv) == 0
    out, err = capsys.readouterr()
    assert err.splitlines() == [
        "INFO: fetch: 3 fetched, 1 failed (see seeds/failed.tsv)"
    ]
    assert all(ln.startswith("ECO=huggingface NAME=") for ln in out.splitlines())
