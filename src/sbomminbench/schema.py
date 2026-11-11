# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""SBOM-MinBench entry schema 0.1.

The source of truth; schema/entry.schema.json is generated from it.
"""

import re
from typing import Literal, Self

from packageurl import PackageURL
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import JsonValue as JsonValue  # noqa: PLC0414

SCHEMA_VERSION = "0.1"
EMAIL_PLACEHOLDER = "email.redacted@example.com"
MAX_RAW_CHARS = 4096
MAX_FILES = 20

Ecosystem = Literal["pypi", "npm", "cargo", "maven", "nuget", "huggingface"]
Element = Literal["name", "provider", "license"]
Stratum = Literal["top", "mid", "edge"]
Source = Literal[
    "pypi_json",
    "METADATA",
    "PKG-INFO",
    "pyproject.toml",
    "npm_registry",
    "package.json",
    "crates_api",
    "Cargo.toml",
    "Cargo.toml.orig",
    "pom.xml",
    "nuget_catalog",
    "nuspec",
    "hf_api",
    "hf_card",
]
PLATFORM_SOURCES = frozenset(
    {"pypi_json", "npm_registry", "crates_api", "nuget_catalog", "hf_api"}
)
NativeType = Literal[
    "string",
    "number",
    "bool",
    "null",
    "absent",
    "object",
    "list_of_strings",
    "list_of_objects",
    "list_mixed",
    "list_empty",
]
SemanticFormat = Literal[
    "empty",
    "placeholder",
    "license_text",
    "spdx_url",
    "osi_url",
    "nuget_license_url",
    "other_url",
    "trove_classifier",
    "file_ref",
    "id_shaped",
    "expression_shaped",
    "nonstandard_compound",
    "prose",
]
FormatValidity = Literal[
    "valid", "noncanonical", "deprecated", "unknown_id", "malformed", "not_applicable"
]
Role = Literal[
    "author", "maintainer", "contributor", "owner", "publisher", "organization"
]
CodeMeta = Literal["name", "license", "author", "maintainer", "contributor"]
ModelFormat = Literal[
    "onnx",
    "gguf",
    "safetensors",
    "pytorch",
    "pytorch_pt2",
    "keras",
    "fasttext",
    "crfsuite",
    "hdf5",
    "numpy",
    "unknown",
]
AutoStatus = Literal[
    "auto_unanimous",
    "auto_conflict",
    "auto_unresolved",
    "absent_in_source",
    "not_assessed",
]
Status = Literal[
    "auto_unanimous",
    "auto_conflict",
    "auto_unresolved",
    "absent_in_source",
    "not_assessed",
    "human_confirmed",
    "human_corrected",
    "excluded",
]
SCORED_STATUSES = frozenset({"auto_unanimous", "human_confirmed", "human_corrected"})
_AUTO = frozenset(
    {
        "auto_unanimous",
        "auto_conflict",
        "auto_unresolved",
        "absent_in_source",
        "not_assessed",
    }
)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_AUTO_FIELDS = ("auto_status", "auto_value_at_review")
UTC_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


def native_type_of(value: object, present: bool = True) -> NativeType:
    if not present:
        return "absent"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        if not value:
            return "list_empty"
        if all(isinstance(v, str) for v in value):
            return "list_of_strings"
        if all(isinstance(v, dict) for v in value):
            return "list_of_objects"
        return "list_mixed"
    raise TypeError(f"not a JSON value: {type(value).__name__}")


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Observation(Base):
    """One declared value exactly as the source stores it (e-mails redacted, long strings cut)."""

    element: Element
    source: Source
    field_path: str = Field(min_length=1)
    present: bool = True
    raw: JsonValue = None
    raw_truncated: bool = False
    raw_length: int | None = None
    raw_sha256: str | None = None
    native_type: NativeType
    semantic_format: SemanticFormat | None = None
    format_validity: FormatValidity | None = None
    role: Role | None = None
    codemeta: CodeMeta | None = None
    has_email: bool = False
    inherited_from: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.field_path}"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.present and (self.raw is not None or self.native_type != "absent"):
            raise ValueError(
                "absent observation needs raw=None and native_type='absent'"
            )
        if self.present and self.native_type == "absent":
            raise ValueError("present observation cannot be 'absent'")
        if (self.semantic_format is not None) != (
            self.element == "license" and isinstance(self.raw, str)
        ):
            raise ValueError(
                "semantic_format is set exactly for licence observations with a string raw"
            )
        if (self.format_validity is None) != (self.semantic_format is None):
            raise ValueError(
                "format_validity is set exactly when semantic_format is set"
            )
        if (self.role is not None) != (self.element == "provider"):
            raise ValueError("role is set exactly for provider observations")
        if self.raw_truncated:
            if not (
                isinstance(self.raw, str)
                and len(self.raw) == MAX_RAW_CHARS
                and (self.raw_length or 0) > MAX_RAW_CHARS
                and _HEX64.fullmatch(self.raw_sha256 or "")
            ):
                raise ValueError(
                    "truncated raw needs a 4096-char str, raw_length, raw_sha256"
                )
        elif self.raw_length is not None or self.raw_sha256 is not None:
            raise ValueError("raw_length/raw_sha256 only when truncated")
        return self


def obs_key(o: Observation) -> str:
    return o.key


def _sorted_unique(obs: list[Observation]) -> None:
    keys = [(o.source, o.field_path) for o in obs]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ValueError(
            "observations must be sorted by (source, field_path) and unique"
        )


class Artifact(Base):
    kind: Literal[
        "sdist",
        "wheel",
        "wheel_metadata",
        "npm_tarball",
        "crate",
        "pom",
        "nupkg",
        "hf_repo",
    ]
    url: str
    filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None  # measured on what we downloaded
    declared_digest: str | None = (
        None  # "<algo>:<value>" or npm "sha512-<b64>", as the platform states
    )
    fetched: bool
    skip_reason: (
        Literal["too_large", "http_404", "http_error", "not_needed", "archive_error"]
        | None
    ) = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.fetched and not _HEX64.fullmatch(self.sha256 or ""):
            raise ValueError("fetched artifact needs sha256")
        if not self.fetched and self.skip_reason is None:
            raise ValueError("unfetched artifact needs skip_reason")
        return self


class Platform(Base):  # LAYER 1
    ecosystem: Ecosystem
    source_url: str
    html_url: str | None = None
    retrieved_at: str = Field(pattern=UTC_PATTERN)
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str
    version: str
    observations: list[Observation] = []
    artifacts: list[Artifact] = []

    @model_validator(mode="after")
    def _check(self) -> Self:
        if any(o.source not in PLATFORM_SOURCES for o in self.observations):
            raise ValueError("platform observations must use a platform source")
        _sorted_unique(self.observations)
        return self


class LicenseidTrail(Base):
    key: str
    input: str = Field(max_length=500)
    expression: str | None
    category: str
    trust: str
    reason: str | None = None


class LicenseValue(Base):
    spdx_expression: str = Field(min_length=1)
    has_license_ref: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.has_license_ref != ("LicenseRef-" in self.spdx_expression):
            raise ValueError("has_license_ref must match the expression")
        return self


class NameValue(Base):
    name: str = Field(min_length=1)
    canonical_key: str = Field(min_length=1)
    accepted: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.name not in self.accepted:
            raise ValueError("accepted must include name")
        return self


class ProviderEntity(Base):
    name: str = Field(min_length=1)
    role: Role = "author"


class ProviderValue(Base):
    entities: list[ProviderEntity]
    has_unnamed_group: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        folded = [e.name.casefold() for e in self.entities]
        if len(set(folded)) != len(folded) or any(
            EMAIL_PLACEHOLDER in e.name for e in self.entities
        ):
            raise ValueError(
                "provider names must be unique and contain no e-mail placeholder"
            )
        return self


class _Expected(Base):
    status: Status
    basis: list[str] = []  # observation keys that voted
    candidates: list[str] = []  # auto_conflict: distinct answers as text
    independent_origins: int = Field(default=0, ge=0)
    note: str | None = None
    auto_status: AutoStatus | None = None  # release overlay only
    reviewer: str | None = None
    reviewed_at: str | None = Field(default=None, pattern=UTC_PATTERN)

    @model_validator(mode="after")
    def _check(self) -> Self:
        value = getattr(self, "value")  # noqa: B009
        s = self.status
        if s in ("auto_unanimous", "human_corrected") and value is None:
            raise ValueError(f"{s} needs a value")
        if (
            s
            in (
                "auto_conflict",
                "auto_unresolved",
                "absent_in_source",
                "not_assessed",
                "excluded",
            )
            and value is not None
        ):
            raise ValueError(f"{s} must have no value")
        if (
            s == "human_confirmed"
            and value is None
            and self.auto_status != "absent_in_source"
        ):
            raise ValueError(
                "human_confirmed without value only confirms absent_in_source"
            )
        reviewed = s not in _AUTO
        if reviewed != (self.auto_status is not None) or reviewed != (
            self.reviewer is not None
        ):
            raise ValueError(
                "auto_status and reviewer are set exactly for reviewed statuses"
            )
        if s == "auto_conflict" and len(self.candidates) < 2:
            raise ValueError("auto_conflict needs >= 2 candidates")
        if self.basis != sorted(set(self.basis)):
            raise ValueError("basis must be sorted and unique")
        return self


class ExpectedName(_Expected):
    value: NameValue | None = None


class ExpectedProvider(_Expected):
    value: ProviderValue | None = None


class ExpectedLicense(_Expected):
    value: LicenseValue | None = None
    licenseid: list[LicenseidTrail] = []


class ExpectedSet(
    Base
):  # LAYER 3; a new element later = a new field; standard ids are stored once per release
    name: ExpectedName
    provider: ExpectedProvider
    license: ExpectedLicense


class Subject(Base):
    id: str = Field(pattern=r"^(root|model:[1-9][0-9]*)$")
    kind: Literal["package", "model"]
    locator: str | None = None  # member path inside the artifact; None for root
    model_format: ModelFormat | None = None
    size_bytes: int | None = None
    manifest_files: list[str] = []
    manifest_observations: list[Observation] = []  # LAYER 2
    files: list[str] = Field(
        default=[], max_length=MAX_FILES
    )  # model files listed (HF siblings, NuGet entries)
    files_count: int = 0
    challenge_tags: list[str] = []
    expected: ExpectedSet

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.id == "root" and self.locator is not None:
            raise ValueError("root has no locator")
        if self.id != "root" and (
            self.kind != "model" or not self.locator or self.model_format is None
        ):
            raise ValueError("model:n needs kind=model, locator, model_format")
        if any(o.source in PLATFORM_SOURCES for o in self.manifest_observations):
            raise ValueError("manifest observations must not use platform sources")
        _sorted_unique(self.manifest_observations)
        if self.challenge_tags != sorted(set(self.challenge_tags)):
            raise ValueError("challenge_tags sorted and unique")
        return self


class Entry(Base):
    schema_version: Literal["0.1"]
    id: str  # canonical purl, no qualifiers or subpath
    ecosystem: Ecosystem
    stratum: Stratum
    rank: int | None = None
    collected_with: dict[str, str]
    platform: Platform
    subjects: list[Subject] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        p = PackageURL.from_string(self.id)
        if p.qualifiers or p.subpath or p.to_string() != self.id:
            raise ValueError("id must be a canonical purl without qualifiers/subpath")
        if p.type != self.ecosystem or self.platform.ecosystem != self.ecosystem:
            raise ValueError("ecosystem mismatch")
        if p.version != self.platform.version:
            raise ValueError("purl version must equal platform.version")
        if not {
            "sbomminbench",
            "licenseid",
            "pitloom",
            "license_list",
            "license_list_format",
        } <= set(self.collected_with):
            raise ValueError("collected_with is missing a key")
        ids = [s.id for s in self.subjects]
        if ids != ["root"] + [f"model:{i}" for i in range(1, len(ids))]:
            raise ValueError("subjects must be root, model:1..n")
        if self.subjects[0].kind != (
            "model" if self.ecosystem == "huggingface" else "package"
        ):
            raise ValueError(
                "root kind must be model for huggingface, package otherwise"
            )
        platform_keys = {o.key: o for o in self.platform.observations}
        for s in self.subjects:
            keys = {o.key: o for o in s.manifest_observations} | (
                platform_keys if s.id == "root" else {}
            )
            for el in ("name", "provider", "license"):
                for k in getattr(s.expected, el).basis:
                    if k not in keys or keys[k].element != el:
                        raise ValueError(
                            f"{s.id}.{el}: basis key {k!r} not an observation of that element"
                        )
        return self


class ReviewLine(Base):
    id: str
    subject: str
    element: Element
    decision: Literal["confirm", "correct", "exclude"]
    value: LicenseValue | NameValue | ProviderValue | None = None
    reviewer: str = Field(pattern=r"^[a-z0-9_-]+$")
    at: str = Field(pattern=UTC_PATTERN)
    note: str | None = None
    # The auto answer the decision was made against (for the stale check).
    auto_status: AutoStatus
    auto_value_at_review: LicenseValue | NameValue | ProviderValue | None

    @model_validator(mode="before")
    @classmethod
    def _typed_value(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        missing = [k for k in _AUTO_FIELDS if k not in data]
        if missing:
            raise ValueError(
                f"review line needs {' and '.join(missing)} (the auto answer "
                "it was made against; use null for no value)"
            )
        models: dict[str, type[BaseModel]] = {
            "license": LicenseValue,
            "name": NameValue,
            "provider": ProviderValue,
        }
        model = models.get(data.get("element", ""))
        if model is not None:
            for key in ("value", "auto_value_at_review"):
                if isinstance(data.get(key), dict):
                    data = {**data, key: model.model_validate(data[key])}
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.decision == "correct") != (self.value is not None):
            raise ValueError("a value is required for correct and forbidden otherwise")
        return self
