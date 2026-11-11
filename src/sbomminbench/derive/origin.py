# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Which upstream file a source copies from.

Two sources with the same origin are one witness, not two: the PyPI JSON copies
METADATA, the npm registry copies package.json, the NuGet catalog copies the
nuspec. The name, licence and provider derivations share this map.
"""

ORIGIN = {
    "pypi_json": "core_metadata",
    "METADATA": "core_metadata",
    "PKG-INFO": "core_metadata",
    "pyproject.toml": "pyproject",
    "npm_registry": "package_json",
    "package.json": "package_json",
    "crates_api": "cargo_manifest",
    "Cargo.toml": "cargo_manifest",
    "Cargo.toml.orig": "cargo_manifest",
    "pom.xml": "pom",
    "nuget_catalog": "nuspec",
    "nuspec": "nuspec",
    "hf_api": "hf",
    "hf_card": "hf",
}
