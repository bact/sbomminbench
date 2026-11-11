# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``REPRODUCE.md`` and ``scripts/reproduce.sh``.

The doc and the script may only use subcommands and flags the real parser has.
The script is replayed offline against the committed fixtures: replay skips
``uv sync`` and ``fetch``, so no socket is opened (a subprocess would bypass
``--disable-socket`` anyway).
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from sbomminbench import __main__ as cli

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
DOC = ROOT / "REPRODUCE.md"
SCRIPT = ROOT / "scripts" / "reproduce.sh"
FENCE = re.compile(r"^```(?:bash|sh|shell)\n(.*?)^```", re.DOTALL | re.MULTILINE)
KV = re.compile(r"^[A-Z0-9_]+=\S")
TAGGED = re.compile(r"^(ERROR|WARNING|INFO): \S")

can_run = sys.platform != "win32" and bool(shutil.which("sh") and shutil.which("uv"))


def subparsers() -> dict[str, argparse.ArgumentParser]:
    """Return ``{subcommand: parser}`` (argparse has no public accessor)."""
    parser = cli._build_parser()  # pylint: disable=protected-access
    for action in parser._actions:  # pylint: disable=protected-access
        if isinstance(action, argparse._SubParsersAction):  # pylint: disable=W0212
            return dict(action.choices)
    raise AssertionError("no subparsers")


def flags_of(parser: argparse.ArgumentParser) -> set[str]:
    return set(parser._option_string_actions)  # pylint: disable=protected-access


def doc_commands(text: str) -> list[list[str]]:
    """Tokens of every ``sbomminbench`` command line in the fenced blocks."""
    out = []
    for block in FENCE.findall(text):
        for line in block.splitlines():
            tokens = shlex.split(line, comments=True)
            if "sbomminbench" in tokens:
                out.append(tokens[tokens.index("sbomminbench") + 1 :])
    return out


def check_commands(commands: list[list[str]]) -> list[str]:
    """Return one problem per unknown subcommand or flag."""
    known = subparsers()
    problems = []
    for tokens in commands:
        sub = tokens[0] if tokens else ""
        if sub not in known:
            problems.append(f"unknown subcommand {sub!r}")
            continue
        for flag in (t.split("=")[0] for t in tokens[1:] if t.startswith("-")):
            if flag not in flags_of(known[sub]):
                problems.append(f"{sub}: unknown flag {flag}")
    return problems


def script_commands() -> list[list[str]]:
    """Subcommands the script runs through ``run``, each with its shared flags."""
    text = SCRIPT.read_text(encoding="utf-8")
    shared = re.search(r'sbomminbench "\$@"(.*)$', text, re.MULTILINE)
    assert shared is not None
    flags = shlex.split(shared.group(1).replace("; }", ""))
    subs = re.findall(r"^\s*(?:step \S+ )?run ([a-z-]+)$", text, re.MULTILINE)
    return [[s, *flags] for s in subs]


# -- doc and script against the parser --------------------------------------------------


def test_doc_commands_exist() -> None:
    commands = doc_commands(DOC.read_text(encoding="utf-8"))
    used = {c[0] for c in commands}
    assert {"fetch", "build", "validate", "export", "rederive-diff", "select"} <= used
    assert not check_commands(commands)


def test_script_commands_exist() -> None:
    commands = script_commands()
    assert {c[0] for c in commands} == {"fetch", "build", "validate", "export"}
    assert not check_commands(commands)


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        ("sbomminbench build --data-dir", "sbomminbench build --datadir", "--datadir"),
        ("sbomminbench export ", "sbomminbench release-check ", "release-check"),
        ("fetch --eco pypi", "fetch --seeds data/seeds", "--seeds"),
    ],
    ids=["bad-flag", "bad-subcommand", "stale-seeds-flag"],
)
def test_checker_catches_a_broken_doc(old: str, new: str, problem: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert old in text
    broken = text.replace(old, new, 1)
    assert broken != text
    problems = check_commands(doc_commands(broken))
    assert any(problem in p for p in problems)


def test_doc_names_the_pinned_versions() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = [d for d in project["project"]["dependencies"] if "==" in d]
    assert {re.split(r"[\[=]", p)[0] for p in pins} >= {
        "pitloom",
        "licenseid",
        "packaging",
    }
    text = DOC.read_text(encoding="utf-8")
    for pin in pins:
        assert pin in text
    assert "3.12" in text


def test_doc_style() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "SBOMMINBENCH" not in text.replace("SBOMMINBENCH_", "")
    assert not re.search(r"SBOM-?MinBench|Sbomminbench", text)
    assert "HF_TOKEN" not in text  # the code never reads it


# -- the script -------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck missing")
def test_shellcheck() -> None:
    done = subprocess.run(
        ["shellcheck", "-x", str(SCRIPT)], capture_output=True, check=False
    )
    assert done.returncode == 0, done.stdout.decode()


def run_script(
    out: Path, tmp_path: Path, **env: str
) -> subprocess.CompletedProcess[bytes]:
    full = {
        **os.environ,
        "SBOMMINBENCH_SEEDS_DIR": str(FIXTURES / "seeds"),
        "SBOMMINBENCH_REPLAY_CACHE": str(FIXTURES / "cache"),
        "SBOMMINBENCH_REFERENCE_SUMS": str(tmp_path / "no-reference"),
        "UV_OFFLINE": "1",
        **env,
    }
    return subprocess.run(
        ["sh", str(SCRIPT), str(out)],
        capture_output=True,
        env=full,
        cwd=tmp_path,
        check=False,
    )


def lines(raw: bytes) -> list[str]:
    return raw.decode("utf-8").split("\n")[:-1]


RING_REVIEW = {
    "at": "2026-11-11T06:40:05Z",
    "auto_status": "auto_unresolved",
    "auto_value_at_review": None,
    "decision": "correct",
    "element": "license",
    "id": "pkg:cargo/ring@0.16.20",
    "note": None,
    "reviewer": "tester",
    "subject": "root",
    "value": {
        "has_license_ref": False,
        "spdx_expression": "Apache-2.0 AND ISC AND MIT",
    },
}


def ring_license_status(out: Path) -> str:
    """Status of the ring licence in the release (reviews are applied by export)."""
    text = (out / "data" / "release" / "entries.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(line) for line in text.splitlines()]
    (entry,) = [e for e in entries if e["id"] == RING_REVIEW["id"]]
    return str(entry["subjects"][0]["expected"]["license"]["status"])


@pytest.mark.skipif(not can_run, reason="needs POSIX sh and uv")
def test_stored_reviews_are_replayed(tmp_path: Path) -> None:
    without = tmp_path / "without"
    done = run_script(without, tmp_path)
    assert done.returncode == 0, done.stderr.decode()
    assert ring_license_status(without) == "auto_unresolved"
    assert any(x.startswith("INFO: reviews: none") for x in lines(done.stderr))

    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "tester.jsonl").write_bytes(
        json.dumps(RING_REVIEW, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    with_reviews = tmp_path / "with"
    done = run_script(with_reviews, tmp_path, SBOMMINBENCH_REVIEWS_DIR=str(reviews))
    assert done.returncode == 0, done.stderr.decode()
    assert ring_license_status(with_reviews) == "human_corrected"
    sums = "data/release/SHA256SUMS"
    assert (without / sums).read_bytes() != (with_reviews / sums).read_bytes()


@pytest.mark.skipif(not can_run, reason="needs POSIX sh and uv")
def test_replay_matches_then_detects_a_difference(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first = run_script(out, tmp_path)
    assert first.returncode == 0, first.stderr.decode()
    assert all(KV.match(x) for x in lines(first.stdout))
    assert all(TAGGED.match(x) for x in lines(first.stderr))
    assert "CHECKSUMS=unchecked" in lines(first.stdout)
    sums = out / "data" / "release" / "SHA256SUMS"
    assert sums.is_file()
    assert (out / "data" / "release" / "entries.jsonl").is_file()

    ref = tmp_path / "ref"
    ref.write_bytes(sums.read_bytes())
    same = run_script(out, tmp_path, SBOMMINBENCH_REFERENCE_SUMS=str(ref))
    assert same.returncode == 0, same.stderr.decode()
    assert "CHECKSUMS=match" in lines(same.stdout)

    ref.write_bytes(sums.read_bytes() + b"0" * 64 + b"  extra.json\n")
    differ = run_script(out, tmp_path, SBOMMINBENCH_REFERENCE_SUMS=str(ref))
    assert differ.returncode != 0
    assert "CHECKSUMS=differ" in lines(differ.stdout)
    assert any(x.startswith("ERROR: checksums differ") for x in lines(differ.stderr))


@pytest.mark.skipif(not can_run, reason="needs POSIX sh and uv")
def test_missing_seeds_is_a_clear_error(tmp_path: Path) -> None:
    done = run_script(
        tmp_path / "out", tmp_path, SBOMMINBENCH_SEEDS_DIR=str(tmp_path / "none")
    )
    assert done.returncode != 0
    assert done.stdout == b""
    assert lines(done.stderr)[0].startswith("ERROR: seeds directory not found")
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not can_run, reason="needs POSIX sh and uv")
@pytest.mark.parametrize("target", ["data", "./data/", ".", str(ROOT)])
def test_refuses_the_real_data_dir(target: str) -> None:
    # Replay env is set so a regression cannot reach the network.
    env = {
        **os.environ,
        "SBOMMINBENCH_SEEDS_DIR": str(FIXTURES / "seeds"),
        "SBOMMINBENCH_REPLAY_CACHE": str(FIXTURES / "cache"),
    }
    before = sorted(p.name for p in ROOT.iterdir())
    done = subprocess.run(
        ["sh", str(SCRIPT), target], capture_output=True, env=env, cwd=ROOT, check=False
    )
    assert done.returncode != 0
    assert lines(done.stderr)[0].startswith("ERROR: refusing to write into")
    assert sorted(p.name for p in ROOT.iterdir()) == before
