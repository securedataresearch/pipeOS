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
mountpoint -q /work || mount -o noatime "$dev" /work || exit 0
mkdir -p /work/repos /work/logs /work/cache /work/claude /work/pipebox /work/backup
