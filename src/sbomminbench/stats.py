# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``stats``: the numbers of the release, written to ``data/release/stats.json``.

Computed from the final entries (human reviews applied over auto answers). The
field contracts and ``conformance`` live in ``stats_contract.py`` and are
re-exported here. Everything is sorted and free of wall-clock time.

See also: ``export.py`` (also writes ``stats.json``), ``export_rows.py``.
"""

import argparse
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from sbomminbench import console, jsonio
from sbomminbench.export_review import load_final
from sbomminbench.export_rows import (
    ELEMENTS,
    compact_json,
    expected_of,
    iter_observations,
    normalise_path,
    observation_contract,
)
from sbomminbench.schema import Entry, Observation
from sbomminbench.stats_contract import (
    CONTRACTS,
    FIELD_CONTRACT,
    Conformance,
    conformance,
    field_contract,
)
from sbomminbench.wilson import wilson

__all__ = [
    "CONTRACTS",
    "FIELD_CONTRACT",
    "Conformance",
    "compute_stats",
    "conformance",
    "field_contract",
    "wilson",
]

type Table = dict[str, Any]

VERDICTS: Final = ("conforms", "lenient", "violates", "not_spdx")
NO_TRAIL = "no_trail"
# LicenseID's reading of a prose form; analysis-time only.
CATEGORY_GROUPS: Final[dict[str, str]] = {
    "listed_name": "full_name",
    "text_title": "full_name",
    "separator": "variant",
    "version_spelling": "variant",
    "prose_form": "variant",
    "alias_name": "variant",
    "trove_name": "classifier",
}
_FRAME_LINE = re.compile(r"^- ([a-z]+): frame (\d{4}-\d{2}-\d{2})\b")
_HUMAN = ("human_confirmed", "human_corrected")


def _nested(counter: Counter[tuple[str, ...]]) -> Table:
    """Turn a counter of key tuples into nested, key-sorted dictionaries."""
    out: Table = {}
    for keys in sorted(counter):
        node = out
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = counter[keys]
    return out


def _status_counts(entries: Iterable[Entry]) -> Table:
    counts: Counter[tuple[str, ...]] = Counter()
    for e in entries:
        for s in e.subjects:
            for el in ELEMENTS:
                counts[(e.ecosystem, el, expected_of(s, el).status)] += 1
    return _nested(counts)


def _origins(entries: Iterable[Entry]) -> Table:
    counts: Counter[tuple[str, ...]] = Counter()
    for e in entries:
        for s in e.subjects:
            for el in ELEMENTS:
                exp = expected_of(s, el)
                counts[("all", el, str(exp.independent_origins))] += 1
                if (exp.auto_status or exp.status) == "auto_unanimous":
                    counts[("auto_unanimous", el, str(exp.independent_origins))] += 1
    return _nested(counts)


def _observation_counts(entries: Iterable[Entry]) -> list[Table]:
    counts: Counter[tuple[str, str, str, str, str]] = Counter()
    for e in entries:
        for _, _, o, _ in iter_observations(e):
            counts[
                (
                    o.source,
                    normalise_path(o.field_path),
                    o.native_type,
                    o.semantic_format or "",
                    o.format_validity or "",
                )
            ] += 1
    return [
        {
            "source": s,
            "field_path": p,
            "native_type": nt,
            "semantic_format": sf or None,
            "format_validity": fv or None,
            "n": n,
        }
        for (s, p, nt, sf, fv), n in sorted(counts.items())
    ]


def _conformance(entries: Iterable[Entry]) -> Table:
    counts: Counter[tuple[str, ...]] = Counter()
    for e in entries:
        for _, _, o, siblings in iter_observations(e):
            verdict = observation_contract(o, siblings)[1]
            if verdict in VERDICTS:
                key = f"{o.source}:{normalise_path(o.field_path)}"
                counts[(e.ecosystem, key, verdict)] += 1
    out = _nested(counts)
    for fields in out.values():
        for verdicts in fields.values():
            for v in VERDICTS:
                verdicts.setdefault(v, 0)
    return out


def _trail_category(entry: Entry, subject_id: str, obs: Observation) -> str:
    """Return LicenseID's category for a licence observation, from the trail."""
    subject = next(s for s in entry.subjects if s.id == subject_id)
    raw = obs.raw if isinstance(obs.raw, str) else ""
    for t in subject.expected.license.licenseid:
        if t.key == obs.key and t.input == raw[:500]:
            return t.category
    return NO_TRAIL


def _format_by_category(entries: Iterable[Entry]) -> Table:
    counts: Counter[tuple[str, ...]] = Counter()
    groups: Counter[tuple[str, ...]] = Counter()
    for e in entries:
        for _, sid, o, _ in iter_observations(e):
            if o.semantic_format is None:
                continue
            cat = _trail_category(e, sid, o)
            counts[(o.semantic_format, cat)] += 1
            groups[(o.semantic_format, CATEGORY_GROUPS.get(cat, "other"))] += 1
    return {"by_category": _nested(counts), "by_reading": _nested(groups)}


def _raw_set(obs: Iterable[Observation], element: str) -> set[str]:
    return {
        compact_json(o.raw)
        for o in obs
        if o.element == element and o.present and o.raw is not None
    }


def _raw_form_agreement(entries: Iterable[Entry]) -> Table:
    """Count root subjects whose platform and manifest raw forms agree.

    Raw forms are compared as given (``MIT`` and ``mit`` differ); this says
    nothing about whether the two sources state the same fact.
    """
    counts: Counter[tuple[str, ...]] = Counter()
    for e in entries:
        plat, manifest = e.platform.observations, e.subjects[0].manifest_observations
        for el in ELEMENTS:
            a, b = _raw_set(plat, el), _raw_set(manifest, el)
            if not a or not b:
                continue
            counts[(e.ecosystem, el, "both")] += 1
            counts[(e.ecosystem, el, "equal")] += a == b
            counts[(e.ecosystem, el, "overlap")] += bool(a & b)
    out = _nested(counts)
    for els in out.values():
        for c in els.values():
            for k in ("both", "equal", "overlap"):
                c.setdefault(k, 0)
    return out


AUDIT_DESCRIPTION: Final = "precision of silver labels on root subjects, non-blind"


def _wilson_or_null(k: int, n: int) -> Table:
    """``k``, ``n`` and the Wilson bounds; ``None`` bounds when nothing was judged."""
    if n <= 0:
        return {"k": k, "n": n, "wilson_low": None, "wilson_high": None}
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "wilson_low": lo, "wilson_high": hi}


def _origins_label(origins: int) -> str:
    return "2+" if origins >= 2 else str(origins)


def _audit(entries: Iterable[Entry]) -> Table:
    """Root-subject answers that were ``auto_unanimous`` and were then reviewed.

    ``k`` of ``n`` judged were confirmed (not corrected): the precision of the
    silver labels, measured by a reviewer who saw the auto answer (non-blind).
    Excluded answers are not judged and are counted apart. The ``by_origins``
    split compares answers backed by one independent origin with 2 or more.
    """
    k: Counter[str] = Counter()
    n: Counter[str] = Counter()
    excluded: Counter[str] = Counter()
    for e in entries:
        for s in e.subjects:
            if s.id != "root":
                continue
            for el in ELEMENTS:
                exp = expected_of(s, el)
                if exp.auto_status != "auto_unanimous":
                    continue
                if exp.status == "excluded":
                    excluded["all"] += 1
                    excluded[el] += 1
                if exp.status not in _HUMAN:
                    continue
                for key in (
                    "all",
                    el,
                    f"origins={_origins_label(exp.independent_origins)}",
                ):
                    n[key] += 1
                    k[key] += exp.status == "human_confirmed"
    out: Table = {"description": AUDIT_DESCRIPTION}
    for key in ("all", *ELEMENTS):
        out[key] = {**_wilson_or_null(k[key], n[key]), "excluded": excluded[key]}
    out["by_origins"] = {
        label: _wilson_or_null(k[f"origins={label}"], n[f"origins={label}"])
        for label in ("1", "2+")
    }
    return out


def frame_dates(data_dir: Path) -> dict[str, list[str]]:
    """Return the selection frame dates per ecosystem from ``seeds/README.md``."""
    path = data_dir / "seeds" / "README.md"
    dates: dict[str, set[str]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _FRAME_LINE.match(line)
            if m:
                dates.setdefault(m.group(1), set()).add(m.group(2))
    return {eco: sorted(d) for eco, d in sorted(dates.items())}


def compute_stats(entries: list[Entry], frames: dict[str, list[str]]) -> Table:
    """Return the content of ``stats.json`` for the final ``entries``."""
    subjects = [s for e in entries for s in e.subjects]
    tags = Counter(t for s in subjects for t in s.challenge_tags)
    models = Counter(s.model_format for s in subjects if s.kind == "model")
    collected: dict[str, set[str]] = {}
    for e in entries:
        for key, value in e.collected_with.items():
            collected.setdefault(key, set()).add(value)
    return {
        "audit": _audit(entries),
        "collected_with": {k: sorted(v) for k, v in sorted(collected.items())},
        "counts": {"entries": len(entries), "subjects": len(subjects)},
        "format_by_licenseid_category": _format_by_category(entries),
        "frame_dates": frames,
        "independent_origins": _origins(entries),
        "license_conformance": _conformance(entries),
        "model_subjects_by_format": dict(
            sorted((k or "unspecified", v) for k, v in models.items())
        ),
        "observation_counts": _observation_counts(entries),
        "raw_form_agreement": _raw_form_agreement(entries),
        "status_counts": _status_counts(entries),
        "tag_counts": dict(sorted(tags.items())),
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``stats`` options to *parser*."""
    console.add_dir_options(parser)


def run_cli(args: argparse.Namespace) -> int:
    """Write ``<data-dir>/release/stats.json``; print the result to stdout."""
    entries, overlay = load_final(args.data_dir)
    path = args.data_dir / "release" / "stats.json"
    text = jsonio.dumps(compute_stats(entries, frame_dates(args.data_dir)))
    changed = jsonio.write_if_changed(path, text)
    console.print_kv(("RESULT", "written" if changed else "unchanged"), ("FILE", path))
    console.info(f"stats: {len(entries)} entries, {overlay.applied} reviews applied")
    return 0
