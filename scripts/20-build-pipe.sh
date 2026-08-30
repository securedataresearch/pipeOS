#!/usr/bin/env bash
# Cross-compile the pipe CLI (and daemon, if present) statically for musl.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

mkdir -p "$OUT/payloads"

if command -v musl-gcc >/dev/null; then
    # cross host (laptop): C deps (ring) need a musl C compiler; Arch's
    # `musl` package provides musl-gcc
    TARGET=x86_64-unknown-linux-musl
    export CC_x86_64_unknown_linux_musl=musl-gcc
    export TARGET_CC=musl-gcc
    export TARGET_AR=ar
    # the `pipe` binary is produced by the pipe-client crate
    cd "$PIPE_SRC"
    cargo build --release -p pipe-client --target "$TARGET"
    src="$PIPE_SRC/target/$TARGET/release/pipe"
elif mountpoint -q "$CHROOT/pipe" 2>/dev/null; then
    # musl host (pipeOS itself): build natively inside the Alpine chroot.
    # Explicit --target keeps RUSTFLAGS off build scripts and proc-macros,
    # so +crt-static yields a static binary without breaking the host builds.
    TARGET=x86_64-alpine-linux-musl
    CR="$PIPEOS_ROOT/scripts/chroot-run.sh"
    "$CR" 'apk add --quiet rust cargo build-base'
    "$CR" "cd /pipe && RUSTFLAGS='-C target-feature=+crt-static' cargo build --release -p pipe-client --target $TARGET"
    src="$PIPE_SRC/target/$TARGET/release/pipe"
else
    echo "no musl toolchain: need musl-gcc (cross host) or a mounted chroot — run 10-mk-chroot.sh" >&2
    exit 1
fi
[ -f "$src" ] || { echo "ERROR: pipe binary not produced" >&2; exit 1; }
cp "$src" "$OUT/payloads/pipe"
file "$OUT/payloads/pipe" | grep -q 'static' \
    || { echo "ERROR: pipe is not statically linked" >&2; exit 1; }

PIPE_VERSION=$(grep -m1 '^version' "$PIPE_SRC/Cargo.toml" | cut -d'"' -f2)
echo "$PIPE_VERSION" > "$OUT/payloads/pipe.version"
echo "pipe $PIPE_VERSION built: $(ls "$OUT/payloads")"
