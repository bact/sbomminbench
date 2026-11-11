# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import importlib
import re
from pathlib import Path

import pytest

from sbomminbench.codemeta import codemeta_label
from sbomminbench.eco.base import CacheStore, read_index
from sbomminbench.schema import CodeMeta


@pytest.mark.parametrize(
    ("source", "path", "term"),
    [
        ("pypi_json", "info.author", "author"),
        ("pypi_json", "info.author_email", "author"),
        ("pypi_json", "info.maintainer_email", "maintainer"),
        ("pypi_json", "info.license_expression", "license"),
        ("pypi_json", "info.classifiers[3]", "license"),
        ("pypi_json", "info.name", "name"),
        ("pypi_json", "info.summary", None),
        ("METADATA", "Author-email", "author"),
        ("PKG-INFO", "Maintainer", "maintainer"),
        ("METADATA", "License-Expression", "license"),
        ("METADATA", "Classifier[0]", "license"),
        ("PKG-INFO", "Name", "name"),
        ("pyproject.toml", "project.authors[0].name", "author"),
        ("pyproject.toml", "tool.poetry.authors", "author"),
        ("pyproject.toml", "project.maintainers", "maintainer"),
        ("pyproject.toml", "project.license", "license"),
        ("pyproject.toml", "project.license.text", "license"),
        ("pyproject.toml", "project.classifiers[2]", "license"),
        ("pyproject.toml", "tool.poetry.license", "license"),
        ("pyproject.toml", "tool.poetry.name", "name"),
        ("npm_registry", "author.name", "author"),
        ("package.json", "contributors[0]", "contributor"),
        ("package.json", "license", "license"),
        ("package.json", "licenses[0].type", "license"),
        ("package.json", "name", "name"),
        ("npm_registry", "maintainers[0].name", None),
        ("npm_registry", "_npmUser.name", None),
        ("Cargo.toml", "package.authors[1]", "author"),
        ("Cargo.toml.orig", "package.license-file", "license"),
        ("Cargo.toml", "package.license", "license"),
        ("Cargo.toml", "package.name", "name"),
        ("crates_api", "version.license", "license"),
        ("crates_api", "version.crate", "name"),
        ("crates_api", "version.published_by.name", None),
        ("pom.xml", "developers.developer[0].name", "author"),
        ("pom.xml", "parent[1].developers.developer[0].name", "author"),
        ("pom.xml", "contributors.contributor[0].name", "contributor"),
        ("pom.xml", "licenses.license[0].name", "license"),
        ("pom.xml", "name", "name"),
        ("pom.xml", "artifactId", None),
        ("pom.xml", "organization.name", None),
        ("nuget_catalog", "id", "name"),
        ("nuget_catalog", "licenseExpression", "license"),
        ("nuget_catalog", "licenseUrl", "license"),
        ("nuget_catalog", "licenseFile", "license"),
        ("nuget_catalog", "authors", "author"),
        ("nuspec", "metadata.id", "name"),
        ("nuspec", "metadata.license", "license"),
        ("nuspec", "metadata.license@type", "license"),
        ("nuspec", "metadata.licenseUrl", "license"),
        ("nuspec", "metadata.authors", "author"),
        ("nuspec", "metadata.owners", None),
        ("hf_api", "tags[0]", None),
        ("hf_card", "cardData.license", None),
        ("METADATA", "Authors", None),
    ],
    ids=lambda p: str(p)[:28],
)
def test_label(source: str, path: str, term: CodeMeta | None) -> None:
    assert codemeta_label(source, path) == term


CACHE = Path(__file__).parent / "fixtures" / "cache"
# Sources with a crosswalk (hf_api and hf_card have no CodeMeta column).
MAPPED_SOURCES = frozenset(
    {
        "pypi_json",
        "PKG-INFO",
        "METADATA",
        "pyproject.toml",
        "npm_registry",
        "package.json",
        "crates_api",
        "Cargo.toml",
        "Cargo.toml.orig",
        "pom.xml",
        "nuget_catalog",
        "nuspec",
    }
)
# Documented list of field paths with no CodeMeta term, per source (a
# publisher or an owner is not an author; a Maven artifactId is not a name).
UNMAPPED = {
    "npm_registry": re.compile(r"(maintainers|_npmUser).*"),
    "package.json": re.compile(r"maintainers.*"),
    "crates_api": re.compile(r"version\.published_by.*"),
    "pom.xml": re.compile(r"(parent\[\d+\]\.)?(artifactId|organization\..*)"),
    "nuspec": re.compile(r"metadata\.owners"),
}


def test_every_recorded_field_of_a_mapped_source_has_its_term() -> None:
    """`observe` over all recorded indexes: only the documented list is None."""
    store = CacheStore(CACHE)
    seen: set[str] = set()
    unmapped: list[str] = []
    for path in sorted((CACHE / "index").rglob("*.json")):
        index = read_index(path)
        mod = importlib.import_module(f"sbomminbench.eco.{index.ecosystem}")
        observed = mod.observe(index, store)
        for row in [*observed.platform.observations, *observed.manifest]:
            if row.source not in MAPPED_SOURCES:
                assert row.codemeta is None, row.key
                continue
            seen.add(row.source)
            listed = UNMAPPED.get(row.source)
            expected_none = bool(listed and listed.fullmatch(row.field_path))
            if (row.codemeta is None) != expected_none:
                unmapped.append(f"{path.name}: {row.key}={row.codemeta}")
    assert not unmapped
    assert seen == MAPPED_SOURCES
