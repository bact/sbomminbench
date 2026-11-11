# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench.eco import maven
from sbomminbench.eco._pkgfetch import EcoError, Failure
from sbomminbench.eco.base import (
    CacheMiss,
    CacheStore,
    FetchIndex,
    XmlError,
    index_path,
    read_index,
    write_index,
)
from sbomminbench.http import FetchFailed, HttpClient, url_key
from sbomminbench.ids import make_purl
from sbomminbench.schema import Observation
from sbomminbench.seeds import Seed, read_seeds, record_failure

CACHE = Path(__file__).parent / "fixtures" / "cache"
SEEDS = Path(__file__).parent / "fixtures" / "seeds" / "maven.tsv"
BIG = 10 * 1024 * 1024


def recorded(name: str, version: str) -> tuple[FetchIndex, CacheStore]:
    purl = make_purl("maven", name, version)
    return read_index(index_path(CACHE, purl)), CacheStore(CACHE)


def rows(observed: Any) -> dict[str, Observation]:
    return {o.field_path: o for o in observed.manifest}


# -- recorded fixtures (offline) -------------------------------------------------


def test_commons_lang3_licence_is_inherited() -> None:
    index, store = recorded("org.apache.commons:commons-lang3", "3.17.0")
    observed = maven.observe(index, store)
    lic = [o for o in observed.manifest if o.element == "license"]
    assert lic and all(o.field_path.startswith("parent[") for o in lic)
    assert all(o.inherited_from == "pkg:maven/org.apache/apache@33" for o in lic)
    assert "maven_inherited" in observed.tags
    # Developers are defined in the POM itself: not inherited, no prefix.
    own = rows(observed)["developers.developer[0].name"]
    assert (own.raw, own.role, own.inherited_from) == ("Daniel Rall", "author", None)
    assert observed.platform.observations == []
    assert observed.platform.source_url == index.responses["pom"]
    assert observed.platform.artifacts[0].kind == "pom"


def test_junit_own_licence_and_developers() -> None:
    index, store = recorded("junit:junit", "4.13.2")
    observed = maven.observe(index, store)
    by_path = rows(observed)
    assert by_path["licenses.license[0].name"].raw == "Eclipse Public License 1.0"
    assert by_path["licenses.license[0].url"].semantic_format == "other_url"
    devs = [o for o in observed.manifest if o.field_path.startswith("developers.")]
    assert devs and all(o.role == "author" for o in devs)
    assert by_path["organization.name"].role == "organization"
    assert by_path["contributors.contributor[0].name"].role == "contributor"
    assert "maven_inherited" not in observed.tags
    assert observed.tags == ["artifact_not_scanned"]
    assert observed.manifest_files == ["junit-4.13.2.pom"]


def test_doctype_pom_gives_no_observations_and_a_failure(tmp_path: Path) -> None:
    # SYNTHETIC fixture (not a Central artefact): a POM with a DTD and an entity.
    index, store = recorded("example.synthetic:doctype-pom", "1.0")
    observed, failures = maven.observe_with_failures(index, store)
    assert observed.manifest == [] and observed.platform.observations == []
    assert failures == [Failure("pom", "xml_refused")]
    # The failure goes to the failure log the way the caller records it.
    log = tmp_path / "failed.tsv"
    record_failure(log, "maven", index.name, index.version, *failures[0])
    assert log.read_text().splitlines()[1].split("\t")[:5] == [
        "maven",
        "example.synthetic:doctype-pom",
        "1.0",
        "pom",
        "xml_refused",
    ]


def test_jackson_databind_walk_stops_at_four_parents() -> None:
    index, store = recorded("com.fasterxml.jackson.core:jackson-databind", "2.18.0")
    assert sorted(index.responses) == [
        "pom",
        "pom_parent1",
        "pom_parent2",
        "pom_parent3",
        "pom_parent4",
    ]
    by_path = rows(maven.observe(index, store))
    assert by_path["licenses.license[0].name"].raw == (
        "The Apache Software License, Version 2.0"
    )
    assert by_path["licenses.license[0].name"].semantic_format == "prose"
    org = by_path["parent[2].organization.name"]
    assert (org.raw, org.role) == ("FasterXML", "organization")
    assert org.inherited_from == "pkg:maven/com.fasterxml.jackson/jackson-bom@2.18.0"


def test_every_recorded_seed_observes_deterministically() -> None:
    store = CacheStore(CACHE)
    seeds = read_seeds(SEEDS, "maven")
    assert len(seeds) == 5
    for seed in seeds:
        purl = make_purl("maven", seed.name, seed.version)
        index = read_index(index_path(CACHE, purl))
        first = maven.observe(index, store)
        second = maven.observe(index, store)
        assert first.platform.model_dump() == second.platform.model_dump()
        assert [o.model_dump() for o in first.manifest] == [
            o.model_dump() for o in second.manifest
        ]
        keys = [(o.source, o.field_path) for o in first.manifest]
        assert keys == sorted(keys) and len(keys) == len(set(keys))
        assert first.tags == sorted(set(first.tags))


# -- fake transport --------------------------------------------------------------


class Reply:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict({})
        self.url = ""

    def close(self) -> None:
        pass


class Session:
    """Serves ``pom(...)`` text by coordinate; records every URL asked for."""

    def __init__(self, poms: Mapping[str, bytes]) -> None:
        self.poms = poms
        self.asked: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Reply:
        self.asked.append(url)
        body = self.poms.get(url)
        reply = Reply(404) if body is None else Reply(200, body)
        reply.url = url
        return reply


def client(tmp_path: Path, poms: Mapping[str, bytes]) -> tuple[HttpClient, Session]:
    session = Session(poms)
    http = HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
    )
    return http, session


def pom(
    n: str,
    *,
    parent: str | None = None,
    licenses: str | None = None,
    developers: str | None = None,
    bom: bool = False,
) -> bytes:
    """A small POM for artifact ``g:n`` version 1; *parent* names its parent."""
    head = f"<parent><groupId>g</groupId><artifactId>{parent}</artifactId>"
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        + (head + "<version>1</version></parent>" if parent else "")
        + f"<artifactId>{n}</artifactId>"
        + (licenses or "")
        + (developers or "")
        + "</project>"
    )
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode()


def url_of(n: str) -> str:
    return maven.Coord("g", n, "1").url


def lic(name: str) -> str:
    return f"<licenses><license><name>{name}</name></license></licenses>"


def fetch_all(
    tmp_path: Path, poms: Mapping[str, bytes], top: str = "a"
) -> tuple[FetchIndex, CacheStore, Session]:
    http, session = client(tmp_path, {url_of(k): v for k, v in poms.items()})
    seed = Seed("maven", f"g:{top}", "1", "edge", None)
    index = maven.fetch(seed, http, max_bytes=BIG)
    return index, CacheStore(tmp_path / "cache"), session


def test_fetch_records_index_and_artifact(tmp_path: Path) -> None:
    index, store, _ = fetch_all(
        tmp_path, {"a": pom("a", parent="b"), "b": pom("b", licenses=lic("MIT"))}
    )
    assert index.responses == {"pom": url_of("a"), "pom_parent1": url_of("b")}
    assert index.purl == "pkg:maven/g/a@1" and index.members_dir is None
    art = index.artifact
    assert art is not None and (art.kind, art.fetched, art.sha256 is not None) == (
        "pom",
        True,
        True,
    )
    assert art.declared_digest is None and index.tags == ["artifact_not_scanned"]
    write_index(index, tmp_path / "cache")
    assert read_index(index_path(tmp_path / "cache", index.purl)) == index
    lic_row = rows(maven.observe(index, store))["parent[1].licenses.license[0].name"]
    assert (lic_row.raw, lic_row.inherited_from) == ("MIT", "pkg:maven/g/b@1")


def test_parent_chain_stops_at_depth_four(tmp_path: Path) -> None:
    names = "abcdefg"
    poms = {
        n: pom(n, parent=names[i + 1] if i + 1 < len(names) else None)
        for i, n in enumerate(names)
    }
    poms["g"] = pom("g", licenses=lic("deep"))
    index, store, session = fetch_all(tmp_path, poms)
    assert sorted(index.responses) == [
        "pom",
        "pom_parent1",
        "pom_parent2",
        "pom_parent3",
        "pom_parent4",
    ]
    assert url_of("f") not in session.asked  # the sixth POM is never requested
    observed = maven.observe(index, store)
    # Not found within four parents: absent, not an error.
    assert rows(observed)["licenses.license"].present is False
    assert observed.manifest_files == [f"{n}-1.pom" for n in "abcde"]


def test_parent_cycle_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="sbomminbench"):
        index, store, session = fetch_all(
            tmp_path, {"a": pom("a", parent="b"), "b": pom("b", parent="a")}
        )
    assert sorted(index.responses) == ["pom", "pom_parent1"]
    assert session.asked.count(url_of("a")) == 1
    assert "cycle" in caplog.text
    assert maven.observe(index, store).manifest  # still usable


def test_licence_absent_after_walk_gives_absent_rows(tmp_path: Path) -> None:
    index, store, _ = fetch_all(tmp_path, {"a": pom("a", parent="b"), "b": pom("b")})
    by_path = rows(maven.observe(index, store))
    lic_row, dev = by_path["licenses.license"], by_path["developers.developer"]
    assert (lic_row.present, lic_row.native_type, lic_row.role) == (
        False,
        "absent",
        None,
    )
    assert (dev.present, dev.role, dev.element) == (False, "author", "provider")
    assert "contributors.contributor" not in by_path  # no absent row for these
    assert "organization.name" not in by_path


def test_child_licence_overrides_parent(tmp_path: Path) -> None:
    index, store, _ = fetch_all(
        tmp_path,
        {
            "a": pom("a", parent="b", licenses=lic("Child")),
            "b": pom("b", licenses=lic("Parent"), developers=dev("Dee")),
        },
    )
    observed = maven.observe(index, store)
    by_path = rows(observed)
    assert by_path["licenses.license[0].name"].raw == "Child"
    assert not any(p.startswith("parent[1].licenses") for p in by_path)
    # Each group walks on its own: developers still come from the parent.
    assert by_path["parent[1].developers.developer[0].name"].raw == "Dee"
    assert "maven_inherited" in observed.tags


def dev(name: str) -> str:
    return f"<developers><developer><name>{name}</name></developer></developers>"


def test_developers_absent_vs_empty(tmp_path: Path) -> None:
    index, store, _ = fetch_all(
        tmp_path,
        {
            "a": pom("a", parent="b", developers="<developers/>"),
            "b": pom("b", developers=dev("Dee")),
        },
    )
    by_path = rows(maven.observe(index, store))
    empty = by_path["developers.developer"]  # present and empty: authoritative
    assert (empty.present, empty.raw, empty.native_type) == (True, [], "list_empty")
    assert not any(p.startswith("parent[1].developers") for p in by_path)


def test_xml_bom_namespaces_and_raw_placeholders(tmp_path: Path) -> None:
    text = lic("${license.name}\n  ")
    index, store, _ = fetch_all(tmp_path, {"a": pom("a", licenses=text, bom=True)})
    row = rows(maven.observe(index, store))["licenses.license[0].name"]
    assert row.raw == "${license.name}\n  "  # not interpolated, not stripped


def test_placeholder_in_parent_coordinates_ends_walk(tmp_path: Path) -> None:
    body = pom("a", parent="${revision}")
    index, _, session = fetch_all(tmp_path, {"a": body})
    assert list(index.responses) == ["pom"] and session.asked == [url_of("a")]


@pytest.mark.parametrize(
    ("name", "version"),
    [("g", "1"), ("../x:a", "1"), ("g:a", "../1"), ("g:a", "."), ("g:a/b", "1")],
    ids=["no-colon", "dotdot-group", "dotdot-version", "dot", "slash"],
)
def test_bad_coordinates_refused(tmp_path: Path, name: str, version: str) -> None:
    http, session = client(tmp_path, {})
    with pytest.raises(EcoError) as err:
        maven.fetch(Seed("maven", name, version, "edge", None), http, max_bytes=BIG)
    assert (err.value.stage, err.value.reason) == ("seed", "bad_coordinates")
    assert session.asked == []


def test_fetch_refuses_doctype_and_not_a_pom(tmp_path: Path) -> None:
    evil = b'<!DOCTYPE project [<!ENTITY x "y">]><project/>'
    for body in (evil, b"<other/>", b"<project>"):
        http, _ = client(tmp_path / str(len(body)), {url_of("a"): body})
        with pytest.raises(EcoError):
            maven.fetch(Seed("maven", "g:a", "1", "edge", None), http, max_bytes=BIG)
        assert isinstance(EcoError("pom", "x"), FetchFailed)


def test_missing_pom_raises_fetch_failed(tmp_path: Path) -> None:
    http, _ = client(tmp_path, {})
    with pytest.raises(FetchFailed):
        maven.fetch(Seed("maven", "g:a", "1", "edge", None), http, max_bytes=BIG)


def test_missing_parent_is_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="sbomminbench"):
        index, store, _ = fetch_all(tmp_path, {"a": pom("a", parent="gone")})
    assert list(index.responses) == ["pom"] and "unavailable" in caplog.text
    # The recorded index says nothing of the parent, so the walk looks complete.
    assert rows(maven.observe(index, store))["licenses.license"].present is False


def test_pom_over_cap_is_recorded_not_parsed(tmp_path: Path) -> None:
    http, _ = client(tmp_path, {url_of("a"): pom("a", licenses=lic("MIT"))})
    seed = Seed("maven", "g:a", "1", "edge", None)
    index = maven.fetch(seed, http, max_bytes=10)
    art = index.artifact
    assert art is not None and (art.fetched, art.skip_reason) == (False, "too_large")
    assert art.sha256 is None and list(index.responses) == ["pom"]
    observed = maven.observe(index, CacheStore(tmp_path / "cache"))
    assert observed.manifest == [] and observed.platform.artifacts == [art]


def test_unreadable_parent_cuts_walk_and_suppresses_absence(tmp_path: Path) -> None:
    index, store, _ = fetch_all(tmp_path, {"a": pom("a", parent="b"), "b": pom("b")})
    # Replace the parent's cached body by a refused document, as a cache could rot.
    k = url_key(url_of("b"))
    body = tmp_path / "cache" / "http" / k[:2] / f"{k}.body"
    body.write_bytes(b"<!DOCTYPE x><project/>")
    observed, failures = maven.observe_with_failures(index, store)
    assert failures == [Failure("pom_parent1", "xml_refused")]
    assert "licenses.license" not in rows(observed)  # absence not established
    assert observed.manifest_files == ["a-1.pom"]


def test_observe_cache_miss_and_xml_error_types(tmp_path: Path) -> None:
    index, _, _ = fetch_all(tmp_path, {"a": pom("a")})
    with pytest.raises(CacheMiss):
        maven.observe(index, CacheStore(tmp_path / "empty"))
    with pytest.raises(XmlError):
        maven.parse_pom(b"<!ENTITY a 'b'><project/>")
