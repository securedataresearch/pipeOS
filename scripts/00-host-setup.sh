#!/usr/bin/env bash
# Install host (CachyOS/Arch) dependencies for building and testing pipeOS.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

sudo pacman -S --needed --noconfirm qemu-base edk2-ovmf musl

rustup target add x86_64-unknown-linux-musl

mkdir -p "$OUT"
[ -f "$OUT/OVMF_VARS.4m.fd" ] || cp "$OVMF_VARS_SRC" "$OUT/OVMF_VARS.4m.fd"

for tool in mformat mcopy sfdisk mkfs.vfat bsdtar truncate; do
    command -v "$tool" >/dev/null || { echo "missing host tool: $tool" >&2; exit 1; }
done
[ -f "$ALPINE_ISO" ] || { echo "missing Alpine ISO: $ALPINE_ISO" >&2; exit 1; }

echo "host setup OK"
