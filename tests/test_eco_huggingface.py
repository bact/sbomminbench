# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import requests

from sbomminbench.eco import huggingface
from sbomminbench.eco._pkgfetch import EcoError
from sbomminbench.eco.base import (
    CacheStore,
    FetchIndex,
    Observed,
    index_path,
    read_index,
)
from sbomminbench.http import HttpClient
from sbomminbench.ids import make_purl
from sbomminbench.schema import Observation
from sbomminbench.seeds import Seed

CACHE = Path(__file__).parent / "fixtures" / "cache"
QWEN = ("Qwen/Qwen2.5-72B-Instruct", "495f39366efef23836d0cfae4fbe635880d2be31")
LLAMA = ("meta-llama/Llama-3.1-8B", "d04e592bb4f6aa9cfee91e2e20afa771667e1d4b")
BERT = ("google-bert/bert-base-uncased", "86b5e0934494bd15c9632b12f734a8a67f723594")
GPT2 = ("openai-community/gpt2", "607a30d783dfa663caf39e06633721c8d4cfcd7e")
SDXL = (
    "stabilityai/stable-diffusion-xl-base-1.0",
    "462165984030d82259a11f4367a4eed129e94a7b",
)
ALL = [QWEN, LLAMA, BERT, GPT2, SDXL]
SHA = "a" * 40


def recorded(model: tuple[str, str]) -> tuple[FetchIndex, CacheStore]:
    index = read_index(index_path(CACHE, make_purl("huggingface", *model)))
    return index, CacheStore(CACHE)


def observed(model: tuple[str, str]) -> Observed:
    return huggingface.observe(*recorded(model))


def row(o: Observed, key: str) -> Observation:
    found = [r for r in [*o.platform.observations, *o.manifest] if r.key == key]
    assert len(found) == 1, key
    return found[0]


# -- recorded fixtures --


def test_qwen_license_name_and_other_tag() -> None:
    o = observed(QWEN)
    name = row(o, "hf_card:cardData.license_name")
    assert (name.raw, name.format_validity) == ("qwen", "unknown_id")
    assert "hf_license_other" in o.tags
    assert row(o, "hf_card:cardData.license").semantic_format == "placeholder"
    link = row(o, "hf_card:cardData.license_link")
    assert (link.semantic_format, link.format_validity) == ("other_url", "valid")


def test_llama_is_gated_with_unknown_licence_id() -> None:
    o = observed(LLAMA)
    assert "hf_gated" in o.tags and "hf_license_other" not in o.tags
    card = row(o, "hf_card:cardData.license")
    assert (card.raw, card.format_validity) == ("llama3.1", "unknown_id")


def test_bert_licence_tag_is_noncanonical_after_prefix() -> None:
    o = observed(BERT)
    tags = [r for r in o.platform.observations if r.element == "license"]
    assert [(r.raw, r.format_validity) for r in tags] == [
        ("license:apache-2.0", "noncanonical")
    ]
    assert tags[0].semantic_format == "id_shaped"
    assert "hf_gated" not in o.tags and "hf_renamed" not in o.tags


@pytest.mark.parametrize("model", ALL, ids=[m[0] for m in ALL])
def test_every_fixture_is_sorted_deterministic_and_pinned(
    model: tuple[str, str],
) -> None:
    index, store = recorded(model)
    first, second = huggingface.observe(index, store), huggingface.observe(index, store)
    assert first == second
    for rows in (first.platform.observations, first.manifest):
        keys = [(r.source, r.field_path) for r in rows]
        assert keys == sorted(set(keys))
    # Revision-pinned URL; the id keeps its slash. A renamed model is requested
    # under the seeded name.
    assert first.platform.source_url == index.responses["platform"]
    assert first.platform.source_url.startswith("https://huggingface.co/api/models/")
    assert first.platform.source_url.endswith(f"/revision/{model[1]}")
    assert index.version == model[1] and index.purl == make_purl("huggingface", *model)
    assert first.platform.html_url == f"https://huggingface.co/{index.name}"
    art = index.artifact
    assert art is not None and (art.kind, art.fetched, art.skip_reason) == (
        "hf_repo",
        False,
        "not_needed",
    )
    assert art.url == f"https://huggingface.co/{index.name}/tree/{model[1]}"
    assert huggingface.observe_with_failures(index, store)[1] == []
    owner = row(first, "hf_api:author")
    assert (owner.role, owner.raw) == ("owner", model[0].split("/")[0])


def test_files_are_model_files_first_twenty_sorted() -> None:
    index, _ = recorded(SDXL)
    assert index.files == sorted(index.files) and len(index.files) <= 20
    assert index.files_count >= len(index.files)
    assert all(
        f.endswith((".safetensors", ".onnx", ".pt", ".ckpt")) or "/" in f
        for f in index.files
    )
    qwen, _ = recorded(QWEN)
    assert (
        qwen.files_count == 37 and qwen.files[0] == "model-00001-of-00037.safetensors"
    )
    assert qwen.model_members == []


def test_gpt2_legacy_id_is_renamed() -> None:
    index, store = recorded(GPT2)
    assert index.name == "openai-community/gpt2"
    assert (
        "hf_renamed" in index.tags
        and "hf_renamed" in huggingface.observe(index, store).tags
    )
    assert row(huggingface.observe(index, store), "hf_api:id").raw == index.name


# -- fetch and observe on synthetic responses ----------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict({})
        self.url = ""

    def close(self) -> None:
        pass


class FakeSession:
    def __init__(self, routes: Mapping[str, FakeResponse]) -> None:
        self.routes = dict(routes)
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, dict(kwargs["headers"])))
        resp = self.routes.get(url, FakeResponse(404))
        resp.url = url
        return resp


def model_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": "o/m",
        "author": "o",
        "sha": SHA,
        "gated": False,
        "tags": ["transformers", "license:mit"],
        "cardData": {"license": "mit"},
        "siblings": [{"rfilename": "README.md"}, {"rfilename": "m.safetensors"}],
    }
    doc.update(overrides)
    return doc


def served(
    tmp_path: Path, doc: Any, name: str = "o/m"
) -> tuple[HttpClient, FakeSession]:
    body = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
    session = FakeSession({huggingface.api_url(name, SHA): FakeResponse(200, body)})
    client = HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )
    return client, session


def run(tmp_path: Path, doc: Any, name: str = "o/m") -> tuple[FetchIndex, Observed]:
    client, _ = served(tmp_path, doc, name)
    index = huggingface.fetch(
        Seed("huggingface", name, SHA, "edge", None), client, max_bytes=1
    )
    return index, huggingface.observe(index, CacheStore(client.cache_dir))


def test_fetch_sends_no_token_and_ignores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        monkeypatch.setenv(var, "hf_secret")
    client, session = served(tmp_path, model_doc())
    huggingface.fetch(
        Seed("huggingface", "o/m", SHA, "edge", None), client, max_bytes=1
    )
    assert [u for u, _ in session.requests] == [
        f"https://huggingface.co/api/models/o/m/revision/{SHA}"
    ]
    headers = {k.lower() for k in session.requests[0][1]}
    assert not headers & {"authorization", "x-hf-token", "cookie"}
    assert "hf_secret" not in json.dumps(session.requests)


def test_fetch_lowercases_sha_and_builds_purl(tmp_path: Path) -> None:
    client, _ = served(tmp_path, model_doc())
    seed = Seed("huggingface", "o/m", SHA.upper(), "mid", 3)
    index = huggingface.fetch(seed, client, max_bytes=1)
    assert index.version == SHA and index.purl == f"pkg:huggingface/o/m@{SHA}"
    assert (index.stratum, index.rank) == ("mid", 3)
    assert index.responses == {"platform": huggingface.api_url("o/m", SHA)}


@pytest.mark.parametrize(
    ("gated", "expected"),
    [
        (False, False),
        ("auto", True),
        ("manual", True),
        (True, True),
        (None, True),
        ("", True),
    ],
    ids=repr,
)
def test_gated_tag_unless_false(tmp_path: Path, gated: object, expected: bool) -> None:
    index, o = run(tmp_path, model_doc(gated=gated))
    assert ("hf_gated" in index.tags) is expected and ("hf_gated" in o.tags) is expected


def test_gated_key_missing_gives_no_tag(tmp_path: Path) -> None:
    doc = model_doc()
    del doc["gated"]
    index, _ = run(tmp_path, doc)
    assert "hf_gated" not in index.tags


@pytest.mark.parametrize("reported", ["O/M", "o/other"], ids=repr)
def test_renamed_when_api_id_differs(tmp_path: Path, reported: str) -> None:
    index, o = run(tmp_path, model_doc(id=reported))
    assert index.name == reported and "hf_renamed" in index.tags
    assert index.purl == make_purl("huggingface", reported, SHA)
    assert row(o, "hf_api:id").raw == reported


@pytest.mark.parametrize(
    ("reported", "seed"), [("m", "o/m"), (None, "gpt2"), ("", "gpt2")]
)
def test_unusable_model_id_raises(tmp_path: Path, reported: object, seed: str) -> None:
    doc = model_doc(id=reported)
    client, _ = served(tmp_path, doc, seed)
    with pytest.raises(EcoError):
        huggingface.fetch(
            Seed("huggingface", seed, SHA, "edge", None), client, max_bytes=1
        )


def test_not_renamed_when_id_matches_or_is_missing(tmp_path: Path) -> None:
    index, _ = run(tmp_path / "same", model_doc())
    assert "hf_renamed" not in index.tags
    doc = model_doc()
    del doc["id"]
    index, o = run(tmp_path / "noid", doc)
    assert index.name == "o/m" and "hf_renamed" not in index.tags
    assert not [r for r in o.platform.observations if r.field_path == "id"]


def test_author_present_empty_null_and_missing(tmp_path: Path) -> None:
    for i, (value, native) in enumerate([("", "string"), (None, "null")]):
        _, o = run(tmp_path / str(i), model_doc(author=value))
        r = row(o, "hf_api:author")
        assert (r.present, r.raw, r.native_type, r.role) == (
            True,
            value,
            native,
            "owner",
        )
    doc = model_doc()
    del doc["author"]
    _, o = run(tmp_path / "missing", doc)
    gone = row(o, "hf_api:author")
    assert (gone.present, gone.native_type, gone.role) == (False, "absent", "owner")


def test_licence_tags_use_position_in_full_list(tmp_path: Path) -> None:
    tags = ["a", 5, "license:mit", "license:other", "licence:x", "License:y"]
    _, o = run(tmp_path, model_doc(tags=tags))
    rows = [r for r in o.platform.observations if r.element == "license"]
    assert [(r.field_path, r.raw) for r in rows] == [
        ("tags[2]", "license:mit"),
        ("tags[3]", "license:other"),
    ]
    assert "hf_license_other" in o.tags


@pytest.mark.parametrize(
    ("card", "tags", "other"),
    [
        ({"license": "other"}, [], True),
        ({"license": ["mit", "other"]}, [], True),
        ({"license": "mit"}, ["license:other"], True),
        ({"license": "mit"}, ["license:mit"], False),
        ({"license_name": "other"}, [], False),
        ({"license": ["mit", 5]}, [], False),
        (None, [], False),
    ],
    ids=["card", "card-list", "tag", "neither", "name-only", "mixed-list", "no-card"],
)
def test_license_other_from_either_source(
    tmp_path: Path, card: object, tags: list[str], other: bool
) -> None:
    index, o = run(tmp_path, model_doc(cardData=card, tags=tags))
    assert ("hf_license_other" in index.tags) is other
    assert ("hf_license_other" in o.tags) is other


def test_card_rows_and_absent_rows(tmp_path: Path) -> None:
    _, o = run(
        tmp_path / "list",
        model_doc(cardData={"license": ["a", "b"], "license_link": 5}),
    )
    lic = row(o, "hf_card:cardData.license")
    assert (lic.raw, lic.native_type, lic.semantic_format) == (
        ["a", "b"],
        "list_of_strings",
        None,
    )
    assert row(o, "hf_card:cardData.license_link").native_type == "number"
    assert o.manifest_files == ["hf_card"]
    for i, card in enumerate([None, {}, "text"]):
        _, o = run(tmp_path / f"c{i}", model_doc(cardData=card))
        assert [(r.key, r.present) for r in o.manifest] == [
            ("hf_card:cardData.license", False)
        ]
        assert o.manifest_files == ([] if not isinstance(card, dict) else ["hf_card"])
    doc = model_doc()
    del doc["cardData"]
    _, o = run(tmp_path / "gone", doc)
    assert row(o, "hf_card:cardData.license").present is False
    _, o = run(tmp_path / "null-license", model_doc(cardData={"license": None}))
    null = row(o, "hf_card:cardData.license")
    assert (null.present, null.native_type) == (True, "null")


def test_files_cap_sorting_and_odd_siblings(tmp_path: Path) -> None:
    names = [f"w/model-{i:03d}.safetensors" for i in range(25)][::-1]
    siblings: list[Any] = [{"rfilename": n} for n in names]
    siblings += [
        {"rfilename": "README.md"},
        {"rfilename": "x.bin"},
        {"rfilename": 7},
        "str",
        {},
    ]
    index, _ = run(tmp_path, model_doc(siblings=siblings))
    assert index.files == sorted(names)[:20] and index.files_count == 25
    assert index.model_members == []
    for i, bad in enumerate([None, "x", 5, []]):
        index, _ = run(tmp_path / f"s{i}", model_doc(siblings=bad))
        assert (index.files, index.files_count) == ([], 0)


def test_email_in_card_is_redacted(tmp_path: Path) -> None:
    _, o = run(
        tmp_path,
        model_doc(cardData={"license": "mit", "license_link": "mailto:a@b.org"}),
    )
    link = row(o, "hf_card:cardData.license_link")
    assert link.has_email and "a@b.org" not in str(link.raw)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(404),
        FakeResponse(410),
        FakeResponse(200, b"[]"),
        FakeResponse(200, b"{nope"),
    ],
    ids=["404", "410", "array", "bad-json"],
)
def test_fetch_unavailable_response_raises(
    tmp_path: Path, response: FakeResponse
) -> None:
    session = FakeSession({huggingface.api_url("o/m", SHA): response})
    client = HttpClient(
        tmp_path / "cache",
        session=session,  # type: ignore[arg-type]
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )
    with pytest.raises(EcoError):
        huggingface.fetch(
            Seed("huggingface", "o/m", SHA, "edge", None), client, max_bytes=1
        )


def test_observe_without_cached_response_raises(tmp_path: Path) -> None:
    index, _ = run(tmp_path, model_doc())
    with pytest.raises(EcoError):
        huggingface.observe(index, CacheStore(tmp_path / "empty"))
    with pytest.raises(EcoError):
        huggingface.observe(
            index.model_copy(update={"responses": {}}), CacheStore(tmp_path)
        )


def test_observe_accepts_bom_and_is_deterministic(tmp_path: Path) -> None:
    body = b"\xef\xbb\xbf" + json.dumps(model_doc()).encode()
    client, _ = served(tmp_path, body)
    seed = Seed("huggingface", "o/m", SHA, "edge", None)
    index = huggingface.fetch(seed, client, max_bytes=1)
    store = CacheStore(client.cache_dir)
    assert huggingface.observe(index, store) == huggingface.observe(index, store)
    assert row(huggingface.observe(index, store), "hf_api:id").raw == "o/m"


def test_api_url_quoting() -> None:
    assert huggingface.api_url("a/b c", "ABC") == (
        "https://huggingface.co/api/models/a/b%20c/revision/abc"
    )
