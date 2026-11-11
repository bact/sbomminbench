# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.stats (stats.json)."""

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from helpers_release import FIXTURES, REQUESTS, make_data_dir, review, write_review

from sbomminbench import export, jsonio
from sbomminbench.export_review import load_final
from sbomminbench.schema import Entry
from sbomminbench.stats import _audit, compute_stats, frame_dates, run_cli

EXAMPLES = Path(__file__).parent / "fixtures" / "examples"


def stats_of(data: Path) -> dict[str, Any]:
    entries, _ = load_final(data)
    return compute_stats(entries, frame_dates(data))


def test_counts_and_status_table(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    s = stats_of(data)
    assert s["counts"] == {"entries": len(FIXTURES), "subjects": 8}
    total = sum(
        n
        for els in s["status_counts"].values()
        for statuses in els.values()
        for n in statuses.values()
    )
    assert total == 8 * 3
    assert s["status_counts"]["pypi"]["license"]["not_assessed"] == 1  # model:1
    assert set(s["status_counts"]) == {
        "cargo",
        "huggingface",
        "maven",
        "npm",
        "nuget",
        "pypi",
    }
    assert s["model_subjects_by_format"] == {"onnx": 1, "unspecified": 1}


def test_tag_and_observation_counts_are_sorted(tmp_path: Path) -> None:
    s = stats_of(make_data_dir(tmp_path))
    assert list(s["tag_counts"]) == sorted(s["tag_counts"])
    keys = [
        (r["source"], r["field_path"], r["native_type"], r["semantic_format"] or "")
        for r in s["observation_counts"]
    ]
    assert keys == sorted(keys)
    assert all("[0" not in r["field_path"] for r in s["observation_counts"])
    assert sum(r["n"] for r in s["observation_counts"]) > 50


def test_license_conformance_has_all_verdicts(tmp_path: Path) -> None:
    s = stats_of(make_data_dir(tmp_path))
    table = s["license_conformance"]
    assert table["npm"]["package.json:license"] == {
        "conforms": 1,
        "lenient": 0,
        "violates": 0,
        "not_spdx": 0,
    }
    assert table["cargo"]["crates_api:version.license"]["violates"] == 1
    for fields in table.values():
        for verdicts in fields.values():
            assert set(verdicts) == {"conforms", "lenient", "violates", "not_spdx"}


def test_cross_tab_uses_the_derive_trail(tmp_path: Path) -> None:
    s = stats_of(make_data_dir(tmp_path))
    cross = s["format_by_licenseid_category"]
    assert cross["by_category"]["id_shaped"]["exact"] >= 1
    n_format = sum(r["n"] for r in s["observation_counts"] if r["semantic_format"])
    n_cross = sum(n for cats in cross["by_category"].values() for n in cats.values())
    assert n_cross == n_format
    for fmt, cats in cross["by_category"].items():
        assert sum(cross["by_reading"][fmt].values()) == sum(cats.values())


def test_cross_tab_without_trail_is_counted(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path, FIXTURES[1:2])
    path = next((data / "entries").rglob("*.json"))
    entry = jsonio.read_entry(path)
    entry.subjects[0].expected.license.licenseid = []
    path.write_bytes(jsonio.dumps(entry.model_dump(mode="json")).encode())
    cross = stats_of(data)["format_by_licenseid_category"]["by_category"]
    assert {c for cats in cross.values() for c in cats} == {"no_trail"}


def test_raw_form_agreement(tmp_path: Path) -> None:
    s = stats_of(make_data_dir(tmp_path))
    assert "source_agreement" not in s
    pypi = s["raw_form_agreement"]["pypi"]["license"]
    assert pypi["both"] >= pypi["overlap"] >= pypi["equal"] >= 0
    assert pypi["both"] >= 1


def test_audit_counts_reviewed_unanimous_answers(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    assert stats_of(data)["audit"]["all"] == {
        "k": 0,
        "n": 0,
        "wilson_low": None,  # nothing judged: no interval, not [0, 1]
        "wilson_high": None,
        "excluded": 0,
    }
    name = {"name": "Requests", "canonical_key": "requests", "accepted": ["Requests"]}
    write_review(
        data,
        "alice",
        review("confirm"),
        review("correct", element="name", value=name),
        review("exclude", element="provider"),
    )
    audit = stats_of(data)["audit"]
    assert audit["description"] == (
        "precision of silver labels on root subjects, non-blind"
    )
    assert (audit["all"]["excluded"], audit["provider"]["excluded"]) == (1, 1)
    assert (audit["all"]["k"], audit["all"]["n"]) == (1, 2)  # excluded is not judged
    by = audit["by_origins"]
    assert by["1"]["n"] + by["2+"]["n"] == audit["all"]["n"]
    assert by["1"]["n"] != 0 or by["2+"]["n"] != 0
    assert (audit["license"]["k"], audit["license"]["n"]) == (1, 1)
    assert (audit["name"]["k"], audit["name"]["n"]) == (0, 1)
    assert 0 < audit["all"]["wilson_low"] < 0.5 < audit["all"]["wilson_high"] < 1
    final = stats_of(data)["status_counts"]["pypi"]
    assert final["provider"]["excluded"] == 1
    assert final["license"]["human_confirmed"] == 1


def test_origins_and_collected_with(tmp_path: Path) -> None:
    s = stats_of(make_data_dir(tmp_path))
    assert set(s["independent_origins"]) == {"all", "auto_unanimous"}
    assert sum(s["independent_origins"]["all"]["name"].values()) == 8
    assert set(s["collected_with"]) >= {"licenseid", "pitloom", "license_list_format"}
    assert all(isinstance(v, list) for v in s["collected_with"].values())


def test_frame_dates(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    assert stats_of(data)["frame_dates"] == {}
    readme = data / "seeds" / "README.md"
    readme.parent.mkdir()
    readme.write_text(
        "# Seed frames\n\n- pypi: frame 2026-11-08, top=2\n- pypi: frame 2026-11-09, x\n"
        "- npm: frame 2026-11-08, top=1\nnot a frame line\n",
        encoding="utf-8",
    )
    assert frame_dates(data) == {
        "npm": ["2026-11-08"],
        "pypi": ["2026-11-08", "2026-11-09"],
    }


def test_deterministic_and_matches_export(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    args = argparse.Namespace(data_dir=data)
    assert run_cli(args) == 0
    path = data / "release" / "stats.json"
    first = path.read_bytes()
    assert run_cli(args) == 0
    assert path.read_bytes() == first
    assert first.endswith(b"\n") and b"\r" not in first
    export.export(data)
    assert path.read_bytes() == first


def test_stats_run_cli_prints_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = make_data_dir(tmp_path)
    run_cli(argparse.Namespace(data_dir=data))
    out, err = capsys.readouterr()
    assert out.startswith("RESULT=written FILE=")
    assert err.startswith("INFO: stats: ")
    run_cli(argparse.Namespace(data_dir=data))
    assert capsys.readouterr().out.startswith("RESULT=unchanged")


def test_stats_empty_data_dir(tmp_path: Path) -> None:
    s = stats_of(tmp_path / "data")
    assert s["counts"] == {"entries": 0, "subjects": 0}
    assert s["status_counts"] == {} and s["tag_counts"] == {}
    assert json.dumps(s)  # serialisable
    assert REQUESTS not in json.dumps(s)


def test_audit_counts_root_subjects_only() -> None:
    """A reviewed non-root answer is not a root-subject precision sample."""
    data = json.loads((EXAMPLES / "magika.json").read_text("utf-8"))
    value = data["subjects"][0]["expected"]["name"]["value"]
    for sub in data["subjects"]:
        ex = sub["expected"]["name"]
        ex.update(
            status="human_confirmed",
            auto_status="auto_unanimous",
            reviewer="r",
            value=value,
        )
    entry = Entry.model_validate(data)
    assert [s.id for s in entry.subjects] == ["root", "model:1"]
    audit = _audit([entry])
    assert (audit["name"]["k"], audit["name"]["n"]) == (1, 1)  # not 2
