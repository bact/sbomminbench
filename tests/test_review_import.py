# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``sbomminbench.review_import`` (``review-import``)."""

import argparse
from pathlib import Path

import pytest
from review_support import make_data, read_rows, with_expected, write_rows

from sbomminbench import review
from sbomminbench.jsonio import write_entry
from sbomminbench.review_common import ReviewError, load_entries
from sbomminbench.review_export import csv_text, queue_rows
from sbomminbench.review_import import check_license, import_reviews
from sbomminbench.review_store import read_lines
from sbomminbench.schema import (
    LicenseValue,
    ProviderValue,
    ReviewLine,
)

AT = "2026-11-09T10:00:00Z"
ALL = ["auto_conflict", "auto_unresolved", "absent_in_source", "not_assessed"]
RING = "pkg:cargo/ring@0.16.20"
FLASK = "pkg:pypi/flask@3.0.3"
CLAP = "pkg:cargo/clap@4.5.20"


def _queue(tmp_path: Path) -> tuple[Path, list[dict[str, str]]]:
    data = make_data(tmp_path)
    return data, read_rows(csv_text(queue_rows(load_entries(data), ALL)))


def _row(rows: list[dict[str, str]], purl: str, element: str) -> dict[str, str]:
    return next(r for r in rows if r["id"] == purl and r["element"] == element)


def _import(tmp_path: Path, data: Path, rows: list[dict[str, str]]) -> object:
    queue = write_rows(tmp_path / "in.csv", rows)
    return import_reviews(queue, data, "alice", AT)


def _lines(data: Path) -> list[ReviewLine]:
    return read_lines(data / "reviews" / "alice.jsonl")


def test_correct_licence_round_trip(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    row = _row(rows, RING, "license")
    row.update(
        decision="correct", value="Apache-2.0 OR MIT", note="read the COPYING\nfile"
    )
    result = _import(tmp_path, data, rows)
    assert (result.added, result.unchanged) == (1, 0)  # type: ignore[attr-defined]
    (line,) = _lines(data)
    assert (line.id, line.subject, line.element) == (RING, "root", "license")
    assert line.decision == "correct" and line.reviewer == "alice" and line.at == AT
    assert line.value == LicenseValue(spdx_expression="Apache-2.0 OR MIT")
    assert line.note == "read the COPYING\nfile"
    # The auto answer file is untouched and keeps its own status.
    kept = next(e for e in load_entries(data) if e.id == RING)
    assert kept.subjects[0].expected.license.status == "auto_unresolved"
    assert (line.auto_status, line.auto_value_at_review) == ("auto_unresolved", None)
    assert not (data / "reviews" / "alice.auto.json").exists()


def test_decision_semantics(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, CLAP, "provider").update(decision="confirm")  # confirms the absence
    _row(rows, FLASK, "provider").update(decision="exclude", note="not a package")
    _row(rows, RING, "license").update(decision="correct", value="LicenseRef-x-1")
    _import(tmp_path, data, rows)
    by = {(x.id, x.element): x for x in _lines(data)}
    assert by[(CLAP, "provider")].decision == "confirm"
    assert by[(CLAP, "provider")].value is None
    assert by[(FLASK, "provider")].decision == "exclude"
    assert by[(FLASK, "provider")].value is None
    corrected = by[(RING, "license")].value
    assert isinstance(corrected, LicenseValue) and corrected.has_license_ref


def test_confirm_on_unanimous_has_no_value(tmp_path: Path) -> None:
    data = make_data(tmp_path)
    rows = read_rows(
        csv_text(
            queue_rows(
                load_entries(data),
                [],
                audit=[("pkg:pypi/requests@2.32.3", "root", "license")],
            )
        )
    )
    rows[0].update(decision="confirm")
    _import(tmp_path, data, rows)
    assert _lines(data)[0].value is None


@pytest.mark.parametrize(
    ("purl", "element"),
    [(FLASK, "license"), (RING, "license")],
    ids=["conflict", "unresolved"],
)
def test_confirm_is_rejected_without_a_value_to_confirm(
    tmp_path: Path, purl: str, element: str
) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, purl, element).update(decision="confirm")
    with pytest.raises(ReviewError, match="confirm is not allowed"):
        _import(tmp_path, data, rows)
    assert not (data / "reviews").exists()


def test_confirm_is_rejected_for_not_assessed(tmp_path: Path) -> None:
    data = make_data(tmp_path)
    entry = next(e for e in load_entries(data) if e.id == RING)
    write_entry(
        with_expected(entry, "name", status="not_assessed", value=None, basis=[]),
        data / "entries",
    )
    rows = read_rows(csv_text(queue_rows(load_entries(data), ["not_assessed"])))
    assert [r["element"] for r in rows] == ["name"]
    rows[0].update(decision="confirm")
    with pytest.raises(ReviewError, match="confirm is not allowed for not_assessed"):
        _import(tmp_path, data, rows)


@pytest.mark.parametrize(
    "value",
    ["Apache 2.0", "", "Foo-1.0 OR MIT", "MIT OR (", "MIT AND LicenseRef-x OR"],
    ids=["space", "empty", "unknown-key", "unbalanced", "dangling"],
)
def test_bad_licence_values_are_rejected(tmp_path: Path, value: str) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, RING, "license").update(decision="correct", value=value)
    with pytest.raises(ReviewError):
        _import(tmp_path, data, rows)
    assert not (data / "reviews").exists()


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("MIT", True),
        ("mit OR Apache-2.0", True),
        ("LicenseRef-acme-1.0 AND MIT", True),
        ("Apache 2.0", False),
        ("licenseref-x", False),
        ("LicenseRef-a OR Foo", False),
    ],
)
def test_check_license(text: str, ok: bool) -> None:
    assert (check_license(text) is None) is ok


def test_provider_correction_splits_names_and_keeps_roles(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    name = 'Zoë "Z", Inc.'
    _row(rows, CLAP, "provider").update(
        decision="correct", value=f"Ana B | {name}", has_unnamed_group="y"
    )
    _import(tmp_path, data, rows)
    value = _lines(data)[0].value
    assert isinstance(value, ProviderValue)
    assert [e.name for e in value.entities] == ["Ana B", name]
    assert value.has_unnamed_group


@pytest.mark.parametrize(
    ("value", "flag"),
    [("A | a", "n"), ("A | ", "n"), ("A |", "n"), ("", "n"), ("A", "maybe")],
    ids=["casefold-duplicate", "empty-name", "bar-in-name", "nothing", "bad-flag"],
)
def test_bad_provider_values_are_rejected(
    tmp_path: Path, value: str, flag: str
) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, CLAP, "provider").update(
        decision="correct", value=value, has_unnamed_group=flag
    )
    with pytest.raises(ReviewError):
        _import(tmp_path, data, rows)


def test_unnamed_group_only_provider_is_valid(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, CLAP, "provider").update(decision="correct", has_unnamed_group="y")
    _import(tmp_path, data, rows)
    value = _lines(data)[0].value
    assert isinstance(value, ProviderValue)
    assert value.entities == [] and value.has_unnamed_group


def test_name_correction_gets_the_canonical_key(tmp_path: Path) -> None:
    data = make_data(tmp_path)
    rows = read_rows(
        csv_text(
            queue_rows(
                load_entries(data),
                [],
                audit=[("pkg:pypi/requests@2.32.3", "root", "name")],
            )
        )
    )
    rows[0].update(decision="correct", value="Re_Quests")
    _import(tmp_path, data, rows)
    value = _lines(data)[0].value
    assert (value.name, value.canonical_key, value.accepted) == (  # type: ignore[union-attr]
        "Re_Quests",
        "re-quests",
        ["Re_Quests"],
    )


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"id": "pkg:cargo/nope@1.0.0"}, "unknown id"),
        ({"id": "pkg:cargo/ri\nng@0.16.20"}, "unknown id"),
        ({"id": "not a purl"}, "."),
        ({"subject": "model:9"}, "unknown subject"),
        ({"element": "version"}, "unknown element"),
        ({"decision": "maybe"}, "unknown decision"),
        ({"auto_status": "auto_unanimous"}, "export again"),
        ({"decision": "exclude", "value": "MIT"}, "takes no value"),
        (
            {"decision": "correct", "value": "MIT", "note": "mail me@corp.test"},
            "e-mail",
        ),
        (
            {"decision": "correct", "value": "MIT", "has_unnamed_group": "y"},
            "provider only",
        ),
    ],
    ids=[
        "id",
        "id-newline",
        "id-garbage",
        "subject",
        "element",
        "decision",
        "stale-queue",
        "exclude-value",
        "email",
        "flag-on-licence",
    ],
)
def test_bad_rows_are_rejected_on_one_line(
    tmp_path: Path, change: dict[str, str], text: str
) -> None:
    data, rows = _queue(tmp_path)
    row = _row(rows, RING, "license")
    row.update(decision="correct", value="MIT")
    row.update(change)
    with pytest.raises(ReviewError, match=text) as info:
        _import(tmp_path, data, rows)
    assert all("\n" not in e for e in info.value.errors)
    assert not (data / "reviews").exists()


def test_one_bad_row_writes_nothing_and_all_errors_are_listed(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, RING, "license").update(decision="correct", value="MIT")
    _row(rows, FLASK, "license").update(decision="confirm")
    _row(rows, CLAP, "provider").update(decision="correct", value="")
    with pytest.raises(ReviewError) as info:
        _import(tmp_path, data, rows)
    assert len(info.value.errors) == 2
    assert not (data / "reviews").exists()


def test_duplicate_decisions_and_wrong_shape_are_rejected(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    twice = [*rows, dict(_row(rows, RING, "license"))]
    for r in twice:
        if r["id"] == RING:
            r.update(decision="exclude")
    with pytest.raises(ReviewError, match="decided twice"):
        _import(tmp_path, data, twice)
    bad_header = tmp_path / "bad.csv"
    bad_header.write_bytes(b"id,decision\nx,confirm\n")
    with pytest.raises(ReviewError, match="header"):
        import_reviews(bad_header, data, "alice", AT)
    short = tmp_path / "short.csv"
    short.write_bytes(write_rows(tmp_path / "ok.csv", rows).read_bytes() + b"a,b\n")
    with pytest.raises(ReviewError, match="columns"):
        import_reviews(short, data, "alice", AT)


def test_bad_reviewer_name_is_rejected(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, RING, "license").update(decision="exclude")
    queue = write_rows(tmp_path / "in.csv", rows)
    with pytest.raises(ValueError, match="reviewer"):
        import_reviews(queue, data, "Alice B", AT)
    assert not (data / "reviews").exists()


def test_import_is_idempotent_and_latest_decision_appends(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, RING, "license").update(decision="correct", value="MIT")
    first = _import(tmp_path, data, rows)
    path = data / "reviews" / "alice.jsonl"
    before = path.read_bytes()
    queue = write_rows(tmp_path / "in.csv", rows)
    again = import_reviews(queue, data, "alice", "2027-01-01T00:00:00Z")  # a later time
    assert (first.added, again.added, again.unchanged) == (1, 0, 1)  # type: ignore[attr-defined]
    assert path.read_bytes() == before
    _row(rows, RING, "license").update(value="Apache-2.0")
    changed = _import(tmp_path, data, rows)
    assert changed.added == 1  # type: ignore[attr-defined]
    assert [x.value.spdx_expression for x in _lines(data)] == ["MIT", "Apache-2.0"]  # type: ignore[union-attr]
    # Going back to the first decision is a new decision, not a no-op.
    _row(rows, RING, "license").update(value="MIT")
    assert _import(tmp_path, data, rows).added == 1  # type: ignore[attr-defined]


def test_rows_without_decision_are_skipped(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    result = _import(tmp_path, data, rows)
    assert (result.added, result.skipped) == (0, len(rows))  # type: ignore[attr-defined]
    assert not (data / "reviews").exists()


def test_review_file_bytes_are_canonical(tmp_path: Path) -> None:
    data, rows = _queue(tmp_path)
    _row(rows, CLAP, "provider").update(decision="correct", value="Zoë | Ana")
    _row(rows, RING, "license").update(decision="exclude")
    _import(tmp_path, data, rows)
    raw = (data / "reviews" / "alice.jsonl").read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw and raw.count(b"\n") == 2
    text = raw.decode("utf-8")
    assert "Zoë" in text  # not escaped
    assert text.index(CLAP) < text.index(RING)  # sorted by id
    assert text.split("\n")[0].startswith('{"at":')  # sorted keys


def test_cli_exit_codes_and_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, rows = _queue(tmp_path)
    parser = argparse.ArgumentParser()
    review.add_arguments(parser.add_subparsers(dest="command"))
    _row(rows, RING, "license").update(decision="correct", value="MIT")
    queue = write_rows(tmp_path / "in.csv", rows)
    argv = ["review-import", str(queue), "--reviewer", "alice", "--at", AT]
    argv += ["--data-dir", str(data)]
    assert review.run_cli(parser.parse_args(argv)) == 0
    out = capsys.readouterr()
    assert out.out == f"ADDED=1 UNCHANGED=0 SKIPPED={len(rows) - 1}\n"
    assert out.err.startswith("INFO: review-import: 1 added")
    _row(rows, RING, "license").update(value="Apache 2.0")
    write_rows(queue, rows)
    assert review.run_cli(parser.parse_args(argv)) == 1
    err = capsys.readouterr()
    assert err.out == "" and err.err.startswith("ERROR: review-import: ")
    assert err.err.count("\n") == 1
