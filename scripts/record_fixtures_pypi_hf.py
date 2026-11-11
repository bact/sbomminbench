# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Record the PyPI and Hugging Face test fixtures (network, run by hand).

Usage::

    uv run python scripts/record_fixtures_pypi_hf.py [--only NAME ...]

For each seed below it runs the real ``fetch`` through ``HttpClient`` into
``tests/fixtures/cache`` (HTTP bodies, extracted ``members/``, ``index/``),
writes ``tests/fixtures/seeds/{pypi,huggingface}.tsv`` and scrubs e-mail
addresses. Downloaded archives stay in the gitignored
``.cache/artifacts``. One thread; ``HttpClient`` rate-limits each host. A
Hugging Face seed without a pinned sha takes the current one from
``/api/models/{id}`` (fetched into a scratch cache that is not kept).

Scrubbing: extracted members get ``email.redacted@example.com``, which the
guard test accepts. HTTP bodies get the stand-in ``fixture.scrubbed@example.invalid``
instead, so the redactor still sees an address there and ``has_email`` stays
true in the tests that exercise it.
"""

import argparse
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from _fixture_scrub import scrub_recording

from sbomminbench.eco import huggingface, pypi
from sbomminbench.eco._pkgfetch import EcoError
from sbomminbench.eco.base import write_index
from sbomminbench.http import FetchFailed, HttpClient
from sbomminbench.seeds import Seed, append_seeds

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
MAX_BYTES = 50 * 1024 * 1024

# name, version, note
PYPI: list[tuple[str, str, str]] = [
    ("requests", "2.32.3", "legacy Author/Author-email pair; unresolvable trove"),
    ("python-dateutil", "2.9.0.post0", "prose Dual License plus two troves"),
    ("pytest", "8.3.3", "7 names in one string plus Others (See AUTHORS)"),
    ("numpy", "2.1.2", "46 KB licence text in License; et al."),
    ("silero-vad", "5.1.2", "trove only; 2 embedded .onnx plus a .jit"),
    ("Flask", "3.0.3", "maintainers only"),
    ("packaging", "26.3", "PEP 639 licence expression"),
    ("magika", "0.6.1", "embedded onnx; [project.license] table"),
    ("torch", "2.5.0", "wheels only, over the cap: PEP 658 path"),
]
# model id, pinned sha ("" = current), note
HF: list[tuple[str, str, str]] = [
    ("google-bert/bert-base-uncased", "", "apache-2.0 lexical"),
    ("meta-llama/Llama-3.1-8B", "", "gated; llama3.1 not an SPDX id"),
    (
        "Qwen/Qwen2.5-72B-Instruct",
        "495f39366efef23836d0cfae4fbe635880d2be31",
        "other plus license_name/license_link",
    ),
    ("stabilityai/stable-diffusion-xl-base-1.0", "", "openrail++"),
    ("gpt2", "", "legacy id redirects: hf_renamed"),
]


def current_sha(model_id: str) -> str:
    """The current revision of *model_id* (scratch cache, not kept)."""
    with tempfile.TemporaryDirectory(prefix="sbomminbench-sha-") as tmp:
        client = HttpClient(Path(tmp))
        url = f"https://huggingface.co/api/models/{model_id}"
        doc, _ = client.get_json(url)
    sha = doc.get("sha") if isinstance(doc, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"no sha for {model_id!r}")
    return sha


def record(
    seeds: list[Seed], fetch: Any, client: HttpClient, cache_dir: Path
) -> list[Seed]:
    done: list[Seed] = []
    for seed in seeds:
        try:
            index = fetch(seed, client, max_bytes=MAX_BYTES)
        except (EcoError, FetchFailed, OSError) as exc:  # go on with the next seed
            print(f"ERROR: {seed.name}@{seed.version}: {exc}", file=sys.stderr)
            continue
        write_index(index, cache_dir)
        scrub_recording(cache_dir, index)
        print(f"INFO: recorded {index.purl}", file=sys.stderr)
        done.append(seed)
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=FIXTURES / "cache")
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / ".cache/artifacts")
    parser.add_argument("--only", nargs="*", default=[], help="seed names to record")
    args = parser.parse_args(argv)
    client = HttpClient(args.cache_dir, artifact_dir=args.artifact_dir)
    keep = set(args.only)
    py = [
        Seed("pypi", n, v, "edge", None, note)
        for n, v, note in PYPI
        if not keep or n in keep
    ]
    hf = [
        Seed("huggingface", n, sha or current_sha(n), "edge", None, note)
        for n, sha, note in HF
        if not keep or n in keep
    ]
    py_done = record(py, pypi.fetch, client, args.cache_dir)
    hf_done = record(hf, huggingface.fetch, client, args.cache_dir)
    append_seeds(FIXTURES / "seeds" / "pypi.tsv", py_done)
    append_seeds(FIXTURES / "seeds" / "huggingface.tsv", hf_done)
    return 0 if len(py_done) == len(py) and len(hf_done) == len(hf) else 1


if __name__ == "__main__":
    sys.exit(main())
