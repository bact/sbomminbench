# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Recorded fixtures hold no real e-mail address."""

import json
from collections.abc import Iterator
from pathlib import Path

from sbomminbench.redact import PLACEHOLDER, find_emails

CACHE = Path(__file__).parent / "fixtures" / "cache"
STAND_IN = "fixture.scrubbed@example.invalid"
ALLOWED = {PLACEHOLDER, STAND_IN}


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)


def _texts(path: Path) -> Iterator[str]:
    """JSON files are scanned as decoded strings (`\\n@app.route` is no address)."""
    data = path.read_bytes()
    try:
        yield from _strings(json.loads(data))
    except ValueError:
        yield data.decode("utf-8", "replace")


def test_no_real_address_in_any_fixture() -> None:
    files = sorted(p for p in CACHE.rglob("*") if p.is_file())
    assert files
    bad = {
        f"{p.relative_to(CACHE).as_posix()}: {m}"
        for p in files
        for text in _texts(p)
        if not text.startswith("pkg:")  # a purl's @ separates the version
        for m in find_emails(text)
        if m not in ALLOWED
    }
    assert not bad
    # A body that has a stand-in must be a recorded HTTP body, not a member or index.
    for p in files:
        if STAND_IN in p.read_bytes().decode("utf-8", "replace"):
            assert p.relative_to(CACHE).parts[0] == "http"


def test_guard_catches_a_real_address(tmp_path: Path) -> None:
    f = tmp_path / "x.body"
    f.write_text('{"a": "Bo <bo@real.example.org>", "b": "\\n@app.route"}')
    found = [m for t in _texts(f) for m in find_emails(t)]
    assert found == ["bo@real.example.org"]
