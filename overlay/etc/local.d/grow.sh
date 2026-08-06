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

# The geometry repair, CHECKED. This used to end in `2>/dev/null || true`, so a
# failed relocate was discarded and the partition write proceeded anyway —
# against a GPT that still under-reports the device.
#
# That is not hypothetical: .63 has a valid GPT whose last-lba is 7372766 on a
# disk of 60604415 sectors, i.e. a header that believes a 31GB stick is 3.5GB.
# `sfdisk -F` there reports 0 B free. Carving against that header is the one
# ordering nobody would choose deliberately, and the old line chose it silently
# whenever the repair failed.
#
# A relocate that fails now aborts. Leaving the disk untouched is always a
# recoverable outcome; writing a partition table against a header we could not
# fix is not.
# Only GPT disks have a backup header to relocate, so ask what the label is
# first: aborting on a *dos* disk would be a regression, since `--relocate
# gpt-bak-std` legitimately does not apply there. "Not applicable" and "the
# repair failed" are different answers and only the second is a reason to stop.
label=$(sfdisk -d "$disk" 2>/dev/null | sed -n 's/^label: *//p')
if [ "$label" = "gpt" ]; then
    if ! sfdisk --relocate gpt-bak-std "$disk"; then
        echo "pipeos: could not relocate the backup GPT on $disk — not carving" >&2
        exit 0
    fi
fi
echo ',+,L,-' | sfdisk -a --no-reread "$disk" || exit 0
blockdev --rereadpt "$disk" 2>/dev/null || partx -a "$disk" 2>/dev/null || true
[ -b "$part2" ] || exit 0
mkfs.ext4 -q -L PIPEWORK "$part2" && echo "pipeos: PIPEWORK ready on $part2"
