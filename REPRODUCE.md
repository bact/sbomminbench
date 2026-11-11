---
Created: 2026-11-09
Last-Modified: 2026-11-11
SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
SPDX-FileType: DOCUMENTATION
SPDX-License-Identifier: CC0-1.0
---

# Reproduce or update sbomminbench

You need Python 3.12, `uv` and network access. You do not need an AI agent, an account or a secret. Commands are the same on Linux, macOS and Windows; Windows users run them one by one in PowerShell instead of `scripts/reproduce.sh` (a POSIX shell script).

See also: [README.md](README.md), [docs/data-card.md](docs/data-card.md).

## 1. Prerequisites and pins

- Python 3.12 (`requires-python = ">=3.12"`, `.python-version`).
- `uv`, from <https://docs.astral.sh/uv/>.
- Pinned in `pyproject.toml` and locked in `uv.lock`: `pitloom[ai,content-type]==0.24.0`, `licenseid==0.8.4`, `packaging==26.3`. Never mix other versions: the derived values depend on them.
- The BOM ontology 0.2.0 is vendored in `vendor/bom/` (checksums in `vendor/bom/SHA256SUMS`); nothing is downloaded for it.

## 2. Reproduce

`build` is offline and byte-identical from the same cache. `fetch` returns what the registries serve today, so a fresh fetch may differ from the release. The fetch cache of the release is not published: it holds registry responses as served, with the e-mail addresses that the dataset leaves out. So a rebuild from a fresh fetch shows what changed upstream since the release, and `rederive-diff` (below) lists it; the released files themselves are checked with `data/release/SHA256SUMS`.

```bash
uv sync --locked
mkdir -p reproduce-out/data
cp -R data/seeds reproduce-out/data/seeds
cp -R data/reviews reproduce-out/data/reviews
uv run sbomminbench fetch --data-dir reproduce-out/data --cache-dir reproduce-out/cache
uv run sbomminbench build --data-dir reproduce-out/data --cache-dir reproduce-out/cache
uv run sbomminbench validate --data-dir reproduce-out/data --cache-dir reproduce-out/cache
uv run sbomminbench export --data-dir reproduce-out/data --cache-dir reproduce-out/cache
```

Seeds are read from `<data-dir>/seeds/<eco>.tsv` and the stored human reviews from `<data-dir>/reviews/*.jsonl`, so a directory other than `data/` needs the two copies above. `export` applies the reviews: without them the reviewed values are missing from the release files and the checksums differ. `export` writes `entries.jsonl`, `subjects.csv`, `observations.csv`, `stats.json`, `standard-elements.json`, `benchmark.csv`, `benchmark-unscored.csv` and `SHA256SUMS` under `<data-dir>/release/`. Compare the checksums:

```bash
diff data/release/SHA256SUMS reproduce-out/data/release/SHA256SUMS
uv run sbomminbench rederive-diff --data-dir data --cache-dir reproduce-out/cache
```

`rederive-diff` re-derives every entry from a cache and lists the entries that differ from the stored ones.

On Windows, replace `mkdir -p` and `cp -R` with `New-Item -ItemType Directory -Force` and `Copy-Item -Recurse`; the `uv run sbomminbench ...` lines are unchanged.

The same steps in one command (Linux and macOS):

```bash
scripts/reproduce.sh reproduce-out
```

It refuses `data` as the output directory, writes step output to `reproduce-out/logs/`, prints `INFO:` and `ERROR:` lines on stderr and `KEY=VALUE` lines on stdout, and stops at the first failure. It compares the rebuilt `SHA256SUMS` with `data/release/SHA256SUMS`. If that file is absent it prints the rebuilt sums and says so (`CHECKSUMS=unchecked`). The cache of an earlier run can be replayed offline, which gives the same bytes as that run:

```bash
SBOMMINBENCH_REPLAY_CACHE=path/to/cache scripts/reproduce.sh reproduce-out
```

Replay skips `uv sync` and `fetch`; run `uv sync --locked` yourself first.

## 3. Update the collection

Work in pilot mode (section 5) until the result looks right.

```bash
uv run sbomminbench select --eco pypi --top 100
uv run sbomminbench fetch --eco pypi
uv run sbomminbench build
uv run sbomminbench validate
uv run sbomminbench export
```

- `select` appends new seeds to `data/seeds/<eco>.tsv`; existing rows stay.
- `fetch` reads the cache first, so rerunning it only requests what is new. `--only NAME` limits it to one package; `--refresh` ignores the cache.
- Entries the auto-derivation could not settle go to the review queue:

```bash
uv run sbomminbench review-export --out queue.csv
uv run sbomminbench review-import queue.csv --reviewer your-id
```

- Human reviews are kept. If a later build changes the automatic answer, `validate` reports `stale_review` for that element and keeps the review until you look at it.

## 4. Upgrade a dependency

```bash
uv lock --upgrade-package licenseid
uv sync --locked
uv run sbomminbench build
uv run sbomminbench rederive-diff
```

`rederive-diff` shows which entries change with the new version. Update the pin in `pyproject.toml` and in every document that names it, and decide whether a changed value is an improvement before you export.

## 5. Pilot mode

Before any live fetch, keep data and cache apart from the real ones:

```bash
mkdir -p .pilot/data
cp -R data/seeds .pilot/data/seeds
uv run sbomminbench fetch --eco npm --only express --data-dir .pilot/data --cache-dir .pilot/cache
uv run sbomminbench build --data-dir .pilot/data --cache-dir .pilot/cache
```

Nothing under `data/` is written in pilot mode. Delete `.pilot/` when done.

## 6. Troubleshooting

- **Rate limits (HTTP 429, 5xx):** requests are retried with a delay. If seeds still fail, rerun `fetch`; finished seeds come from the cache.
- **`failed.tsv`:** `data/seeds/failed.tsv` lists the seeds that could not be fetched or built, with the reason. Exit status 0 does not mean no failures; read this file. Fix the cause, then rerun `fetch` and `build`.
- **Gated or renamed Hugging Face models:** metadata is read from the public API without a token. A gated model still gets its public metadata and the tag `hf_gated`; a renamed one gets `hf_renamed`.
- **`ERROR: seeds directory not found`:** run from a clone that contains `data/seeds`, or set `SBOMMINBENCH_SEEDS_DIR`.
- **Checksums differ after a fresh `fetch`:** expected when a registry changed; `rederive-diff` lists the entries that differ. A replay from the cache of an earlier run (`SBOMMINBENCH_REPLAY_CACHE`) gives the same bytes as that run.
- **Licence values differ:** check that `uv.lock` pins `licenseid==0.8.4` and that you ran `uv sync --locked`.

## 7. What needs a human

- Needs a human: answering the review queue (`review-export`, then `review-import`), and judging a changed value after a dependency upgrade.
- Never needs one: `select`, `fetch`, `build`, `validate`, `export`, `stats` and `rederive-diff`. They run without prompts and without secrets.

## 8. Documents and the model-header probe

These are not part of `scripts/reproduce.sh`; run them after `export`.

```bash
uv run python scripts/make_data_card_tables.py
```

It rewrites the generated tables of `docs/data-card.md` from `data/release/` (`--check` only reports whether they are up to date).

The model-file header probe is separate from the dataset and writes to `experimental/`:

```bash
uv run python scripts/s1_embedded_model_headers.py
```

```bash
uv run python scripts/s1_hf_model_headers.py --repos 640
```

The second text matcher needs Java and its own Redis database (see the script's docstring):

```bash
uv run --with license-matcher==2.11.1 python scripts/s4_spdx_license_matcher.py
```

The comparison of licence readers is offline:

```bash
uv run python scripts/s3_normaliser_comparison.py
```

```bash
uv run licenseid --db licenses.db update --source packaged
```

```bash
uv run python scripts/s2_license_text_crosscheck.py --db licenses.db
```

- The first is offline and needs the fetch cache (`.cache/`) of a run in this directory.
- The second uses the network, one request per second, and can take a few hours. It reads the ranking of the day, so the repositories beyond the dataset's own differ from run to run. It stops by itself when the host keeps answering "too many requests"; run it again to continue.
- The licence text cross-check (the last two commands) builds a LicenseID database from the copy shipped with LicenseID, then fetches the licence URLs of the unsettled entries, one request per second. `--no-network` uses only what is cached.
