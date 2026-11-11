# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Selection of the seed packages from the ecosyste.ms rankings.

``top`` takes ranks 1..N in order. ``mid`` is a cluster sample: a seeded draw of
ranking pages 11-100 (ranks 1,001-10,000), then a seeded draw of rows from each
page. Rank is the position in the full ranking, ``(page - 1) * 100 + index + 1``.
Seeds are appended to ``<data_dir>/seeds/<eco>.tsv`` (see :func:`run`).
"""

import argparse
import logging
import math
import random
import re
import sys
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, get_args

from sbomminbench.http import FetchFailed, HttpClient, OfflineMiss
from sbomminbench.schema import Ecosystem
from sbomminbench.seeds import Seed, append_seeds, read_seeds

ECOSYSTEMS_URL = (
    "https://packages.ecosyste.ms/api/v1/registries/{registry}/packages"
    "?sort={sort}&order=desc&per_page=100&page={page}"
)
REGISTRIES: dict[str, tuple[str, str]] = {
    "pypi": ("pypi.org", "downloads"),
    "npm": ("npmjs.org", "downloads"),
    "cargo": ("crates.io", "downloads"),
    "maven": ("repo1.maven.org", "dependent_packages_count"),
    "nuget": ("nuget.org", "downloads"),
    "huggingface": ("huggingface.co", "downloads"),
}
MAVEN_METADATA_URL = "https://repo1.maven.org/maven2/{path}/maven-metadata.xml"
DEFAULT_SEED = 20261107
PAGE_SIZE = 100
MID_FIRST_PAGE = 11
MID_LAST_PAGE = 100
_LOG = logging.getLogger(__name__)


class FrameError(RuntimeError):
    """A ranking page could not be read; a frame is never silently short."""


@dataclass(frozen=True)
class Frame:
    """The selected seeds with where and when they were drawn."""

    seeds: list[Seed]
    urls: list[str]
    date: str


@dataclass(frozen=True)
class _Row:
    name: str
    version: str
    rank: int


def _page_url(eco: Ecosystem, page: int) -> str:
    registry, sort = REGISTRIES[eco]
    return ECOSYSTEMS_URL.format(registry=registry, sort=sort, page=page)


def _valid(eco: Ecosystem, item: object, rank: int) -> _Row | None:
    """Return the row if it has a usable name and release, else ``None``."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    version = item.get("latest_release_number")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(version, str) or not version:
        return None
    if eco == "huggingface":
        if "/" not in name:
            return None
        version = version.lower()
    return _Row(name, version, rank)


def _read_page(
    eco: Ecosystem, http: HttpClient, page: int
) -> tuple[list[_Row], str, str]:
    """Return the valid rows, URL and fetch date of one ranking page."""
    url = _page_url(eco, page)
    try:
        data, resp = http.get_json(url)
    except (FetchFailed, OfflineMiss) as exc:
        raise FrameError(f"{url}: {exc}") from exc
    if not isinstance(data, list):
        raise FrameError(f"{url}: no ranking page (HTTP {resp.status})")
    base = (page - 1) * PAGE_SIZE
    rows = [_valid(eco, item, base + i + 1) for i, item in enumerate(data)]
    return [r for r in rows if r is not None], url, resp.fetched_at[:10]


_VERSION_TOKEN = re.compile(r"\d+|[A-Za-z]+")
_MAVEN_VERSIONS = re.compile(r"<version>\s*([^<\s]+)\s*</version>")
_UNSTABLE = frozenset(
    [
        "alpha",
        "beta",
        "rc",
        "cr",
        "milestone",
        "snapshot",
        "preview",
        "dev",
        "ea",
        "pre",
    ]
)
_DATE_STAMP_DIGITS = 8


def maven_is_stable(version: str) -> bool:
    """Return whether ``version`` has no pre-release qualifier token.

    Tokens are digit or letter runs, case-insensitive. ``alpha``, ``beta``,
    ``rc``, ``cr``, ``milestone``, ``snapshot``, ``preview``, ``dev``, ``ea``,
    ``pre`` and ``m<digits>`` mark a pre-release; ``jre``, ``android``, ``final``,
    ``ga`` and ``release`` do not.
    """
    tokens = [t.lower() for t in _VERSION_TOKEN.findall(version)]
    for i, tok in enumerate(tokens):
        if tok in _UNSTABLE:
            return False
        if tok == "m" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            return False
    return True


def maven_version_key(version: str) -> tuple[tuple[int, ...], str]:
    """Return a sort key following Maven's numeric-component order.

    The leading numeric components compare as integers (``2.9`` < ``2.10``;
    trailing zeros are insignificant, ``1.0`` == ``1.0.0``); the remaining
    suffix breaks ties as a lower-case string (``33.8.0-jre`` > ``-android``).
    """
    tokens = _VERSION_TOKEN.findall(version)
    numbers: list[int] = []
    for tok in tokens:
        if not tok.isdigit():
            break
        numbers.append(int(tok))
    rest = "".join(tokens[len(numbers) :]).lower()
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers), rest


def _is_date_stamp(version: str) -> bool:
    first = _VERSION_TOKEN.findall(version)[:1]
    return bool(first) and first[0].isdigit() and len(first[0]) >= _DATE_STAMP_DIGITS


def maven_latest_stable(versions: Collection[str]) -> str | None:
    """Return the highest stable version of ``versions``, or ``None``.

    Pre-releases are dropped (:func:`maven_is_stable`). A version whose first
    component is a date stamp (8 or more digits, ``20030203.000550``) would
    outrank every real release numerically, so it is used only when nothing
    else is stable.
    """
    stable = [v for v in versions if maven_is_stable(v)]
    real = [v for v in stable if not _is_date_stamp(v)] or stable
    return max(real, key=lambda v: (maven_version_key(v), v), default=None)


def _maven_release(http: HttpClient, name: str, fallback: str) -> str:
    """Return the highest stable version in the artifact's ``maven-metadata.xml``.

    ecosyste.ms ranks versions by publication date, so an old artifact
    republished late (``commons-io`` 20030203.000550) is reported as the latest,
    and Central's ``<release>`` can be a pre-release (``3.10.1-RC2``). The
    ``<versions>`` list is authoritative. If it cannot be read or holds no stable
    version, keep the ecosyste.ms version and say so.
    """
    group, _, artifact = name.partition(":")
    url = MAVEN_METADATA_URL.format(path=f"{group.replace('.', '/')}/{artifact}")
    try:
        resp = http.get(url)
    except (FetchFailed, OfflineMiss) as exc:
        _LOG.warning("%s: no maven-metadata.xml (%s); keeping %s", name, exc, fallback)
        return fallback
    text = resp.body.decode("utf-8", "replace") if resp.body else ""
    best = maven_latest_stable(_MAVEN_VERSIONS.findall(text))
    if best is None:
        _LOG.warning("%s: no stable version in %s; keeping %s", name, url, fallback)
        return fallback
    return best


def _seed(eco: Ecosystem, row: _Row, stratum: str, http: HttpClient) -> Seed:
    version = (
        _maven_release(http, row.name, row.version) if eco == "maven" else row.version
    )
    return Seed(
        eco=eco,
        name=row.name,
        version=version,
        stratum=cast(Any, stratum),
        rank=row.rank,
    )


def frame(
    eco: Ecosystem,
    http: HttpClient,
    *,
    top: int,
    mid: int,
    seed: int = DEFAULT_SEED,
    mid_pages: int = 4,
    exclude: Collection[str] = frozenset(),
) -> Frame:
    """Select the ``top`` and ``mid`` seeds of ``eco`` with their provenance."""
    if top < 0 or mid < 0:
        raise ValueError("top and mid must not be negative")
    span = MID_LAST_PAGE - MID_FIRST_PAGE + 1
    if mid and not 1 <= mid_pages <= span:
        raise ValueError(f"mid_pages must be 1..{span}")
    taken = set(exclude)
    seeds: list[Seed] = []
    urls: list[str] = []
    dates: list[str] = []
    for page in range(1, math.ceil(top / PAGE_SIZE) + 1):
        rows, url, date = _read_page(eco, http, page)
        urls.append(url)
        dates.append(date)
        for row in rows:
            if len(seeds) < top and row.name not in taken:
                taken.add(row.name)
                seeds.append(_seed(eco, row, "top", http))
    if len(seeds) < top:
        _LOG.warning("%s: top frame holds %d of %d seeds", eco, len(seeds), top)
    if mid:
        rng = random.Random(seed)
        pages = sorted(rng.sample(range(MID_FIRST_PAGE, MID_LAST_PAGE + 1), mid_pages))
        per_page = math.ceil(mid / mid_pages)
        picked: list[_Row] = []
        for page in pages:
            rows, url, date = _read_page(eco, http, page)
            urls.append(url)
            dates.append(date)
            pool = [r for r in rows if r.name not in taken]
            for row in rng.sample(pool, min(per_page, len(pool))):
                if len(picked) < mid and row.name not in taken:
                    taken.add(row.name)
                    picked.append(row)
        if len(picked) < mid:
            _LOG.warning("%s: mid frame holds %d of %d seeds", eco, len(picked), mid)
        seeds.extend(_seed(eco, r, "mid", http) for r in picked)
    return Frame(seeds, urls, min(dates) if dates else "")


def select(
    eco: Ecosystem,
    http: HttpClient,
    *,
    top: int,
    mid: int,
    seed: int = DEFAULT_SEED,
    mid_pages: int = 4,
    exclude: Collection[str] = frozenset(),
) -> list[Seed]:
    """Return the ``top`` then ``mid`` seeds of ``eco`` (see :func:`frame`)."""
    return frame(
        eco, http, top=top, mid=mid, seed=seed, mid_pages=mid_pages, exclude=exclude
    ).seeds


def _note_line(eco: str, fr: Frame, top: int, mid: int, seed: int) -> str:
    return (
        f"- {eco}: frame {fr.date}, top={top}, mid={mid}, seed={seed}, "
        f"urls: {' '.join(fr.urls)}"
    )


def _append_note(path: Path, line: str) -> None:
    """Append ``line`` to the seeds README unless it is already there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if line in existing.split("\n"):
        return
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        if not existing:
            fh.write("# Seed frames\n\n")
        elif not existing.endswith("\n"):
            fh.write("\n")
        fh.write(line + "\n")


def run(
    eco: Ecosystem,
    http: HttpClient,
    seeds_dir: Path,
    *,
    top: int,
    mid: int,
    seed: int = DEFAULT_SEED,
    mid_pages: int = 4,
) -> int:
    """Select for ``eco``, append to ``seeds_dir/<eco>.tsv``; return rows added.

    The draw depends only on the seed and the pages, so a rerun adds nothing. A
    name already in the file is never added with another version: its pinned row
    stays until the user removes it.
    """
    path = seeds_dir / f"{eco}.tsv"
    fr = frame(
        eco,
        http,
        top=top,
        mid=mid,
        seed=seed,
        mid_pages=mid_pages,
    )
    known = {s.name for s in read_seeds(path, eco)}
    added = append_seeds(path, [s for s in fr.seeds if s.name not in known])
    _append_note(seeds_dir / "README.md", _note_line(eco, fr, top, mid, seed))
    return added


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``select`` options to ``parser``."""
    parser.add_argument(
        "--eco",
        action="append",
        choices=sorted(get_args(Ecosystem)),
        help="ecosystem (repeatable; default: all)",
    )
    parser.add_argument("--top", type=int, default=40, help="top-stratum size")
    parser.add_argument("--mid", type=int, default=20, help="mid-stratum size")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--mid-pages", type=int, default=4)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    parser.add_argument("--offline", action="store_true")


def run_cli(args: argparse.Namespace) -> int:
    """Run ``select`` for the parsed ``args``; return the exit status."""
    http = HttpClient(args.cache_dir, offline=args.offline)
    ecos: list[Ecosystem] = args.eco or sorted(get_args(Ecosystem))
    for eco in ecos:
        try:
            added = run(
                eco,
                http,
                args.data_dir / "seeds",
                top=args.top,
                mid=args.mid,
                seed=args.seed,
                mid_pages=args.mid_pages,
            )
        except (FrameError, ValueError) as exc:
            print(f"ERROR: select {eco}: {exc}", file=sys.stderr)
            return 1
        print(f"INFO: select {eco}: {added} seeds added", file=sys.stderr)
    return 0
