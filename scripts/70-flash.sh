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
if lsblk -nro MOUNTPOINTS "$DEV" | grep -q .; then
    echo "refusing: $DEV (or a partition on it) is mounted:" >&2
    lsblk "$DEV" >&2
    exit 1
fi
ROOTDISK=$(lsblk -nro PKNAME "$(findmnt -nro SOURCE /)" 2>/dev/null || true)
if [ -n "$ROOTDISK" ] && [ "/dev/$ROOTDISK" = "$DEV" ]; then
    echo "refusing: $DEV holds the host root filesystem" >&2
    exit 1
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
