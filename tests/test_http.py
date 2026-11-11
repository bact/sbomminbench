# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench.http import (
    USER_AGENT,
    FetchFailed,
    HttpClient,
    OfflineMiss,
)


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
        url: str = "",
    ) -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.url = url
        self.closed = False
        self.chunks_read = 0

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for i in range(0, len(self.content), chunk_size):
            self.chunks_read += 1
            yield self.content[i : i + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self.queue = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs["headers"]))
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if not item.url:
            item.url = url
        return item


class Clock:
    """A fake clock whose sleep advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(
    tmp_path: Path, session: FakeSession, clock: Clock | None = None, **kw: Any
) -> HttpClient:
    clock = clock or Clock()
    return HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        clock=clock.time,
        sleep=clock.sleep,
        **kw,
    )


URL = "https://example.org/a.json"


def test_cache_hit_offline(tmp_path: Path) -> None:
    session = FakeSession(FakeResponse(200, b'{"a": 1}', {"Content-Type": "x/y"}))
    first = make(tmp_path, session).get(URL)
    assert (first.status, first.body, first.from_cache) == (200, b'{"a": 1}', False)
    assert session.calls[0][1]["User-Agent"] == USER_AGENT
    again = make(tmp_path, FakeSession(), offline=True).get(URL)
    assert again.from_cache and again.body == first.body
    assert (again.sha256, again.fetched_at) == (first.sha256, first.fetched_at)
    assert again.content_type == "x/y"


@pytest.mark.parametrize("status", [404, 410])
def test_negative_cached(tmp_path: Path, status: int) -> None:
    session = FakeSession(FakeResponse(status))
    first = make(tmp_path, session).get(URL)
    assert (first.status, first.body, first.sha256) == (status, None, None)
    again = make(tmp_path, FakeSession(), offline=True).get(URL)
    assert (again.status, again.body, again.from_cache) == (status, None, True)
    value, _ = make(tmp_path, FakeSession(), offline=True).get_json(URL)
    assert value is None


def test_offline_miss(tmp_path: Path) -> None:
    with pytest.raises(OfflineMiss):
        make(tmp_path, FakeSession(), offline=True).get(URL)
    with pytest.raises(OfflineMiss):
        make(tmp_path, FakeSession(), offline=True).download(URL, max_bytes=10)


def test_retry_backoff(tmp_path: Path) -> None:
    clock = Clock()
    session = FakeSession(
        FakeResponse(503), FakeResponse(503), FakeResponse(200, b"ok")
    )
    resp = make(tmp_path, session, clock).get(URL)
    assert resp.status == 200 and len(session.calls) == 3
    assert clock.sleeps == [2, 4]


def test_retry_after(tmp_path: Path) -> None:
    clock = Clock()
    session = FakeSession(
        FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse()
    )
    make(tmp_path, session, clock).get(URL)
    assert clock.sleeps == [7]


@pytest.mark.parametrize("value", ["61", "soon", "-1"])
def test_retry_after_ignored(tmp_path: Path, value: str) -> None:
    clock = Clock()
    session = FakeSession(
        FakeResponse(429, headers={"Retry-After": value}), FakeResponse()
    )
    make(tmp_path, session, clock).get(URL)
    assert clock.sleeps == [2]


def test_exhausted_not_cached(tmp_path: Path) -> None:
    clock = Clock()
    session = FakeSession(*[FakeResponse(500) for _ in range(4)])
    client = make(tmp_path, session, clock)
    with pytest.raises(FetchFailed):
        client.get(URL)
    assert clock.sleeps == [2, 4, 8]
    with pytest.raises(OfflineMiss):
        make(tmp_path, FakeSession(), offline=True).get(URL)


def test_connection_error_retried_and_403_not(tmp_path: Path) -> None:
    clock = Clock()
    session = FakeSession(requests.ConnectionError("boom"), FakeResponse(200, b"x"))
    assert make(tmp_path, session, clock).get(URL).body == b"x"
    denied = FakeSession(FakeResponse(403))
    with pytest.raises(FetchFailed):
        make(tmp_path / "b", denied, clock).get(URL)
    assert len(denied.calls) == 1


def test_rate_limit_per_host(tmp_path: Path) -> None:
    clock = Clock()
    session = FakeSession(FakeResponse(), FakeResponse(), FakeResponse())
    client = make(tmp_path, session, clock)
    client.get("https://crates.io/a")
    assert clock.sleeps == []
    client.get("https://crates.io/b")
    assert len(clock.sleeps) == 1 and clock.sleeps[0] >= 1.0
    client.get("https://other.example/c")
    assert len(clock.sleeps) == 1


def test_redirect_final_url_and_key(tmp_path: Path) -> None:
    session = FakeSession(FakeResponse(200, b"x", url="https://example.org/final"))
    first = make(tmp_path, session).get(URL)
    assert first.url == "https://example.org/final"
    again = make(tmp_path, FakeSession(), offline=True).get(URL)
    assert again.url == "https://example.org/final" and again.from_cache


@pytest.mark.parametrize(
    "body", [b"\xff\xfe not json", b"{broken", "é".encode("latin-1")]
)
def test_get_json_bad(tmp_path: Path, body: bytes) -> None:
    value, resp = make(tmp_path, FakeSession(FakeResponse(200, body))).get_json(URL)
    assert value is None and resp.body == body


def test_get_json_ok_and_bom(tmp_path: Path) -> None:
    body = b"\xef\xbb\xbf" + b'{"a": [1]}'
    value, _ = make(tmp_path, FakeSession(FakeResponse(200, body))).get_json(URL)
    assert value == {"a": [1]}


def test_refresh_refetches(tmp_path: Path) -> None:
    make(tmp_path, FakeSession(FakeResponse(200, b"old"))).get(URL)
    session = FakeSession(FakeResponse(200, b"new"))
    resp = make(tmp_path, session, refresh=True).get(URL)
    assert resp.body == b"new" and not resp.from_cache
    assert make(tmp_path, FakeSession(), offline=True).get(URL).body == b"new"
    with pytest.raises(ValueError):
        make(tmp_path, FakeSession(), offline=True, refresh=True)


def test_download_too_large_header(tmp_path: Path) -> None:
    session = FakeSession(FakeResponse(200, b"x", {"Content-Length": "62914560"}))
    dl = make(tmp_path, session).download(
        "https://example.org/big.whl", max_bytes=52428800
    )
    assert dl.too_large and dl.path is None
    assert not [p for p in (tmp_path / "cache").rglob("*") if p.is_file()]


def test_download_too_large_streaming(tmp_path: Path) -> None:
    body = b"a" * (2 * 1024 * 1024 + 5)
    session = FakeSession(FakeResponse(200, body))
    dl = make(tmp_path, session).download(
        "https://example.org/big.whl", max_bytes=2 * 1024 * 1024
    )
    assert dl.too_large and dl.path is None and dl.size is None
    assert not [p for p in (tmp_path / "cache").rglob("*") if p.is_file()]


def test_download_ok_and_cached(tmp_path: Path) -> None:
    import hashlib

    body = b"wheel-bytes"
    url = "https://example.org/dir/pkg-1.0-py3-none-any.whl"
    dl = make(tmp_path, FakeSession(FakeResponse(200, body))).download(
        url, max_bytes=100
    )
    assert dl.path is not None and dl.path.read_bytes() == body
    assert dl.path.name == "pkg-1.0-py3-none-any.whl"
    assert dl.sha256 == hashlib.sha256(body).hexdigest() and dl.size == len(body)
    again = make(tmp_path, FakeSession(), offline=True).download(url, max_bytes=100)
    assert again == dl


def test_download_404(tmp_path: Path) -> None:
    dl = make(tmp_path, FakeSession(FakeResponse(404))).download(URL, max_bytes=10)
    assert (dl.status, dl.path, dl.too_large) == (404, None, False)
