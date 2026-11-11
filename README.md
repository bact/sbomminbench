---
Created: 2026-11-09
Last-Modified: 2026-11-11
SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
SPDX-FileType: DOCUMENTATION
SPDX-License-Identifier: CC0-1.0
---

# SBOM-MinBench

SBOM-MinBench is a dataset of package and model metadata with expected SBOM elements (name, provider, licence), a collection script and an evaluator.

It covers one part of SBOM generation: reading the name, the authors and the licence of a component from registry and manifest metadata. It does not test a whole SBOM (dependencies, hashes, schema validity).

Licences:

- Data: CC-BY-4.0 (see `LICENSE`), with one exception.
- Exception: the seed lists in `data/seeds/` and the `rank` values wherever they appear (`data/entries/`, `entries.jsonl`, `subjects.csv`) are derived from [Ecosyste.ms](https://packages.ecosyste.ms/) data and are under CC-BY-SA-4.0 (see `LICENSE-DATA-SEEDS`). Attribution: Ecosyste.ms: Packages, rankings read on 2026-11-09.
- Code (`src/`, `scripts/`, `tests/`): Apache-2.0 (see `LICENSE-CODE`).

## Quick start

Needs Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
```

```bash
uv run sbomminbench validate
```

```bash
uv run sbomminbench evaluate --gold data/release/benchmark.csv --self-test
```

`validate` checks the entries and the release files. The self-test scores the expected values against themselves and fails unless every rate is 1.0.

## Data

| Path | Content |
| --- | --- |
| `data/release/benchmark.csv` | One row per subject with the expected name, provider and licence. Start here. |
| `data/release/benchmark-unscored.csv` | Subjects with no scored value, and why. |
| `data/release/entries.jsonl` | Every entry in full: platform metadata, manifest metadata as stored, expected values with their evidence. |
| `data/release/subjects.csv`, `observations.csv` | Flat views of the subjects and of each raw observation. |
| `data/release/stats.json` | Counts used in the data card and the paper. |
| `data/release/standard-elements.json` | The three elements mapped to minimum-elements standards. |
| `data/release/SHA256SUMS` | Checksums of the release files. |
| `data/entries/` | One JSON file per entry, as built (before human review is applied). |
| `data/seeds/` | The selected packages and models, with stratum and rank. |
| `data/reviews/` | Human review decisions, one line each. |
| `experimental/` | Non-voting probes: model-file headers, licence-text cross-check with two text matchers, comparison of licence readers. Not part of the dataset. |

How entries were selected and curated, what the statuses mean, ethics and limitations: see [docs/data-card.md](docs/data-card.md).

An entry is one release of a package (PyPI, npm, Cargo, Maven, NuGet) or one revision of a Hugging Face model repository. Its id is a package URL, for example `pkg:pypi/requests@2.32.3`.

## Schema

The entry schema is generated into `schema/entry.schema.json` by `sbomminbench export-schema`.

## Evaluate

Write your tool's answers to a CSV file with the columns `id`, `name`, `provider` and `license`:

```csv
id,name,provider,license
pkg:cargo/aho-corasick@1.1.5,aho-corasick,Andrew Gallant,MIT OR Unlicense
```

- `id` must equal the id in `benchmark.csv`.
- `provider` holds the author names, joined by `; `.
- `license` is an SPDX expression. The order of operands does not matter.
- An empty cell means "no answer" (an abstention). A missing row or column counts as a miss.
- An optional `subject` column names a model file inside a package (`model:1`); the default is `root`.

```bash
uv run sbomminbench evaluate --gold data/release/benchmark.csv --pred predictions.csv
```

The output is one `KEY=VALUE` line per metric, with a confidence interval. `--format markdown` or `--format json` gives the same numbers in another form, and `--by-standard` reports them per minimum-elements standard. JSONL predictions and the full `entries.jsonl` as gold are accepted too; see `sbomminbench evaluate --help`.

## Reproduce

`scripts/reproduce.sh` rebuilds the release into a separate directory and compares the checksums. See [REPRODUCE.md](REPRODUCE.md).

## Credits

Candidate packages and models were selected from the rankings of [Ecosyste.ms: Packages](https://packages.ecosyste.ms/) (data under CC BY-SA 4.0); only names, release numbers and rank positions are taken from it. All metadata values were read from the registries themselves: PyPI, npm, crates.io, Maven Central, NuGet and the Hugging Face Hub. See [docs/data-card.md](docs/data-card.md#sources-and-credits).

## Author

Arthit Suriyawongkul (ORCID: [0000-0002-9698-1899](https://orcid.org/0000-0002-9698-1899)). To cite, see `CITATION.cff`.

## Takedown contact

To ask for a name or another value to be removed or corrected, open an issue at <https://github.com/bact/sbomminbench/issues>. Say which entry it is (its `id`) and what should change. Do not put personal data you want removed into the issue text itself; naming the entry and the field is enough.
