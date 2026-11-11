# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""HTTP client with an on-disk cache, per-host rate limit, retries and a size cap.

Cache layout (``cache_dir``): ``http/<k[:2]>/<k>.body`` and ``<k>.meta.json``
with ``k = sha256(requested url)``. A 404 or 410 is cached as a negative
result (meta only). Artefacts live under ``artifact_dir/<k>/<filename>``.
Only this module touches the network.
"""

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from sbomminbench import __version__

USER_AGENT = f"sbomminbench/{__version__} (+https://github.com/bact/sbomminbench)"
MIN_INTERVAL: dict[str, float] = {
    "crates.io": 1.0,
    "packages.ecosyste.ms": 0.5,
    "huggingface.co": 0.2,
}
DEFAULT_INTERVAL = 0.05
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
NEGATIVE_STATUS = frozenset({404, 410})
BACKOFF = (2.0, 4.0, 8.0)
MAX_RETRY_AFTER = 60
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes | None
    sha256: str | None
    fetched_at: str
    content_type: str | None
    from_cache: bool


@dataclass(frozen=True)
class Download:
    path: Path | None
    size: int | None
    sha256: str | None
    too_large: bool
    status: int


class OfflineMiss(RuntimeError):
    """Offline mode and the URL is not in the cache."""


class FetchFailed(RuntimeError):
    """The request failed after the retries (not cached)."""


def url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class HttpClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        artifact_dir: Path | None = None,
        offline: bool = False,
        refresh: bool = False,
        timeout: float = 30.0,
        retries: int = 3,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if offline and refresh:
            raise ValueError("offline and refresh are mutually exclusive")
        self.cache_dir = cache_dir
        self.artifact_dir = artifact_dir or cache_dir / "artifacts"
        self.offline = offline
        self.refresh = refresh
        self.timeout = timeout
        self.retries = max(0, retries)
        self._session = session if session is not None else requests.Session()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._hosts: dict[str, tuple[threading.Lock, list[float]]] = {}

    # -- cache ---------------------------------------------------------------

    def _paths(self, url: str) -> tuple[Path, Path]:
        k = url_key(url)
        base = self.cache_dir / "http" / k[:2]
        return base / f"{k}.body", base / f"{k}.meta.json"

    def _read_cache(self, url: str) -> Response | None:
        body_path, meta_path = self._paths(url)
        try:
            meta = json.loads(meta_path.read_bytes())
            status = int(meta["status"])
            body = body_path.read_bytes() if meta.get("has_body") else None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return Response(
            url=str(meta.get("final_url") or url),
            status=status,
            body=body,
            sha256=hashlib.sha256(body).hexdigest() if body is not None else None,
            fetched_at=str(meta.get("fetched_at", "")),
            content_type=meta.get("content_type"),
            from_cache=True,
        )

    def _write_cache(self, url: str, resp: Response) -> None:
        body_path, meta_path = self._paths(url)
        if resp.body is not None:
            _atomic_write(body_path, resp.body)
        else:
            body_path.unlink(missing_ok=True)
        meta = {
            "url": url,
            "final_url": resp.url,
            "status": resp.status,
            "has_body": resp.body is not None,
            "content_type": resp.content_type,
            "fetched_at": resp.fetched_at,
        }
        text = json.dumps(meta, sort_keys=True, indent=2) + "\n"
        _atomic_write(meta_path, text.encode("utf-8"))

    # -- rate limit and retries ----------------------------------------------

    def _host_state(self, host: str) -> tuple[threading.Lock, list[float]]:
        with self._lock:
            if host not in self._hosts:
                self._hosts[host] = (threading.Lock(), [0.0])
            return self._hosts[host]

    def _throttle(self, url: str) -> None:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        interval = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
        lock, next_ok = self._host_state(host)
        with lock:
            wait = next_ok[0] - self._clock()
            if wait > 0:
                self._sleep(wait)
            next_ok[0] = self._clock() + interval

    @staticmethod
    def _retry_delay(resp: Any, attempt: int) -> float:
        value = None
        if resp is not None:
            value = resp.headers.get("Retry-After")
        if isinstance(value, str) and value.strip().isdecimal():
            seconds = int(value.strip())
            if seconds <= MAX_RETRY_AFTER:
                return float(seconds)
        return BACKOFF[min(attempt, len(BACKOFF) - 1)]

    def _request(
        self, url: str, headers: Mapping[str, str] | None, *, stream: bool
    ) -> Any:
        """Return a requests response with a non-retryable status."""
        merged = {"User-Agent": USER_AGENT, **(headers or {})}
        last = "no attempt"
        for attempt in range(self.retries + 1):
            resp = None
            try:
                self._throttle(url)
                resp = self._session.get(
                    url, headers=merged, timeout=self.timeout, stream=stream
                )
                if resp.status_code not in RETRY_STATUS:
                    return resp
                last = f"HTTP {resp.status_code}"
            except requests.RequestException as exc:
                last = type(exc).__name__
            if attempt >= self.retries:
                break
            delay = self._retry_delay(resp, attempt)
            if resp is not None:
                resp.close()
            self._sleep(delay)
        if resp is not None:
            resp.close()
        raise FetchFailed(f"{url}: {last} after {self.retries + 1} attempt(s)")

    # -- public API ----------------------------------------------------------

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> Response:
        if not self.refresh:
            cached = self._read_cache(url)
            if cached is not None:
                return cached
        if self.offline:
            raise OfflineMiss(url)
        resp = self._request(url, headers, stream=False)
        try:
            status = int(resp.status_code)
            if status == 200:
                body: bytes | None = bytes(resp.content)
            elif status in NEGATIVE_STATUS:
                body = None
            else:
                raise FetchFailed(f"{url}: HTTP {status}")
            result = Response(
                url=str(resp.url or url),
                status=status,
                body=body,
                sha256=hashlib.sha256(body).hexdigest() if body is not None else None,
                fetched_at=_now_iso(),
                content_type=resp.headers.get("Content-Type"),
                from_cache=False,
            )
        finally:
            resp.close()
        self._write_cache(url, result)
        return result

    def get_json(self, url: str) -> tuple[Any | None, Response]:
        resp = self.get(url)
        if resp.body is None:
            return None, resp
        try:
            return json.loads(resp.body), resp
        except ValueError:
            return None, resp

    def _artifact_path(self, url: str) -> Path:
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
        safe = "".join(c if c.isalnum() or c in "._-+" else "_" for c in name)
        safe = safe.lstrip(".") or "artifact"
        return self.artifact_dir / url_key(url) / safe

    def download(self, url: str, *, max_bytes: int) -> Download:
        path = self._artifact_path(url)
        if not self.refresh and path.is_file():
            return self._hash_file(path)
        if self.offline:
            raise OfflineMiss(url)
        resp = self._request(url, None, stream=True)
        try:
            status = int(resp.status_code)
            if status != 200:
                if status in NEGATIVE_STATUS:
                    return Download(None, None, None, False, status)
                raise FetchFailed(f"{url}: HTTP {status}")
            length = resp.headers.get("Content-Length")
            declared = length.strip() if length is not None else ""
            if declared.isdecimal() and int(declared) > max_bytes:
                return Download(None, None, None, True, status)
            return self._stream_to(resp, path, max_bytes)
        finally:
            resp.close()

    def _stream_to(self, resp: Any, path: Path, max_bytes: int) -> Download:
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        Path(tmp).unlink(missing_ok=True)
                        return Download(None, None, None, True, 200)
                    digest.update(chunk)
                    handle.write(chunk)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return Download(path, size, digest.hexdigest(), False, 200)

    @staticmethod
    def _hash_file(path: Path) -> Download:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                size += len(chunk)
                digest.update(chunk)
        return Download(path, size, digest.hexdigest(), False, 200)
