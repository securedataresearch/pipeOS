#!/usr/bin/env bash
# Assemble out/pipeos.apkovl.tar.gz from overlay/etc plus generated bits:
# abuild pubkey, /etc/shadow with the default root password, runlevel symlinks.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

STAGE="$OUT/ovl"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "$PIPEOS_ROOT/overlay/etc" "$STAGE/etc"
# the pipebox scripts (+usr/local is in lbu.list) and the agent's pipe policy
cp -a "$PIPEOS_ROOT/overlay/usr" "$STAGE/usr"
cp -a "$PIPEOS_ROOT/overlay/root" "$STAGE/root"
chmod 700 "$STAGE/root"

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

# /etc/passwd: root logs in on bash. bash ships in the image's own repo, so
# this can never dangle offline — the pairing matters: a bash root shell
# with no bash on the boot media locks every login out after a reboot.
awk -F: 'BEGIN{OFS=":"} $1=="root"{$7="/bin/bash"} {print}' \
    "$CHROOT/etc/passwd" > "$STAGE/etc/passwd"

# runlevel symlinks (kept out of git; generated here)
mk_runlevel() {
    level=$1; shift
    mkdir -p "$STAGE/etc/runlevels/$level"
    for s in "$@"; do ln -sf "/etc/init.d/$s" "$STAGE/etc/runlevels/$level/$s"; done
}
mk_runlevel sysinit devfs dmesg mdev hwdrivers modloop
mk_runlevel boot     modules sysctl hostname bootmisc syslog networking hwclock seedrng
mk_runlevel default  crond chronyd sshd local pipe-daemon pipebox-listener
mk_runlevel shutdown killprocs mount-ro savecache

chmod +x "$STAGE/etc/local.d/"*.start "$STAGE/etc/local.d/"*.stop \
         "$STAGE/etc/init.d/"* \
         "$STAGE/etc/periodic/15min/"* "$STAGE/etc/periodic/daily/"* \
         "$STAGE/etc/periodic/weekly/"* \
         "$STAGE/usr/local/bin/"* 2>/dev/null || true

tar -C "$STAGE" --numeric-owner --owner=0 --group=0 \
    -czf "$OUT/pipeos.apkovl.tar.gz" etc usr root
ls -lh "$OUT/pipeos.apkovl.tar.gz"
