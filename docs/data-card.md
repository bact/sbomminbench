---
Created: 2026-11-11
Last-Modified: 2026-11-11
SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
SPDX-FileType: DOCUMENTATION
SPDX-License-Identifier: CC0-1.0
---

# Data card

SBOM-MinBench is a dataset of package and model metadata with the expected
values of three SBOM elements: the name, the provider and the licence. It
comes with the script that collected it and an evaluator.

The tables between `generated` markers are written by
`scripts/make_data_card_tables.py` from the release files; do not edit them by
hand.

## Purpose and scope

A tool that writes an SBOM has to read a component's name, who provides it and
its licence from metadata that was not written for that purpose. The dataset
turns this part of SBOM generation into three extraction tasks that can be
scored one by one: given what a registry and a manifest say, what is the name,
who are the authors, and what is the licence as an SPDX expression.

It does not test a whole SBOM: dependency graphs, hashes, file lists and
schema validity are out of scope.

## Content of this release

<!-- generated:entries:start -->

| Channel | top | mid | edge | Entries | Subjects |
| --- | --- | --- | --- | --- | --- |
| PyPI | 60 | 42 | 8 | 110 | 113 |
| npm | 60 | 42 | 5 | 107 | 107 |
| Cargo | 60 | 42 | 5 | 107 | 107 |
| Maven | 60 | 42 | 4 | 106 | 106 |
| NuGet | 60 | 42 | 6 | 108 | 108 |
| Hugging Face | 69 | 30 | 3 | 102 | 102 |
| All | 369 | 240 | 31 | 640 | 643 |

<!-- generated:entries:end -->

A subject is the package or model itself (`root`) or a model file found inside
a package (`model:N`). Status of the expected value, per element, over all
subjects:

<!-- generated:status:start -->

| Element | Unanimous | Corrected | Absent in source | Unresolved | Excluded | Not assessed | Subjects |
| --- | --- | --- | --- | --- | --- | --- | --- |
| name | 640 | 0 | 0 | 0 | 0 | 3 | 643 |
| provider | 550 | 9 | 80 | 1 | 0 | 3 | 643 |
| license | 535 | 61 | 27 | 2 | 15 | 3 | 643 |

<!-- generated:status:end -->

A value is scored when its status is unanimous, confirmed or corrected. The
other statuses are kept in the data and reported, and are never counted for or
against a tool.

<!-- generated:versions:start -->

| Item | Value |
| --- | --- |
| sbomminbench | 0.1.0 |
| Pitloom | 0.24.0 |
| LicenseID | 0.8.4 |
| SPDX licence list used to derive licences (LicenseID) | 3.29.0 |
| SPDX licence list used to classify forms (`packaging`) | 3.27.0 |
| Ranking read on | 2026-11-09 |

<!-- generated:versions:end -->

## How the list was curated, and why

The aim was a list that is small enough for one person to review, and that
still shows both the common case and the hard cases. Each step below says what
was done and why.

1. **Six channels.** PyPI, npm, Cargo, Maven and NuGet for packages, and
   Hugging Face for models. *Why:* each has its own manifest format and its
   own habits for stating authors and licences, and an SBOM tool meets all of
   them. Models are included because guidance on SBOMs for AI asks for the
   same elements.
2. **One ranking source for all channels.** The ecosyste.ms rankings. *Why:*
   one source, read on one day, gives the same definition of "popular"
   everywhere and one place to cite. A registry's own ranking exists for some
   channels only.
3. **A `top` stratum by rank.** *Why:* these are the components most SBOMs
   contain, so an error on them matters most.
4. **A `mid` stratum by seeded random draw from ranks 1,001 to 10,000.**
   *Why:* the most popular packages are also the best maintained, and their
   metadata is cleaner than usual. A random draw further down shows the
   ordinary case, and a fixed seed lets anyone redraw the same sample.
5. **An `edge` stratum chosen by hand.** *Why:* some forms are rare in a
   sample of this size but known to break tools, for example a licence in a
   parent POM or a licence given only as a file. They are marked, so results
   can be reported with and without them.
6. **Pinned versions.** One release, or one commit of a model repository.
   *Why:* metadata changes between releases; a pin makes an expected value a
   fact about one artefact.
7. **Everything stored as found.** Platform metadata and manifest fields are
   kept as raw observations with their source field. *Why:* the expected value
   can then be traced to its evidence, and a later tool version can re-derive
   it without fetching again.
8. **Expected values by agreement of independent sources.** A value is
   `auto_unanimous` when every source that states one resolves to the same
   value. *Why:* no single tool, including the ones used here, decides the
   answer alone. A copy of the same field in two places counts as one origin.
9. **Human review of what did not agree.** Values the sources could not
   settle went to a queue for one reviewer, who read the evidence and, where
   needed, the licence file. *Why:* these are the cases where a tool's guess
   is least reliable. Automatic answers and human decisions are stored apart,
   and a decision is flagged as stale if the automatic answer later changes.
10. **Nothing removed for being hard.** An unsettled value stays in the data
    with its status. *Why:* dropping it would make the ecosystems look cleaner
    than they are, and the count of unsettled values is itself a result.
11. **No e-mail addresses.** They are replaced by a placeholder at collection.
    *Why:* the elements under study do not need them.

## Selection criteria

An entry is one release of a package (PyPI, npm, Cargo, Maven, NuGet) or one
revision of a Hugging Face model repository. The same rules apply to all six
distribution channels.

**Source.** One source for every channel: the
[ecosyste.ms](https://packages.ecosyste.ms) package rankings, read on
2026-11-09. The ranking key is downloads, except for Maven, where download
counts are not published and the key is the number of dependent packages.

**Version.** Each seed is pinned to the latest release named by the ranking.
For Maven the latest stable release is read from `maven-metadata.xml`. For
Hugging Face the version is the commit hash of the revision.

**Strata.** Every entry records its stratum and, for `top` and `mid`, its rank.

| Stratum | Rule |
| --- | --- |
| `top` | Ranks 1 to N, in ranking order. |
| `mid` | Ranks 1,001 to 10,000: four ranking pages drawn with a fixed random seed, then rows drawn from each page with the same seed. Two such draws were made (seeds 20261107 and 20261111); the Hugging Face channel has the first only. |
| `edge` | Chosen by hand because of a known hard case. The seed file gives the reason for each, for example a licence written `MIT/Apache-2.0`, a licence inherited from a Maven parent, a package that names maintainers only, or a gated model. |

**Seeds per channel.**

| Channel | top | mid | edge | Seeds |
| --- | --- | --- | --- | --- |
| PyPI | 60 | 42 | 8 | 110 |
| npm | 60 | 42 | 5 | 107 |
| Cargo | 60 | 42 | 5 | 107 |
| Maven | 60 | 42 | 4 | 106 |
| NuGet | 60 | 42 | 6 | 108 |
| Hugging Face | 70 | 30 | 3 | 103 |

For the five package channels, `mid` is 20 seeds from the first draw and 22
from the second (24 were drawn; 2 per channel were already selected).

The 641 seeds give 640 entries. The `edge` seed `gpt2` is a legacy name that
redirects to `openai-community/gpt2`, which is also a `top` seed, so the two
make one entry (kept as `edge`). Twelve packages appear twice, at two versions:
the latest release in `top` and an older release in `edge` that shows the hard
case.

**Skipped rows.** A ranking row without a usable name or release is skipped. A
Hugging Face name without an owner part (`owner/name`) is skipped.

**Reproducing the selection.** `data/seeds/<channel>.tsv` holds the name,
version, stratum, rank and note of each seed. `data/seeds/README.md` holds the
ranking URLs, the date and the random seed. `sbomminbench select` redraws the
same `top` and `mid` seeds from the same ranking pages; see `REPRODUCE.md`.

**Limits of the selection.**

- `top` and `mid` follow popularity, so results describe widely used packages
  and models, not those below rank 10,000.
- `edge` entries are hand-picked and are not a sample. Report rates with and
  without them.
- The Maven ranking key differs from the other channels.
- A ranking changes over time: a later draw gives other packages and versions.
  The seed files are the record of this release.

## Sources and credits

**Ecosyste.ms.** The list of candidate packages and models comes from
[Ecosyste.ms: Packages](https://packages.ecosyste.ms/), an open API over
package registries. Without one ranking that covers six registries in the same
way, the selection could not have been made the same way for each channel.

- *What is used.* The ranking pages of each registry (ordered by downloads; by
  dependent packages for Maven), and from each row three things: the name, the
  latest release number, and the position in the list, stored as `rank`.
- *What is not used.* No licence, author, description or other metadata value
  of the dataset comes from Ecosyste.ms. Every observation is read from the
  registry itself or from the package's own files, so that the dataset tests
  the same inputs an SBOM tool reads.
- *Terms.* Ecosyste.ms publishes its data under
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The names,
  release numbers and ranks in `data/seeds/` and the `rank` field of an entry
  are taken from it, and are released under the same licence
  (`LICENSE-DATA-SEEDS`), attributed to Ecosyste.ms. The rest of the data is
  CC-BY-4.0. The ranking URLs and the date are in `data/seeds/README.md`.

**Registries.** Platform metadata and package files were read from PyPI, the
npm registry, crates.io, Maven Central, NuGet and the Hugging Face Hub, each
through its public API, with a cache and a rate limit. Names and licence
strings remain their owners'.

**Tools.** Pitloom reads author fields and recognises model files; LicenseID
resolves licence strings to SPDX expressions; the BOM ontology gives the
element ids of the minimum-elements standards. Versions are in the table
above.

**What this dataset adds.** The contribution is not the list of names. It is:
the design of the strata and the hand-picked hard cases; a pinned snapshot of
what each registry and each manifest states, kept as found with its source
field; expected values derived by agreement of independent sources and not by
one tool; a human review of what did not agree, stored apart from the
automatic answers; the mapping of the three elements to minimum-elements
standards; and an evaluator that scores each element on its own.

## Review

- One reviewer, who is the dataset's author and works on SPDX.
- The queue held the values with status `auto_unresolved`; the status table
  above shows how many were corrected and how many remain.
- A licence the SPDX list does not have is written as
  `LicenseRef-<channel>-<key>`, for example `LicenseRef-huggingface-llama3.1`.
  The evaluator credits the same key.
- A value the reviewer looked at and could not state as one expression is
  `excluded`, with a note. In this release these are 15 NuGet packages whose
  only licence information is one link, behind which the licence differs by
  platform (MIT on Linux and macOS, the .NET Library License on Windows).
- The review was not blind: the reviewer saw the candidates and the evidence.
- No audit sample of the unanimous values was reviewed in this release, so
  their precision is not measured (see Limitations).

## Minimum-elements standards

Each of the three elements is labelled with the matching element of several
SBOM minimum-elements documents, so a result can be reported per document
(`sbomminbench evaluate --by-standard`). The ids, labels and SPDX 3 targets
come only from the BOM ontology, release 0.2.0 (`https://w3id.org/bom/`,
retrieved 2026-11-09, vendored under `vendor/bom/`). "Fit" says how well the
element of the document matches the element of the dataset.

<!-- generated:standards:start -->

| Standard | Element | Label in the standard | BOM notation | Obligation | Fit | Applies to |
| --- | --- | --- | --- | --- | --- | --- |
| BSI TR-03183-2 | name | Component name | `BOM-BSI2025-COMP-002` | Requirement | exact | model, package |
| BSI TR-03183-2 | provider | Component creator | `BOM-BSI2025-COMP-001` | Requirement | exact | model, package |
| BSI TR-03183-2 | license | Original licences | `BOM-BSI2025-COMP-014` | ConditionalRequirement | exact | model, package |
| CERT-In 2025 | name | Model Name | `BOM-CERTIN2025-AIBOM-001` | Requirement | exact | model |
| CERT-In 2025 | name | Component Name | `BOM-CERTIN2025-SBOM-001` | Requirement | exact | package |
| CERT-In 2025 | provider | Model Developer | `BOM-CERTIN2025-AIBOM-004` | Requirement | exact | model |
| CERT-In 2025 | license | Model Licensing Information | `BOM-CERTIN2025-AIBOM-005` | Requirement | exact | model |
| CERT-In 2025 | license | Component License | `BOM-CERTIN2025-SBOM-005` | Requirement | exact | package |
| CISA 2026 | name | Component Name | `BOM-CISA2026-COMP-007` | Requirement | exact | model, package |
| CISA 2026 | provider | Component Producer | `BOM-CISA2026-COMP-001` | Requirement | exact | model, package |
| CISA 2026 | license | Component License | `BOM-CISA2026-COMP-006` | Requirement | exact | model, package |
| FSCT 2024 | name | Component Name | `BOM-FSCT2024-COMP-001` | Requirement | exact | model, package |
| FSCT 2024 | provider | Supplier | `BOM-FSCT2024-COMP-003` | Requirement | close | model, package |
| FSCT 2024 | license | Concluded License | `BOM-FSCT2024-COMP-007` | Requirement | close | model, package |
| G7 AI 2026 | name | Model name | `BOM-G7AI2026-MDL-001` | Requirement | exact | model |
| G7 AI 2026 | provider | Model producer | `BOM-G7AI2026-MDL-005` | Requirement | exact | model |
| G7 AI 2026 | license | Model license | `BOM-G7AI2026-MDL-012` | Requirement | exact | model |
| GPAI MDF 2025 | name | Model name | `BOM-GPAIMDF2025-004` | Requirement | exact | model |
| GPAI MDF 2025 | provider | Legal name for the model provider | `BOM-GPAIMDF2025-003` | Requirement | exact | model |
| GPAI MDF 2025 | license | License | `BOM-GPAIMDF2025-016` | Requirement | exact | model |
| NTIA 2021 | name | Component Name | `BOM-NTIA2021-DF-002` | Requirement | exact | model, package |
| NTIA 2021 | provider | Supplier Name | `BOM-NTIA2021-DF-001` | Requirement | close | model, package |

<!-- generated:standards:end -->

## Ethics and personal data

- **Names.** Author, maintainer and owner names are published as the packages
  and models declare them. They are public attribution, and the provider
  element cannot be studied without them.
- **E-mail addresses.** None is released. Each is replaced by
  `email.redacted@example.com` when it is collected, also inside URLs, and the
  export refuses to write a file that contains one.
- **Upstream text.** Package names, model names and licence strings remain
  their owners'. A long raw value (such as a licence text in a metadata field)
  is cut at 4,096 characters, with its full length and SHA-256.
- **Gated models.** No access token is used. A gated repository is described
  by its public API data only.
- **Takedown.** A person who wants a name removed can open an issue in the
  GitHub repository; see `README.md`.
- **Licences.** Data: CC-BY-4.0 (`LICENSE`), except
  the seed lists in `data/seeds/` and the `rank` values wherever they
  appear (`data/entries/`, `entries.jsonl`, `subjects.csv`), which are
  derived from Ecosyste.ms data and are under CC-BY-SA-4.0
  (`LICENSE-DATA-SEEDS`). Code: Apache-2.0 (`LICENSE-CODE`).

## Limitations

- **Coverage.** About 107 packages per channel and 102 models. Rates per
  channel have wide intervals; the evaluator reports them.
- **Popularity.** `top` and `mid` come from a popularity ranking; nothing
  below rank 10,000 is sampled. Maven is ranked by dependent packages, the
  others by downloads.
- **Silver labels.** A unanimous value is what the sources agree on, which is
  not always what is true: several sources can repeat one mistake. No audit
  sample was reviewed, so this error rate is not measured.
- **One reviewer.** Corrections were made by one person, not blind, with no
  second reviewer.
- **Provider means authors.** Maintainers, owners and publishers are recorded
  but are not the expected provider. For a converted model the hub owner is
  the account that distributes the file (see the header probe below).
- **Licence means the declared licence** of the package or repository as a
  whole, as one SPDX expression. A licence that differs by platform, or by
  file, cannot be stated this way; such values are left unresolved.
- **Two SPDX list versions.** Licence forms are classified with list 3.27.0
  and licences are derived with list 3.29.0; an id in only one of them is
  flagged, not hidden.
- **A snapshot.** Rankings, releases and model cards change. The seed files
  and the pinned versions are the record of this release; a fresh fetch can
  differ.
- **Model files.** For a model inside a package only the file name, size and
  format are in the dataset. What the file's header states is in the separate
  probe below.

## Licence text cross-check (experimental, non-voting)

For the 78 root subjects whose licence the automatic derivation could not
settle, LicenseID's text detector was run on what the metadata points to: the
licence files in the cached package archive (PyPI, npm, Cargo) and the page
behind each licence URL (Maven, NuGet, Hugging Face). The top detection was
compared with the reviewer's value. A detection is a computed value, so it is
not a source of the expected values, and nothing in the dataset depends on it.
A top detection with a score under 0.9 is counted as no detection, because the
detector always names its closest text.

| Result for the entry (best source) | Entries |
| --- | --- |
| Same licences as the reviewer | 18 |
| One licence of a reviewer's `AND` expression | 1 |
| A different licence | 3 |
| No detection (score under 0.9) | 44 |
| Nothing detected, or the page could not be fetched | 4 |
| No file and no URL to read | 8 |

- Where the detector names a licence with a score of 0.9 or more, it agrees
  with the reviewer in 18 of 21 entries.
- A file with two licence texts gave one of them (`python-dateutil`).
- 2 of the 3 "different" entries share one file: the licence file of a model
  with its own licence terms was detected as `BSD-3-Clause` with a score of
  0.90. The third is a Maven bundle whose metadata also lists the licences of
  third-party code inside it; the detector read those pages correctly, but
  they are not the licence of the package.
- Most URLs do not lead to a licence text: 51 of the 67 URL references (40
  distinct pages) gave no detection. They are licence overview pages,
  end-user agreements, redirecting links, or the generic page that NuGet puts
  in `licenseUrl` when the licence is a file in the package.
- NuGet packages were not downloaded, so a licence given as a file inside a
  `.nupkg` was not read.

File: `experimental/s2-license-text-crosscheck.jsonl`, written by
`scripts/s2_license_text_crosscheck.py` (pages fetched on 2026-11-11).

### A second text matcher

SPDX License Matcher 2.11.1 (`license-matcher`, with the SPDX Java tools) was
run on the same licence files and cached pages, with its default threshold of
0.9. Its licence list is the one in the local matcher database (811 licences
and exceptions), which may differ from the list LicenseID used.

| Result for the entry (best source) | SPDX License Matcher | LicenseID detector |
| --- | --- | --- |
| Same licences as the reviewer | 11 | 18 |
| One licence of a reviewer's `AND` expression | 0 | 1 |
| A different licence | 2 | 3 |
| A close text, not confirmed as a match | 6 | -- |
| No match, or a score under 0.9 | 51 | 48 |
| No file and no URL to read | 8 | 8 |

- The two tools agree with the reviewer on the same 10 entries; each also
  finds entries the other misses (1 and 8).
- For 3 pages the closest text by the first-stage score was `ECL-2.0` (the
  Apache License 2.0 with an added clause), and the second stage did not
  confirm a match; for 2 of them the reviewer's value is `Apache-2.0`.
- A GNU GPL version 3 text was matched as `GPL-3.0-or-later` where the
  metadata says `GPL-3.0`; a licence text alone does not tell "only" from
  "or later".
- Both tools leave about two thirds of these entries without an answer: the
  hard values are hard because the metadata does not lead to a licence text.

File: `experimental/s4-spdx-license-matcher.jsonl`, written by
`scripts/s4_spdx_license_matcher.py` (offline; needs Java and the matcher's
Redis database).

## Licence readers compared (experimental, non-voting)

For each of the 596 root subjects with a scored licence, the first declared
licence string (platform first, a `license:` hub prefix removed) was read in
three ways, and the reading compared with the expected expression
(equivalence, not spelling).

| Reader | Answered | Correct | PyPI | npm | Cargo | Maven | NuGet | Hugging Face |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| The string as it is | 594 | 433 | 56 | 104 | 99 | 29 | 65 | 80 |
| `license-expression` 30.4.4, with SPDX validation | 435 | 433 | 56 | 104 | 99 | 29 | 65 | 80 |
| LicenseID 0.8.4, `resolve()` | 527 | 522 | 86 | 104 | 105 | 82 | 65 | 80 |
| Subjects | 596 | 596 | 104 | 106 | 107 | 100 | 88 | 91 |

- A strict parser gives no wrong answer except for 2 Maven subjects, where the
  first of several declared licences is not the whole expression. It answers
  73% of the subjects.
- LicenseID adds 89 correct readings, nearly all on PyPI and Maven, where
  names, classifiers and URLs are declared. Its 5 wrong answers are the same
  kind (one of several declared values) and 2 values that the reviewer read
  differently.
- The automatic labels were derived with LicenseID, so its rate on those 535
  subjects is not an independent measurement. On the 61 subjects a reviewer
  settled, neither reader gives a correct answer: these are the values that
  need more than one declared string, or a licence outside the SPDX list.

File: `experimental/s3-normaliser-comparison.json`, written by
`scripts/s3_normaliser_comparison.py` (offline).

## Model-file headers (experimental, non-voting)

The expected values come from platform metadata, manifests and model cards.
As a separate probe, the header of some model files was read with the readers
of Pitloom 0.24.0, to see what the files state about themselves. The probe is
not part of the dataset: `build`, `validate` and `export` never read it, and no
expected value or status depends on it.

**What was read.**

- 3 ONNX models embedded in PyPI packages (silero-vad 5.1.2, magika 0.6.1),
  from the cached archives.
- Model files of 640 Hugging Face repositories: the 102 entries of the
  dataset at their pinned revision, and the next 538 repositories of the same
  downloads ranking (read on 2026-11-11, down to rank 611). The 538 are not
  entries of the dataset; their rows say `in_dataset: false`.
- Per repository at most 2 Safetensors, 1 GGUF and 1 ONNX file, the first by
  name. For Safetensors and GGUF only the header bytes were fetched; no tensor
  data was read. An ONNX file was fetched whole when it was at most 100 MB.
- 108 of the 640 repositories have no file of these three formats. At least
  one file was read in 496 repositories.

| Format | Read | Not read |
| --- | --- | --- |
| Safetensors | 576 | 26 (gated repository; no token is sent) |
| GGUF | 90 | 13 (7 over a size bound of the reader, 5 with a header over 64 MB, 1 gated) |
| ONNX (Hugging Face) | 19 | 72 (71 over 100 MB, 1 gated) |
| ONNX (embedded) | 3 | 0 |

**Findings.**

1. *Safetensors headers carry almost no provenance.* Of 576 files, 405 state
   only `format`, and 119 have no metadata. 15 files (9 repositories) state a
   name, and 12 files (7 repositories) state a licence and an author; all 7
   are image-generation models.
2. *A Safetensors header licence is a licence title, not an identifier.* The
   12 files give it as text such as `CreativeML Open RAIL++-M License`, where
   the hub gives a key (`openrail++`) or only `other`. One fine-tuned model
   keeps the name and the author of its base model in its header.
3. *An ONNX graph name is the exporter's default, not the model's name.* All
   22 files read have one of `main_graph` (14), `torch_jit` (5), `tf2onnx`,
   `spox_graph` or `mxnet_converted_model` (1 each). None has a metadata
   property, so none states an author or a licence.
4. *A GGUF header name is often not the repository's model name.* Of 90 files,
   81 state a name. In 41 the name is contained in the repository name; 4 are
   the single character `.`; 36 are another name, among them the name of a
   base model (`LLaMA v2`, `WizardCoder Python 7B V1.0`) and a bare `Hf`.
5. *Nearly half of the GGUF headers state a licence, and it nearly always
   agrees with the hub.* 41 of 90 state one, always as a Hugging Face licence
   key. Against the licence tag of the hub: 39 agree, 1 disagrees (header
   `apache-2.0`, hub `cc-by-nc-4.0`), and 1 repository has no licence on the
   hub while its header states `apache-2.0`. In 44 files only the hub states a
   licence, and in 5 neither does. A header with a non-SPDX key (`llama2`,
   `qwen`) repeats the key of the hub, so it does not resolve it.
6. *Header and hub name different providers.* 12 of 90 GGUF files name an
   organisation; in 10 it is not the hub owner but the maker of the original
   model (for example `OpenAI`, `NVIDIA` or `Google` in a repository of a
   converter). 23 files name the organisation of a base model, and 14 name who
   quantised the file. The hub owner of a converted model is the account that
   distributes the file.

**Limits.** The files are a sample (a few per repository, the first by name)
of popular repositories, so these are observations about that sample. Large
ONNX files (over 100 MB) were not read, and they are the majority (71 of 91). Parameter
counts in the probe come from the header.

**Files.** `experimental/s1-embedded-model-headers.jsonl` and
`experimental/s1-hf-model-headers.jsonl`, written by
`scripts/s1_embedded_model_headers.py` (offline) and
`scripts/s1_hf_model_headers.py --repos 640` (network, one request per second).
E-mail addresses are redacted as in the dataset.
