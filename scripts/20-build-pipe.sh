#!/usr/bin/env bash
# Cross-compile the pipe CLI (and daemon, if present) statically for musl.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

TARGET=x86_64-unknown-linux-musl
mkdir -p "$OUT/payloads"

# C deps (ring) need a musl C compiler; Arch's `musl` package provides musl-gcc
command -v musl-gcc >/dev/null || { echo "musl-gcc missing — run 00-host-setup.sh" >&2; exit 1; }
export CC_x86_64_unknown_linux_musl=musl-gcc
export TARGET_CC=musl-gcc
export TARGET_AR=ar

# the `pipe` binary is produced by the pipe-client crate
cd "$PIPE_SRC"
cargo build --release -p pipe-client --target "$TARGET"

src="$PIPE_SRC/target/$TARGET/release/pipe"
[ -f "$src" ] || { echo "ERROR: pipe binary not produced" >&2; exit 1; }
cp "$src" "$OUT/payloads/pipe"
file "$OUT/payloads/pipe" | grep -q 'static' \
    || { echo "ERROR: pipe is not statically linked" >&2; exit 1; }

PIPE_VERSION=$(grep -m1 '^version' "$PIPE_SRC/Cargo.toml" | cut -d'"' -f2)
echo "$PIPE_VERSION" > "$OUT/payloads/pipe.version"
echo "pipe $PIPE_VERSION built: $(ls "$OUT/payloads")"
