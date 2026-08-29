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

# Optional payloads (config.sh). overlay/etc/apk/world stays the base image's
# world in the tree — this appends, so a `git diff` of the overlay never has to
# be read as "did someone leave the 200MB CLI switched on?". The answer lives in
# one place, WITH_ANTIGRAVITY, and shows up in the build log below.
if [ -n "$EXTRA_WORLD" ]; then
    for _pkg in $EXTRA_WORLD; do
        grep -qxF "$_pkg" "$STAGE/etc/apk/world" || echo "$_pkg" >> "$STAGE/etc/apk/world"
    done
    sort -o "$STAGE/etc/apk/world" "$STAGE/etc/apk/world"
    echo "==> optional payloads in world: $EXTRA_WORLD"
fi

# ---- optional: bake a model card (make stick CARD=docs/cards/<box>.card).
# The image then boots AS that box: repo card installed verbatim (so the
# deploy-overlay card gate matches from day one), derived files generated at
# build time, and — because the card names a NICK — the generator sets the
# provisioned marker, so autosave is live from the first boot. Without CARD
# the image ships the unprovisioned default from overlay/etc/pipeos/card.conf.
# Not --strict: GENERIC cards ship OWNER_NICK empty on purpose (filled on the
# customer's premises by pipebox-setup) and must stay bakeable.
if [ -n "${CARD:-}" ]; then
    [ -r "$CARD" ] || { echo "CARD not readable: $CARD" >&2; exit 1; }
    cp "$CARD" "$STAGE/etc/pipeos/card.conf"
    sh "$PIPEOS_ROOT/overlay/usr/local/bin/pipebox-card" generate \
        --card "$STAGE/etc/pipeos/card.conf" \
        --root "$STAGE" \
        --templates "$PIPEOS_ROOT/overlay/usr/local/share/pipeos/card"
    echo "baked card: $CARD (NICK=$(sed -n 's/^NICK=//p' "$CARD" | head -1))"
fi

# ---- optional: headless first contact (appliance plan — remote admin is
# non-negotiable; the machines ship with no keyboard, no display).
# AUTH_KEYS=<pubkey file> bakes the operator's ssh public key, so ssh works
# from the first boot with no password. PIPE_KEY=<one-time key> stages a
# pipe sign-in the pipeos-signin service consumes on first boot: the box
# signs itself in, persists, and DMs its owner — after which it is
# administered over pipe from anywhere, no inbound access needed. The pipe
# key is valid ~15 minutes from minting, so bake-and-boot promptly.
if [ -n "${AUTH_KEYS:-}" ]; then
    [ -r "$AUTH_KEYS" ] || { echo "AUTH_KEYS not readable: $AUTH_KEYS" >&2; exit 1; }
    grep -qE '^(ssh|ecdsa)-' "$AUTH_KEYS" \
        || { echo "AUTH_KEYS does not look like an ssh public key: $AUTH_KEYS" >&2; exit 1; }
    mkdir -p "$STAGE/root/.ssh"
    chmod 700 "$STAGE/root/.ssh"
    cat "$AUTH_KEYS" >> "$STAGE/root/.ssh/authorized_keys"
    chmod 600 "$STAGE/root/.ssh/authorized_keys"
    echo "baked ssh key: $AUTH_KEYS"
fi
if [ -n "${PIPE_KEY:-}" ]; then
    printf '%s\n' "$PIPE_KEY" > "$STAGE/etc/pipeos/pipe-signin.key"
    chmod 600 "$STAGE/etc/pipeos/pipe-signin.key"
    echo "staged pipe sign-in key (valid ~15 min — boot the box promptly)"
fi

# apk must trust our repo signing key at initramfs install time.
# overlay/etc/apk/keys is an empty dir in the tree, and git does not track
# empty dirs — so on a clean checkout it is absent and the cp below fails with
# "Not a directory". Create it rather than depend on the checkout carrying it.
ls "$OUT/keys/"*.rsa.pub >/dev/null 2>&1 || { echo "no abuild key — run 10-mk-chroot.sh" >&2; exit 1; }
mkdir -p "$STAGE/etc/apk/keys"
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
mk_runlevel boot     modules sysctl hostname bootmisc syslog networking hwclock seedrng watchdog
mk_runlevel default  crond chronyd sshd local pipeos-workspace pipe-daemon pipeos-signin pipebox-listener pipeos-selfcheck
mk_runlevel shutdown killprocs mount-ro savecache

chmod +x "$STAGE/etc/local.d/"*.start "$STAGE/etc/local.d/"*.stop \
         "$STAGE/etc/init.d/"* \
         "$STAGE/etc/periodic/15min/"* "$STAGE/etc/periodic/daily/"* \
         "$STAGE/etc/periodic/weekly/"* \
         "$STAGE/usr/local/bin/"* 2>/dev/null || true

# busybox tar (pipeOS's own tar, so a box can build itself) rejects
# --owner/--group and prints usage. Running as root — which the build always is
# on a box — the staged tree is already root-owned, so --numeric-owner alone
# gives 0:0. GNU tar (a non-root dev host) needs the explicit flags to force it.
if tar --version 2>/dev/null | grep -q GNU; then
    tar -C "$STAGE" --numeric-owner --owner=0 --group=0 \
        -czf "$OUT/pipeos.apkovl.tar.gz" etc usr root
else
    tar -C "$STAGE" --numeric-owner \
        -czf "$OUT/pipeos.apkovl.tar.gz" etc usr root
fi
ls -lh "$OUT/pipeos.apkovl.tar.gz"
