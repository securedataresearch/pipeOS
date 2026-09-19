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
i=0
until dev=$(findfs LABEL=PIPEWORK 2>/dev/null) || [ -n "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ]; do
	i=$((i+1))
	if [ "$i" -ge 15 ]; then
		logger -s -t workspace "LABEL=PIPEWORK not found after 15s; /work unavailable"
		exit 0
	fi
	sleep 1
done
mkdir -p /work
# -t ext4 EXPLICITLY: busybox mount auto-detection misread this ext4
# partition as FAT on the first customer boot (kernel: "FAT-fs (sda2): utf8
# is not a recommended IO charset") and failed — /work then never mounted
# and every /work-dependent service stayed down. Measured on the pilot box,
# 2026-08-16. PIPEWORK is always ext4 (grow.sh makes it); say so.
# commit=120,lazytime: the stick is /work on these Machines — a journal flush
# every 5 s and an mtime write per touch are heat (pipeos-work's header). The
# hot set is in RAM anyway; this is for what stays on disk.
if [ -z "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ]; then
	mountpoint -q "$WORK" || mount -t ext4 -o noatime,lazytime,commit=120 "$dev" "$WORK" || exit 0
fi
mkdir -p "$WORK"/repos "$WORK"/logs "$WORK"/cache "$WORK"/claude "$WORK"/pipebox "$WORK"/backup "$WORK"/home
# `/data` is the name the bulk volume is getting (pipeOS#219, docs/cluster.md
# §8). This release makes the NAME work — the volume still mounts at /work and
# /data is a symlink to it, so both paths reach the same bytes on every box,
# old and new, with nothing moved and no reboot needed. A later release flips
# which of the two is the mount; by then every box already answers to both.
# A real directory at /data is not ours (something wrote there before the link
# existed): empty, it is replaced; with content in it, it is left alone and
# said, because moving an unknown directory is not this script's call.
if [ -L "$DATA" ]; then
	:
elif [ ! -e "$DATA" ]; then
	ln -s "$WORK" "$DATA"
elif [ -d "$DATA" ] && rmdir "$DATA" 2>/dev/null; then
	ln -s "$WORK" "$DATA"
else
	logger -s -t workspace "$DATA exists and is not a link to $WORK — the volume is $WORK; move what is in $DATA and remove it, then this link is laid at the next boot"
fi
# Agent memory belongs on ext4 from the box's FIRST boot (pipeOS#80): if
# /root/.claude/projects does not exist yet, lay the symlink before claude's
# first run can create a real tmpfs directory there — a box born migrated
# never loses a transcript. A real directory already present is a pre-fix box
# mid-life: leave it for the copy-first operator migration; selfcheck warns.
if [ -z "${PIPEOS_WORKSPACE_NO_MOUNT:-}" ] && [ ! -e /root/.claude/projects ] && [ ! -L /root/.claude/projects ]; then
	mkdir -p "$WORK"/claude/projects /root/.claude
	ln -s "$WORK"/claude/projects /root/.claude/projects
fi
