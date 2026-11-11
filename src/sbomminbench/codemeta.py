# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Crosswalk from (source, field_path) to a CodeMeta term.

NuGet follows the Maven/npm siblings: authors are authors, the publisher
(`owners`) has no CodeMeta term (like npm `maintainers`). Hugging Face has no
rows: it has no CodeMeta column.
"""

import re

from sbomminbench.schema import CodeMeta

_POM = r"(?:parent\[\d+\]\.)?"

TABLE: list[tuple[str, re.Pattern[str], CodeMeta | None]] = [
    ("pypi_json", re.compile(r"info\.author(_email)?"), "author"),
    ("pypi_json", re.compile(r"info\.maintainer(_email)?"), "maintainer"),
    (
        "pypi_json",
        re.compile(r"info\.(license(_expression)?|classifiers\[\d+\])"),
        "license",
    ),
    ("pypi_json", re.compile(r"info\.name"), "name"),
]
for _src in ("METADATA", "PKG-INFO"):
    TABLE += [
        (_src, re.compile(r"Author(-email)?"), "author"),
        (_src, re.compile(r"Maintainer(-email)?"), "maintainer"),
        (
            _src,
            re.compile(r"(License|License-Expression|Classifier\[\d+\])"),
            "license",
        ),
        (_src, re.compile(r"Name"), "name"),
    ]
TABLE += [
    (
        "pyproject.toml",
        re.compile(r"(project|tool\.poetry)\.authors.*"),
        "author",
    ),
    (
        "pyproject.toml",
        re.compile(r"(project|tool\.poetry)\.maintainers.*"),
        "maintainer",
    ),
    (
        "pyproject.toml",
        re.compile(
            r"(project\.license.*|project\.classifiers\[\d+\]|tool\.poetry\.license)"
        ),
        "license",
    ),
    ("pyproject.toml", re.compile(r"(project|tool\.poetry)\.name"), "name"),
]
for _src in ("npm_registry", "package.json"):
    TABLE += [
        (_src, re.compile(r"author.*"), "author"),
        (_src, re.compile(r"contributors.*"), "contributor"),
        (_src, re.compile(r"licenses?.*"), "license"),
        (_src, re.compile(r"name"), "name"),
        (_src, re.compile(r"(maintainers|_npmUser).*"), None),
    ]
for _src in ("Cargo.toml", "Cargo.toml.orig"):
    TABLE += [
        (_src, re.compile(r"package\.authors.*"), "author"),
        (_src, re.compile(r"package\.license(-file)?.*"), "license"),
        (_src, re.compile(r"package\.name"), "name"),
    ]
TABLE += [
    ("crates_api", re.compile(r"version\.license"), "license"),
    ("crates_api", re.compile(r"version\.crate"), "name"),
    ("crates_api", re.compile(r"version\.published_by.*"), None),
    ("pom.xml", re.compile(_POM + r"developers\..*"), "author"),
    ("pom.xml", re.compile(_POM + r"contributors\..*"), "contributor"),
    ("pom.xml", re.compile(_POM + r"licenses\..*"), "license"),
    ("pom.xml", re.compile(_POM + r"name"), "name"),
    ("pom.xml", re.compile(_POM + r"(artifactId|organization\..*)"), None),
    ("nuget_catalog", re.compile(r"id"), "name"),
    ("nuget_catalog", re.compile(r"license(Expression|Url|File)"), "license"),
    ("nuget_catalog", re.compile(r"authors"), "author"),
    ("nuspec", re.compile(r"metadata\.id"), "name"),
    ("nuspec", re.compile(r"metadata\.license(@type|Url)?"), "license"),
    ("nuspec", re.compile(r"metadata\.authors"), "author"),
    ("nuspec", re.compile(r"metadata\.owners"), None),
]


def codemeta_label(source: str, field_path: str) -> CodeMeta | None:
    """CodeMeta term for a field; the first full match wins, else None."""
    for src, pattern, term in TABLE:
        if src == source and pattern.fullmatch(field_path):
            return term
    return None
