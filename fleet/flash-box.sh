#!/bin/sh
# flash-box.sh <box> <device> <image> — compatibility wrapper. The one
# flasher is scripts/70-flash.sh, which now carries ALL the guards: serial
# (from serials.txt, previously this script's only check), plus the mount,
# root-disk, whole-disk and typed-confirmation guards this script never had.
# Two flashers with disjoint safety sets meant the operator picked their
# protections by picking a script; this keeps the old calling convention and
# routes it through the guarded path.
#
#   sudo sh fleet/flash-box.sh box0 /dev/sde out/pipeos-usb-box0.img [--yes]
set -e
BOX="$1"; DEV="$2"; IMG="$3"
[ -n "$BOX" ] && [ -n "$DEV" ] && [ -n "$IMG" ] || {
    echo "usage: flash-box.sh <box> <device> <image> [--yes]" >&2; exit 1; }
shift 3
exec bash "$(dirname "$0")/../scripts/70-flash.sh" \
    --box "$BOX" --image "$IMG" "$DEV" "$@"
