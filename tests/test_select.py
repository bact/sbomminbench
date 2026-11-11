# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import json
import re
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench.http import HttpClient
from sbomminbench.schema import Ecosystem
from sbomminbench.seeds import read_seeds
from sbomminbench.select import (
    REGISTRIES,
    FrameError,
    maven_is_stable,
    maven_latest_stable,
    maven_version_key,
    run,
    select,
)


class FakeResponse:
    def __init__(self, status: int, body: bytes, url: str) -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict({})
        self.url = url

    def close(self) -> None:
        pass


class FakeSession:
    """Serves ranking pages: page n holds rows ``p<n>-<i>`` unless overridden."""

    def __init__(self, pages: dict[int, Any] | None = None, fail: int = 0) -> None:
        self.pages = pages or {}
        self.fail = fail
        self.pages_seen: list[int] = []
        self.metadata: dict[str, str] = {}
        self.metadata_seen: list[str] = []

    def get(self, url: str, **_: Any) -> FakeResponse:
        if "maven-metadata.xml" in url:
            self.metadata_seen.append(url)
            body = self.metadata.get(url)
            if body is None:
                return FakeResponse(404, b"", url)
            return FakeResponse(200, body.encode(), url)
        page = int(re.search(r"&page=(\d+)", url).group(1))  # type: ignore[union-attr]
        self.pages_seen.append(page)
        if page == self.fail:
            return FakeResponse(500, b"", url)
        rows = self.pages.get(page)
        if rows is None:
            rows = [
                {"name": f"p{page}-{i}", "latest_release_number": f"{page}.{i}"}
                for i in range(100)
            ]
        return FakeResponse(200, json.dumps(rows).encode(), url)


def client(tmp_path: Path, session: FakeSession) -> HttpClient:
    return HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        retries=0,
        clock=lambda: 0.0,
        sleep=lambda _: None,
    )


def test_same_seed_identical_files(tmp_path: Path) -> None:
    outs = []
    for n in ("a", "b"):
        http = client(tmp_path / n, FakeSession())
        run("pypi", http, tmp_path / n / "seeds", top=3, mid=10, seed=7)
        outs.append((tmp_path / n / "seeds" / "pypi.tsv").read_bytes())
    assert outs[0] == outs[1]
    other = client(tmp_path / "c", FakeSession())
    run("pypi", other, tmp_path / "c" / "seeds", top=3, mid=10, seed=8)
    assert (tmp_path / "c" / "seeds" / "pypi.tsv").read_bytes() != outs[0]


def test_top_takes_ranks_in_order(tmp_path: Path) -> None:
    seeds = select("pypi", client(tmp_path, FakeSession()), top=3, mid=0)
    assert [(s.name, s.rank, s.stratum) for s in seeds] == [
        ("p1-0", 1, "top"),
        ("p1-1", 2, "top"),
        ("p1-2", 3, "top"),
    ]


def test_mid_sampling_and_rank(tmp_path: Path) -> None:
    session = FakeSession()
    seeds = select("npm", client(tmp_path, session), top=0, mid=10, mid_pages=4)
    assert len(seeds) == 10
    assert session.pages_seen == sorted(session.pages_seen)
    assert all(11 <= p <= 100 for p in session.pages_seen)
    for s in seeds:
        page, idx = map(int, s.name[1:].split("-"))
        assert s.stratum == "mid"
        assert s.rank == (page - 1) * 100 + idx + 1


def test_hf_ownerless_skipped_and_sha_lowercased(tmp_path: Path) -> None:
    rows = [
        {"name": "gpt2", "latest_release_number": "a" * 40},
        {"name": "o/m", "latest_release_number": "AB" * 20},
        {"name": None, "latest_release_number": "1"},
        {"name": "o/nover", "latest_release_number": None},
    ]
    seeds = select(
        "huggingface", client(tmp_path, FakeSession({1: rows})), top=5, mid=0
    )
    assert [(s.name, s.version, s.rank) for s in seeds] == [("o/m", "ab" * 20, 2)]


def test_duplicate_and_excluded_names_skipped(tmp_path: Path) -> None:
    rows = [{"name": n, "latest_release_number": "1"} for n in ("a", "a", "b", "c")]
    seeds = select(
        "cargo", client(tmp_path, FakeSession({1: rows})), top=4, mid=0, exclude={"b"}
    )
    assert [s.name for s in seeds] == ["a", "c"]


def test_mid_excludes_top_names(tmp_path: Path) -> None:
    pages = {p: [{"name": "same", "latest_release_number": "1"}] for p in range(1, 101)}
    seeds = select("pypi", client(tmp_path, FakeSession(pages)), top=1, mid=3)
    assert [s.name for s in seeds] == ["same"]


@pytest.mark.parametrize("case", ["top", "mid", "notlist"])
def test_failing_page_raises(tmp_path: Path, case: str) -> None:
    if case == "top":
        session = FakeSession(fail=1)
    elif case == "notlist":
        session = FakeSession({1: {"error": "x"}})
    else:
        session = FakeSession()
        probe = select("pypi", client(tmp_path / "p", session), top=0, mid=4)
        assert probe
        session = FakeSession(fail=session.pages_seen[1])
    with pytest.raises(FrameError):
        select(
            "pypi",
            client(tmp_path / "q", session),
            top=1 if case != "mid" else 0,
            mid=4,
        )


def test_run_appends_once_and_notes_frame(tmp_path: Path) -> None:
    http = client(tmp_path, FakeSession())
    seeds_dir = tmp_path / "seeds"
    assert run("maven", http, seeds_dir, top=2, mid=0) == 2
    assert run("maven", http, seeds_dir, top=2, mid=0) == 0
    assert [s.name for s in read_seeds(seeds_dir / "maven.tsv", "maven")] == [
        "p1-0",
        "p1-1",
    ]
    readme = (seeds_dir / "README.md").read_text(encoding="utf-8")
    assert readme.count("- maven: frame ") == 1
    assert "sort=dependent_packages_count" in readme


def test_registries_cover_ecosystems() -> None:
    eco: tuple[Ecosystem, ...] = (
        "pypi",
        "npm",
        "cargo",
        "maven",
        "nuget",
        "huggingface",
    )
    assert sorted(REGISTRIES) == sorted(eco)


# Minimal real ecosyste.ms row (2026-11-09): latest_release_number is the 2005
# republication, not the current release.
COMMONS_IO_ROW = {
    "name": "commons-io:commons-io",
    "latest_release_number": "20030203.000550",
    "latest_release_published_at": "2005-11-08T22:29:08.000Z",
    "versions_count": 35,
}
COMMONS_IO_URL = (
    "https://repo1.maven.org/maven2/commons-io/commons-io/maven-metadata.xml"
)
COMMONS_IO_META = (
    "<metadata><versioning><latest>2.22.0</latest><release>2.22.0</release>"
    "<versions><version>2.9.0</version><version>2.10.0</version>"
    "<version>2.22.0</version></versions></versioning></metadata>"
)


@pytest.mark.parametrize("meta", ["ok", "missing", "no_versions"])
def test_maven_version_from_registry_metadata(tmp_path: Path, meta: str) -> None:
    session = FakeSession({1: [COMMONS_IO_ROW]})
    if meta == "ok":
        session.metadata[COMMONS_IO_URL] = COMMONS_IO_META
    elif meta == "no_versions":
        session.metadata[COMMONS_IO_URL] = "<metadata/>"
    seeds = select("maven", client(tmp_path, session), top=1, mid=0)
    assert session.metadata_seen == [COMMONS_IO_URL]
    expected = "2.22.0" if meta == "ok" else "20030203.000550"
    assert [s.version for s in seeds] == [expected]


def test_non_maven_version_not_looked_up(tmp_path: Path) -> None:
    session = FakeSession({1: [{"name": "a", "latest_release_number": "1.0"}]})
    seeds = select("pypi", client(tmp_path, session), top=1, mid=0)
    assert [s.version for s in seeds] == ["1.0"] and not session.metadata_seen


def meta_xml(release: str, versions: list[str]) -> str:
    items = "".join(f"<version>{v}</version>" for v in versions)
    return (
        f"<metadata><versioning><release>{release}</release>"
        f"<versions>{items}</versions></versioning></metadata>"
    )


# Minimal real maven-metadata.xml extracts (2026-11-09): <release> is a
# pre-release for the first two; guava has -jre and -android twins.
MAVEN_CASES = {
    "scala": (
        "org.scala-lang:scala-library",
        meta_xml(
            "3.10.1-RC2",
            ["2.13.19-M1", "3.9.0", "3.10.0-RC3", "3.10.0", "3.10.1-RC1", "3.10.1-RC2"],
        ),
        "3.10.0",
    ),
    "slf4j": (
        "org.slf4j:slf4j-api",
        meta_xml(
            "2.1.0-alpha1",
            ["1.7.36", "2.0.9", "2.0.10", "2.0.20", "2.1.0-alpha0", "2.1.0-alpha1"],
        ),
        "2.0.20",
    ),
    "guava": (
        "com.google.guava:guava",
        meta_xml(
            "33.8.0-jre",
            ["33.7.0-jre", "33.8.0-android", "33.8.0-jre", "33.9.0-rc1-jre"],
        ),
        "33.8.0-jre",
    ),
    "datestamp": (
        "commons-io:commons-io",
        meta_xml("2.22.0", ["20030203.000550", "2.9.0", "2.10.0", "2.22.0"]),
        "2.22.0",
    ),
}


@pytest.mark.parametrize("case", sorted(MAVEN_CASES))
def test_maven_picks_highest_stable_version(tmp_path: Path, case: str) -> None:
    name, xml, expected = MAVEN_CASES[case]
    path = name.split(":")[0].replace(".", "/") + "/" + name.split(":")[1]
    session = FakeSession({1: [{"name": name, "latest_release_number": "0"}]})
    session.metadata[f"https://repo1.maven.org/maven2/{path}/maven-metadata.xml"] = xml
    seeds = select("maven", client(tmp_path, session), top=1, mid=0)
    assert [s.version for s in seeds] == [expected]


@pytest.mark.parametrize(
    ("version", "stable"),
    [
        *[
            (v, False)
            for v in (
                "1.0-alpha1",
                "2.0.0-Beta2",
                "3.1-RC2",
                "3.1-CR1",
                "1.0-M5",
                "1.0.milestone-2",
                "1.0-SNAPSHOT",
                "1.0-preview",
                "1.0-dev",
                "1.0-EA",
                "2.13.0-pre-20d3c21",
            )
        ],
        *[
            (v, True)
            for v in (
                "33.8.0-jre",
                "33.8.0-android",
                "1.0.Final",
                "1.0-ga",
                "5.3.RELEASE",
                "4.13.2",
                "1.0-metrics",
                "1.0-m",
            )
        ],
    ],
    ids=lambda p: str(p),
)
def test_maven_is_stable(version: str, stable: bool) -> None:
    assert maven_is_stable(version) is stable


def test_maven_order_is_numeric_not_lexicographic() -> None:
    assert maven_latest_stable(["2.9", "2.10", "2.2"]) == "2.10"
    assert maven_version_key("1.0") == maven_version_key("1.0.0")
    assert maven_latest_stable(["33.8.0-android", "33.8.0-jre"]) == "33.8.0-jre"
    assert maven_latest_stable(["1.0-rc1"]) is None
    assert maven_latest_stable(["20030203.000550"]) == "20030203.000550"


def test_run_does_not_add_second_version_of_known_name(tmp_path: Path) -> None:
    seeds_dir = tmp_path / "seeds"
    first = FakeSession({1: [{"name": "a", "latest_release_number": "1"}]})
    assert run("pypi", client(tmp_path / "c1", first), seeds_dir, top=1, mid=0) == 1
    second = FakeSession({1: [{"name": "a", "latest_release_number": "2"}]})
    assert run("pypi", client(tmp_path / "c2", second), seeds_dir, top=1, mid=0) == 0
    assert [
        (s.name, s.version) for s in read_seeds(seeds_dir / "pypi.tsv", "pypi")
    ] == [("a", "1")]
