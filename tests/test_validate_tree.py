# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.validate_tree: V10 (seeds), V12-V15, V17, V18, V20."""

import json
import shutil
from pathlib import Path

import pytest
from validate_support import (
    BOM,
    EXPECTED,
    GPT2,
    PACKAGING,
    SCHEMA,
    SLF4J,
    edit,
    hits,
    make_tree,
    run,
)

from sbomminbench import build, validate_tree
from sbomminbench.jsonio import dumps
from sbomminbench.schema import Ecosystem
from sbomminbench.standards import release_metadata

CACHE = Path(__file__).parent / "fixtures" / "cache"
SEED_HEADER = "name\tversion\tstratum\trank\tnote\n"
ALL_ECOS: list[Ecosystem] = ["cargo", "huggingface", "maven", "npm", "nuget", "pypi"]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _seeds(data: Path, eco: str, *rows: str) -> None:
    _write(data / "seeds" / f"{eco}.tsv", SEED_HEADER + "".join(r + "\n" for r in rows))


# -- V13 and V10 ---------------------------------------------------------------------


def test_v13_seed_with_an_entry_is_clean(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING, GPT2))
    _seeds(data, "pypi", "packaging\t26.3\ttop\t1\t")
    _seeds(
        data,
        "huggingface",
        "openai-community/gpt2\t607a30d783dfa663caf39e06633721c8d4cfcd7e\ttop\t1\t",
    )
    report = run(data)
    assert not hits(report, "V13") and not hits(report, "V10")


def test_v13_seed_without_entry_or_failure_row(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _seeds(data, "pypi", "packaging\t26.3\ttop\t1\t", "idna\t3.10\ttop\t2\t")
    (found,) = hits(run(data), "V13")
    assert found.target == "pypi:idna" and "no entry" in found.detail


def test_v13_failure_row_covers_a_seed_without_entry(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _seeds(data, "pypi", "packaging\t26.3\ttop\t1\t", "idna\t3.10\ttop\t2\t")
    _write(
        data / "seeds" / "failed.tsv",
        "eco\tname\tversion\tstage\treason\tat\npypi\tidna\t3.10\tlayer1\thttp_404\t\n",
    )
    assert not hits(run(data), "V13")


@pytest.mark.parametrize("version", ["", "latest", ">=1.0", "^1.2", "1.*"], ids=str)
def test_v13_unpinned_version(tmp_path: Path, version: str) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _seeds(data, "pypi", f"idna\t{version}\ttop\t2\t")
    found = hits(run(data), "V13")
    assert found and "not pinned" in found[0].detail


def test_v13_unreadable_seed_file(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _write(data / "seeds" / "pypi.tsv", "wrong header\n")
    (found,) = hits(run(data), "V13")
    assert found.target == "seeds/pypi.tsv"


def test_v13_renamed_hub_model_is_found_by_revision(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (GPT2,))
    _seeds(
        data, "huggingface", "gpt2\t607A30D783DFA663CAF39E06633721C8D4CFCD7E\ttop\t1\t"
    )
    assert not hits(run(data), "V13")


def test_v10_entry_version_must_be_a_seed_pin(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _seeds(data, "pypi", "packaging\t26.2\ttop\t1\t")
    (found,) = hits(run(data), "V10", "ERROR")
    assert "26.2" in found.detail


def test_v10_entry_without_any_seed_is_a_warning(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING, SLF4J))
    _seeds(data, "pypi", "packaging\t26.3\ttop\t1\t")
    (found,) = hits(run(data), "V10")
    assert found.severity == "WARN" and found.target.startswith("pkg:maven/")


# -- V12 -----------------------------------------------------------------------------

PURL = "pkg:pypi/packaging@26.3"


def _review(**kw: object) -> str:
    element = str(kw.get("element", "license"))
    auto = json.loads((EXPECTED / "pypi/_/packaging@26.3.json").read_bytes())[
        "subjects"
    ][0]["expected"][element]
    line = {
        "id": PURL,
        "subject": "root",
        "element": "license",
        "decision": "confirm",
        "reviewer": "ann",
        "at": "2026-11-09T10:00:00Z",
        "auto_status": auto["status"],
        "auto_value_at_review": auto["value"],
    }
    line.update(kw)
    return json.dumps(line, sort_keys=True) + "\n"


def _reviews(tmp_path: Path, *lines: str) -> Path:
    data = make_tree(tmp_path, (PACKAGING,))
    _write(data / "reviews" / "ann.jsonl", "".join(lines))
    return data


def test_v12_valid_lines_are_clean(tmp_path: Path) -> None:
    data = _reviews(
        tmp_path,
        _review(),
        _review(element="name", decision="exclude"),
        _review(
            decision="correct",
            value={"spdx_expression": "LicenseRef-x", "has_license_ref": True},
        ),
    )
    assert not hits(run(data), "V12")


@pytest.mark.parametrize(
    "line",
    [
        "{not json}\n",
        _review(decision="correct"),
        _review(value={"spdx_expression": "MIT", "has_license_ref": False}),
        _review(subject="model:1"),
        _review(id="pkg:pypi/other@1.0"),
        _review(
            decision="correct",
            value={"spdx_expression": "Apache 2.0", "has_license_ref": False},
        ),
        _review(
            element="name",
            decision="correct",
            value={"name": "X", "canonical_key": "nope", "accepted": ["X"]},
        ),
        _review(
            element="provider",
            decision="correct",
            value={
                "entities": [
                    {"name": "A", "role": "author"},
                    {"name": "a", "role": "author"},
                ]
            },
        ),
        _review(reviewer="Ann Smith"),
    ],
    ids=[
        "json",
        "correct-no-value",
        "confirm-value",
        "subject",
        "id",
        "licence",
        "name",
        "provider",
        "reviewer",
    ],
)
def test_v12_rejects_a_bad_line(tmp_path: Path, line: str) -> None:
    found = hits(run(_reviews(tmp_path, line)), "V12", "ERROR")
    assert len(found) == 1 and found[0].target.startswith(("reviews/", "pkg:"))


def test_v12_stale_confirm_is_a_warning(tmp_path: Path) -> None:
    data = _reviews(tmp_path, _review())
    assert not hits(run(data), "V12")
    edit(
        data,
        PACKAGING,
        lambda d: d["subjects"][0]["expected"]["license"].update(  # type: ignore[index]
            status="auto_conflict", value=None, candidates=["a", "b"]
        ),
    )
    report = run(data)
    (found,) = hits(report, "V12")
    assert found.severity == "WARN" and "stale_review" in found.detail
    assert report.errors == 0


@pytest.mark.parametrize("decision", ["confirm", "correct", "exclude"])
def test_v12_stale_value_change_is_a_warning(tmp_path: Path, decision: str) -> None:
    extra = (
        {"value": {"spdx_expression": "MIT", "has_license_ref": False}}
        if decision == "correct"
        else {}
    )
    data = _reviews(tmp_path, _review(decision=decision, **extra))
    assert not hits(run(data), "V12")
    edit(
        data,
        PACKAGING,
        lambda d: d["subjects"][0]["expected"]["license"]["value"].update(  # type: ignore[index]
            spdx_expression="GPL-3.0-only"
        ),
    )
    (found,) = hits(run(data), "V12")
    assert found.severity == "WARN" and "stale_review" in found.detail


# -- V14 -----------------------------------------------------------------------------


def test_v14_mixed_versions_are_a_warning_listing_the_mix(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING, SLF4J))
    assert not hits(run(data), "V14")
    edit(data, SLF4J, lambda d: d["collected_with"].update(licenseid="0.8.3"))  # type: ignore[attr-defined]
    (found,) = hits(run(data), "V14")
    assert found.severity == "WARN"
    assert "0.8.3 x1" in found.detail and "0.8.4 x1" in found.detail


# -- V15 -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("built") / "data"
    assert build.run_build(CACHE, data, ALL_ECOS, []).failed == 0
    return data


def _copy(built: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "data"
    shutil.copytree(built, dest)
    return dest


def test_v15_rebuild_from_the_cache_is_identical(built: Path, tmp_path: Path) -> None:
    data = _copy(built, tmp_path)
    report = run(data, cache=CACHE)
    assert not hits(report, "V15")
    assert report.errors == 0, report.findings


def test_v15_a_changed_file_differs_from_the_rebuild(
    built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_tree, "SAMPLE_SIZE", 1000)
    data = _copy(built, tmp_path)
    path = data / "entries" / PACKAGING
    edit(data, PACKAGING, lambda d: d.update(rank=999))
    assert path.read_bytes() != (built / "entries" / PACKAGING).read_bytes()
    found = hits(run(data, cache=CACHE), "V15", "ERROR")
    assert [f.target for f in found] == [PURL]


def test_v15_samples_a_fixed_subset(
    built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _copy(built, tmp_path)
    monkeypatch.setattr(validate_tree, "SAMPLE_SIZE", 2)
    for name in (PACKAGING, SLF4J):
        edit(data, name, lambda d: d.update(rank=999))
    first = [f.target for f in hits(run(data, cache=CACHE), "V15")]
    second = [f.target for f in hits(run(data, cache=CACHE), "V15")]
    assert len(first) <= 2 and first == second


def test_v15_without_a_cache_is_a_warning_and_skipped(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    (found,) = hits(run(data), "V15")
    assert found.severity == "WARN"


# -- V17 -----------------------------------------------------------------------------


def _release(data: Path, ids: list[str], rows: int | None = None) -> None:
    _write(
        data / "release" / "entries.jsonl",
        "".join(json.dumps({"id": i}) + "\n" for i in ids),
    )
    n = 3 * len(ids) if rows is None else rows
    body = "".join(f"{ids[0]},root,x\n" for _ in range(n)) if ids else ""
    _write(data / "release" / "subjects.csv", "id,subject,element\n" + body)


def test_v17_release_matches_entries(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _release(data, [PURL])
    assert not hits(run(data), "V17")


@pytest.mark.parametrize(
    ("ids", "rows"),
    [([], None), ([PURL, "pkg:pypi/extra@1"], None), ([PURL], 2)],
    ids=["missing-id", "extra-id", "csv-rows"],
)
def test_v17_release_differs_from_entries(
    tmp_path: Path, ids: list[str], rows: int | None
) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _release(data, ids, rows)
    assert hits(run(data), "V17", "ERROR")


def test_v17_unparsable_release_line(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _release(data, [PURL])
    _write(data / "release" / "entries.jsonl", "oops\n")
    (found,) = hits(run(data), "V17")
    assert "line 1" in found.detail


# -- V18 -----------------------------------------------------------------------------


def test_v18_schema_file_must_equal_the_generated_schema(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    assert not hits(run(data), "V18")
    stale = tmp_path / "entry.schema.json"
    stale.write_bytes(SCHEMA.read_bytes().replace(b"stratum", b"stratus", 1))
    assert hits(run(data, schema=stale), "V18", "ERROR")
    assert hits(run(data, schema=tmp_path / "missing.json"), "V18", "ERROR")


# -- V20 -----------------------------------------------------------------------------


def _bom(tmp_path: Path) -> Path:
    dest = tmp_path / "bom"
    shutil.copytree(BOM, dest)
    return dest


def test_v20_vendored_pin_is_clean(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    assert not hits(run(data, bom=_bom(tmp_path)), "V20")


def test_v20_a_changed_vendored_file(tmp_path: Path) -> None:
    data, bom = make_tree(tmp_path, (PACKAGING,)), _bom(tmp_path)
    target = min((bom / "mapping").glob("*.tsv"))
    target.write_bytes(target.read_bytes() + b"x")
    (found,) = hits(run(data, bom=bom), "V20")
    assert "mapping/" in found.target


def test_v20_a_missing_listed_file(tmp_path: Path) -> None:
    data, bom = make_tree(tmp_path, (PACKAGING,)), _bom(tmp_path)
    min((bom / "mapping").glob("*.tsv")).unlink()
    assert hits(run(data, bom=bom), "V20", "ERROR")


@pytest.mark.parametrize("key", ["bom_release", "retrieved"])
def test_v20_source_json_must_state_the_pin(tmp_path: Path, key: str) -> None:
    data, bom = make_tree(tmp_path, (PACKAGING,)), _bom(tmp_path)
    source = json.loads((bom / "SOURCE.json").read_bytes())
    source[key] = "1999"
    (bom / "SOURCE.json").write_bytes(dumps(source).encode("utf-8"))
    (found,) = hits(run(data, bom=bom), "V20")
    assert key in found.detail


def test_v20_crosswalk_in_release_must_equal_release_metadata(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    path = data / "release" / "standard-elements.json"
    _write(path, dumps(release_metadata()))
    assert not hits(run(data), "V20")
    changed = release_metadata()
    changed["bom_release"] = "9.9.9"
    _write(path, dumps(changed))
    assert hits(run(data), "V20", "ERROR")
    path.unlink()
    assert hits(run(data), "V20", "ERROR")
