#!/bin/sh
# pipeOS workspace: big, ext4, found by label (USB stick today, NVMe tomorrow).
# Everything bulky and mutable lives here instead of tmpfs or the vfat apkovl:
# git checkouts, agent memory/transcripts, logs, caches, backups.
# Device enumeration can lag boot (USB especially) — poll for the label
# instead of probing once. Never block boot on a missing disk; the daemons'
# start_pre waits too and the boot selfcheck reports the miss.
i=0
until dev=$(findfs LABEL=PIPEWORK 2>/dev/null); do
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
mountpoint -q /work || mount -t ext4 -o noatime "$dev" /work || exit 0
mkdir -p /work/repos /work/logs /work/cache /work/claude /work/pipebox /work/backup
# Agent memory belongs on ext4 from the box's FIRST boot (pipeOS#80): if
# /root/.claude/projects does not exist yet, lay the symlink before claude's
# first run can create a real tmpfs directory there — a box born migrated
# never loses a transcript. A real directory already present is a pre-fix box
# mid-life: leave it for the copy-first operator migration; selfcheck warns.
if [ ! -e /root/.claude/projects ] && [ ! -L /root/.claude/projects ]; then
	mkdir -p /work/claude/projects /root/.claude
	ln -s /work/claude/projects /root/.claude/projects
fi
