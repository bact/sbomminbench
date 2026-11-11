# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for sbomminbench.evaluate.matching and the independence rule."""

import subprocess
import sys
from typing import Any

import pytest
from helpers_evaluate import MAGIKA, load

from sbomminbench.evaluate.matching import (
    license_equivalent,
    name_match,
    normalise_provider,
    provider_counts,
    provider_prf,
)
from sbomminbench.evaluate.predictions import respell_equivalent
from sbomminbench.schema import Entry, NameValue, ProviderValue

# -- licence ---------------------------------------------------------------------------

LICENSE_CASES = [
    ("MIT", "MIT", "exact"),
    (" MIT ", "MIT", "exact"),
    ("MIT OR Apache-2.0", "Apache-2.0 OR MIT", "equivalent"),
    ("mit", "MIT", "equivalent"),
    ("(MIT)", "MIT", "equivalent"),
    ("MIT AND (Apache-2.0)", "Apache-2.0 AND MIT", "equivalent"),
    ("GPL-2.0+", "GPL-2.0-or-later", "equivalent"),
    ("GPL-2.0", "GPL-2.0-only", "equivalent"),
    ("GPL-2.0+", "GPL-2.0-only", "different"),
    (
        "GPL-2.0-only WITH Classpath-exception-2.0",
        "GPL-2.0 WITH classpath-exception-2.0",
        "equivalent",
    ),
    ("GPL-2.0-only", "GPL-2.0-only WITH Classpath-exception-2.0", "different"),
    ("MIT", "Apache-2.0", "different"),
    ("MIT AND Apache-2.0", "MIT OR Apache-2.0", "different"),
    ("Apache 2.0", "Apache-2.0", "invalid"),
    ("MIT AND", "MIT", "invalid"),
    ("(MIT", "MIT", "invalid"),
    ("qwen", "Apache-2.0", "invalid"),
    ("LicenseRef-foo", "LicenseRef-foo", "exact"),
    ("MIT OR LicenseRef-foo", "LicenseRef-foo OR MIT", "equivalent"),
    ("LicenseRef-foo", "LicenseRef-bar", "different"),
    ("OpenMDW-1.0", "OpenMDW-1.0", "exact"),
    ("OpenMDW-1.0", "MIT", "invalid"),
    (None, "MIT", "missing"),
    ("", "MIT", "missing"),
    (" noassertion ", "MIT", "missing"),
    ("NONE", "MIT", "missing"),
]


@pytest.mark.parametrize(
    ("pred", "gold", "want"),
    LICENSE_CASES,
    ids=[f"{i}-{c[2]}" for i, c in enumerate(LICENSE_CASES)],
)
def test_license_equivalent(pred: str | None, gold: str, want: str) -> None:
    assert license_equivalent(pred, gold) == want


def test_unknown_key_used_by_gold_is_not_invalid() -> None:
    # A key the installed SPDX list lacks but the gold also uses is the list's gap.
    assert license_equivalent("OpenMDW-1.0 OR MIT", "MIT OR OpenMDW-1.0") != "invalid"
    assert license_equivalent("OpenMDW-1.0 OR Apache-2.0", "MIT") == "invalid"


# -- name ------------------------------------------------------------------------------

PYPI = NameValue(
    name="charset-normalizer",
    canonical_key="charset-normalizer",
    accepted=["charset-normalizer"],
)
HF = NameValue(
    name="Qwen2.5-72B-Instruct",
    canonical_key="qwen/qwen2.5-72b-instruct",
    accepted=["Qwen2.5-72B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
)
NPM = NameValue(name="Left-Pad", canonical_key="Left-Pad", accepted=["Left-Pad"])


def named(name: str, *accepted: str) -> NameValue:
    return NameValue(name=name, canonical_key=name, accepted=[name, *accepted])


# Strict follows the ecosystem's own identity rule; tolerant ignores separators too
# and can credit a different package (the reason strict is the headline).
IDENTITY_CASES = [
    ("npm", "my.pkg", named("my-pkg"), (False, True)),
    ("npm", "MY-PKG", named("my-pkg"), (False, True)),
    ("maven", "org-foo:bar", named("org.foo:bar"), (False, True)),
    ("nuget", "newtonsoft.json", named("Newtonsoft.Json"), (True, True)),
    ("nuget", "Newtonsoft-Json", named("Newtonsoft.Json"), (False, True)),
    ("cargo", "serde_json", named("serde-json"), (True, True)),
    ("cargo", "serde.json", named("serde-json"), (False, True)),
    ("pypi", "Foo.Bar", named("foo-bar"), (True, True)),
    ("pypi", "  requests\n", named("requests"), (True, True)),
    ("pypi", "   ", named("requests"), (False, False)),
    ("huggingface", "BGE-M3", named("bge-m3"), (True, True)),
    ("huggingface", "BAAI/BGE-M3", named("baai/bge-m3", "bge-m3"), (True, True)),
]


@pytest.mark.parametrize(
    ("eco", "pred", "gold", "want"),
    IDENTITY_CASES,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(IDENTITY_CASES)],
)
def test_name_identity_rules(
    eco: Any, pred: str, gold: NameValue, want: tuple[bool, bool]
) -> None:
    assert name_match(eco, pred, gold) == want


@pytest.mark.parametrize(
    ("eco", "pred", "gold", "want"),
    [
        ("pypi", "charset-normalizer", PYPI, (True, True)),
        ("pypi", "Charset_Normalizer", PYPI, (True, True)),
        ("pypi", "charset normalizer", PYPI, (False, True)),
        ("pypi", "requests", PYPI, (False, False)),
        ("huggingface", "Qwen2.5-72B-Instruct", HF, (True, True)),
        ("huggingface", "QWEN/qwen2.5-72b-instruct", HF, (True, True)),
        ("huggingface", "qwen2.5-72b-instruct", HF, (True, True)),
        ("huggingface", " QWEN2.5-72B-INSTRUCT ", HF, (True, True)),
        ("huggingface", "Qwen/Qwen2.5-72B Instruct", HF, (False, True)),
        ("huggingface", "Qwen2.5-7B-Instruct", HF, (False, False)),
        ("npm", "Left-Pad", NPM, (True, True)),
        ("npm", "left-pad", NPM, (False, True)),
        ("npm", None, NPM, (False, False)),
    ],
    ids=lambda v: str(v)[:20],
)
def test_name_match(
    eco: Any, pred: str | None, gold: NameValue, want: tuple[bool, bool]
) -> None:
    assert name_match(eco, pred, gold) == want


# -- provider --------------------------------------------------------------------------


def gold_provider(*names: str) -> ProviderValue:
    return ProviderValue.model_validate({"entities": [{"name": n} for n in names]})


@pytest.mark.parametrize(
    ("pred", "gold", "lenient", "want"),
    [
        (["Alice J"], ("Alice J",), False, (1, 0, 0)),
        (["Alice J <email.redacted@example.com>"], ("Alice J",), False, (1, 0, 0)),
        (["alice   j (Org)"], ("Alice J",), False, (0, 1, 1)),
        (["Foo (Bar Inc.)"], ("Foo (Baz Corp)",), False, (0, 1, 1)),
        (["(Foo)"], ("(Foo)",), False, (1, 0, 0)),
        (["Alice J (https://example.org/a)"], ("Alice J",), False, (1, 0, 0)),
        (["Alice J <https://example.org>"], ("Alice J",), False, (1, 0, 0)),
        (["Alice J (a@b.org) <c@d.org>"], ("Alice J",), False, (1, 0, 0)),
        (["Bob K", "Alice J"], ("Alice J", "Bob K"), False, (2, 0, 0)),
        (["Alice J", "Carol"], ("Alice J",), False, (1, 1, 0)),
        (["Alice J"], ("Alice J", "Bob K"), False, (1, 0, 1)),
        (["Acme, Inc."], ("Acme",), False, (0, 1, 1)),
        (["Acme, Inc."], ("Acme",), True, (1, 0, 0)),
        (["Alice J", "alice j"], ("Alice J",), False, (1, 0, 0)),
        (["email.redacted@example.com", "<x@y.z>"], ("Alice J",), False, (0, 0, 1)),
        (None, ("Alice J",), False, (0, 0, 1)),
        ([], ("Alice J",), True, (0, 0, 1)),
    ],
    ids=lambda v: str(v)[:25],
)
def test_provider_prf(
    pred: list[str] | None,
    gold: tuple[str, ...],
    lenient: bool,
    want: tuple[int, int, int],
) -> None:
    assert provider_prf(pred, gold_provider(*gold), lenient=lenient) == want


def group_provider(*names: str) -> ProviderValue:
    return ProviderValue.model_validate(
        {"entities": [{"name": n} for n in names], "has_unnamed_group": True}
    )


@pytest.mark.parametrize(
    ("pred", "want", "unmatched"),
    [
        (["Alice J", "Zed"], (1, 0, 0), 1),  # unknown under an open list: not a FP
        (["Alice J et al."], (1, 0, 0), 0),
        (["Alice J, et al"], (1, 0, 0), 0),
        (["Alice J and others"], (1, 0, 0), 0),
        (["Alice J", "et al."], (1, 0, 0), 0),
        (["Zed"], (0, 0, 1), 1),
        ([], (0, 0, 1), 0),
    ],
    ids=[
        "unknown",
        "et-al",
        "comma-et-al",
        "and-others",
        "bare-tail",
        "wrong",
        "empty",
    ],
)
def test_unnamed_group_spares_unmatched_predictions(
    pred: list[str], want: tuple[int, int, int], unmatched: int
) -> None:
    gold = group_provider("Alice J")
    assert provider_prf(pred, gold, lenient=False) == want
    assert provider_counts(pred, gold, lenient=False) == (want, unmatched)
    # without the flag the same names are false positives
    closed = gold_provider("Alice J")
    assert provider_prf(["Alice J", "Zed"], closed, lenient=False) == (1, 1, 0)
    assert provider_prf(["Alice J et al."], closed, lenient=False) == (0, 1, 1)


def test_provider_norm_nfkc_and_nested_suffix() -> None:
    full = "\uff21lice  J"  # fullwidth A
    assert normalise_provider(f"{full} (a@x.io) <b@y.io>", lenient=False) == "alice j"
    # bracketed text that is not an e-mail or URL is part of the name
    assert normalise_provider(f"{full} (a) <b>", lenient=False) == "alice j (a) <b>"
    assert (
        normalise_provider("Co. Ltd.", lenient=True) == "co. ltd."
    )  # nothing left: keep strict


# -- independence --


@pytest.mark.parametrize(
    "module",
    [
        "evaluate.cli",
        "evaluate.metrics",
        "evaluate.matching",
        "evaluate.predictions",
        "evaluate.scoring",
        "evaluate.intervals",
        "evaluate.caveats",
        "evaluate.render",
        "wilson",
    ],
)
def test_evaluator_does_not_import_licenseid_or_pitloom(module: str) -> None:
    code = (
        "import sys\n"
        "for n in ('licenseid', 'pitloom', 'sbomminbench.derive'):\n"
        "    sys.modules[n] = None\n"  # a None entry makes any import raise
        f"import sbomminbench.{module}\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('licenseid', 'pitloom')"
        " and sys.modules[m] is not None]\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, check=False
    )
    assert done.returncode == 0, done.stderr.decode()


def test_respelling_keeps_a_text_whose_case_fold_upper_casing_would_change() -> None:
    """Regression: "Yazıcı" upper-cased is "YAZICI", which folds to "yazici"."""
    entry = Entry.model_validate(load("magika"))
    rec: dict[str, object] = {
        "id": MAGIKA,
        "name": "magika",
        "provider": ["Volkan Yazıcı", "Jane Roe"],
        "license": "MIT",
    }
    out = respell_equivalent([rec], [entry])[0]["provider"]
    assert out == [
        "JANE ROE <email.redacted@example.com>",
        "Volkan Yazıcı <email.redacted@example.com>",
    ]
