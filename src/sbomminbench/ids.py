# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0

"""Package URLs, entry paths and canonical name keys.

One place builds the purl of an entry, maps it to its path under ``data/entries``
and normalises names for lookups, so that every ecosystem goes through the same
rules.
"""

from pathlib import PurePosixPath
from urllib.parse import quote

from packageurl import PackageURL
from packaging.utils import canonicalize_name

from sbomminbench.schema import Ecosystem


def _split_once(name: str, sep: str, eco: str) -> tuple[str, str]:
    """Split ``name`` on the first ``sep``; both parts must be non-empty."""
    head, found, tail = name.partition(sep)
    if not found or not head or not tail:
        raise ValueError(f"{eco} name {name!r} needs the form 'a{sep}b'")
    return head, tail


def make_purl(eco: Ecosystem, name: str, version: str) -> str:
    """Return the canonical purl of a pinned package or model."""
    match eco:
        case "pypi":
            purl = PackageURL("pypi", name=canonicalize_name(name), version=version)
        case "npm" if name.startswith("@"):
            scope, pkg = _split_once(name, "/", eco)
            purl = PackageURL("npm", namespace=scope, name=pkg, version=version)
        case "npm":
            purl = PackageURL("npm", name=name, version=version)
        case "maven":
            group, artifact = _split_once(name, ":", eco)
            purl = PackageURL("maven", namespace=group, name=artifact, version=version)
        case "huggingface":
            owner, model = _split_once(name, "/", eco)
            purl = PackageURL(
                "huggingface",
                namespace=owner,
                name=model,
                version=version.lower(),
            )
        case "cargo" | "nuget":
            purl = PackageURL(eco, name=name, version=version)
        case _:
            raise ValueError(f"unknown ecosystem {eco!r}")
    return purl.to_string()


def entry_relpath(purl: str) -> PurePosixPath:
    """Return the entry path ``<type>/<ns>/<name>@<version>.json``, relative.

    Namespace, name and version are quoted with ``safe=""``; a missing
    namespace becomes ``_``.
    """
    parsed = PackageURL.from_string(purl)
    if parsed.version is None:
        raise ValueError(f"purl {purl!r} has no version")
    namespace = quote(parsed.namespace, safe="") if parsed.namespace else "_"
    name = quote(parsed.name, safe="")
    version = quote(parsed.version, safe="")
    return PurePosixPath(parsed.type, namespace, f"{name}@{version}.json")


def canonical_name_key(eco: Ecosystem, name: str) -> str:
    """Return the key under which two spellings of one package name compare equal."""
    match eco:
        case "pypi":
            return canonicalize_name(name)
        case "cargo":
            return name.lower().replace("_", "-")
        case "nuget":
            return name.lower()
        case "huggingface":
            return name.casefold()
        case "npm" | "maven":
            return name
        case _:
            raise ValueError(f"unknown ecosystem {eco!r}")
