#!/bin/sh
# SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
# SPDX-FileType: SOURCE
# SPDX-License-Identifier: Apache-2.0
#
# Rebuild the collection into a separate directory and compare it with the
# committed release. Usage: scripts/reproduce.sh [OUT_DIR]  (default ./reproduce-out)
#
# Environment (all optional):
#   SBOMMINBENCH_SEEDS_DIR     seeds to copy (default: <repo>/data/seeds)
#   SBOMMINBENCH_REVIEWS_DIR   stored human reviews to copy, when the directory
#                              exists (default: reviews/ next to the seeds)
#   SBOMMINBENCH_REFERENCE_SUMS SHA256SUMS to compare with
#                              (default: <repo>/data/release/SHA256SUMS)
#   SBOMMINBENCH_REPLAY_CACHE  a cache directory to copy instead of running
#                              `fetch` and `uv sync` (offline replay)
#
# Stdout: KEY=VALUE lines only. Stderr: INFO: / ERROR: lines. Step output goes
# to <OUT_DIR>/logs/.
set -eu

info() { printf 'INFO: %s\n' "$*" >&2; }
fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

here=$(cd "$(dirname "$0")" && pwd -P)
root=$(dirname "$here")

out_arg=${1:-./reproduce-out}
if [ -d "$out_arg" ]; then
    out=$(cd "$out_arg" && pwd -P)
else
    out_parent=$(dirname "$out_arg")
    [ -d "$out_parent" ] || fail "parent of output directory does not exist: $out_parent"
    out="$(cd "$out_parent" && pwd -P)/$(basename "$out_arg")"
fi
if [ "$out" = "$root/data" ] || [ "$out" = "$root" ]; then
    fail "refusing to write into $out; give another output directory"
fi

seeds=${SBOMMINBENCH_SEEDS_DIR:-$root/data/seeds}
reviews=${SBOMMINBENCH_REVIEWS_DIR:-$(dirname "$seeds")/reviews}
reference=${SBOMMINBENCH_REFERENCE_SUMS:-$root/data/release/SHA256SUMS}
replay=${SBOMMINBENCH_REPLAY_CACHE:-}

[ -d "$seeds" ] || fail "seeds directory not found: $seeds (the dataset is not committed yet?)"
command -v uv >/dev/null 2>&1 || fail "uv not found; install it from https://docs.astral.sh/uv/"
if [ -n "$replay" ]; then
    [ -d "$replay" ] || fail "replay cache not found: $replay"
fi

cd "$root"
mkdir -p "$out/data" "$out/logs"
[ -z "$(ls -A "$out/data" 2>/dev/null)" ] || info "reusing existing $out/data (build is idempotent)"

# step NAME COMMAND...: run, send stdout to logs/NAME.log, stop on failure.
step() {
    name=$1
    shift
    info "$name"
    "$@" >"$out/logs/$name.log" || fail "$name failed (see $out/logs/$name.log)"
}

if [ -n "$replay" ]; then
    info "replay: skipping uv sync and fetch"
else
    step sync uv sync --locked
fi

mkdir -p "$out/data/seeds"
cp -R "$seeds/." "$out/data/seeds/"
# Human decisions are an input of export: without them the reviewed values are
# missing from the release files and the checksums differ.
if [ -d "$reviews" ]; then
    info "reviews: copying $reviews"
    mkdir -p "$out/data/reviews"
    cp -R "$reviews/." "$out/data/reviews/"
else
    info "reviews: none at $reviews"
fi

run() { uv run --no-sync sbomminbench "$@" --data-dir "$out/data" --cache-dir "$out/cache"; }

if [ -n "$replay" ]; then
    mkdir -p "$out/cache"
    cp -R "$replay/." "$out/cache/"
else
    step fetch run fetch
fi
step build run build
step validate run validate
step export run export
step validate-release run validate

built="$out/data/release/SHA256SUMS"
[ -f "$built" ] || fail "export wrote no SHA256SUMS"
printf 'OUT=%s\n' "$out"
if [ -f "$reference" ]; then
    if cmp -s "$reference" "$built"; then
        info "checksums match $reference"
        printf 'CHECKSUMS=match\n'
    else
        printf 'CHECKSUMS=differ\n'
        fail "checksums differ from $reference; compare with: diff $reference $built"
    fi
else
    info "no reference SHA256SUMS at $reference; printing the rebuilt sums only"
    printf 'CHECKSUMS=unchecked\n'
    while read -r sum name; do
        printf 'SHA256=%s FILE=%s\n' "$sum" "$name"
    done <"$built"
fi
