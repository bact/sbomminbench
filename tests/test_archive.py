# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

import io
import struct
import tarfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

import pytest

from sbomminbench import archive, models_scan
from sbomminbench.archive import (
    ArchiveError,
    Member,
    MemberTooLarge,
    archive_kind,
    extract_members,
    is_safe_name,
    list_members,
    read_head,
    read_member,
)
from sbomminbench.models_scan import ModelMember, scan_archive, scan_names

ONNX = b"\x08\x08\x12\x07tf2onnx" + b"0" * 40
SAFET = struct.pack("<Q", 2) + b"{}" + b"0" * 30
NPY = b"\x93NUMPY\x01\x00" + b"0" * 30


def make_zip(path: Path, files: Mapping[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return path


def make_tar(
    path: Path, files: Mapping[str, bytes], links: Mapping[str, str] | None = None
) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for name, target in (links or {}).items():
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            tf.addfile(ti)
    return path


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.whl", "zip"),
        ("a.ZIP", "zip"),
        ("a.nupkg", "zip"),
        ("a.jar", "zip"),
        ("a.tar.gz", "tar"),
        ("a.tgz", "tar"),
        ("a.crate", "tar"),
        ("a.tar.bz2", "tar"),
        ("a.tar", "tar"),
        ("a.txt", None),
    ],
)
def test_archive_kind(name: str, kind: str | None) -> None:
    assert archive_kind(name) == kind


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("pkg/a.txt", True),
        ("./a", True),
        ("a/b..c", True),
        ("", False),
        ("../evil", False),
        ("a/../../x", False),
        ("/abs", False),
        ("a\\b", False),
        ("C:/x", False),
        ("c:x", False),
        ("a\x00b", False),
    ],
)
def test_is_safe_name(name: str, safe: bool) -> None:
    assert is_safe_name(name) is safe
    if safe:
        win = PureWindowsPath(name)
        assert not win.is_absolute()
        assert not win.drive
        assert ".." not in win.parts


def test_tar_lists_only_safe_names(tmp_path: Path) -> None:
    tar = make_tar(
        tmp_path / "a.tar.gz",
        {"../evil": b"x", "/abs": b"y", "pkg/a.txt": b"z"},
    )
    assert list_members(tar) == [Member("pkg/a.txt", 1, True)]


def test_zip_list_sorted_with_dirs(tmp_path: Path) -> None:
    z = make_zip(tmp_path / "a.zip", {"b/": b"", "b/x": b"12", "a": b"1", "../e": b""})
    assert list_members(z) == [
        Member("a", 1, True),
        Member("b/", 0, False),
        Member("b/x", 2, True),
    ]


def test_member_too_large_zip_and_boundary(tmp_path: Path) -> None:
    z = make_zip(tmp_path / "a.zip", {"big": b"0" * (3 * 1024 * 1024)})
    with pytest.raises(MemberTooLarge):
        read_member(z, "big")
    assert len(read_member(z, "big", max_bytes=3 * 1024 * 1024) or b"") == 3 * 1024**2
    with pytest.raises(MemberTooLarge):
        read_member(z, "big", max_bytes=3 * 1024 * 1024 - 1)


def test_member_too_large_tar(tmp_path: Path) -> None:
    t = make_tar(tmp_path / "a.tgz", {"big": b"0" * 100})
    with pytest.raises(MemberTooLarge):
        read_member(t, "big", max_bytes=99)
    assert read_member(t, "big", max_bytes=100) == b"0" * 100


def test_crlf_bytes_unchanged(tmp_path: Path) -> None:
    data = b"a\r\nb\r\n"
    for arc in (
        make_zip(tmp_path / "a.zip", {"f": data}),
        make_tar(tmp_path / "a.tar", {"f": data}),
    ):
        assert read_member(arc, "f") == data
        assert read_head(arc, "f", 3) == b"a\r\n"


def test_symlink_dir_absent_unsafe_return_none(tmp_path: Path) -> None:
    t = make_tar(tmp_path / "a.tar.gz", {"pkg/a": b"1"}, {"pkg/l": "/etc/passwd"})
    assert read_member(t, "pkg/l") is None
    assert [m.is_file for m in list_members(t) if m.name == "pkg/l"] == [False]
    z = make_zip(tmp_path / "a.zip", {"d/": b"", "../x": b"1"})
    assert read_member(z, "d/") is None
    assert read_member(z, "missing") is None
    assert read_member(z, "../x") is None
    assert read_head(z, "missing", 4) == b""


def test_member_count_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive, "MAX_MEMBERS", 3)
    files = {f"f{i}": b"x" for i in range(4)}
    for arc in (
        make_zip(tmp_path / "a.zip", files),
        make_tar(tmp_path / "a.tar", files),
    ):
        with pytest.raises(ArchiveError, match="too many"):
            list_members(arc)
    ok = make_tar(tmp_path / "b.tar", {f"f{i}": b"x" for i in range(3)})
    assert len(list_members(ok)) == 3


def test_truncated_and_corrupt(tmp_path: Path) -> None:
    z = make_zip(tmp_path / "a.zip", {"f": b"0" * 5000})
    zraw = z.read_bytes()
    tz = tmp_path / "t.zip"
    tz.write_bytes(zraw[: len(zraw) // 2])
    t = make_tar(tmp_path / "a.tar.gz", {"f": bytes(range(256)) * 200})
    traw = t.read_bytes()
    tt = tmp_path / "t.tar.gz"
    tt.write_bytes(traw[: len(traw) // 2])
    junk = tmp_path / "j.tar.gz"
    junk.write_bytes(b"not an archive")
    other = tmp_path / "x.txt"
    other.write_bytes(b"x")
    for bad in (tz, tt, junk, other, tmp_path / "missing.zip"):
        with pytest.raises(ArchiveError):
            list_members(bad)
        with pytest.raises(ArchiveError):
            read_member(bad, "f")


def test_extract_members(tmp_path: Path) -> None:
    z = make_zip(tmp_path / "a.zip", {"a/b.txt": b"1\r\n", "c": b"2"})
    dest = tmp_path / "out"
    got = extract_members(z, ["a/b.txt", "nope"], dest)
    assert list(got) == ["a/b.txt"]
    assert got["a/b.txt"].read_bytes() == b"1\r\n"
    assert got["a/b.txt"].resolve().is_relative_to(dest.resolve())
    assert not (dest / "c").exists()


@pytest.mark.parametrize("name", ["a/../../x", "../x", "/abs", "a\\..\\x", "C:/x"])
def test_extract_rejects_unsafe(tmp_path: Path, name: str) -> None:
    z = make_zip(tmp_path / "a.zip", {"a": b"1"})
    with pytest.raises(ArchiveError):
        extract_members(z, [name], tmp_path / "out")
    assert not (tmp_path / "x").exists()


def test_scan_archive(tmp_path: Path) -> None:
    z = make_zip(
        tmp_path / "a.whl",
        {
            "m/model.onnx": ONNX,
            "x/distutils-precedence.pth": b"import os\n",
            "w.safetensors": SAFET,
            "d/data.npy": NPY,
        },
    )
    models, tags = scan_archive(z, list_members(z))
    assert models == [
        ModelMember("m/model.onnx", len(ONNX), "onnx"),
        ModelMember("w.safetensors", len(SAFET), "safetensors"),
    ]
    assert tags == ["array_data_not_scanned"]


def test_scan_archive_cap_and_non_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_scan, "MAX_MODEL_SUBJECTS", 2)
    files = {f"m{i}.onnx": ONNX for i in (3, 1, 2)}
    files["lfs.safetensors"] = b"version https://git-lfs.github.com/spec/v1\n"
    z = make_zip(tmp_path / "a.zip", files)
    models, tags = scan_archive(z, list_members(z))
    assert [m.path for m in models] == ["m1.onnx", "m2.onnx"]
    assert tags == ["model_subjects_capped"]
    none, no_tags = scan_archive(z, [Member("m1.onnx/", 0, False)])
    assert (none, no_tags) == ([], [])


def test_scan_names() -> None:
    assert scan_names(["b/Model.GGUF", "a.onnx", "w.bin", "t.model", "r.txt"]) == [
        ("a.onnx", "onnx"),
        ("b/Model.GGUF", "gguf"),
    ]
