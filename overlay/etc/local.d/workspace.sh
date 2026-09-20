#!/bin/sh
# pipeOS workspace: big, ext4, found by label (USB stick today, NVMe tomorrow).
# Everything bulky and mutable lives here instead of tmpfs or the vfat apkovl:
# git checkouts, agent memory/transcripts, logs, caches, backups.
# Device enumeration can lag boot (USB especially) — poll for the label
# instead of probing once. Never block boot on a missing disk; the daemons'
# start_pre waits too and the boot selfcheck reports the miss.
# Seams (the probe, never production): PIPEOS_WORKSPACE_WORK, _DATA, _NO_MOUNT.
WORK=${PIPEOS_WORKSPACE_WORK:-/work}
DATA=${PIPEOS_WORKSPACE_DATA:-/data}

# `/data` is the volume's name (pipeOS#219/#330, docs/cluster.md §8). Step 1
# made the name work with /data a symlink to the /work mount. THIS is the
# flip: the volume mounts at /data and /work becomes the symlink.
#
# Both directions stay legal, because a live box meets this script twice in
# the wrong order: `deploy-overlay` installs it and restarts services while
# the volume is STILL mounted at /work, and only the next boot mounts it at
# /data. So: whichever name the volume is already mounted on is the mount,
# and the other name is linked to it. Only a box with nothing mounted yet —
# a boot — takes the new direction. Every path resolves either way, which is
# what lets the deploy and the reboot happen minutes or days apart.
#
# A real directory at the link's name is not ours: empty, it is replaced;
# with content in it, it is left alone and said, because moving an unknown
# directory is not this script's call.
link_to() {
	# link_to TARGET LINK — lay LINK -> TARGET, idempotently.
	_t=$1 _l=$2
	if [ -L "$_l" ] && [ "$(readlink "$_l")" = "$_t" ]; then
		return 0
	elif [ -L "$_l" ]; then
		# a link, but not to the volume (a dangling one, or somebody's own):
		# ours to repoint — the name belongs to the volume, and leaving it
		# would make every boot and every deploy a no-op while selfcheck
		# asked for one
		logger -s -t workspace "$_l pointed at $(readlink "$_l") — repointing it at the volume ($_t)"
		rm -f "$_l" && ln -s "$_t" "$_l"
	elif [ ! -e "$_l" ]; then
		ln -s "$_t" "$_l"
	elif [ -d "$_l" ] && rmdir "$_l" 2>/dev/null; then
		ln -s "$_t" "$_l"
	else
		logger -s -t workspace "$_l exists and is not a link to $_t — the volume is $_t; move what is in $_l and remove it, then this link is laid at the next boot"
	fi
}

# Which name is the volume on right now? A symlink is never the mount itself
# — and it has to be asked first, because `mountpoint` follows the link into
# the other name and would call both of them mounted.
mounted_on() {
	[ -L "$1" ] && return 1
	mountpoint -q "$1" 2>/dev/null
}

i=0
until dev=$(findfs LABEL=PIPEWORK 2>/dev/null) || [ -n "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ]; do
	i=$((i+1))
	if [ "$i" -ge 15 ]; then
		logger -s -t workspace "LABEL=PIPEWORK not found after 15s; $DATA unavailable"
		exit 0
	fi
	sleep 1
done

# The mount point for THIS run: wherever the volume already is, else /data.
MOUNT=$DATA
LINK=$WORK
if mounted_on "$WORK"; then
	# pre-flip box, between the deploy and its reboot: the volume is on
	# /work and nothing may move it out from under a running daemon.
	MOUNT=$WORK
	LINK=$DATA
fi

mkdir -p "$MOUNT"
# -t ext4 EXPLICITLY: busybox mount auto-detection misread this ext4
# partition as FAT on the first customer boot (kernel: "FAT-fs (sda2): utf8
# is not a recommended IO charset") and failed — the volume then never
# mounted and every service that depends on it stayed down. Measured on the
# pilot box, 2026-08-16. PIPEWORK is always ext4 (grow.sh makes it); say so.
# commit=120,lazytime: the stick is the volume on these Machines — a journal
# flush every 5 s and an mtime write per touch are heat (pipeos-work's
# header). The hot set is in RAM anyway; this is for what stays on disk.
if [ -z "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ]; then
	mountpoint -q "$MOUNT" || mount -t ext4 -o noatime,lazytime,commit=120 "$dev" "$MOUNT" || exit 0
fi
mkdir -p "$MOUNT"/repos "$MOUNT"/logs "$MOUNT"/cache "$MOUNT"/claude "$MOUNT"/pipebox "$MOUNT"/backup "$MOUNT"/home
link_to "$MOUNT" "$LINK"

# The paths that were written down while the volume was /work — stored job
# cwds, the ledger's cursor keys, claude's trust and project dirs — are
# rewritten here, once, after the flip (pipeOS#330). Idempotent; a box that
# has never been /work-mounted finds nothing to do.
if [ -z "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ] && [ "$MOUNT" = "$DATA" ] && [ -x /usr/local/bin/pipeos-data-migrate ]; then
	PIPEOS_MIGRATE_FROM="$WORK" PIPEOS_MIGRATE_TO="$DATA" /usr/local/bin/pipeos-data-migrate || \
		logger -s -t workspace "pipeos-data-migrate reported a problem — see its output"
fi

# Agent memory belongs on ext4 from the box's FIRST boot (pipeOS#80): if
# /root/.claude/projects does not exist yet, lay the symlink before claude's
# first run can create a real tmpfs directory there — a box born migrated
# never loses a transcript. A real directory already present is a pre-fix box
# mid-life: leave it for the copy-first operator migration; selfcheck warns.
if [ -z "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ] && [ ! -e /root/.claude/projects ] && [ ! -L /root/.claude/projects ]; then
	mkdir -p "$MOUNT"/claude/projects /root/.claude
	ln -s "$MOUNT"/claude/projects /root/.claude/projects
fi
