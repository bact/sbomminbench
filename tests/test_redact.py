# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for e-mail redaction."""

import pytest

from sbomminbench.redact import (
    PLACEHOLDER,
    JsonValue,
    find_emails,
    redact_emails,
    redact_json,
)

P = PLACEHOLDER


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Alice J, Bob K <x@mail.com>", f"Alice J, Bob K <{P}>"),
        ("mailto:dev@numpy.org", f"mailto:{P}"),
        ("jane [at] example [dot] org", P),
        ("Ann (ann at uni dot edu)", f"Ann ({P})"),
        ("git+ssh://git@github.com/o/r.git", f"git+ssh://{P}/o/r.git"),
        ("https://x.org/?c=a%40b.com&d=1", f"https://x.org/?c={P}&d=1"),
        ("josé@exemple.fr", P),
        ("Zoë <zoë.k@uni.de>", f"Zoë <{P}>"),
        ("a@b.com, c@d.org; e@f.net", f"{P}, {P}; {P}"),
        ("Alice\nbob@x.com\nCarol", f"Alice\n{P}\nCarol"),
        ("A <a@x.io> and A <a@x.io>.", f"A <{P}> and A <{P}>."),
    ],
    ids=[
        "multi-author",
        "mailto",
        "bracket-at",
        "word-at",
        "url-userinfo",
        "percent-at-in-url",
        "unicode-local",
        "unicode-angle",
        "several",
        "newline",
        "repeated",
    ],
)
def test_redacts(text: str, expected: str) -> None:
    assert redact_emails(text) == (expected, True)
    # Idempotent: redacting twice equals once, and the second pass finds nothing.
    assert redact_emails(expected) == (expected, False)


@pytest.mark.parametrize(
    "text",
    [
        "pkg:npm/%40babel/core@7.26.0",
        "pkg:pypi/requests@2.32.3",
        "requests@2.32.3",
        "@babel/core",
        P,
        f"Name <{P}>",
        "Alice J, Bob K",
        "https://github.com/o/r",
        "",
    ],
    ids=[
        "purl-npm",
        "purl-pypi",
        "version",
        "scope",
        "placeholder",
        "placeholder-angle",
        "names",
        "url",
        "empty",
    ],
)
def test_unchanged(text: str) -> None:
    assert redact_emails(text) == (text, False)


def test_purl_skipped_even_if_it_looks_like_mail() -> None:
    text = "pkg:generic/a@b.com"
    assert redact_emails(text) == (text, False)


def test_json_recursive_and_keys_untouched() -> None:
    value: JsonValue = {
        "author": {"name": "TJ", "email": "tj@vision-media.ca"},
        "list": ["ok", "a@b.com", 3, None, True, 1.5],
        "a@b.com": "key stays",
    }
    out, found = redact_json(value)
    assert found
    assert out == {
        "author": {"name": "TJ", "email": P},
        "list": ["ok", P, 3, None, True, 1.5],
        "a@b.com": "key stays",
    }
    assert redact_json(out) == (out, False)


def test_json_raw_otherwise_untouched() -> None:
    raw: JsonValue = {
        "n": " Padded Name \n",
        "t": ["  a  ", ""],
        "x": None,
        "u": "Ünï\u200bcode",
    }
    out, found = redact_json(raw)
    assert not found
    assert out == raw


def test_text_otherwise_untouched() -> None:
    text = "  Alice  <x@y.com>\t \n"
    out, _ = redact_emails(text)
    assert out == f"  Alice  <{P}>\t \n"
    assert out != text


def test_find_emails() -> None:
    assert find_emails(f"a@b.com {P} c%40d.org") == ["a@b.com", "c%40d.org"]
    assert find_emails(P) == []
    assert find_emails("none here") == []
    assert find_emails(redact_emails("x@y.com")[0]) == []


PURLS = [
    "pkg:maven/org.jboss.windup.exec/windup-exec@6.3.9.Final",
    "pkg:maven/org.springframework/spring-core@5.3.0.RELEASE",
    "pkg:nuget/Foo@1.0.0.rc",
    "pkg:maven/a/b@2.0.0.GA",
    "pkg:npm/%40babel/core@7.26.0",
]


@pytest.mark.parametrize("purl", PURLS, ids=lambda p: p.rsplit("@", 1)[1])
def test_purl_is_neither_found_nor_rewritten(purl: str) -> None:
    for text in (purl, f"  {purl}", f'{{"id": "{purl}"}}', f"a {purl}, b ({purl})"):
        assert find_emails(text) == []
        assert redact_emails(text) == (text, False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "pkg:npm/x@1.0.0 mail me at bob@corp.com",
            f"pkg:npm/x@1.0.0 mail me at {P}",
        ),
        ("bob@corp.com,pkg:maven/a/b@1.Final", f"{P},pkg:maven/a/b@1.Final"),
        ("PKG:maven/a/b@1.Final <bob@corp.com>", f"PKG:maven/a/b@1.Final <{P}>"),
        (
            "pkg:npm/x?repository_url=git+ssh://git@github.com/o/r",
            f"pkg:npm/x?repository_url=git+ssh://{P}/o/r",
        ),
    ],
    ids=["after", "before", "uppercase-type", "qualifier"],
)
def test_address_beside_a_purl_is_still_redacted(text: str, expected: str) -> None:
    assert redact_emails(text) == (expected, True)
    assert find_emails(text)
