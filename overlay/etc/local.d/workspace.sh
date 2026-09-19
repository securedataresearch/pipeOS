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
		logger -s -t workspace "LABEL=PIPEWORK not found after 15s; /data unavailable"
		exit 0
	fi
	sleep 1
done
# /data is the name (pipeOS#219, docs/cluster.md §8); /work was, and stays a
# symlink for a release or two so nothing that still says /work breaks. Two
# cases: at boot (root is a fresh tmpfs) mount /data and link /work -> /data;
# on a live box between the deploy and its reboot /work is still the mount,
# so link /data -> /work instead — every path resolves either way. The label
# stays PIPEWORK: relabelling sticks in the field buys nothing the owner sees.
link() {
	# make $1 a symlink to $2 — unless $1 already is a link, or is a real
	# non-empty directory (never `ln` INTO a directory: that makes $1/$2)
	[ -L "$1" ] && return 0
	if [ -e "$1" ]; then rmdir "$1" 2>/dev/null || return 0; fi
	ln -s "$2" "$1"
}
if mountpoint -q /work 2>/dev/null && [ ! -L /data ] && ! mountpoint -q /data 2>/dev/null; then
	link /data /work
	logger -s -t workspace "/data -> /work until the next reboot (the volume mounts at /data from then on)"
else
	mkdir -p /data
# -t ext4 EXPLICITLY: busybox mount auto-detection misread this ext4
# partition as FAT on the first customer boot (kernel: "FAT-fs (sda2): utf8
# is not a recommended IO charset") and failed — /data then never mounted
# and every /data-dependent service stayed down. Measured on the pilot box,
# 2026-08-16. PIPEWORK is always ext4 (grow.sh makes it); say so.
# commit=120,lazytime: the stick is /data on these Machines — a journal flush
# every 5 s and an mtime write per touch are heat (pipeos-work's header). The
# hot set is in RAM anyway; this is for what stays on disk.
	mountpoint -q /data || mount -t ext4 -o noatime,lazytime,commit=120 "$dev" /data || exit 0
	link /work /data
fi
mkdir -p /data/repos /data/logs /data/cache /data/claude /data/pipebox /data/backup /data/home
# Agent memory belongs on ext4 from the box's FIRST boot (pipeOS#80): if
# /root/.claude/projects does not exist yet, lay the symlink before claude's
# first run can create a real tmpfs directory there — a box born migrated
# never loses a transcript. A real directory already present is a pre-fix box
# mid-life: leave it for the copy-first operator migration; selfcheck warns.
if [ ! -e /root/.claude/projects ] && [ ! -L /root/.claude/projects ]; then
	mkdir -p /data/claude/projects /root/.claude
	ln -s /data/claude/projects /root/.claude/projects
fi
