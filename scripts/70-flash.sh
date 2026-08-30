#!/usr/bin/env bash
# The ONE flasher (appliance plan, decision 3). Writes a pipeOS image to a
# whole disk with every guard this repo has: whole-disk-only, nothing-mounted,
# not-the-root-disk, typed confirmation — and, with --box, the fleet serial
# guard that used to live only in fleet/flash-box.sh. Two flashers with
# disjoint safety sets meant the operator picked their protections by picking
# a script; fleet/flash-box.sh is now a wrapper over this one.
#
# Usage: 70-flash.sh [--box NAME] [--image PATH] /dev/nvmeXn1|/dev/sdX [--yes]
#   --box NAME    require the device's serial to match NAME's line in
#                 fleet/serials.txt; an unregistered box offers to register
#                 the serial (interactive only) instead of pushing the
#                 operator onto an unguarded path.
#   --image PATH  flash PATH instead of the VARIANT-derived $IMG (e.g. a
#                 `make stick` image, out/pipeos-usb-box4.img).
set -euo pipefail
. "$(dirname "$0")/../config.sh"

DEV=""; CONFIRM=""; BOX=""
while [ $# -gt 0 ]; do
    case "$1" in
        --box)   BOX="${2:?--box needs a name}"; shift 2 ;;
        --image) IMG="${2:?--image needs a path}"; shift 2 ;;
        --yes)   CONFIRM="--yes"; shift ;;
        -*) echo "usage: $0 [--box NAME] [--image PATH] /dev/sdX [--yes]" >&2; exit 1 ;;
        *)  DEV="$1"; shift ;;
    esac
done
[ -n "$DEV" ] || { echo "usage: $0 [--box NAME] [--image PATH] /dev/sdX [--yes]" >&2; exit 1; }
[ -f "$IMG" ] || { echo "no image at $IMG — run 50-build-image.sh (or pass --image)" >&2; exit 1; }
[ -b "$DEV" ] || { echo "$DEV is not a block device" >&2; exit 1; }

# whole-disk targets only: NVMe namespaces or sd disks (USB sticks, SATA)
case "$DEV" in
    /dev/nvme*n[0-9]) ;;
    /dev/sd[a-z]) ;;
    *) echo "refusing: $DEV is not a whole disk (/dev/nvmeXn1 or /dev/sdX)" >&2; exit 1 ;;
esac

# refuse anything mounted or holding the host root
#
# This was `lsblk -nro MOUNTPOINTS "$DEV" | grep -q .`, which fails open in the
# worst possible place: lsblk is not installed on pipeOS, so the pipeline
# produced no output, grep found nothing, and "nothing is mounted" was the
# answer for every device. On this very box /media/usb — the boot media — is
# sda1, and the guard that exists to stop you flashing it was answering "go
# ahead". /proc/mounts needs no package and cannot be absent.
mounted_nodes() {
    if command -v lsblk >/dev/null 2>&1; then
        lsblk -nro MOUNTPOINTS "$1" 2>/dev/null | grep . && return 0
        return 1
    fi
    # The device itself or anything whose sysfs parent is the device.
    node=$(basename "$1")
    awk -v dev="$1" -v node="$node" '
        $1 == dev { print $1 " on " $2; hit = 1; next }
        $1 ~ "^" dev { print $1 " on " $2; hit = 1 }
        END { exit !hit }
    ' /proc/mounts 2>/dev/null
}
if MOUNTS=$(mounted_nodes "$DEV"); then
    echo "refusing: $DEV (or a partition on it) is mounted:" >&2
    echo "$MOUNTS" >&2
    exit 1
fi
# A guard that cannot evaluate must REFUSE, not pass. This used to be
# `|| true` with an `[ -n "$ROOTDISK" ]` test, so an empty result read as
# "not the root disk" — and empty is exactly what you get for an LVM/dm/md
# root, a whole-disk root with no partition table, or a tmpfs root. On pipeOS
# itself neither lsblk nor findmnt is installed at all, so `|| true` swallowed
# the failure entirely and this check passed on every device. It failed open
# on precisely the configurations where being wrong destroys the host.
#
# Refusing when the tools are missing is only half a fix, and on its own it is
# a different bug: neither findmnt nor lsblk is installed on pipeOS, so a bare
# "refuse if findmnt fails" would refuse on every pipeOS box — the platform
# this script exists to flash from. So each lookup has a no-package fallback,
# and only a genuinely unanswerable question refuses.
root_source() {
    if command -v findmnt >/dev/null 2>&1; then
        findmnt -nro SOURCE / 2>/dev/null
        return
    fi
    # /proc/mounts is the same kernel truth findmnt reads and needs nothing
    # installed. Last matching line wins: a later mount of / shadows an
    # earlier one, which is what the kernel means by the same ordering.
    awk '$2 == "/" { src = $1 } END { if (src == "") exit 1; print src }' \
        /proc/mounts 2>/dev/null
}

# The disk behind a partition, without lsblk. /sys is authoritative: a
# partition's parent is simply its parent directory in sysfs.
parent_disk() {
    if command -v lsblk >/dev/null 2>&1; then
        lsblk -nro PKNAME "$1" 2>/dev/null && return
    fi
    node=$(basename "$1")
    for d in /sys/block/*/"$node"; do
        [ -d "$d" ] || continue
        basename "$(dirname "$d")"
        return
    done
    return 1
}

ROOTSRC=$(root_source) || ROOTSRC=""
if [ -z "$ROOTSRC" ]; then
    echo "refusing: cannot determine what backs / — neither findmnt nor" >&2
    echo "          /proc/mounts answered. Refusing rather than guessing." >&2
    exit 1
fi
case "$ROOTSRC" in
    /dev/*) ;;
    # tmpfs, overlay, an NFS root: / is not on a block device, so no disk
    # here holds it. That is a determinate answer, not a failed check — and
    # it is the normal answer on pipeOS, whose root is tmpfs.
    *) ROOTSRC="" ;;
esac
if [ -n "$ROOTSRC" ]; then
    ROOTDISK=$(parent_disk "$ROOTSRC") || ROOTDISK=""
    # An empty parent means the root IS a whole disk (no partition table), so
    # compare the source itself before concluding anything.
    [ -n "$ROOTDISK" ] || ROOTDISK=$(basename "$ROOTSRC")
    if [ -z "$ROOTDISK" ]; then
        echo "refusing: could not resolve which disk holds $ROOTSRC" >&2
        exit 1
    fi
    if [ "/dev/$ROOTDISK" = "$DEV" ] || [ "$ROOTSRC" = "$DEV" ]; then
        echo "refusing: $DEV holds the host root filesystem" >&2
        exit 1
    fi
fi

# ---- the fleet serial guard (--box). Identity rides the stick, sticks get
# scrambled, and dd to the wrong stick destroys another box's identity — so
# with --box the device's serial must match fleet/serials.txt. The guard
# FAILS CLOSED: no readable serial refuses rather than proceeding (the old
# lsblk-only lookup would have answered "no serial" on pipeOS, where lsblk
# is not installed). An unregistered box gets an interactive offer to
# register the serial here, instead of being pushed onto a guardless path.
dev_serial() {
    local s
    if command -v lsblk >/dev/null 2>&1; then
        s=$(lsblk -dno SERIAL "$1" 2>/dev/null | head -1)
        [ -n "$s" ] && { echo "$s"; return 0; }
    fi
    if command -v udevadm >/dev/null 2>&1; then
        s=$(udevadm info --query=property --name="$1" 2>/dev/null \
            | sed -n 's/^ID_SERIAL_SHORT=//p' | head -1)
        [ -n "$s" ] && { echo "$s"; return 0; }
    fi
    # sysfs: usb-storage exposes the serial on the device's parent chain
    s=$(cat "/sys/block/$(basename "$1")/device/serial" 2>/dev/null || true)
    [ -n "$s" ] && { echo "$s"; return 0; }
    return 1
}
if [ -n "$BOX" ]; then
    SERIALS="$PIPEOS_ROOT/fleet/serials.txt"
    HAVE=$(dev_serial "$DEV") || {
        echo "refusing: --box given but no serial is readable for $DEV —" >&2
        echo "          the serial guard cannot evaluate, so it refuses." >&2
        exit 1
    }
    WANT=$(awk -v b="$BOX" '$1==b{print $2}' "$SERIALS" 2>/dev/null || true)
    if [ -n "$WANT" ]; then
        case "$HAVE" in
            *"$WANT"*) echo "serial guard: $DEV matches $BOX ($WANT)" ;;
            *) echo "ABORT: $DEV serial '$HAVE' does not match $BOX ('$WANT') — refusing" >&2
               exit 1 ;;
        esac
    else
        echo "no serial registered for '$BOX' in fleet/serials.txt"
        echo "this device's serial: $HAVE"
        if [ "$CONFIRM" = "--yes" ]; then
            echo "refusing to register a stick non-interactively — add the line" >&2
            echo "  $BOX $HAVE" >&2
            echo "to fleet/serials.txt yourself, commit it, and re-run." >&2
            exit 1
        fi
        printf "register '%s %s' and continue? Type the box name to confirm: " "$BOX" "$HAVE"
        read -r ans
        [ "$ans" = "$BOX" ] || { echo "aborted"; exit 1; }
        printf '%s %s\n' "$BOX" "$HAVE" >> "$SERIALS"
        echo "registered — remember to commit fleet/serials.txt"
    fi
fi

echo "target device:"
# display only — but under `set -e` a missing lsblk (pipeOS) would kill the
# script here, after every guard passed; fall back rather than die
lsblk -o NAME,MODEL,SERIAL,SIZE,TYPE,MOUNTPOINTS "$DEV" 2>/dev/null \
    || { echo "$DEV ($(blockdev --getsize64 "$DEV" 2>/dev/null || echo '?') bytes)"; }
echo
if [ "$CONFIRM" != "--yes" ]; then
    printf 'This will DESTROY all data on %s. Type the device path to confirm: ' "$DEV"
    read -r ans
    [ "$ans" = "$DEV" ] || { echo "aborted"; exit 1; }
fi

sudo dd if="$IMG" of="$DEV" bs=4M oflag=direct conv=fsync status=progress
sync
echo "flashed. Boot the machine in UEFI mode with Secure Boot disabled."
echo "Default login: root / $DEFAULT_ROOT_PW (ssh enabled; first login provisions)."
