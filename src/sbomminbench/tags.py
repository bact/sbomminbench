# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Challenge-tag names and the structural tags read from observations.

Tags set at fetch or derive time (outside `structural_tags`) are listed too
so every name lives in one place; `ALL_TAGS` is the closed vocabulary.
"""

import re
from collections.abc import Iterable

from sbomminbench import authors_rules
from sbomminbench.classify import HF_LICENSE_PREFIX, PLACEHOLDERS, license_format
from sbomminbench.license_entries import dict_name, entry_ref
from sbomminbench.schema import EMAIL_PLACEHOLDER, JsonValue, Observation

# Licence validity (any checked-form licence observation).
LICENSE_SPDX_NONCANONICAL = "license_spdx_noncanonical"
LICENSE_SPDX_DEPRECATED = "license_spdx_deprecated"
LICENSE_SPDX_INVALID = "license_spdx_invalid"  # unknown_id or malformed

# Licence form.
LICENSE_ABSENT = "license_absent"
LICENSE_PROSE = "license_prose"
LICENSE_IS_URL = "license_is_url"
LICENSE_TROVE_ONLY = "license_trove_only"
LICENSE_TROVE_AMBIGUOUS = "license_trove_ambiguous"  # set by derive
LICENSE_TROVE_MULTIPLE = "license_trove_multiple"
LICENSE_FILE_ONLY = "license_file_only"
LICENSE_TEXT_IN_FIELD = "license_text_in_field"
LICENSE_PLACEHOLDER = "license_placeholder"
LICENSE_COMPOUND = "license_compound"
LICENSE_NONSTANDARD_COMPOUND = "license_nonstandard_compound"
LICENSE_MULTIPLE_VALUES = "license_multiple_values"

# Provider.
AUTHOR_ABSENT_MAINTAINER_ONLY = "author_absent_maintainer_only"
AUTHOR_MULTI_IN_ONE_STRING = "author_multi_in_one_string"
AUTHOR_EMAIL_ONLY = "author_email_only"
AUTHOR_OBJECT_FORM = "author_object_form"
AUTHOR_CONTAINS_COLLECTIVE = "author_contains_collective"  # set by derive
AUTHOR_ORG_LIKE = "author_org_like"  # set by derive
AUTHOR_URL_ONLY = "author_url_only"  # set by derive

# Source and artifact conditions (set by fetchers, readers or derive).
SCHEMA_TYPE_VIOLATION = "schema_type_violation"
DYNAMIC_FIELD = "dynamic_field"
NAME_CASE_DIFFERS = "name_case_differs"
NAME_SEPARATOR_DIFFERS = "name_separator_differs"  # set by derive
PLATFORM_MANIFEST_DIFFER = "platform_manifest_differ"
MAVEN_INHERITED = "maven_inherited"
MAVEN_PROPERTY_REF = "maven_property_ref"  # "${" in raw
WORKSPACE_INHERITED = "workspace_inherited"
VERSION_NORMALIZED = "version_normalized"
HF_LICENSE_OTHER = "hf_license_other"
HF_GATED = "hf_gated"
HF_RENAMED = "hf_renamed"
EMBEDDED_MODELS = "embedded_models"
ARTIFACT_TOO_LARGE = "artifact_too_large"
ARTIFACT_NOT_SCANNED = "artifact_not_scanned"
ARRAY_DATA_NOT_SCANNED = "array_data_not_scanned"
MODEL_SUBJECTS_CAPPED = "model_subjects_capped"

# Derive-time tags.
LICENSE_UNRESOLVED_VALUE = "license_unresolved_value"
LICENSE_ASSUMED_ONLY = "license_assumed_only"
LICENSE_MULTI_SOURCE_DISAGREE = "license_multi_source_disagree"
LICENSE_REF_NEEDED = "license_ref_needed"
LICENSE_ENTRY_UNRESOLVED = "license_entry_unresolved"  # a multi-entry entry has no vote
LICENSE_LIST_OR = "license_list_or"  # Maven licences read as `A OR B`
LICENSE_MULTIPLE_ENTRIES = "license_multiple_entries"  # several entries, not Maven

ALL_TAGS: frozenset[str] = frozenset(
    v for k, v in dict(globals()).items() if k.isupper() and v == k.lower()
)

_CHECKED_FORMS = frozenset(
    {"id_shaped", "expression_shaped", "spdx_url", "nuget_license_url"}
)
_VALUE_FORMS = frozenset(
    {"id_shaped", "expression_shaped", "nonstandard_compound", "prose"}
)
_URL_FORMS = frozenset({"spdx_url", "osi_url", "nuget_license_url", "other_url"})
_ANGLE = re.compile(r"<[^>]*>")


def _licence_text(o: Observation) -> str:
    """The licence value compared across sources (hf_api drops `license:`)."""
    text = o.raw.strip() if isinstance(o.raw, str) else ""
    return text.removeprefix(HF_LICENSE_PREFIX) if o.source == "hf_api" else text


def _entry_values(o: Observation) -> list[tuple[str, str]]:
    """`(entry, value)` pairs of the strings an observation states."""
    raw = o.raw
    if isinstance(raw, str):
        return [(entry_ref(o)[1], _licence_text(o))]
    out: list[tuple[str, str]] = []
    for i, item in enumerate(raw if isinstance(raw, list) else [raw]):
        name = item if isinstance(item, str) else None
        if isinstance(item, dict):
            name = dict_name(item)
        if name is not None:
            out.append((entry_ref(o, i if isinstance(raw, list) else None)[1], name))
    return out


def _multiple_values(live: list[Observation]) -> bool:
    """Does one declaration list two or more entries with different licences?

    Only licence-like values count: not URLs, files, placeholders, text or
    troves. Values are compared case-folded. A `@type` row or a sibling field
    is a declaration of its own, so it never counts as a second entry.
    """
    by_decl: dict[str, dict[str, set[str]]] = {}
    for o in live:
        if o.raw_truncated:
            continue
        for entry, value in _entry_values(o):
            if license_format(value) not in _VALUE_FORMS:
                continue
            decl = entry_ref(o)[0]
            by_decl.setdefault(decl, {}).setdefault(entry, set()).add(value.casefold())
    return any(
        len({frozenset(v) for v in entries.values()}) > 1
        for entries in by_decl.values()
    )


def _license_tags(obs: list[Observation]) -> set[str]:
    tags: set[str] = set()
    live = [o for o in obs if o.present]
    if not live:
        return {LICENSE_ABSENT}
    forms = [o.semantic_format for o in live if o.semantic_format is not None]
    simple = {
        "prose": LICENSE_PROSE,
        "license_text": LICENSE_TEXT_IN_FIELD,
        "placeholder": LICENSE_PLACEHOLDER,
        "expression_shaped": LICENSE_COMPOUND,
        "nonstandard_compound": LICENSE_NONSTANDARD_COMPOUND,
    }
    tags |= {tag for form, tag in simple.items() if form in forms}
    if _URL_FORMS & set(forms):
        tags.add(LICENSE_IS_URL)
    if forms and all(f == "trove_classifier" for f in forms):
        tags.add(LICENSE_TROVE_ONLY)
    if forms and all(f == "file_ref" for f in forms):
        tags.add(LICENSE_FILE_ONLY)
    troves = {o.raw for o in live if o.semantic_format == "trove_classifier"}
    if len(troves) > 1:
        tags.add(LICENSE_TROVE_MULTIPLE)
    if _multiple_values(live):
        tags.add(LICENSE_MULTIPLE_VALUES)
    validity = {o.format_validity for o in live if o.semantic_format in _CHECKED_FORMS}
    if "noncanonical" in validity:
        tags.add(LICENSE_SPDX_NONCANONICAL)
    if "deprecated" in validity:
        tags.add(LICENSE_SPDX_DEPRECATED)
    if validity & {"unknown_id", "malformed"}:
        tags.add(LICENSE_SPDX_INVALID)
    return tags


def _has_value(raw: JsonValue) -> bool:
    """Does *raw* name someone? Null, empty and placeholder values do not."""
    if raw is None:
        return False
    if isinstance(raw, str):
        text = raw.strip()
        return bool(text) and text.casefold() not in PLACEHOLDERS
    if isinstance(raw, (list, dict)):
        return bool(raw)
    return True


def _author_tags(obs: list[Observation]) -> set[str]:
    tags: set[str] = set()
    live = [o for o in obs if o.present]
    authors = [o for o in live if o.role == "author" and _has_value(o.raw)]
    if not authors and any(o.role == "maintainer" for o in live):
        tags.add(AUTHOR_ABSENT_MAINTAINER_ONLY)
    for o in authors:
        if o.native_type in {"object", "list_of_objects"}:
            tags.add(AUTHOR_OBJECT_FORM)
        if isinstance(o.raw, str):
            outside = _ANGLE.sub(" ", o.raw)
            if authors_rules.MULTI.search(outside) or authors_rules.has_line_break(
                o.raw
            ):
                tags.add(AUTHOR_MULTI_IN_ONE_STRING)
            if o.raw.replace("<", "").replace(">", "").strip() == EMAIL_PLACEHOLDER:
                tags.add(AUTHOR_EMAIL_ONLY)
    return tags


def _source_tags(obs: list[Observation], eco: str) -> set[str]:
    tags: set[str] = set()
    for o in obs:
        if not o.present:
            continue
        if eco == "maven" and o.inherited_from is not None:
            tags.add(MAVEN_INHERITED)
        if isinstance(o.raw, str) and "${" in o.raw:
            tags.add(MAVEN_PROPERTY_REF)
        if (
            eco == "cargo"
            and isinstance(o.raw, dict)
            and o.raw.get("workspace") is True
        ):
            tags.add(WORKSPACE_INHERITED)
    return tags


def structural_tags(
    platform_obs: Iterable[Observation],
    manifest_obs: Iterable[Observation],
    eco: str,
) -> list[str]:
    """Tags readable from the root observations alone; sorted and unique."""
    obs = [*platform_obs, *manifest_obs]
    tags = _source_tags(obs, eco)
    tags |= _license_tags([o for o in obs if o.element == "license"])
    tags |= _author_tags([o for o in obs if o.element == "provider"])
    return sorted(tags)
