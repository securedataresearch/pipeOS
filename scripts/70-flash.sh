#!/usr/bin/env bash
# Flash out/pipeos.img to a real NVMe drive.
# Usage: 70-flash.sh /dev/nvmeXn1 --yes
set -euo pipefail
. "$(dirname "$0")/../config.sh"

DEV="${1:-}"; CONFIRM="${2:-}"
[ -n "$DEV" ] || { echo "usage: $0 /dev/nvmeXn1|/dev/sdX --yes" >&2; exit 1; }
[ -f "$IMG" ] || { echo "no image — run 50-build-image.sh" >&2; exit 1; }
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

echo "target device:"
lsblk -o NAME,MODEL,SERIAL,SIZE,TYPE,MOUNTPOINTS "$DEV"
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
