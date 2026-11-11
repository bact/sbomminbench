# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``validate``: the checks that compare entries with the rest of the tree.

V10 (seed pins), V12 (reviews), V13 (seeds), V14 (``collected_with``), V15
(determinism), V17 (release), V18 (JSON Schema) and V20 (BOM pin).

See also: ``validate.py``, ``validate_entry.py``, ``validate_findings.py``.
"""

import csv
import hashlib
import json
import os
import random
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import get_args

from pydantic import ValidationError

from sbomminbench.ids import canonical_name_key, entry_relpath, make_purl
from sbomminbench.jsonio import dumps
from sbomminbench.review_store import is_stale
from sbomminbench.schema import (
    Ecosystem,
    Entry,
    LicenseValue,
    NameValue,
    ProviderValue,
    ReviewLine,
)
from sbomminbench.seeds import Seed, read_seeds
from sbomminbench.standards import BOM_RELEASE, BOM_RETRIEVED, release_metadata
from sbomminbench.validate_entry import (
    clip,
    license_problem,
    name_key_problem,
    provider_problem,
    subject_ids,
)
from sbomminbench.validate_findings import Finding, error, warn

SAMPLE_SIZE = 20
SAMPLE_SEED = 20261107
_UNPINNED = re.compile(r"[<>=!~*,^]")


# -- V10 and V13: seeds -------------------------------------------------------------


def _failed_rows(path: Path) -> set[tuple[str, str, str]]:
    """The ``(eco, name, version)`` of each row of ``failed.tsv`` (none if absent)."""
    if not os.path.isfile(path):
        return set()
    lines = path.read_bytes().decode("utf-8", "replace").split("\n")
    rows = (ln.split("\t") for ln in lines[1:] if ln)
    return {(r[0], r[1], r[2]) for r in rows if len(r) >= 3}


def _pinned(version: str) -> bool:
    return (
        bool(version) and version.lower() != "latest" and not _UNPINNED.search(version)
    )


def _read_all_seeds(data_dir: Path) -> tuple[list[Seed], list[Finding]]:
    seeds: list[Seed] = []
    found: list[Finding] = []
    for eco in sorted(get_args(Ecosystem)):
        path = data_dir / "seeds" / f"{eco}.tsv"
        if not os.path.isfile(path):
            continue
        try:
            seeds += read_seeds(path, eco)
        except ValueError as exc:
            found.append(error("V13", f"seeds/{eco}.tsv", clip(f"unreadable: {exc}")))
    return seeds, found


def _seed_has_entry(seed: Seed, entries: Mapping[str, Entry], root: Path) -> bool:
    try:
        if os.path.isfile(
            root / entry_relpath(make_purl(seed.eco, seed.name, seed.version))
        ):
            return True
    except ValueError:
        pass
    # A renamed Hub model has the API id in its purl: the pinned revision names it.
    return seed.eco == "huggingface" and any(
        e.ecosystem == seed.eco and e.platform.version == seed.version.lower()
        for e in entries.values()
    )


def check_seeds(data_dir: Path, entries: Mapping[str, Entry]) -> list[Finding]:
    """V13: every seed is pinned and has an entry or a ``failed.tsv`` row."""
    seeds, found = _read_all_seeds(data_dir)
    failed = _failed_rows(data_dir / "seeds" / "failed.tsv")
    for s in seeds:
        label = f"seeds/{s.eco}.tsv: {s.name}@{s.version}"
        if not _pinned(s.version):
            found.append(error("V13", f"{s.eco}:{s.name}", f"{label} is not pinned"))
        elif (s.eco, s.name, s.version) not in failed and not _seed_has_entry(
            s, entries, data_dir / "entries"
        ):
            found.append(
                error(
                    "V13",
                    f"{s.eco}:{s.name}",
                    f"{label} has no entry and no failure row",
                )
            )
    return found + _check_pins(seeds, entries)


def _version_key(eco: str, version: str) -> str:
    return version.casefold() if eco == "huggingface" else version


def _check_pins(seeds: list[Seed], entries: Mapping[str, Entry]) -> list[Finding]:
    """V10: each entry's version is the pin of a seed of the same name."""
    if not seeds:
        return []
    pins: dict[tuple[str, str], set[str]] = {}
    revisions: set[tuple[str, str]] = set()
    for s in seeds:
        key = (s.eco, canonical_name_key(s.eco, s.name))
        pins.setdefault(key, set()).add(_version_key(s.eco, s.version))
        revisions.add((s.eco, _version_key(s.eco, s.version)))
    found: list[Finding] = []
    for purl, e in entries.items():
        eco, version = e.ecosystem, _version_key(e.ecosystem, e.platform.version)
        known = pins.get((eco, canonical_name_key(eco, e.platform.name)))
        if known is not None and version not in known:
            found.append(
                error("V10", purl, f"version is not a seed pin ({sorted(known)})")
            )
        elif known is None and (eco, version) not in revisions:
            found.append(warn("V10", purl, "no seed names this entry"))
    return found


# -- V12: reviews -------------------------------------------------------------------


def _review_value_problem(line: ReviewLine, entry: Entry) -> str | None:
    value = line.value
    if line.decision != "correct" or value is None:
        return None
    if line.element == "license" and isinstance(value, LicenseValue):
        return license_problem(value.spdx_expression)
    if line.element == "name" and isinstance(value, NameValue):
        return name_key_problem(entry.ecosystem, value)
    if line.element == "provider" and isinstance(value, ProviderValue):
        return provider_problem([e.name for e in value.entities])
    return "value does not fit the element"


def _check_review(
    line: ReviewLine, entries: Mapping[str, Entry], where: str
) -> Iterator[Finding]:
    entry = entries.get(line.id)
    subject = subject_ids(entry).get(line.subject) if entry else None
    if entry is None or subject is None:
        yield error("V12", line.id, f"{where}: no entry or subject for this review")
        return
    if problem := _review_value_problem(line, entry):
        yield error("V12", line.id, f"{where}: {problem}")
    if is_stale(line, getattr(subject.expected, line.element)):
        yield warn(
            "V12",
            line.id,
            f"{where}: stale_review: {line.decision}, but the auto answer of "
            f"{line.subject}.{line.element} changed since the review",
        )


def check_reviews(data_dir: Path, entries: Mapping[str, Entry]) -> list[Finding]:
    """V12: every review line validates, points at a real element and is not stale."""
    found: list[Finding] = []
    for path in sorted((data_dir / "reviews").glob("*.jsonl")):
        text = path.read_bytes().decode("utf-8", "replace").removeprefix("﻿")
        for n, raw in enumerate(text.split("\n"), 1):
            if not raw.strip():
                continue
            where = f"reviews/{path.name}:{n}"
            try:
                line = ReviewLine.model_validate_json(raw)
            except ValidationError as exc:
                first = exc.errors()[0]
                msg = str(first["msg"]).removeprefix("Value error, ")
                found.append(error("V12", where, clip(msg)))
                continue
            found.extend(_check_review(line, entries, where))
    return found


# -- V14 ----------------------------------------------------------------------------


def check_collected_with(entries: Mapping[str, Entry]) -> list[Finding]:
    """V14 (WARN): the tool versions are the same for every entry."""
    seen: dict[str, dict[str, int]] = {}
    for e in entries.values():
        for k, v in e.collected_with.items():
            seen.setdefault(k, {}).setdefault(v, 0)
            seen[k][v] += 1
    found: list[Finding] = []
    for key in sorted(seen):
        if len(seen[key]) > 1:
            mix = ", ".join(f"{v} x{n}" for v, n in sorted(seen[key].items()))
            found.append(warn("V14", "-", f"collected_with.{key} is mixed: {mix}"))
    return found


# -- V15 ----------------------------------------------------------------------------


def check_determinism(
    entries: Mapping[str, Entry], data_dir: Path, cache_dir: Path
) -> list[Finding]:
    """V15: rebuild ``SAMPLE_SIZE`` entries (fixed seed) from the cache, byte for byte."""
    if not os.path.isdir(cache_dir / "index"):
        return [warn("V15", "-", "no cache directory: determinism not checked")]
    from sbomminbench.build import build_entry, iter_indexes
    from sbomminbench.eco._pkgfetch import EcoError
    from sbomminbench.eco.base import CacheStore

    ecos: list[Ecosystem] = sorted(get_args(Ecosystem))
    indexes = {i.purl: i for i in iter_indexes(cache_dir, ecos)}
    cached = sorted(p for p in entries if p in indexes)
    if not cached:
        return [
            warn("V15", "-", "no entry has a cached index: determinism not checked")
        ]
    store = CacheStore(cache_dir)
    sample = random.Random(SAMPLE_SEED).sample(cached, min(SAMPLE_SIZE, len(cached)))
    found: list[Finding] = []
    for purl in sorted(sample):
        path = data_dir / "entries" / entry_relpath(purl)
        try:
            built = build_entry(indexes[purl], store, entries[purl].collected_with)
        except EcoError as exc:
            found.append(error("V15", purl, clip(f"rebuild failed: {exc}")))
            continue
        if dumps(built.model_dump(mode="json")).encode("utf-8") != path.read_bytes():
            found.append(
                error("V15", purl, "rebuild from the cache differs from the file")
            )
    return found


# -- V17 ----------------------------------------------------------------------------


def _release_ids(path: Path) -> tuple[list[str], str | None]:
    """The ``id`` of each line of ``entries.jsonl``, and a parse problem if any."""
    ids: list[str] = []
    for n, raw in enumerate(
        path.read_bytes().decode("utf-8", "replace").split("\n"), 1
    ):
        if not raw:
            continue
        try:
            ids.append(str(json.loads(raw)["id"]))
        except (ValueError, KeyError, TypeError):
            return ids, f"line {n} is not an entry"
    return ids, None


def _subjects_csv(path: Path) -> tuple[set[str], int]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {r.get("id", "") for r in rows}, len(rows)


def check_release(data_dir: Path, entries: Mapping[str, Entry]) -> list[Finding]:
    """V17: ``release/`` holds the same ids and counts as ``entries/``."""
    release = data_dir / "release"
    if not os.path.isdir(release):
        return []
    found: list[Finding] = []
    want = set(entries)
    jsonl = release / "entries.jsonl"
    if not os.path.isfile(jsonl):
        found.append(error("V17", "release/entries.jsonl", "missing"))
    else:
        ids, problem = _release_ids(jsonl)
        if problem:
            found.append(error("V17", "release/entries.jsonl", problem))
        elif sorted(ids) != sorted(want):
            found.append(
                error("V17", "release/entries.jsonl", _diff(set(ids), want, len(ids)))
            )
    csv_path = release / "subjects.csv"
    if os.path.isfile(csv_path):
        ids_in_csv, rows = _subjects_csv(csv_path)
        subjects = sum(len(e.subjects) for e in entries.values())
        if ids_in_csv != want or rows != 3 * subjects:
            found.append(
                error(
                    "V17",
                    "release/subjects.csv",
                    f"{rows} rows for {subjects} subjects; "
                    + _diff(ids_in_csv, want, rows),
                )
            )
    return found


def _diff(have: set[str], want: set[str], count: int) -> str:
    extra, missing = sorted(have - want), sorted(want - have)
    return clip(
        f"{count} lines; not in entries: {extra[:3]}; missing from release: {missing[:3]}"
    )


# -- V18 ----------------------------------------------------------------------------


def check_schema_file(schema_path: Path) -> list[Finding]:
    """V18: the committed JSON Schema is the one ``schema.py`` generates now."""
    if not os.path.isfile(schema_path):
        return [error("V18", str(schema_path), "missing; run export-schema")]
    fresh = dumps(Entry.model_json_schema())
    if schema_path.read_bytes() != fresh.encode("utf-8"):
        return [error("V18", str(schema_path), "differs from the generated schema")]
    return []


# -- V20 ----------------------------------------------------------------------------


def _sha256_sums(bom_dir: Path) -> Iterator[Finding]:
    sums = bom_dir / "SHA256SUMS"
    if not os.path.isfile(sums):
        yield error("V20", "vendor/bom/SHA256SUMS", "missing")
        return
    for line in sums.read_bytes().decode("utf-8", "replace").split("\n"):
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        member = bom_dir / name
        if not name or not os.path.isfile(member):
            yield error(
                "V20", "vendor/bom/SHA256SUMS", clip(f"{name!r} is listed but missing")
            )
        elif hashlib.sha256(member.read_bytes()).hexdigest() != digest:
            yield error("V20", f"vendor/bom/{name}", "sha256 differs from SHA256SUMS")


def _source_json(bom_dir: Path) -> Iterator[Finding]:
    path = bom_dir / "SOURCE.json"
    try:
        source = json.loads(path.read_bytes())
    except (OSError, ValueError):
        yield error("V20", "vendor/bom/SOURCE.json", "missing or not JSON")
        return
    pins = (("bom_release", BOM_RELEASE), ("retrieved", BOM_RETRIEVED))
    for key, want in pins:
        if not isinstance(source, dict) or source.get(key) != want:
            yield error("V20", "vendor/bom/SOURCE.json", f"{key} is not {want!r}")


def _crosswalk(data_dir: Path) -> Iterator[Finding]:
    if not os.path.isdir(data_dir / "release"):
        return
    path = data_dir / "release" / "standard-elements.json"
    try:
        stored = json.loads(path.read_bytes())
    except (OSError, ValueError):
        yield error("V20", "release/standard-elements.json", "missing or not JSON")
        return
    if stored != json.loads(json.dumps(release_metadata())):
        yield error(
            "V20", "release/standard-elements.json", "differs from release_metadata()"
        )


def check_bom_pin(bom_dir: Path, data_dir: Path) -> list[Finding]:
    """V20: the vendored BOM ontology files and the crosswalk match the pin."""
    return [*_sha256_sums(bom_dir), *_source_json(bom_dir), *_crosswalk(data_dir)]
