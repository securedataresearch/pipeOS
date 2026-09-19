#!/usr/bin/env bash
# Cross-compile netgaze (SamReeves/netgaze) statically for musl — the LAN
# collector the dashboard's network map runs (pipeOS#217). Same shape as
# 20-build-pipe.sh: a host with the musl target builds it, else the chroot.
set -euo pipefail
. "$(dirname "$0")/../config.sh"
mkdir -p "$OUT/payloads"
NETGAZE_SRC="${NETGAZE_SRC:-$PIPEOS_ROOT/vendor/netgaze}"
NETGAZE_REPO="${NETGAZE_REPO:-https://github.com/SamReeves/netgaze.git}"
NETGAZE_REF="${NETGAZE_REF:-master}"
if [ ! -d "$NETGAZE_SRC/.git" ]; then
    git clone -q "$NETGAZE_REPO" "$NETGAZE_SRC"
fi
git -C "$NETGAZE_SRC" fetch -q origin "$NETGAZE_REF"
git -C "$NETGAZE_SRC" checkout -q FETCH_HEAD
TARGET=x86_64-unknown-linux-musl
if rustup target list --installed 2>/dev/null | grep -qx "$TARGET"; then
    (cd "$NETGAZE_SRC" && cargo build --release -p netgaze --target "$TARGET")
    src="$NETGAZE_SRC/target/$TARGET/release/netgaze"
elif mountpoint -q "$CHROOT/netgaze" 2>/dev/null; then
    TARGET=x86_64-alpine-linux-musl
    CR="$PIPEOS_ROOT/scripts/chroot-run.sh"
    "$CR" 'apk add --quiet rust cargo build-base'
    "$CR" "cd /netgaze && RUSTFLAGS='-C target-feature=+crt-static' cargo build --release -p netgaze --target $TARGET"
    src="$NETGAZE_SRC/target/$TARGET/release/netgaze"
else
    echo "no musl toolchain: rustup target add $TARGET, or mount the source into the chroot" >&2
    exit 1
fi
[ -f "$src" ] || { echo "ERROR: netgaze binary not produced" >&2; exit 1; }
cp "$src" "$OUT/payloads/netgaze"
file "$OUT/payloads/netgaze" | grep -q 'static' \
    || { echo "ERROR: netgaze is not statically linked" >&2; exit 1; }
NETGAZE_VERSION=$(grep -m1 '^version' "$NETGAZE_SRC/Cargo.toml" | cut -d'"' -f2)
NETGAZE_SHA=$(git -C "$NETGAZE_SRC" rev-parse --short HEAD)
echo "${NETGAZE_VERSION}_git${NETGAZE_SHA}" > "$OUT/payloads/netgaze.version"
echo "netgaze $NETGAZE_VERSION ($NETGAZE_SHA) built: $OUT/payloads/netgaze"
