#!/usr/bin/env bash
# Install host dependencies for building and testing pipeOS, on any of the
# common distro families — a self-builder's machine is not the maintainer's
# (pipeOS launch plan, Phase 3). Detects the package manager; asks before
# nothing except packages themselves (--needed/-y semantics per family).
set -euo pipefail
. "$(dirname "$0")/../config.sh"

if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm qemu-base edk2-ovmf musl mtools \
        dosfstools util-linux libarchive rustup
elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y qemu-system-x86 ovmf musl-tools mtools \
        dosfstools fdisk libarchive-tools curl
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y qemu-kvm edk2-ovmf musl-gcc mtools dosfstools \
        util-linux bsdtar curl
elif command -v apk >/dev/null 2>&1; then
    # building ON pipeOS/Alpine itself: 20-build-pipe.sh's chroot branch
    # handles the compile; this covers the image-assembly half
    sudo apk add mtools dosfstools sfdisk libarchive-tools
else
    echo "unrecognized distro — install by hand: qemu + OVMF, musl-gcc," >&2
    echo "mtools, dosfstools, sfdisk, bsdtar; then re-run" >&2
    exit 1
fi

# Rust musl target for the cross build (skipped when rustup is absent — the
# chroot fallback in 20-build-pipe.sh builds without it, just slower)
command -v rustup >/dev/null 2>&1 && rustup target add x86_64-unknown-linux-musl

mkdir -p "$OUT"
if [ ! -f "$OUT/OVMF_VARS.4m.fd" ]; then
    for v in "$OVMF_VARS_SRC" /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd \
             /usr/share/edk2/ovmf/OVMF_VARS.fd; do
        [ -f "$v" ] && { cp "$v" "$OUT/OVMF_VARS.4m.fd"; break; }
    done
fi

for tool in mformat mcopy sfdisk mkfs.vfat bsdtar truncate; do
    command -v "$tool" >/dev/null || { echo "missing host tool: $tool" >&2; exit 1; }
done

# The Alpine ISO the boot tree comes from: fetch it if absent, and verify the
# published sha256 — a self-builder must not need tribal knowledge about
# ~/Downloads (nor trust an unverified 1.5G download).
if [ ! -f "$ALPINE_ISO" ]; then
    iso_name="alpine-$ALPINE_FLAVOR-$ALPINE_PATCH-$ALPINE_ARCH.iso"
    iso_url="$ALPINE_MIRROR/v$ALPINE_VERSION/releases/$ALPINE_ARCH/$iso_name"
    echo "==> fetching $iso_name (~1.5G) + its published sha256"
    mkdir -p "$(dirname "$ALPINE_ISO")"
    curl -fL --retry 3 -o "$ALPINE_ISO.part" "$iso_url"
    curl -fsSL -o "$ALPINE_ISO.sha256" "$iso_url.sha256"
    ( cd "$(dirname "$ALPINE_ISO")" \
      && sed "s|$iso_name|$(basename "$ALPINE_ISO.part")|" "$ALPINE_ISO.sha256" | sha256sum -c - )
    mv "$ALPINE_ISO.part" "$ALPINE_ISO"
    rm -f "$ALPINE_ISO.sha256"
    echo "==> ISO verified: $ALPINE_ISO"
fi

echo "host setup OK"
