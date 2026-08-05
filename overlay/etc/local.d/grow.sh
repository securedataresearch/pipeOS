#!/bin/sh
# pipeOS first-boot grow: claim everything after the boot partition as the
# ext4 PIPEWORK workspace. Runs before 01-workspace.start (alphabetical).
#
# Gated hard — all of these must hold or it exits untouched:
#   - a PIPEOS-labelled boot partition exists and is partition 1
#   - no PIPEWORK label exists anywhere
#   - the boot disk has no partition 2
# Safe to leave enabled forever; on an already-grown disk it is a no-op.

dev=$(findfs LABEL=PIPEOS 2>/dev/null) || exit 0
case "$dev" in
    *p1) disk="${dev%p1}"; part2="${disk}p2" ;;   # /dev/nvme0n1p1, /dev/mmcblk0p1
    *1)  disk="${dev%1}";  part2="${disk}2"  ;;   # /dev/sda1
    *)   exit 0 ;;
esac

findfs LABEL=PIPEWORK >/dev/null 2>&1 && exit 0
[ -b "$part2" ] && exit 0
command -v sfdisk >/dev/null 2>&1 || exit 0
command -v mkfs.ext4 >/dev/null 2>&1 || exit 0

echo "pipeos: claiming remaining space on $disk for PIPEWORK"
sfdisk --relocate gpt-bak-std "$disk" 2>/dev/null || true
echo ',+,L,-' | sfdisk -a --no-reread "$disk" || exit 0
blockdev --rereadpt "$disk" 2>/dev/null || partx -a "$disk" 2>/dev/null || true
[ -b "$part2" ] || exit 0
mkfs.ext4 -q -L PIPEWORK "$part2" && echo "pipeos: PIPEWORK ready on $part2"
