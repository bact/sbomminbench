# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Record the npm and cargo test fixtures (network; run by hand, not by pytest).

    uv run python scripts/record_fixtures_npm_cargo.py [--only NAME ...]

Writes ``tests/fixtures/seeds/{npm,cargo}.tsv`` and, under
``tests/fixtures/cache``, ``http/`` (small JSON bodies), ``members/`` (the
extracted manifests) and ``index/``. The archives themselves go to a
temporary directory that is removed afterwards. E-mail addresses are scrubbed
as ``_fixture_scrub`` describes: a stand-in in bodies, the
placeholder in members.
Requests are sequential and rate limited by ``HttpClient``.
"""

import argparse
import logging
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from _fixture_scrub import scrub_recording

from sbomminbench.eco import cargo, npm
from sbomminbench.eco._pkgfetch import EcoError
from sbomminbench.eco.base import write_index
from sbomminbench.http import HttpClient
from sbomminbench.seeds import Seed, append_seeds

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
MAX_BYTES = 50 * 1024 * 1024

NPM = [
    ("express", "4.21.1", "registry author object; contributors"),
    ("@babel/core", "7.26.0", "scoped name; author string with (url)"),
    ("json-schema", "0.2.3", "legacy licenses[] objects, non-SPDX types"),
    ("sha.js", "2.4.11", "(MIT AND BSD-3-Clause) with parentheses"),
    ("@types/node", "22.9.0", "no author, contributors only"),
]
CARGO = [
    ("serde", "1.0.210", "MIT OR Apache-2.0 reordered"),
    ("winapi", "0.3.9", "MIT/Apache-2.0"),
    ("ring", "0.16.20", "platform non-standard plus license-file"),
    ("tokio", "1.41.0", "authors probably Tokio Contributors"),
    ("clap", "4.5.20", "license.workspace = true in .orig"),
]


def record(eco: str, rows: list[tuple[str, str, str]], only: set[str]) -> None:
    module = npm if eco == "npm" else cargo
    seeds = [Seed(eco, n, v, "edge", None, note) for n, v, note in rows]  # type: ignore[arg-type]
    append_seeds(FIXTURES / "seeds" / f"{eco}.tsv", seeds)
    cache = FIXTURES / "cache"
    with tempfile.TemporaryDirectory(prefix="sbomminbench-rec-") as tmp:
        http = HttpClient(cache, artifact_dir=Path(tmp))
        for seed in seeds:
            if only and seed.name not in only:
                continue
            try:
                index = module.fetch(seed, http, max_bytes=MAX_BYTES)
            except EcoError as exc:
                print(
                    f"ERROR: {eco} {seed.name}@{seed.version}: {exc}", file=sys.stderr
                )
                continue
            scrub_recording(cache, index)
            write_index(index, cache)
            print(f"INFO: recorded {index.purl}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", default=[], help="package names")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    record("npm", NPM, set(args.only))
    record("cargo", CARGO, set(args.only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
