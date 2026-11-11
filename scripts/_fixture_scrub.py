# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Scrub e-mail addresses from recorded fixtures.

Shared by the ``record_fixtures_*`` scripts. Two spellings, on purpose:

* Recorded HTTP bodies (``http/``) get the stand-in
  ``fixture.scrubbed@example.invalid``, so the redactor still sees an address
  and ``has_email`` is exercised on the fixtures.
* Extracted members (``members/``) get ``email.redacted@example.com``, which
  is what a read value looks like after redaction.

``tests/test_fixture_emails.py`` accepts exactly these two.
"""

import json
from pathlib import Path
from typing import Any

from sbomminbench.eco.base import FetchIndex
from sbomminbench.http import url_key
from sbomminbench.redact import EMAIL_RE, redact_emails

STAND_IN = "fixture.scrubbed@example.invalid"
_GZIP_MAGIC = b"\x1f\x8b"


def _scrub_json(value: Any) -> Any:
    if isinstance(value, str):
        return EMAIL_RE.sub(STAND_IN, value)
    if isinstance(value, list):
        return [_scrub_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_json(v) for k, v in value.items()}
    return value


def scrub_body(cache_dir: Path, url: str) -> None:
    """Replace addresses in one cached HTTP body (JSON, or text such as a POM).

    Raises:
        ValueError: the body is still gzip-compressed (an address could hide).
    """
    k = url_key(url)
    path = cache_dir / "http" / k[:2] / f"{k}.body"
    if not path.is_file():
        return
    data = path.read_bytes()
    if data.startswith(_GZIP_MAGIC):
        raise ValueError(f"{url}: compressed body cannot be scrubbed")
    try:
        doc = json.loads(data)
    except ValueError:
        text = EMAIL_RE.sub(STAND_IN, data.decode("utf-8", "surrogateescape"))
        path.write_bytes(text.encode("utf-8", "surrogateescape"))
        return
    scrubbed = json.dumps(_scrub_json(doc), ensure_ascii=False, separators=(",", ":"))
    path.write_bytes(scrubbed.encode("utf-8"))


def scrub_members(cache_dir: Path, index: FetchIndex) -> None:
    """Replace addresses in the extracted members with the placeholder."""
    if index.members_dir is None:
        return
    for name in index.members.values():
        path = cache_dir / index.members_dir / name
        text = path.read_bytes().decode("utf-8", "surrogateescape")
        path.write_bytes(redact_emails(text)[0].encode("utf-8", "surrogateescape"))


def scrub_recording(cache_dir: Path, index: FetchIndex) -> None:
    """Scrub every body and member that *index* recorded."""
    for url in sorted(set(index.responses.values())):
        scrub_body(cache_dir, url)
    scrub_members(cache_dir, index)
