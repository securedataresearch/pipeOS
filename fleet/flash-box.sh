#!/bin/sh
# flash-box.sh <box> <device> <image> — write a pipeOS image to a box's USB
# stick, guarded so it can only touch that box's stick. Run on the operator
# host (the stick plugged in there), NOT on a box.
#
# The guard is the point: identity rides the stick, sticks get scrambled, and
# dd to the wrong device destroys another box's identity or the host's system
# disk. So this refuses unless <device>'s serial matches the box's serial in
# fleet/serials.txt. Proven on box3, 2026-08-09.
#
#   sudo sh fleet/flash-box.sh box0 /dev/sde out/pipeos-usb.img
set -e
BOX="$1"; DEV="$2"; IMG="$3"
SELF=$(dirname "$0")
[ -n "$BOX" ] && [ -n "$DEV" ] && [ -n "$IMG" ] || { echo "usage: flash-box.sh <box> <device> <image>" >&2; exit 1; }
[ -b "$DEV" ] || { echo "ABORT: $DEV is not a block device" >&2; exit 1; }
[ -f "$IMG" ] || { echo "ABORT: image $IMG not found" >&2; exit 1; }

WANT=$(awk -v b="$BOX" '$1==b{print $2}' "$SELF/serials.txt")
[ -n "$WANT" ] || { echo "ABORT: no serial for '$BOX' in serials.txt" >&2; exit 1; }
HAVE=$(lsblk -dno SERIAL "$DEV" 2>/dev/null)
case "$HAVE" in
    *"$WANT"*) : ;;
    *) echo "ABORT: $DEV serial '$HAVE' does not match $BOX ('$WANT') — refusing" >&2; exit 1 ;;
esac

echo "flashing $IMG -> $DEV ($BOX, serial matches $WANT)"
dd if="$IMG" of="$DEV" bs=4M conv=fsync
sync
blockdev --rereadpt "$DEV" 2>/dev/null || partprobe "$DEV" 2>/dev/null || true
echo "FLASH DONE — reinsert into $BOX's machine and boot, then run pipebox-setup"
