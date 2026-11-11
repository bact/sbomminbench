# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.validate_files: V08 (e-mail scan) and V16 (hygiene)."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from validate_support import PACKAGING, edit, hits, make_tree, run

from sbomminbench import validate_files
from sbomminbench.jsonio import dumps
from sbomminbench.schema import EMAIL_PLACEHOLDER

ADDRESS = "someone@corp-example.org"


def _reversed(value: object) -> object:
    if isinstance(value, dict):
        return {k: _reversed(v) for k, v in reversed(value.items())}
    if isinstance(value, list):
        return [_reversed(v) for v in value]
    return value


def _put(data: Path, rel: str, content: str) -> None:
    path = data / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def test_v08_real_address_in_an_entry(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    assert not hits(run(data), "V08")

    def change(doc: dict[str, object]) -> None:
        obs: list[dict[str, object]] = doc["platform"]["observations"]  # type: ignore[index]
        obs[0]["raw"] = f"Ann <{ADDRESS}>"

    edit(data, PACKAGING, change)
    (found,) = hits(run(data), "V08")
    assert found.target == "pkg:pypi/packaging@26.3"
    assert ADDRESS not in found.detail  # the address is never printed
    assert "$.platform.observations[0].raw" in found.detail


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("reviews/ann.jsonl", json.dumps({"note": f"mail {ADDRESS}"}) + "\n"),
        ("reviews/ann.jsonl", f"not json {ADDRESS}\n"),
        (
            "seeds/pypi.tsv",
            f"name\tversion\tstratum\trank\tnote\nx\t1\ttop\t1\t{ADDRESS}\n",
        ),
        ("release/subjects.csv", f'id,note\n"a","line\n{ADDRESS}"\n'),
        ("release/stats.json", dumps({ADDRESS: 1})),
    ],
    ids=["jsonl", "bad-jsonl-line", "tsv", "csv-multiline", "json-key"],
)
def test_v08_scans_every_file_under_data(
    tmp_path: Path, rel: str, content: str
) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _put(data, rel, content)
    found = hits(run(data), "V08")
    assert found and all(ADDRESS not in f.detail for f in found)
    assert found[0].target == rel


def test_v08_allows_the_placeholder_and_purls(tmp_path: Path) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _put(data, "release/note.json", dumps({"a": f"Ann <{EMAIL_PLACEHOLDER}>"}))
    _put(data, "release/purls.csv", "id\npkg:npm/foo@1.0.0\n")
    _put(data, "release/at.json", dumps({"id": "pkg:npm/%40scope/name@1.0.0"}))
    assert not hits(run(data), "V08")


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("pkg:npm/x@someone@corp-example.org", False),
        (" pkg:maven/org.jboss.windup.exec/windup-exec@6.3.9.Final", False),
        ("see pkg:maven/a/b@5.3.0.RELEASE, pkg:nuget/c@1.0.0.rc", False),
        (f"pkg:npm/x@1.0.0 mail me at {ADDRESS}", True),
        (f"{ADDRESS} pkg:npm/x@1.0.0", True),
        (f"pkg:npm/x?repository_url=git+ssh://{ADDRESS}/o/r", True),
    ],
    ids=["start", "indented-final", "inline", "after", "before", "qualifier"],
)
def test_v08_masks_purl_tokens_only(tmp_path: Path, text: str, flagged: bool) -> None:
    data = make_tree(tmp_path, (PACKAGING,))
    _put(data, "release/purl.json", dumps({"a": text}))
    assert bool(hits(run(data), "V08")) is flagged


# -- V16 -----------------------------------------------------------------------------


def _canonical(tmp_path: Path) -> tuple[Path, Path]:
    data = make_tree(tmp_path, (PACKAGING,))
    return data, data / "entries" / PACKAGING


def test_v16_clean_tree_has_no_hygiene_finding(tmp_path: Path) -> None:
    data, _ = _canonical(tmp_path)
    assert not hits(run(data), "V16")


@pytest.mark.parametrize(
    ("transform", "word"),
    [
        (lambda b: b.replace(b"\n", b"\r\n"), "CR"),
        (lambda b: b.rstrip(b"\n"), "newline"),
        (lambda b: b"\xef\xbb\xbf" + b, "byte order mark"),
        (
            lambda b: (
                json.dumps(json.loads(b), indent=4, sort_keys=True).encode() + b"\n"
            ),
            "canonical",
        ),
        (
            lambda b: json.dumps(_reversed(json.loads(b)), indent=2).encode() + b"\n",
            "not sorted",
        ),
    ],
    ids=["crlf", "no-final-lf", "bom", "indent", "unsorted"],
)
def test_v16_flags_each_hygiene_rule(
    tmp_path: Path, transform: Callable[[bytes], bytes], word: str
) -> None:
    data, path = _canonical(tmp_path)
    before = path.read_bytes()
    path.write_bytes(transform(before))
    assert path.read_bytes() != before
    found = hits(run(data), "V16")
    assert found and any(word in f.detail for f in found)


def test_v16_unsorted_nested_keys_in_jsonl(tmp_path: Path) -> None:
    data, _ = _canonical(tmp_path)
    _put(data, "release/entries.jsonl", '{"a":{"z":1,"b":2}}\n')
    assert any("not sorted" in f.detail for f in hits(run(data), "V16"))
    _put(data, "release/entries.jsonl", '{"a":{"b":2,"z":1}}\n')
    assert not [f for f in hits(run(data), "V16") if "entries.jsonl" in f.detail]


def test_v16_entry_file_over_the_size_limit(tmp_path: Path) -> None:
    data, path = _canonical(tmp_path)
    edit(data, PACKAGING, lambda d: subject_note(d, "x" * 210_000))
    assert path.stat().st_size > validate_files.MAX_FILE_BYTES
    assert any("over" in f.detail for f in hits(run(data), "V16"))


def subject_note(doc: dict[str, object], text: str) -> None:
    subjects: list[dict[str, dict[str, dict[str, object]]]] = doc["subjects"]  # type: ignore[assignment]
    subjects[0]["expected"]["name"]["note"] = text


def test_v16_whole_tree_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, _ = _canonical(tmp_path)
    monkeypatch.setattr(validate_files, "MAX_TREE_BYTES", 10)
    found = [f for f in hits(run(data), "V16") if f.target == "-"]
    assert len(found) == 1 and "over 10" in found[0].detail


def test_v16_large_release_file_is_allowed(tmp_path: Path) -> None:
    data, _ = _canonical(tmp_path)
    _put(data, "release/entries.jsonl", ('{"id":"' + "x" * 300_000 + '"}\n'))
    assert not [f for f in hits(run(data), "V16") if "over" in f.detail]
