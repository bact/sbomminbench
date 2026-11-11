# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""The ``fetch`` driver: seeds in, cache and ``FetchIndex`` files out.

The only code path of the pipeline (besides ``select``) that uses the network.
Rate limits, retries and the size cap live in ``HttpClient``; a seed that
cannot be fetched is logged in ``failed.tsv`` and the run continues.
"""

import argparse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from sbomminbench import console
from sbomminbench.eco import REGISTRY
from sbomminbench.eco._pkgfetch import EcoError
from sbomminbench.eco.base import FetchIndex, write_index
from sbomminbench.failures import record_once
from sbomminbench.http import FetchFailed, HttpClient
from sbomminbench.ids import canonical_name_key
from sbomminbench.schema import Ecosystem
from sbomminbench.seeds import Seed, read_seeds

MIB = 1024 * 1024


@dataclass(frozen=True)
class SeedResult:
    """The outcome of one seed: an index, or the stage and reason it failed."""

    seed: Seed
    index: FetchIndex | None
    stage: str = ""
    reason: str = ""


@dataclass
class FetchSummary:
    fetched: int = 0
    failed: int = 0


def fetch_one(
    seed: Seed, http: HttpClient, cache_dir: Path, max_bytes: int
) -> SeedResult:
    """Fetch one seed and write its index; a fetch failure is a result."""
    try:
        index = REGISTRY[seed.eco].fetch(seed, http, max_bytes=max_bytes)
    except EcoError as exc:
        return SeedResult(seed, None, exc.stage, exc.reason)
    except FetchFailed as exc:
        return SeedResult(seed, None, "http", str(exc))
    write_index(index, cache_dir)
    return SeedResult(seed, index)


def read_selected_seeds(
    data_dir: Path, ecos: Sequence[Ecosystem], only: Sequence[str]
) -> list[Seed]:
    """Seeds of ``<data_dir>/seeds/<eco>.tsv`` in file order, filtered by name."""
    chosen: list[Seed] = []
    for eco in sorted(ecos):
        wanted = {canonical_name_key(eco, n) for n in only}
        chosen += [
            s
            for s in read_seeds(data_dir / "seeds" / f"{eco}.tsv", eco)
            if not wanted or canonical_name_key(eco, s.name) in wanted
        ]
    return chosen


def _run_pool(
    seeds: Sequence[Seed], http: HttpClient, cache_dir: Path, jobs: int, max_bytes: int
) -> list[SeedResult]:
    pool = ThreadPoolExecutor(max_workers=max(1, jobs))
    try:
        futures = [
            pool.submit(fetch_one, seed, http, cache_dir, max_bytes) for seed in seeds
        ]
        results = [f.result() for f in futures]
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return results


def run_fetch(
    http: HttpClient,
    data_dir: Path,
    cache_dir: Path,
    ecos: Sequence[Ecosystem],
    only: Sequence[str],
    *,
    jobs: int = 8,
    max_bytes: int = 50 * MIB,
) -> FetchSummary:
    """Fetch the selected seeds; failures go to ``<data_dir>/seeds/failed.tsv``.

    Results are reported in seed order, whatever order the threads finish in.
    """
    seeds = read_selected_seeds(data_dir, ecos, only)
    summary = FetchSummary()
    for res in _run_pool(seeds, http, cache_dir, jobs, max_bytes):
        s = res.seed
        ident = (("ECO", s.eco), ("NAME", s.name), ("VERSION", s.version))
        if res.index is not None:
            summary.fetched += 1
            console.print_kv(*ident, ("RESULT", "fetched"))
            continue
        summary.failed += 1
        record_once(
            data_dir / "seeds" / "failed.tsv",
            s.eco,
            s.name,
            s.version,
            res.stage,
            res.reason,
        )
        console.print_kv(
            *ident,
            ("RESULT", "failed"),
            ("STAGE", res.stage),
            ("REASON", res.reason),
        )
    return summary


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``fetch`` options to *parser*."""
    console.add_common_options(parser)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="where archives are kept (default: <cache-dir>/artifacts)",
    )
    parser.add_argument("-j", "--jobs", type=int, default=8)
    parser.add_argument(
        "--refresh", action="store_true", help="ignore the cache and request again"
    )
    parser.add_argument(
        "--max-mb", type=int, default=50, help="archive size cap in MiB"
    )


def run_cli(args: argparse.Namespace) -> int:
    """Run ``fetch`` for the parsed *args*; return the exit status."""
    http = HttpClient(
        args.cache_dir, artifact_dir=args.artifact_dir, refresh=args.refresh
    )
    summary = run_fetch(
        http,
        args.data_dir,
        args.cache_dir,
        console.resolve_ecos(args.eco),
        args.only,
        jobs=args.jobs,
        max_bytes=args.max_mb * MIB,
    )
    console.info(
        f"fetch: {summary.fetched} fetched, {summary.failed} failed "
        "(see seeds/failed.tsv)"
    )
    return 0
