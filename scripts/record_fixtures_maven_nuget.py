# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Record the maven and nuget test fixtures (network; run once, then commit).

Usage:  uv run python scripts/record_fixtures_maven_nuget.py [--eco maven|nuget]

Reads ``tests/fixtures/seeds/{maven,nuget}.tsv``, fetches every seed into
``tests/fixtures/cache`` (``http/`` and ``index/``), one request at a time with a
pause between packages, then replaces every e-mail address in the recorded
bodies by the stand-in of ``_fixture_scrub`` (the POMs and catalog entries
carry real ones).
Finally it writes the synthetic DOCTYPE POM fixture (no network).
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import requests
from _fixture_scrub import scrub_body

from sbomminbench.eco import maven, nuget
from sbomminbench.eco.base import FetchIndex, write_index
from sbomminbench.http import HttpClient
from sbomminbench.seeds import Seed, read_seeds

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CACHE = FIXTURES / "cache"
MAX_BYTES = 200 * 1024 * 1024
PAUSE = 1.0

SYNTHETIC_NAME = "example.synthetic:doctype-pom"
SYNTHETIC_VERSION = "1.0"
SYNTHETIC_FETCHED_AT = "2026-11-09T00:00:00Z"
SYNTHETIC_POM = b"""<?xml version="1.0"?>
<!DOCTYPE project [<!ENTITY lic "Apache-2.0">]>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>example.synthetic</groupId>
  <artifactId>doctype-pom</artifactId>
  <version>1.0</version>
  <licenses><license><name>&lic;</name></license></licenses>
</project>
"""


def record(eco: str, http: HttpClient) -> list[str]:
    """Fetch the seeds of *eco*; return every URL recorded."""
    urls: list[str] = []
    fetcher: Any = maven if eco == "maven" else nuget
    seeds = read_seeds(FIXTURES / "seeds" / f"{eco}.tsv", eco)  # type: ignore[arg-type]
    for seed in seeds:
        print(f"INFO: fetching {eco} {seed.name} {seed.version}", file=sys.stderr)
        index = fetcher.fetch(seed, http, max_bytes=MAX_BYTES)
        write_index(index, CACHE)
        for url in sorted(set(index.responses.values())):
            scrub_body(CACHE, url)
        urls += index.responses.values()
        time.sleep(PAUSE)
    return urls


class _Reply:
    status_code = 200
    content = SYNTHETIC_POM
    headers = requests.structures.CaseInsensitiveDict({"Content-Type": "text/xml"})
    url = ""

    def close(self) -> None:
        pass


class _Session:
    def get(self, url: str, **_: Any) -> _Reply:
        reply = _Reply()
        reply.url = url
        return reply


def record_synthetic() -> None:
    """A POM that declares a DTD: SYNTHETIC, not a real Central artefact."""
    seed = Seed("maven", SYNTHETIC_NAME, SYNTHETIC_VERSION, "edge", None)
    coord = maven.seed_coord(seed)
    http = HttpClient(CACHE, session=_Session(), sleep=lambda _: None)  # type: ignore[arg-type]
    http.get(coord.url)  # writes the cache record
    write_index(
        FetchIndex(
            ecosystem="maven",
            name=seed.name,
            version=seed.version,
            purl=coord.purl,
            stratum="edge",
            rank=None,
            responses={"pom": coord.url},
            artifact=None,
            members_dir=None,
            members={},
            fetched_at=SYNTHETIC_FETCHED_AT,
            tags=["artifact_not_scanned"],
        ),
        CACHE,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eco", choices=["maven", "nuget"], action="append")
    args = parser.parse_args()
    http = HttpClient(CACHE)
    urls: list[str] = []
    for eco in args.eco or ["maven", "nuget"]:
        urls += record(eco, http)
    print(f"INFO: scrubbed {len(set(urls))} URL(s)", file=sys.stderr)
    record_synthetic()
    return 0


if __name__ == "__main__":
    sys.exit(main())
