# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Safe archive reader: members are read by name, never extracted blindly.

Archive member names and sizes are attacker text. Unsafe names are skipped,
reads are capped, and extraction stays under the destination directory.
"""

import tarfile
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MAX_MEMBERS = 50_000
MAX_MEMBER_BYTES = 2 * 1024 * 1024

_ZIP_SUFFIXES = (".whl", ".zip", ".nupkg", ".jar")
_TAR_SUFFIXES = (".tar.gz", ".tgz", ".crate", ".tar.bz2", ".tar")


@dataclass(frozen=True)
class Member:
    """One regular or non-regular archive member with a safe name."""

    name: str
    size: int
    is_file: bool


class ArchiveError(ValueError):
    """The archive is unreadable, corrupt, truncated or over a cap."""


class MemberTooLarge(ArchiveError):
    """A member is larger than the read cap."""


_READ_ERRORS = (
    zipfile.BadZipFile,
    tarfile.TarError,
    EOFError,
    OSError,
    zlib.error,
    NotImplementedError,
    RuntimeError,
)


def archive_kind(filename: str) -> Literal["zip", "tar"] | None:
    """Return the archive family named by *filename*, or ``None``."""
    lower = filename.lower()
    if lower.endswith(_ZIP_SUFFIXES):
        return "zip"
    if lower.endswith(_TAR_SUFFIXES):
        return "tar"
    return None


def is_safe_name(name: str) -> bool:
    """Return whether *name* is a relative, traversal-free archive path."""
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        return False
    if len(name) >= 2 and name[1] == ":":
        return False
    return ".." not in name.split("/")


def _kind(path: Path) -> Literal["zip", "tar"]:
    kind = archive_kind(path.name)
    if kind is None:
        raise ArchiveError(f"unsupported archive type: {path.name!r}")
    return kind


def list_members(path: Path) -> list[Member]:
    """Return the safe members of *path*, sorted by name."""
    members: list[Member] = []
    try:
        if _kind(path) == "zip":
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                if len(infos) > MAX_MEMBERS:
                    raise ArchiveError("too many members")
                for info in infos:
                    if is_safe_name(info.filename):
                        members.append(
                            Member(
                                info.filename,
                                info.file_size,
                                not info.is_dir(),
                            )
                        )
        else:
            with tarfile.open(path, "r:*") as tf:
                for i, m in enumerate(tf):
                    if i >= MAX_MEMBERS:
                        raise ArchiveError("too many members")
                    if is_safe_name(m.name):
                        members.append(Member(m.name, m.size, m.isreg()))
    except ArchiveError:
        raise
    except _READ_ERRORS as exc:
        raise ArchiveError(f"cannot read {path.name!r}: {exc}") from exc
    return sorted(members, key=lambda m: m.name)


def _read(path: Path, name: str, limit: int, *, cap: bool) -> bytes | None:
    """Read up to *limit* (+1 when *cap*) bytes of the regular member *name*."""
    if not is_safe_name(name):
        return None
    want = limit + 1 if cap else limit
    try:
        if _kind(path) == "zip":
            with zipfile.ZipFile(path) as zf:
                for i, info in enumerate(zf.infolist()):
                    if i >= MAX_MEMBERS:
                        raise ArchiveError("too many members")
                    if info.filename == name and not info.is_dir():
                        with zf.open(info) as fz:
                            return fz.read(want)
            return None
        with tarfile.open(path, "r:*") as tf:
            for i, m in enumerate(tf):
                if i >= MAX_MEMBERS:
                    raise ArchiveError("too many members")
                if m.name == name and m.isreg():
                    ft = tf.extractfile(m)
                    return ft.read(want) if ft else b""
    except ArchiveError:
        raise
    except _READ_ERRORS as exc:
        raise ArchiveError(f"cannot read {path.name!r}: {exc}") from exc
    return None


def read_member(
    path: Path, name: str, *, max_bytes: int = MAX_MEMBER_BYTES
) -> bytes | None:
    """Return the bytes of member *name*, or ``None`` if absent or unsafe.

    Raises:
        MemberTooLarge: the member holds more than *max_bytes* bytes. The size
            is measured by reading, never taken from the archive header.
    """
    data = _read(path, name, max_bytes, cap=True)
    if data is not None and len(data) > max_bytes:
        raise MemberTooLarge(f"member over {max_bytes} bytes")
    return data


def read_head(path: Path, name: str, n: int) -> bytes:
    """Return up to *n* leading bytes of *name* (empty when absent)."""
    return _read(path, name, n, cap=False) or b""


def extract_members(path: Path, names: Iterable[str], dest: Path) -> dict[str, Path]:
    """Write the named regular members under *dest*; absent ones are skipped.

    Raises:
        ArchiveError: a name is unsafe or resolves outside *dest*.
    """
    root = dest.resolve()
    out: dict[str, Path] = {}
    for name in names:
        if not is_safe_name(name):
            raise ArchiveError("unsafe member name")
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ArchiveError("member path leaves the destination")
        data = read_member(path, name)
        if data is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out[name] = target
    return out
