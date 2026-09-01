#!/usr/bin/env bash
# Assemble the flashable image out/pipeos.img: GPT + one FAT32 ESP holding the
# Alpine ISO tree, our grub.cfg, apkovl, local apk repo, and apk cache.
# Entirely unprivileged: sfdisk + mkfs.vfat + mtools, no loop mounts.
set -euo pipefail
shopt -s dotglob nullglob
. "$(dirname "$0")/../config.sh"

for f in "$OUT/pipeos.apkovl.tar.gz" "$OUT/repo/pipeos/$ALPINE_ARCH/APKINDEX.tar.gz"; do
    [ -f "$f" ] || { echo "missing $f — run earlier stages" >&2; exit 1; }
done

# --- repo gates: staleness (image does not depend on the slow apks target,
# so refuse to ship an out/repo older than its sources) and coherence (the
# 2026-08-05 media incident was an incoherent repo shipped unchecked).
# FORCE=1 skips the staleness gate only.
if [ "${FORCE:-0}" != 1 ]; then
    stale=$(find "$PIPEOS_ROOT/aports" "$OUT/payloads" -type f \
        -newer "$OUT/repo/pipeos/$ALPINE_ARCH/APKINDEX.tar.gz" 2>/dev/null | head -n1)
    [ -n "$stale" ] && { echo "out/repo is older than $stale — run 'make apks' first (or FORCE=1)" >&2; exit 1; }
fi
"$(dirname "$0")/verify-repo.sh" "$OUT/repo/pipeos/$ALPINE_ARCH" "$OUT/repo/extra/$ALPINE_ARCH"

# --- extract ISO tree once per flavor
ISOTREE="$OUT/isotree-$ALPINE_FLAVOR"
if [ ! -d "$ISOTREE/boot" ]; then
    echo "==> extracting $ALPINE_ISO"
    [ -f "$ALPINE_ISO" ] || { echo "missing $ALPINE_ISO" >&2; exit 1; }
    rm -rf "$ISOTREE"; mkdir -p "$ISOTREE"
    bsdtar -xf "$ALPINE_ISO" -C "$ISOTREE"
    chmod -R u+w "$ISOTREE"
fi

# --- grub.cfg, shaped by the variant
MODULES=loop,squashfs,sd-mod,usb-storage,vfat,nvme
UCODE_INITRDS=""
for u in intel-ucode.img amd-ucode.img; do
    [ -f "$ISOTREE/boot/$u" ] && UCODE_INITRDS="$UCODE_INITRDS /boot/$u"
done
K="vmlinuz-$KERNEL_FLAVOR"
I="initramfs-$KERNEL_FLAVOR"
ML="modloop=/boot/modloop-$KERNEL_FLAVOR"
if [ "$VARIANT" = vm ]; then
    # qemu -nographic: everything over the emulated UART.
    cat > "$ISOTREE/boot/grub/grub.cfg" <<EOF
set timeout=1
set default=0
serial --unit=0 --speed=115200
terminal_input console serial
terminal_output console serial

menuentry "pipeOS (diskless Alpine, $VARIANT)" {
    linux /boot/$K modules=$MODULES $ML console=tty0 console=ttyS0,115200 quiet
    initrd$UCODE_INITRDS /boot/$I
}
EOF
else
    # Real hardware: no grub `serial` setup (it can hang firmware with no
    # UART), no `quiet` (a silent failure on a screen is undebuggable),
    # tty0 LAST so /dev/console is the panel while ttyS0 still mirrors for
    # qemu smoke tests. waitusb rides out slow stick enumeration (usb
    # variant only); the nomodeset entry is the escape hatch for KMS
    # blanking the panel.
    WAITUSB=""
    [ "$VARIANT" = usb ] && WAITUSB="waitusb=3 "
    # usbcore.autosuspend=-1: USB autosuspend eats keystrokes on real
    # consoles (login typed as "pipo"/"pies" on the ThinkCentre — first
    # keys after a pause vanish while the keyboard wakes). loglevel=4:
    # verbose boot, but stop the kernel spraying over the login prompt
    # once up.
    HWARGS="usbcore.autosuspend=-1 loglevel=4"
    cat > "$ISOTREE/boot/grub/grub.cfg" <<EOF
set timeout=3
set default=0

menuentry "pipeOS (diskless Alpine, $VARIANT)" {
    linux /boot/$K modules=$MODULES $ML ${WAITUSB}$HWARGS console=ttyS0,115200 console=tty0
    initrd$UCODE_INITRDS /boot/$I
}
menuentry "pipeOS (safe graphics: nomodeset)" {
    linux /boot/$K modules=$MODULES $ML ${WAITUSB}$HWARGS nomodeset console=ttyS0,115200 console=tty0
    initrd$UCODE_INITRDS /boot/$I
}
EOF
fi

# --- partition table
echo "==> writing GPT to $IMG"
rm -f "$IMG"
truncate -s "${IMG_SIZE_MB}M" "$IMG"
P1_SIZE_MB=$(( IMG_SIZE_MB - PART_OFFSET_MB - 1 ))   # leave room for backup GPT
sfdisk --quiet "$IMG" <<EOF
label: gpt
start=$(( PART_OFFSET_MB * 2048 )), size=$(( P1_SIZE_MB * 2048 )), type=uefi, name="PIPEOS"
EOF

# --- FAT32 partition as a standalone file
P1="$OUT/p1.img"
rm -f "$P1"
truncate -s "${P1_SIZE_MB}M" "$P1"
mkfs.vfat -F32 -n PIPEOS "$P1" >/dev/null

M="mmd -i $P1"
MC() { mcopy -i "$P1" -Q -o "$@"; }
export MTOOLS_SKIP_CHECK=1

echo "==> copying ISO tree"
MC -s "$ISOTREE"/* ::/

echo "==> copying apkovl, repo, cache"
MC "$OUT/pipeos.apkovl.tar.gz" ::/pipeos.apkovl.tar.gz
MC -s "$OUT/repo/pipeos" ::/apks/pipeos
MC -s "$OUT/repo/extra" ::/apks/extra
mmd -i "$P1" ::/cache 2>/dev/null || true
# .boot_repository markers let the initramfs discover these repos on ANY boot
# device (USB/NVMe/SATA) without depending on hardcoded media paths
: > "$OUT/.boot_repository"
MC "$OUT/.boot_repository" ::/apks/pipeos/.boot_repository
MC "$OUT/.boot_repository" ::/apks/extra/.boot_repository

# The image describes itself (#179): what a box is running, and the p1
# geometry an on-box flasher must match before it writes. Read by
# `pipeos status` and by pipeos-flash; harmless to everything older.
{
    echo "variant=$VARIANT"
    echo "commit=$(git -C "$PIPEOS_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "p1_start_sectors=$((PART_OFFSET_MB * 2048))"
    echo "p1_size_sectors=$((P1_SIZE_MB * 2048))"
    echo "with_antigravity=$WITH_ANTIGRAVITY"
} > "$OUT/pipeos-image.txt"
MC "$OUT/pipeos-image.txt" ::/pipeos-image.txt

echo "==> merging partition into image"
dd if="$P1" of="$IMG" bs=1M seek=$PART_OFFSET_MB conv=notrunc,sparse status=none
rm -f "$P1"   # merged into $IMG; keeping it double-books P1_SIZE_MB of scratch
ls -lh "$IMG"
echo "image ready: $IMG"
