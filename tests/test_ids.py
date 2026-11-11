# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.ids."""

from pathlib import PurePosixPath

import pytest

from sbomminbench.ids import canonical_name_key, entry_relpath, make_purl
from sbomminbench.schema import Ecosystem

SHA = "495F39" + "0" * 34


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        ("pkg:npm/%40babel/core@7.26.0", "npm/%40babel/core@7.26.0.json"),
        ("pkg:pypi/foo@1.0%2Blocal", "pypi/_/foo@1.0%2Blocal.json"),
        (
            "pkg:maven/org.apache.commons/commons-lang3@3.17.0",
            "maven/org.apache.commons/commons-lang3@3.17.0.json",
        ),
        (
            "pkg:huggingface/Qwen/Qwen2.5-72B-Instruct@" + SHA.lower(),
            "huggingface/Qwen/Qwen2.5-72B-Instruct@" + SHA.lower() + ".json",
        ),
    ],
    ids=["npm-scope", "pypi-local-version", "maven-group", "huggingface-owner"],
)
def test_entry_relpath(purl: str, expected: str) -> None:
    assert entry_relpath(purl) == PurePosixPath(expected)


def test_entry_relpath_rejects_versionless_purl() -> None:
    with pytest.raises(ValueError, match="no version"):
        entry_relpath("pkg:pypi/foo")


@pytest.mark.parametrize(
    ("eco", "name", "version", "expected"),
    [
        (
            "maven",
            "org.apache.commons:commons-lang3",
            "3.17.0",
            "pkg:maven/org.apache.commons/commons-lang3@3.17.0",
        ),
        (
            "huggingface",
            "Qwen/Qwen2.5-72B-Instruct",
            SHA,
            f"pkg:huggingface/Qwen/Qwen2.5-72B-Instruct@{SHA.lower()}",
        ),
        ("npm", "@babel/core", "7.26.0", "pkg:npm/%40babel/core@7.26.0"),
        ("npm", "left-pad", "1.3.0", "pkg:npm/left-pad@1.3.0"),
        ("cargo", "serde_json", "1.0.0", "pkg:cargo/serde_json@1.0.0"),
        ("nuget", "Newtonsoft.Json", "13.0.3", "pkg:nuget/Newtonsoft.Json@13.0.3"),
        ("pypi", "Foo_Bar", "1.0+local", "pkg:pypi/foo-bar@1.0%2Blocal"),
    ],
    ids=[
        "maven",
        "huggingface-lowercase-sha",
        "npm-scope",
        "npm-plain",
        "cargo-as-given",
        "nuget-as-given",
        "pypi-normalised",
    ],
)
def test_make_purl(eco: Ecosystem, name: str, version: str, expected: str) -> None:
    assert make_purl(eco, name, version) == expected


@pytest.mark.parametrize(
    ("eco", "name"),
    [("npm", "@babel"), ("maven", "commons-lang3"), ("huggingface", "Qwen")],
    ids=["npm-scope-without-name", "maven-without-group", "hf-without-owner"],
)
def test_make_purl_rejects_malformed_name(eco: Ecosystem, name: str) -> None:
    with pytest.raises(ValueError, match="needs the form"):
        make_purl(eco, name, "1.0")


def test_purl_drift_guard() -> None:
    # Lazy import: pitloom is imported only inside this test.
    from pitloom.core.models import build_pypi_purl
    from pitloom.core.purl import build_huggingface_purl

    pypi = make_purl("pypi", "Foo_Bar", "1.0+local")
    assert pypi == build_pypi_purl("Foo_Bar", "1.0+local")
    assert pypi == "pkg:pypi/foo-bar@1.0%2Blocal"
    hf = make_purl("huggingface", "Qwen/Qwen2.5-72B-Instruct", SHA)
    assert hf == build_huggingface_purl("Qwen/Qwen2.5-72B-Instruct", SHA)


@pytest.mark.parametrize(
    ("eco", "name", "expected"),
    [
        ("cargo", "Serde_Json", "serde-json"),
        ("pypi", "Foo_Bar.baz", "foo-bar-baz"),
        ("nuget", "Newtonsoft.Json", "newtonsoft.json"),
        ("npm", "@Babel/Core", "@Babel/Core"),
        ("maven", "Org.Apache:Commons", "Org.Apache:Commons"),
        ("huggingface", "Qwen/Straße", "qwen/strasse"),
    ],
    ids=["cargo", "pypi", "nuget", "npm-as-is", "maven-as-is", "huggingface-casefold"],
)
def test_canonical_name_key(eco: Ecosystem, name: str, expected: str) -> None:
    assert canonical_name_key(eco, name) == expected
