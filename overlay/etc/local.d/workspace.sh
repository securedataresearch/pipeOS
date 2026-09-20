#!/bin/sh
# pipeOS workspace: big, ext4, found by label (USB stick today, NVMe tomorrow).
# Everything bulky and mutable lives here instead of tmpfs or the vfat apkovl:
# git checkouts, agent memory/transcripts, logs, caches, backups.
# Device enumeration can lag boot (USB especially) — poll for the label
# instead of probing once. Never block boot on a missing disk; the daemons'
# start_pre waits too and the boot selfcheck reports the miss.
# Seams (the probe, never production): PIPEOS_WORKSPACE_DATA, _NO_MOUNT.
#
# The volume is /data (pipeOS#219, #330, docs/cluster.md §8). One name, and
# the name it replaced is not referenced anywhere in this tree: a second name
# kept "for a release or two" is a name kept for ever, and every reader then
# has to handle both.
#
# The filesystem LABEL stays PIPEWORK: a label is not a path, the owner never
# sees it, and relabelling sticks in the field could only lose a volume.
DATA=${PIPEOS_WORKSPACE_DATA:-/data}
i=0
until dev=$(findfs LABEL=PIPEWORK 2>/dev/null) || [ -n "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ]; do
	i=$((i+1))
	if [ "$i" -ge 15 ]; then
		logger -s -t workspace "LABEL=PIPEWORK not found after 15s; $DATA unavailable"
		exit 0
	fi
	sleep 1
done
mkdir -p "$DATA"
# -t ext4 EXPLICITLY: busybox mount auto-detection misread this ext4
# partition as FAT on the first customer boot (kernel: "FAT-fs (sda2): utf8
# is not a recommended IO charset") and failed — the volume then never
# mounted and every service that depends on it stayed down. Measured on the
# pilot box, 2026-08-16. PIPEWORK is always ext4 (grow.sh makes it); say so.
# commit=120,lazytime: the stick is the volume on these Machines — a journal
# flush every 5 s and an mtime write per touch are heat (pipeos-work's
# header). The hot set is in RAM anyway; this is for what stays on disk.
if [ -z "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ]; then
	mountpoint -q "$DATA" || mount -t ext4 -o noatime,lazytime,commit=120 "$dev" "$DATA" || exit 0
fi
mkdir -p "$DATA"/repos "$DATA"/logs "$DATA"/cache "$DATA"/claude "$DATA"/pipebox "$DATA"/backup "$DATA"/home
# Agent memory belongs on ext4 from the box's FIRST boot (pipeOS#80): if
# /root/.claude/projects does not exist yet, lay the symlink before claude's
# first run can create a real tmpfs directory there — a box born migrated
# never loses a transcript. A real directory already present is a pre-fix box
# mid-life: leave it for the copy-first operator migration; selfcheck warns.
if [ -z "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ] && [ ! -e /root/.claude/projects ] && [ ! -L /root/.claude/projects ]; then
	mkdir -p "$DATA"/claude/projects /root/.claude
	ln -s "$DATA"/claude/projects /root/.claude/projects
fi
