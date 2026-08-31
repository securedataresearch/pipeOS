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

# ---- optional: operator ssh (fleet sticks — remote admin with no console).
# AUTH_KEYS=<pubkey file> bakes the operator's ssh public key, so key-based
# ssh works from the first boot. Client first contact is the LAN web wizard
# (pipeos-web, http://pipeos.local) — the old PIPE_KEY one-time-key staging
# is gone: its ~15-min TTL started at minting, so it was expired on every
# box that wasn't booted at the flashing desk (basho0, 2026-08-29).
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
    echo "PIPE_KEY is gone: the staged one-time key expired before any shipped box booted." >&2
    echo "Sign pipe in from the web wizard (http://<nick>.local) or pipebox-setup." >&2
    exit 1
fi

# ---- enabled services (the wizard's declarative record). The generic client
# image ships everything OFF — the wizard turns services on. A fleet stick
# (CARD with a NICK) is pipe-administered, so it ships pipe on and the pipe
# pair in the default runlevel below; a client box gets neither until claimed.
FLEET_SVCS=""
if [ -n "${CARD:-}" ] && [ -n "$(sed -n 's/^NICK=//p' "$CARD" | head -1)" ]; then
    FLEET_SVCS="pipe-daemon pipebox-listener"
    printf 'SERVICE_PIPE=on\nSERVICE_CLAUDE=on\nSERVICE_STREAM=off\nSERVICE_AGY=off\nSERVICE_SUPPORT=off\nSERVICE_ASSISTANT=off\n' \
        > "$STAGE/etc/pipeos/services.conf"
else
    printf 'SERVICE_PIPE=off\nSERVICE_CLAUDE=off\nSERVICE_STREAM=off\nSERVICE_AGY=off\nSERVICE_SUPPORT=off\nSERVICE_ASSISTANT=off\n' \
        > "$STAGE/etc/pipeos/services.conf"
fi

# apk must trust our repo signing key at initramfs install time.
# overlay/etc/apk/keys is an empty dir in the tree, and git does not track
# empty dirs — so on a clean checkout it is absent and the cp below fails with
# "Not a directory". Create it rather than depend on the checkout carrying it.
ls "$OUT/keys/"*.rsa.pub >/dev/null 2>&1 || { echo "no abuild key — run 10-mk-chroot.sh" >&2; exit 1; }
mkdir -p "$STAGE/etc/apk/keys"
cp "$OUT/keys/"*.rsa.pub "$STAGE/etc/apk/keys/"

# /etc/shadow: take the chroot's stock alpine-base shadow and set root's
# password field. The client posture (ROOT_LOGIN=locked, the default) LOCKS
# root — no baked well-known password on a box that ships with a LAN web
# wizard; the wizard owns the first credential and operator access is
# AUTH_KEYS ssh. Fleet builds set ROOT_LOGIN=password to keep the console
# password (DEFAULT_ROOT_PW; provisioning forces a change on first login).
# '*', not '!': both make password login impossible, but sshd treats a
# '!'-prefixed field as a LOCKED account and refuses public-key auth too —
# which would brick the AUTH_KEYS operator path (found in the VM drill).
case "${ROOT_LOGIN}" in
    locked)   HASH='*' ;;
    password) HASH=$(openssl passwd -6 "$DEFAULT_ROOT_PW") ;;
    *) echo "unknown ROOT_LOGIN '$ROOT_LOGIN' (locked|password)" >&2; exit 1 ;;
esac
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
# The pipe pair is NOT in the client default runlevel: pipe is a service the
# wizard turns on (rc-update at claim time), not the box's spine. Fleet sticks
# (CARD with a NICK) get it back via FLEET_SVCS above.
# shellcheck disable=SC2086
mk_runlevel default  crond chronyd sshd local pipeos-workspace pipeos-web pipeos-mdns $FLEET_SVCS pipeos-selfcheck
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
