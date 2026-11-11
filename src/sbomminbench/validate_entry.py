# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""``validate``: the checks that look at one entry (V01-V07, V09-V11, V19).

``load_entry`` reads a file and reports what ``schema.py`` rejects; the rejection
is filed under the check whose rule it breaks (V03, V04, ...), and under V01
only when no check owns it. ``check_entry`` then applies the rules the schema
does not state. Pitloom is imported lazily and only for the V10 drift guard.

See also: ``validate.py``, ``validate_findings.py``.
"""

import json
from collections.abc import Iterator
from functools import cache
from pathlib import Path, PurePosixPath

from license_expression import Licensing, get_spdx_licensing
from packageurl import PackageURL
from pydantic import ValidationError

from sbomminbench.derive.license import OK_CATEGORIES
from sbomminbench.ids import canonical_name_key, entry_relpath, make_purl
from sbomminbench.schema import (
    EMAIL_PLACEHOLDER,
    Ecosystem,
    Entry,
    ExpectedProvider,
    NameValue,
    Observation,
    Subject,
    native_type_of,
)
from sbomminbench.validate_findings import Finding, error, warn

ELEMENTS = ("name", "provider", "license")
HUMAN_STATUSES = frozenset({"human_confirmed", "human_corrected", "excluded"})
LICENSE_FORMS = frozenset(
    {"id_shaped", "expression_shaped", "spdx_url", "nuget_license_url"}
)
MAX_DETAIL = 300
_UTF8_BOM = b"\xef\xbb\xbf"
_UNKNOWN_KEYS = "Unknown license key(s)"
_LICENSE_REF = "LicenseRef-"
_DOC_REF = "DocumentRef-"

# Schema messages (substring, or location suffix) filed under the owning check.
_ROUTES: tuple[tuple[str, str], ...] = (
    ("root has no locator", "V03"),
    ("model:n needs", "V03"),
    ("subjects must be root", "V03"),
    ("root kind must be", "V03"),
    ("challenge_tags sorted", "V03"),
    ("manifest observations must not use", "V03"),
    ("String should match pattern '^(root|model", "V03"),
    ("needs a value", "V04"),
    ("must have no value", "V04"),
    ("human_confirmed without value", "V04"),
    ("auto_status and reviewer", "V04"),
    ("auto_conflict needs", "V04"),
    ("basis must be sorted", "V04"),
    ("basis key", "V04"),
    ("has_license_ref must match", "V05"),
    ("accepted must include name", "V06"),
    ("provider names must be unique", "V07"),
    ("observations must be sorted", "V09"),
    ("absent observation", "V09"),
    ("present observation cannot", "V09"),
    ("semantic_format is set", "V09"),
    ("format_validity is set", "V09"),
    ("role is set exactly", "V09"),
    ("truncated raw", "V09"),
    ("only when truncated", "V09"),
    ("platform observations must use", "V09"),
    ("fetched artifact needs", "V11"),
    ("unfetched artifact needs", "V11"),
    ("id must be a canonical purl", "V02"),
    ("ecosystem mismatch", "V10"),
    ("purl version must equal", "V10"),
    ("collected_with is missing", "V14"),
)
_LOC_ROUTES: tuple[tuple[str, str], ...] = (
    ("spdx_expression", "V05"),
    ("canonical_key", "V06"),
    ("accepted", "V06"),
)


def clip(text: str) -> str:
    """Return *text* cut to ``MAX_DETAIL`` characters."""
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL] + "..."


def _loc(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(p) for p in loc) or "(entry)"


def route_error(loc: tuple[int | str, ...], msg: str) -> str:
    """Return the check that owns a schema rejection (``V01`` when none does)."""
    for needle, check in _ROUTES:
        if needle in msg:
            return check
    last = str(loc[-1]) if loc else ""
    for suffix, check in _LOC_ROUTES:
        if last == suffix:
            return check
    return "V01"


def schema_findings(exc: ValidationError, target: str) -> list[Finding]:
    """Turn a pydantic rejection into findings, one per distinct problem."""
    seen: set[tuple[str, str]] = set()
    found: list[Finding] = []
    for err in exc.errors():
        msg = err["msg"].removeprefix("Value error, ")
        check = route_error(err["loc"], err["msg"])
        detail = clip(f"{_loc(err['loc'])}: {msg}")
        if (check, detail) not in seen:
            seen.add((check, detail))
            found.append(error(check, target, detail))
    return found


def load_entry(path: Path, rel: str) -> tuple[Entry | None, list[Finding]]:
    """Read one entry file; return it (``None`` when invalid) and the findings (V01)."""
    try:
        data = path.read_bytes().removeprefix(_UTF8_BOM)
        raw = json.loads(data)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, [error("V01", rel, clip(f"not readable JSON: {exc}"))]
    target = (
        raw["id"] if isinstance(raw, dict) and isinstance(raw.get("id"), str) else rel
    )
    try:
        return Entry.model_validate_json(data), []
    except ValidationError as exc:
        return None, schema_findings(exc, target)


def raw_id(path: Path) -> str | None:
    """The ``id`` string of an entry file, or ``None`` when it has none."""
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    value = raw.get("id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) else None


# -- V02 ---------------------------------------------------------------------------


def check_path(rel: str, purl: str) -> list[Finding]:
    """V02: the file path is the entry path of the id (relative to ``entries/``)."""
    try:
        want = entry_relpath(purl)
    except ValueError as exc:
        return [error("V02", rel, clip(f"id {purl!r} is not a purl: {exc}"))]
    if PurePosixPath(rel) != want:
        return [error("V02", purl, f"file {rel!r} should be {want.as_posix()!r}")]
    return []


def check_unique_paths(records: list[tuple[str, str]]) -> list[Finding]:
    """V02: no duplicate id and no two paths equal after casefolding.

    ``records`` holds ``(id, relative path)`` pairs.
    """
    found: list[Finding] = []
    by_id: dict[str, list[str]] = {}
    by_fold: dict[str, list[str]] = {}
    for purl, rel in records:
        by_id.setdefault(purl, []).append(rel)
        by_fold.setdefault(rel.casefold(), []).append(rel)
    for purl, rels in sorted(by_id.items()):
        if len(rels) > 1:
            found.append(error("V02", purl, f"duplicate id in {sorted(rels)}"))
    for rels in sorted(by_fold.values()):
        if len(set(rels)) > 1:
            found.append(
                error("V02", "-", f"paths equal when casefolded: {sorted(rels)}")
            )
    return found


# -- V04 to V07 --------------------------------------------------------------------


def check_no_human_status(entry: Entry) -> Iterator[Finding]:
    """V04: a human status belongs to the release overlay, not to ``entries/``."""
    for s in entry.subjects:
        for el in ELEMENTS:
            status = getattr(s.expected, el).status
            if status in HUMAN_STATUSES:
                yield error("V04", entry.id, f"{s.id}.{el}: status {status} in entries")


@cache
def _licensing() -> Licensing:
    return get_spdx_licensing()


def license_problem(expression: str) -> str | None:
    """V05 rule: why *expression* is not acceptable, or ``None``.

    It must parse with ``license-expression``; every unknown key must be a
    ``LicenseRef-`` (optionally behind ``DocumentRef-x:``).
    """
    if not expression.strip():
        return "empty expression"
    try:
        info = _licensing().validate(expression)
    except (ValueError, AttributeError, TypeError) as exc:  # library error paths
        return clip(f"does not parse: {exc}")
    if any(not e.startswith(_UNKNOWN_KEYS) for e in info.errors):
        return clip(f"does not parse: {'; '.join(info.errors)}")
    bad = sorted(s for s in map(str, info.invalid_symbols) if not _is_license_ref(s))
    return clip(f"unknown key(s): {', '.join(bad)}") if bad else None


def _is_license_ref(key: str) -> bool:
    if key.startswith(_DOC_REF):
        key = key.partition(":")[2]
    return key.startswith(_LICENSE_REF)


def check_answers(entry: Entry) -> Iterator[Finding]:
    """V05, V06, V07 on the values of every subject."""
    for s in entry.subjects:
        lic = s.expected.license.value
        if lic is not None and (problem := license_problem(lic.spdx_expression)):
            yield error("V05", entry.id, f"{s.id}.license: {problem}")
        name = s.expected.name.value
        if name is not None and (problem := name_key_problem(entry.ecosystem, name)):
            yield error("V06", entry.id, f"{s.id}.name: {problem}")
        yield from _provider_problems(entry.id, s.id, s.expected.provider)


def name_key_problem(eco: Ecosystem, name: NameValue) -> str | None:
    """V06 rule: why ``canonical_key`` is wrong, or ``None``.

    The key is the ecosystem rule applied to the name or to an accepted
    spelling (a Maven or Hub name is shown without its group or owner).
    """
    wanted = {canonical_name_key(eco, n) for n in (name.name, *name.accepted)}
    if name.canonical_key in wanted:
        return None
    return clip(f"canonical_key {name.canonical_key!r} is not one of {sorted(wanted)}")


def provider_problem(names: list[str]) -> str | None:
    """V07 rule: why a list of provider names is not acceptable, or ``None``."""
    for n in names:
        if not n.strip() or n.strip() == EMAIL_PLACEHOLDER:
            return f"empty or placeholder name {n!r}"
    folded = [n.strip().casefold() for n in names]
    return "names repeat after casefolding" if len(set(folded)) != len(folded) else None


def _provider_problems(
    purl: str, sid: str, expected: ExpectedProvider
) -> Iterator[Finding]:
    if expected.value is not None:
        names = [e.name for e in expected.value.entities]
        if problem := provider_problem(names):
            yield error("V07", purl, f"{sid}.provider: {problem}")


# -- V09 ---------------------------------------------------------------------------


def _all_observations(entry: Entry) -> Iterator[tuple[str, Observation]]:
    for o in entry.platform.observations:
        yield "root", o
    for s in entry.subjects:
        for o in s.manifest_observations:
            yield s.id, o


def check_native_types(entry: Entry) -> Iterator[Finding]:
    """V09: ``native_type`` agrees with the type of ``raw``."""
    for sid, o in _all_observations(entry):
        want = native_type_of(o.raw, o.present)
        if o.native_type != want:
            yield error(
                "V09",
                entry.id,
                f"{sid} {o.key}: native_type {o.native_type} but raw is {want}",
            )


# -- V10 ---------------------------------------------------------------------------


def _pitloom_purl(eco: str, name: str, version: str) -> str | None:
    """Pitloom's own purl for a pypi or Hub entry (``None`` for other ecosystems)."""
    from pitloom.core.models import build_pypi_purl
    from pitloom.core.purl import build_huggingface_purl

    if eco == "pypi":
        return build_pypi_purl(name, version)
    if eco == "huggingface":
        return build_huggingface_purl(name, version)
    return None


def check_purl(entry: Entry) -> Iterator[Finding]:
    """V10: the id round-trips and equals the purl built from name and version."""
    p = entry.platform
    try:
        trip = PackageURL.from_string(entry.id).to_string()
    except ValueError as exc:
        yield error("V10", entry.id, clip(f"not a purl: {exc}"))
        return
    if trip != entry.id:
        yield error("V10", entry.id, f"purl round-trips to {trip!r}")
    try:
        built = make_purl(entry.ecosystem, p.name, p.version)
    except ValueError as exc:
        yield error("V10", entry.id, clip(f"no purl from name and version: {exc}"))
        return
    if built != entry.id:
        yield error("V10", entry.id, f"name and version give {built!r}")
    try:
        theirs = _pitloom_purl(entry.ecosystem, p.name, p.version)
    except ImportError as exc:
        yield warn(
            "V10", entry.id, clip(f"pitloom not importable, no drift check: {exc}")
        )
        return
    if theirs is not None and theirs != entry.id:
        yield error("V10", entry.id, f"pitloom builds {theirs!r}")


def drift_findings() -> list[Finding]:
    """V10: ``make_purl`` and Pitloom's builders agree on fixed samples."""
    samples: tuple[tuple[Ecosystem, str, str], ...] = (
        ("pypi", "Foo_Bar", "1.0+local"),
        ("huggingface", "Org/Model", "ABCDEF0123456789ABCDEF0123456789ABCDEF01"),
    )
    found: list[Finding] = []
    for eco, name, version in samples:
        try:
            theirs = _pitloom_purl(eco, name, version)
        except ImportError:
            return [warn("V10", "-", "pitloom not importable, no drift check")]
        if theirs != make_purl(eco, name, version):
            found.append(error("V10", "-", f"{eco}: make_purl and pitloom disagree"))
    return found


# -- V19 ---------------------------------------------------------------------------


def _bad_form(validity: str | None, category: str) -> str | None:
    ok = category in OK_CATEGORIES
    if validity in ("valid", "noncanonical") and not ok:
        return f"classifier says {validity}, LicenseID category is {category}"
    if validity in ("unknown_id", "malformed") and ok:
        return f"classifier says {validity}, LicenseID category is {category}"
    return None


def check_form_vs_resolver(entry: Entry) -> Iterator[Finding]:
    """V19 (WARN): the classifier and LicenseID disagree on an id or expression form."""
    platform = {o.key: o for o in entry.platform.observations}
    for s in entry.subjects:
        obs = dict(platform) if s.id == "root" else {}
        obs.update({o.key: o for o in s.manifest_observations})
        for t in s.expected.license.licenseid:
            o = obs.get(t.key)
            if (
                o is None
                or o.semantic_format not in LICENSE_FORMS
                or not isinstance(o.raw, str)
                or t.input != o.raw[:500]
            ):
                continue
            why = _bad_form(o.format_validity, t.category)
            if why is not None:
                yield warn("V19", entry.id, f"{s.id} {t.key}: {why}")


def check_entry(entry: Entry, rel: str) -> list[Finding]:
    """Run the per-entry checks on a valid *entry* stored at *rel*."""
    found = check_path(rel, entry.id)
    for check in (
        check_no_human_status,
        check_answers,
        check_native_types,
        check_purl,
        check_form_vs_resolver,
    ):
        found.extend(check(entry))
    return found


def subject_ids(entry: Entry) -> dict[str, Subject]:
    """The subjects of *entry* by id."""
    return {s.id: s for s in entry.subjects}
