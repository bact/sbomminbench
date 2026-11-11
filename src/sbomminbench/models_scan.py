# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Find embedded AI model files in an archive from names and leading bytes."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sbomminbench.archive import Member, read_head

SCANNED_FORMATS = frozenset(
    {
        "onnx",
        "gguf",
        "safetensors",
        "pytorch",
        "pytorch_pt2",
        "keras",
        "fasttext",
        "crfsuite",
    }
)
MAX_MODEL_SUBJECTS = 50
_ARRAY_FORMATS = frozenset({"numpy", "hdf5"})
_ARRAY_TAG = "array_data_not_scanned"
_CAPPED_TAG = "model_subjects_capped"

# Suffix-only guesses; .bin and .model are too ambiguous to name a format.
_SUFFIX_FORMAT = {
    ".onnx": "onnx",
    ".gguf": "gguf",
    ".safetensors": "safetensors",
    ".pt": "pytorch",
    ".pth": "pytorch",
    ".pt2": "pytorch_pt2",
    ".keras": "keras",
    ".ftz": "fasttext",
    ".crfsuite": "crfsuite",
}


@dataclass(frozen=True)
class ModelMember:
    """An archive member recognised as a model file."""

    path: str
    size: int
    format: str


def scan_archive(
    archive: Path, members: list[Member]
) -> tuple[list[ModelMember], list[str]]:
    """Return ``(models, tags)`` for the model files inside *archive*."""
    from pitloom.core.ai_metadata import model_file_suffixes
    from pitloom.extract.ai_model.reader import (
        SNIFF_BYTES,
        detect_ai_model_format_from_header,
    )

    suffixes = model_file_suffixes()
    models: list[ModelMember] = []
    tags: set[str] = set()
    for member in sorted(members, key=lambda m: m.name):
        if not member.is_file:
            continue
        if PurePosixPath(member.name).suffix.lower() not in suffixes:
            continue
        head = read_head(archive, member.name, SNIFF_BYTES)
        fmt = detect_ai_model_format_from_header(head, member.name).value
        if fmt in SCANNED_FORMATS:
            models.append(ModelMember(member.name, member.size, fmt))
        elif fmt in _ARRAY_FORMATS:
            tags.add(_ARRAY_TAG)
    if len(models) > MAX_MODEL_SUBJECTS:
        models = models[:MAX_MODEL_SUBJECTS]
        tags.add(_CAPPED_TAG)
    return models, sorted(tags)


def scan_names(names: list[str]) -> list[tuple[str, str]]:
    """Return ``(name, format)`` for names with an unambiguous model suffix."""
    found = []
    for name in sorted(names):
        fmt = _SUFFIX_FORMAT.get(PurePosixPath(name).suffix.lower())
        if fmt is not None:
            found.append((name, fmt))
    return found
