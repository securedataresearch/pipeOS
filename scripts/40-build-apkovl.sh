#!/usr/bin/env bash
# Assemble out/pipeos.apkovl.tar.gz from overlay/etc plus generated bits:
# abuild pubkey, /etc/shadow with the default root password, runlevel symlinks.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

STAGE="$OUT/ovl"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "$PIPEOS_ROOT/overlay/etc" "$STAGE/etc"

# apk must trust our repo signing key at initramfs install time
ls "$OUT/keys/"*.rsa.pub >/dev/null 2>&1 || { echo "no abuild key — run 10-mk-chroot.sh" >&2; exit 1; }
cp "$OUT/keys/"*.rsa.pub "$STAGE/etc/apk/keys/"

# /etc/shadow: take the chroot's stock alpine-base shadow, set root's hash to
# the default password (provisioning forces a change on first login)
HASH=$(openssl passwd -6 "$DEFAULT_ROOT_PW")
sudo cat "$CHROOT/etc/shadow" \
    | awk -F: -v h="$HASH" 'BEGIN{OFS=":"} $1=="root"{$2=h} {print}' \
    > "$STAGE/etc/shadow"
chmod 600 "$STAGE/etc/shadow"

# runlevel symlinks (kept out of git; generated here)
mk_runlevel() {
    level=$1; shift
    mkdir -p "$STAGE/etc/runlevels/$level"
    for s in "$@"; do ln -sf "/etc/init.d/$s" "$STAGE/etc/runlevels/$level/$s"; done
}
mk_runlevel sysinit devfs dmesg mdev hwdrivers modloop
mk_runlevel boot     modules sysctl hostname bootmisc syslog networking hwclock seedrng
mk_runlevel default  crond chronyd sshd local
mk_runlevel shutdown killprocs mount-ro savecache

chmod +x "$STAGE/etc/local.d/"*.start "$STAGE/etc/local.d/"*.stop \
         "$STAGE/etc/periodic/15min/"* 2>/dev/null || true

tar -C "$STAGE" --numeric-owner --owner=0 --group=0 \
    -czf "$OUT/pipeos.apkovl.tar.gz" etc
ls -lh "$OUT/pipeos.apkovl.tar.gz"
