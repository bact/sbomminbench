# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.jsonio."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from sbomminbench.ids import entry_relpath
from sbomminbench.jsonio import (
    LeakError,
    dumps,
    iter_entries,
    read_entry,
    write_entry,
    write_if_changed,
)
from sbomminbench.schema import Entry

EXAMPLES = Path(__file__).parent / "fixtures" / "examples"


def example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_bytes())
    return data


def test_dumps_is_canonical() -> None:
    text = dumps({"b": "é", "a": [1, {"d": None, "c": True}]})
    assert text == (
        '{\n  "a": [\n    1,\n    {\n      "c": true,\n      "d": null\n    }\n  ],\n'
        '  "b": "é"\n}\n'
    )
    assert "\r" not in text
    assert "\\u" not in text


@pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_dumps_rejects_non_finite(bad: float) -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        dumps({"x": bad})


def test_write_if_changed_skips_identical_content(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "f.json"
    assert write_if_changed(path, "one\n") is True
    assert write_if_changed(path, "one\n") is False
    assert write_if_changed(path, "two\n") is True
    assert path.read_bytes() == b"two\n"


def test_write_entry_round_trip(tmp_path: Path) -> None:
    entry = Entry.model_validate(example("magika"))
    path = write_entry(entry, tmp_path)
    assert path == tmp_path / entry_relpath(entry.id)
    raw = path.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw
    assert read_entry(path) == entry
    # A second write of the same entry changes nothing on disk.
    mtime = path.stat().st_mtime_ns
    write_entry(entry, tmp_path)
    assert path.stat().st_mtime_ns == mtime


def test_write_entry_refuses_address(tmp_path: Path) -> None:
    data = copy.deepcopy(example("magika"))
    data["platform"]["name"] = "maintainer x@mail.com"
    entry = Entry.model_validate(data)
    with pytest.raises(LeakError):
        write_entry(entry, tmp_path)
    assert not any(tmp_path.rglob("*.json"))


def test_write_entry_accepts_placeholder(tmp_path: Path) -> None:
    data = copy.deepcopy(example("magika"))
    data["platform"]["name"] = "maintainer email.redacted@example.com"
    write_entry(Entry.model_validate(data), tmp_path)


def test_read_entry_accepts_bom(tmp_path: Path) -> None:
    entry = Entry.model_validate(example("qwen"))
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + dumps(entry.model_dump(mode="json")).encode())
    assert read_entry(path) == entry


def test_iter_entries_sorted_by_path(tmp_path: Path) -> None:
    magika = Entry.model_validate(example("magika"))
    qwen = Entry.model_validate(example("qwen"))
    write_entry(qwen, tmp_path)
    write_entry(magika, tmp_path)
    (tmp_path / "notes.txt").write_text("skip\n", encoding="utf-8")
    listed = list(iter_entries(tmp_path))
    paths = [p.relative_to(tmp_path).as_posix() for p, _ in listed]
    assert paths == sorted(paths)
    assert {e.id for _, e in listed} == {magika.id, qwen.id}


@pytest.mark.parametrize(
    "purl",
    [
        "pkg:maven/org.jboss.windup.exec/windup-exec@6.3.9.Final",
        "pkg:maven/a/b@5.3.0.RELEASE",
        "pkg:nuget/c@1.0.0.rc",
        "pkg:maven/a/b@2.0.0.GA",
    ],
    ids=["final", "release", "rc", "ga"],
)
def test_write_entry_accepts_purl_versions(tmp_path: Path, purl: str) -> None:
    data = copy.deepcopy(example("magika"))
    data["platform"]["name"] = f"built with {purl}"
    path = write_entry(Entry.model_validate(data), tmp_path)
    assert purl in path.read_text(encoding="utf-8")
