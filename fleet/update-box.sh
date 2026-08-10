#!/bin/sh
# update-box.sh — apply the built pipeos repo to THIS box's boot media, the
# sanctioned Path B sequence (docs/fleet-update-runbook.md). Runs on the box.
# Requires the built repo at /work/repos/pipeOS/out/repo/pipeos (a build box,
# or one that fetched it). This is what took the fleet 0.41.15 -> 0.41.31 by
# hand on 2026-08-09; kept here so that update is one command, not improvised.
#
# For a box updating from a remote publisher instead, use `pipeos selfupdate`
# (overlay/usr/local/bin/pipeos-selfupdate) — this script is the local-repo
# path used on the build box itself.
set -e
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
SRC=/work/repos/pipeOS/out/repo/pipeos/x86_64
[ -f "$SRC/APKINDEX.tar.gz" ] || { echo "no built repo at $SRC" >&2; exit 1; }

echo "== sync media (verified, atomic, self-reverting) =="
pipeos sync-media
echo "== upgrade + persist =="
apk update >/dev/null 2>&1
apk upgrade -a 2>&1 | tail -2
pipeos save
echo "== verify =="
pipe --version
pipeos verify 2>&1 | tail -1
echo "update-box: done (reboot to confirm criterion 3)"
