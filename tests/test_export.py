# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.export (release files)."""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from helpers_release import FIXTURES, REQUESTS, make_data_dir, review, write_review

from sbomminbench import benchmark_csv, export, jsonio, standards
from sbomminbench.export_rows import OBSERVATION_COLUMNS, SUBJECT_COLUMNS
from sbomminbench.redact import find_emails
from sbomminbench.schema import Entry, ExpectedLicense

LICENSE_CORRECTION = {
    "value": {"spdx_expression": "Apache-2.0 OR MIT", "has_license_ref": False}
}


def rows(path: Path) -> list[dict[str, str]]:
    text = path.read_bytes().decode("utf-8")
    assert "\r" not in text
    return list(csv.DictReader(io.StringIO(text, newline="")))


def release(data: Path) -> Path:
    return data / "release"


def statuses(data: Path) -> dict[tuple[str, str, str], str]:
    return {
        (r["id"], r["subject"], r["element"]): r["status"]
        for r in rows(release(data) / "subjects.csv")
    }


def test_files_and_columns(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    n, written, applied = export.export(data)
    assert (n, written, applied) == (len(FIXTURES), 8, 0)
    names = sorted(p.name for p in release(data).iterdir())
    assert names == sorted([*export.RELEASE_FILES, "SHA256SUMS"])
    header = (
        (release(data) / "subjects.csv").read_text(encoding="utf-8").splitlines()[0]
    )
    assert header.split(",") == SUBJECT_COLUMNS
    header = (release(data) / "observations.csv").read_text(encoding="utf-8")
    assert header.splitlines()[0].split(",") == OBSERVATION_COLUMNS


def test_benchmark_csv_is_derived_from_the_final_entries(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    scored = rows(release(data) / "benchmark.csv")
    unscored = rows(release(data) / "benchmark-unscored.csv")
    subjects = {(r["id"], r["subject"]) for r in rows(release(data) / "subjects.csv")}
    keys = [(r["id"], r["subject"]) for r in scored + unscored]
    assert sorted(keys) == sorted(subjects) and list(unscored[0]) == [
        *benchmark_csv.COLUMNS,
        "reason",
    ]
    assert list(scored[0]) == benchmark_csv.COLUMNS
    assert [r["id"] for r in scored] == sorted(r["id"] for r in scored)
    write_review(data, "alice", review("correct", **LICENSE_CORRECTION))
    export.export(data)
    row = next(r for r in rows(release(data) / "benchmark.csv") if r["id"] == REQUESTS)
    assert (row["expected_license"], row["license_status"]) == (
        "Apache-2.0 OR MIT",
        "human_corrected",
    )


def test_entries_jsonl_compact_sorted(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    lines = (release(data) / "entries.jsonl").read_bytes().decode().split("\n")
    assert lines[-1] == ""
    ids = [json.loads(line)["id"] for line in lines[:-1]]
    assert ids == sorted(ids) and len(ids) == len(FIXTURES)
    assert all(
        ": " not in line[:40] and line.startswith('{"collected_with"')
        for line in lines[:-1]
    )
    for line in lines[:-1]:
        Entry.model_validate_json(line)


def test_three_rows_per_subject(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    entries = [e for _, e in jsonio.iter_entries(data / "entries")]
    subjects = sum(len(e.subjects) for e in entries)
    table = rows(release(data) / "subjects.csv")
    assert subjects > len(entries)  # a model subject is present
    assert len(table) == 3 * subjects
    per = {}
    for r in table:
        per.setdefault((r["id"], r["subject"]), []).append(r["element"])
    assert all(v == ["name", "provider", "license"] for v in per.values())


def test_observation_rows(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    table = rows(release(data) / "observations.csv")
    layers = {r["layer"] for r in table}
    assert layers == {"platform", "manifest"}
    assert {r["subject"] for r in table if r["layer"] == "platform"} == {"root"}
    lic = [r for r in table if r["element"] == "license" and r["conformance"]]
    assert {r["conformance"] for r in lic} <= {
        "conforms",
        "lenient",
        "violates",
        "not_spdx",
        "not_applicable",
    }
    assert any(
        r["field_contract"] == "npm_license" and r["conformance"] == "conforms"
        for r in lic
    )
    assert all(r["field_contract"] == "" for r in table if r["element"] != "license")


def test_raw_json_is_cut_and_flagged(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path, FIXTURES[1:2])
    path = next((data / "entries").rglob("*.json"))
    entry = jsonio.read_entry(path)
    obs = next(
        o for o in entry.platform.observations if o.element == "name" and o.present
    )
    obs.raw = "x" * 1500
    path.write_bytes(jsonio.dumps(entry.model_dump(mode="json")).encode())
    export.export(data)
    cut = [
        r
        for r in rows(release(data) / "observations.csv")
        if r["raw_truncated"] == "true"
    ]
    assert len(cut) == 1 and len(cut[0]["raw_json"]) == 1000


def test_deterministic_and_only_when_changed(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    first = {p.name: p.read_bytes() for p in release(data).iterdir()}
    mtimes = {p.name: p.stat().st_mtime_ns for p in release(data).iterdir()}
    _, written, _ = export.export(data)
    second = {p.name: p.read_bytes() for p in release(data).iterdir()}
    assert written == 0
    assert first == second
    assert mtimes == {p.name: p.stat().st_mtime_ns for p in release(data).iterdir()}
    assert all(b"\r" not in b and b.endswith(b"\n") for b in first.values() if b)


def test_entry_input_order_does_not_matter(tmp_path: Path) -> None:
    a = make_data_dir(tmp_path / "a", FIXTURES)
    b = make_data_dir(tmp_path / "b", tuple(reversed(FIXTURES)))
    export.export(a)
    export.export(b)
    for name in (*export.RELEASE_FILES, "SHA256SUMS"):
        assert (release(a) / name).read_bytes() == (release(b) / name).read_bytes()


def test_standard_elements_equals_release_metadata(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    assert (release(data) / "standard-elements.json").read_bytes() == jsonio.dumps(
        standards.release_metadata()
    ).encode()


def test_sha256sums(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    sums = (release(data) / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert [s.split("  ")[1] for s in sums] == sorted(export.RELEASE_FILES)
    for line in sums:
        digest, name = line.split("  ")
        assert digest == hashlib.sha256((release(data) / name).read_bytes()).hexdigest()


def test_review_changes_exactly_one_status(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    before = statuses(data)
    write_review(data, "alice", review("correct", **LICENSE_CORRECTION))
    _, _, applied = export.export(data)
    after = statuses(data)
    assert applied == 1
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert changed == {
        (REQUESTS, "root", "license"): ("auto_unanimous", "human_corrected")
    }
    row = next(
        r
        for r in rows(release(data) / "subjects.csv")
        if (r["id"], r["subject"], r["element"]) == (REQUESTS, "root", "license")
    )
    assert row["auto_status"] == "auto_unanimous"
    assert json.loads(row["gold_json"])["spdx_expression"] == "Apache-2.0 OR MIT"
    final = [
        json.loads(x)
        for x in (release(data) / "entries.jsonl").read_text().splitlines()
    ]
    exp = next(e for e in final if e["id"] == REQUESTS)["subjects"][0]["expected"][
        "license"
    ]
    assert (exp["reviewer"], exp["reviewed_at"]) == ("alice", "2026-11-10T01:00:00Z")
    assert exp["status"] == "human_corrected" and exp["licenseid"]
    # the entries under data/entries are never rewritten with the human answer
    assert (
        "human_corrected"
        not in (data / "entries/pypi/_/requests@2.32.3.json").read_text()
    )


def test_confirm_keeps_value_and_latest_review_wins(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    write_review(
        data,
        "alice",
        review("correct", at="2026-11-10T02:00:00Z", **LICENSE_CORRECTION),
        review("confirm", at="2026-11-10T03:00:00Z"),
    )
    write_review(data, "bob", review("exclude", at="2026-11-10T01:00:00Z"))
    export.export(data)
    row = next(
        r
        for r in rows(release(data) / "subjects.csv")
        if (r["id"], r["subject"], r["element"]) == (REQUESTS, "root", "license")
    )
    assert row["status"] == "human_confirmed"
    assert json.loads(row["gold_json"])["spdx_expression"] != "Apache-2.0 OR MIT"


def test_excluded_entry_has_no_gold(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    write_review(data, "alice", review("exclude", element="name"))
    export.export(data)
    row = next(
        r
        for r in rows(release(data) / "subjects.csv")
        if (r["id"], r["element"]) == (REQUESTS, "name")
    )
    assert (row["status"], row["auto_status"], row["gold_json"]) == (
        "excluded",
        "auto_unanimous",
        "",
    )


def test_confirm_on_conflict_is_ignored_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = make_data_dir(tmp_path, FIXTURES[1:2])
    path = next((data / "entries").rglob("*.json"))
    entry = jsonio.read_entry(path)
    entry.subjects[0].expected.license = ExpectedLicense(
        status="auto_conflict", candidates=["MIT", "Apache-2.0"]
    )
    path.write_bytes(jsonio.dumps(entry.model_dump(mode="json")).encode())
    line = review("confirm", auto_status="auto_conflict", auto_value_at_review=None)
    write_review(data, "alice", line)
    _, _, applied = export.export(data)
    assert applied == 0
    assert "does not apply to auto_conflict" in capsys.readouterr().err
    assert statuses(data)[(REQUESTS, "root", "license")] == "auto_conflict"


@pytest.mark.parametrize("decision", ["confirm", "exclude"])
def test_stale_review_is_skipped_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], decision: str
) -> None:
    data = make_data_dir(tmp_path)
    line = review(decision, auto_value_at_review={"spdx_expression": "MIT"})
    write_review(data, "alice", line)
    _, _, applied = export.export(data)
    assert applied == 0
    assert "WARNING: review ignored" in capsys.readouterr().err
    assert statuses(data)[(REQUESTS, "root", "license")] == "auto_unanimous"


def test_review_for_unknown_entry_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = make_data_dir(tmp_path)
    line = {**review("exclude"), "id": "pkg:pypi/nothing@1.0"}
    write_review(data, "alice", line)
    export.export(data)
    assert "1 review lines match no entry" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text",
    [
        '{"id": "x"}',
        json.dumps(review("correct", element="name", **LICENSE_CORRECTION)),
    ],
    ids=["missing-fields", "value-of-wrong-element"],
)
def test_invalid_review_line_names_file(tmp_path: Path, text: str) -> None:
    data = make_data_dir(tmp_path)
    bad = data / "reviews" / "carol.jsonl"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(text.encode() + b"\n")
    with pytest.raises(ValueError, match=r"carol.jsonl.*line 1"):
        export.export(data)


def test_empty_data_dir(tmp_path: Path) -> None:
    data = tmp_path / "data"
    n, written, _ = export.export(data)
    assert (n, written) == (0, 8)
    assert (release(data) / "entries.jsonl").read_bytes() == b""
    assert rows(release(data) / "subjects.csv") == []
    stats = json.loads((release(data) / "stats.json").read_text())
    assert stats["counts"] == {"entries": 0, "subjects": 0}
    assert export.export(data)[1] == 0


def test_release_has_no_foreign_email(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    export.export(data)
    for p in release(data).iterdir():
        for line in p.read_text(encoding="utf-8").splitlines():
            assert not find_emails(line)
    assert (
        "email.redacted@example.com" in (release(data) / "observations.csv").read_text()
    )


def test_leak_stops_the_export(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path, FIXTURES[1:2])
    path = next((data / "entries").rglob("*.json"))
    entry = jsonio.read_entry(path)
    entry.platform.name = "requests <someone@mail.com>"
    path.write_bytes(jsonio.dumps(entry.model_dump(mode="json")).encode())
    with pytest.raises(jsonio.LeakError, match="entries.jsonl"):
        export.export(data)
    assert not (release(data) / "entries.jsonl").exists()


def test_run_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = make_data_dir(tmp_path)
    args = argparse.Namespace(data_dir=data)
    assert export.run_cli(args) == 0
    out, err = capsys.readouterr()
    assert [x.split("=", 1)[0] for x in out.splitlines()] == ["FILE"] * 8
    assert err.startswith(f"INFO: export: {len(FIXTURES)} entries")


def test_export_accepts_a_purl_ending_final(tmp_path: Path) -> None:
    purl = "pkg:maven/org.jboss.windup.exec/windup-exec@6.3.9.Final"
    data = make_data_dir(tmp_path, FIXTURES[:1])
    path = next((data / "entries").rglob("*.json"))
    doc = json.loads(path.read_bytes())
    doc["platform"]["name"] = f"uses {purl}"
    path.write_bytes(jsonio.dumps(doc).encode("utf-8"))
    export.export(data)
    assert purl in (release(data) / "entries.jsonl").read_text(encoding="utf-8")
