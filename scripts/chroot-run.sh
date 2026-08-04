#!/usr/bin/env bash
# Run a command inside the Alpine build chroot. Usage: chroot-run.sh [-u builder] "cmd..."
set -euo pipefail
. "$(dirname "$0")/../config.sh"

user=root
if [ "${1:-}" = "-u" ]; then user=$2; shift 2; fi

mountpoint -q "$CHROOT/proc" || { echo "chroot not mounted — run 10-mk-chroot.sh first" >&2; exit 1; }

if [ "$user" = root ]; then
    sudo chroot "$CHROOT" /bin/sh -lc "$*"
else
    sudo chroot "$CHROOT" /bin/su -l "$user" -c "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; $*"
fi
