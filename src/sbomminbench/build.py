# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Build entries from the cache (offline): layer 3 on top of layers 1 and 2.

Nothing here touches the network: ``build_entry`` reads a ``FetchIndex`` and the
cached responses and members through a ``CacheStore``.
"""

import argparse
import importlib.metadata
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from sbomminbench import __version__, console, jsonio, tags
from sbomminbench.classify import SPDX_LIST_VERSION
from sbomminbench.derive.license import derive_license
from sbomminbench.derive.name import derive_name
from sbomminbench.derive.provider import derive_provider
from sbomminbench.eco import REGISTRY
from sbomminbench.eco._pkgfetch import EcoError, Failure
from sbomminbench.eco.base import CacheStore, FetchIndex, read_index
from sbomminbench.failures import record_once
from sbomminbench.ids import canonical_name_key, entry_relpath
from sbomminbench.models_scan import scan_names
from sbomminbench.schema import (
    Ecosystem,
    Entry,
    ExpectedLicense,
    ExpectedName,
    ExpectedProvider,
    ExpectedSet,
    ModelFormat,
    Subject,
)


def collected_versions() -> dict[str, str]:
    """The tool versions an entry was derived with (no Python version)."""
    from licenseid import __version__ as licenseid_version
    from licenseid import resolve

    listing = cast(Mapping[str, object], resolve("MIT", source="packaged"))["lists"]
    lists = cast(Sequence[Mapping[str, object]], listing)
    return {
        "license_list": str(lists[0]["version"]),
        "license_list_format": SPDX_LIST_VERSION,
        "licenseid": str(licenseid_version),
        "pitloom": importlib.metadata.version("pitloom"),
        "sbomminbench": __version__,
    }


def _not_assessed() -> ExpectedSet:
    return ExpectedSet(
        name=ExpectedName(status="not_assessed"),
        provider=ExpectedProvider(status="not_assessed"),
        license=ExpectedLicense(status="not_assessed"),
    )


def hub_model_format(index: FetchIndex) -> ModelFormat | None:
    """The one format of the listed Hub model files; ``None`` if mixed or capped.

    ``index.files`` already holds only files with a model suffix, cut at
    ``MAX_FILES``: a cut list cannot show that the format is the only one.
    """
    if index.files_count != len(index.files):
        return None
    found = {fmt for _, fmt in scan_names(index.files)}
    return cast(ModelFormat, next(iter(found))) if len(found) == 1 else None


def _model_subjects(index: FetchIndex) -> list[Subject]:
    members = sorted(index.model_members, key=lambda m: m.path)
    return [
        Subject(
            id=f"model:{n}",
            kind="model",
            locator=m.path,
            model_format=m.format,
            size_bytes=m.size,
            expected=_not_assessed(),
        )
        for n, m in enumerate(members, start=1)
    ]


def build_entry_with_failures(
    index: FetchIndex, store: CacheStore, collected_with: dict[str, str]
) -> tuple[Entry, list[Failure]]:
    """Like :func:`build_entry`, also returning the layer 2 files not read.

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: the cached layer 1 document is
            missing or malformed.
    """
    observed, failures = REGISTRY[index.ecosystem].observe_with_failures(index, store)
    platform, manifest = observed.platform, observed.manifest
    # The API id (index.name), not the seed name: a renamed Hub model differs.
    name, name_tags = derive_name(
        index.ecosystem, index.name, platform.observations, manifest
    )
    provider, provider_tags = derive_provider(
        index.ecosystem, platform.observations, manifest
    )
    licence, licence_tags = derive_license(platform.observations, manifest)
    all_tags = {
        *tags.structural_tags(platform.observations, manifest, index.ecosystem),
        *index.tags,
        *observed.tags,
        *name_tags,
        *provider_tags,
        *licence_tags,
    }
    if index.model_members:
        all_tags.add(tags.EMBEDDED_MODELS)
    hub = index.ecosystem == "huggingface"
    root = Subject(
        id="root",
        kind="model" if hub else "package",
        model_format=hub_model_format(index) if hub else None,
        manifest_files=observed.manifest_files,
        manifest_observations=manifest,
        files=index.files,
        files_count=index.files_count,
        challenge_tags=sorted(all_tags),
        expected=ExpectedSet(name=name, provider=provider, license=licence),
    )
    entry = Entry(
        schema_version="0.1",
        id=index.purl,
        ecosystem=index.ecosystem,
        stratum=index.stratum,
        rank=index.rank,
        collected_with=collected_with,
        platform=platform,
        subjects=[root, *_model_subjects(index)],
    )
    return entry, failures


def build_entry(
    index: FetchIndex, store: CacheStore, collected_with: dict[str, str]
) -> Entry:
    """Build the entry of one cached package or model (offline, deterministic).

    ``Platform.retrieved_at`` is the ``fetched_at`` of the cached layer 1
    response (read by the ecosystem's ``observe``).

    Raises:
        sbomminbench.eco._pkgfetch.EcoError: see :func:`build_entry_with_failures`.
    """
    return build_entry_with_failures(index, store, collected_with)[0]


# -- the whole cache ---------------------------------------------------------------


@dataclass
class BuildItem:
    """One cached package: its entry, or why none could be built."""

    index: FetchIndex
    entry: Entry | None
    failures: list[Failure] = field(default_factory=list)


def iter_indexes(
    cache_dir: Path, ecos: Sequence[Ecosystem], only: Sequence[str] = ()
) -> Iterator[FetchIndex]:
    """Yield the ``FetchIndex`` files under ``<cache_dir>/index``, by path.

    ``only`` keeps the names that compare equal the ecosystem's way (PEP 503
    for PyPI, case-insensitive for NuGet); the name is the one in the index
    (the Hub id for a renamed model).
    """
    for eco in sorted(ecos):
        root = cache_dir / "index" / eco
        paths = sorted(
            (p for p in root.rglob("*.json") if os.path.isfile(p)),
            key=lambda p: p.relative_to(root).as_posix(),
        )
        wanted = {canonical_name_key(eco, n) for n in only}
        for path in paths:
            index = read_index(path)
            if not wanted or canonical_name_key(eco, index.name) in wanted:
                yield index


def build_all(
    cache_dir: Path, ecos: Sequence[Ecosystem], only: Sequence[str] = ()
) -> Iterator[BuildItem]:
    """Build every cached index; an unusable layer 1 gives an item without entry."""
    store = CacheStore(cache_dir)
    versions = collected_versions()
    for index in iter_indexes(cache_dir, ecos, only):
        try:
            entry, failures = build_entry_with_failures(index, store, versions)
        except EcoError as exc:
            yield BuildItem(index, None, [Failure(exc.stage, exc.reason)])
        else:
            yield BuildItem(index, entry, failures)


@dataclass
class BuildSummary:
    written: int = 0
    unchanged: int = 0
    failed: int = 0  # packages without an entry
    failure_rows: int = 0  # new rows in failed.tsv


def _write(entry: Entry, root: Path) -> bool:
    """Write the entry; ``True`` when the file content changed."""
    path = root / entry_relpath(entry.id)
    before = path.read_bytes() if os.path.isfile(path) else None
    return jsonio.write_entry(entry, root).read_bytes() != before


def run_build(
    cache_dir: Path, data_dir: Path, ecos: Sequence[Ecosystem], only: Sequence[str]
) -> BuildSummary:
    """Build the entries of the cache into ``<data_dir>/entries`` (offline).

    Nothing is removed. A failed package goes to ``<data_dir>/seeds/failed.tsv``
    (once) and the run continues.
    """
    summary = BuildSummary()
    failed_tsv = data_dir / "seeds" / "failed.tsv"
    for item in build_all(cache_dir, ecos, only):
        idx = item.index
        for f in item.failures:
            added = record_once(failed_tsv, idx.ecosystem, idx.name, idx.version, *f)
            summary.failure_rows += added
        if item.entry is None:
            summary.failed += 1
            console.print_kv(
                ("ID", idx.purl),
                ("RESULT", "failed"),
                ("STAGE", item.failures[0].stage),
                ("REASON", item.failures[0].reason),
            )
            continue
        changed = _write(item.entry, data_dir / "entries")
        summary.written += changed
        summary.unchanged += not changed
        console.print_kv(
            ("ID", idx.purl), ("RESULT", "written" if changed else "unchanged")
        )
    return summary


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``build`` options to *parser*."""
    console.add_common_options(parser)


def run_cli(args: argparse.Namespace) -> int:
    """Run ``build`` for the parsed *args*; return the exit status."""
    ecos = console.resolve_ecos(args.eco)
    summary = run_build(args.cache_dir, args.data_dir, ecos, args.only)
    console.info(
        f"build: {summary.written} written, {summary.unchanged} unchanged, "
        f"{summary.failed} without entry, {summary.failure_rows} new "
        "rows in seeds/failed.tsv"
    )
    return 0
