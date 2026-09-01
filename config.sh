# pipeOS shared build configuration. Sourced by the build scripts in scripts/.
# All paths are absolute so scripts can run from anywhere.
#
# No shebang: this file is sourced, never executed. shellcheck cannot infer a
# dialect without one, so name it — bash, not sh, and BASH_SOURCE below is why.
# shellcheck shell=bash

PIPEOS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
OUT="$PIPEOS_ROOT/out"

ALPINE_VERSION=3.24
ALPINE_PATCH=3.24.1
ALPINE_ARCH=x86_64
ALPINE_MIRROR="https://dl-cdn.alpinelinux.org/alpine"
ALPINE_MAIN="$ALPINE_MIRROR/v$ALPINE_VERSION/main"
ALPINE_COMMUNITY="$ALPINE_MIRROR/v$ALPINE_VERSION/community"

# ── variants ──────────────────────────────────────────────────────────────
# One release (3.24.1), one shared chroot/apkovl/repo; what differs per
# target is the Alpine ISO flavor the boot tree comes from and the grub.cfg
# written over it. Select with VARIANT=usb (env or `make image VARIANT=usb`).
#   vm    — virt ISO (virtio-tuned kernel), serial console for -nographic.
#   usb   — extended ISO: fullest firmware modloop for unknown hardware;
#           hardware grub.cfg (no `serial` command — it can hang firmware
#           with no UART), verbose, waitusb, nomodeset fallback entry.
#   metal — standard ISO, the stock bare-metal experience; hardware
#           grub.cfg without the usb wait.
# Secure Boot must be OFF on real hardware: every Alpine flavor ships an
# unsigned GRUB, and SB-on fails as INVALID SIGNATURE before our code runs.
VARIANT="${VARIANT:-vm}"
case "$VARIANT" in
    vm)    ALPINE_FLAVOR=virt;     KERNEL_FLAVOR=virt; IMG_BASENAME=pipeos-vm.img;    IMG_SIZE_MB=3200 ;;
    usb)   ALPINE_FLAVOR=extended; KERNEL_FLAVOR=lts;  IMG_BASENAME=pipeos-usb.img;   IMG_SIZE_MB=3600 ;;
    metal) ALPINE_FLAVOR=standard; KERNEL_FLAVOR=lts;  IMG_BASENAME=pipeos-metal.img; IMG_SIZE_MB=3200 ;;
    *) echo "unknown VARIANT '$VARIANT' (vm|usb|metal)" >&2; exit 1 ;;
esac
# The ISO the boot tree comes from. Three tiers, the same shape PIPE_SRC and
# HERMES_SRC use below: an explicit env override wins, then the cross-host
# default, then the on-box fallback — because on pipeOS itself there is no
# $HOME/Downloads and the ISO is staged on the ext4 workspace.
#
# box2 found this was the only $HOME-rooted path in this file with no on-box
# fallback and asked BUILD whether a convention already existed. It does, and
# it has one more tier than their patch: `${VAR:-...}` means an operator can
# point at an ISO anywhere. Without it the assignment below overwrites
# whatever they exported, so `ALPINE_ISO=... make image` silently did nothing.
#
# NOTE, inherited not introduced: an explicit override naming a file that does
# not exist falls through to the on-box copy rather than failing. PIPE_SRC and
# HERMES_SRC already behave that way, so this matches them deliberately — but
# it means a typo'd override builds from a DIFFERENT ISO than the one asked
# for. Worth fixing for all three together; out of scope for a path fallback.
ALPINE_ISO="${ALPINE_ISO:-$HOME/Downloads/alpine-$ALPINE_FLAVOR-$ALPINE_PATCH-$ALPINE_ARCH.iso}"
ALPINE_ISO_ONBOX="/work/isos/alpine-$ALPINE_FLAVOR-$ALPINE_PATCH-$ALPINE_ARCH.iso"
[ -f "$ALPINE_ISO" ] || { [ -f "$ALPINE_ISO_ONBOX" ] && ALPINE_ISO="$ALPINE_ISO_ONBOX"; }

CHROOT="$OUT/chroot"

# ── the abuild signing key ────────────────────────────────────────────────
# Root of trust for the whole fleet: every flashed stick trusts this key in
# /etc/apk/keys, so if it is lost no box can ever be handed another package —
# only a full re-image. It used to live in exactly two places, out/keys and
# the chroot, both inside the gitignored out/ tree that every disk sweep and
# `rm -rf out` reaches for first. This is its durable home, outside the repo
# on purpose so nothing repo-scoped can take it. Override to relocate; the
# restore in 10-mk-chroot.sh follows it. (pipeOS#90)
#
# Same three tiers as ALPINE_ISO above — override, then the cross-host default,
# then the on-box fallback — and it points that way round for the reason box2
# gave on #92: this file's only unconditional /work path was this one, and the
# machine that actually runs 10-mk-chroot.sh is a workstation, not a box (the
# script needs sudo chroot and apk, both hard-banned on a box). /work does not
# exist there and an unprivileged user cannot create it, so under `set -e` the
# old default was a hard build stop at the key step on the only machine that
# builds the fleet.
#
# The on-box tier is still worth having, and it is not the -d /work test alone:
# on a box $HOME is /root on tmpfs, which is the one place a key must NOT live.
# So the fallback wants a durable /work — and an existing store there wins
# outright, because a builder that already has the fleet key on the ext4
# workspace must not silently start looking somewhere else.
#
# The fallbacks apply ONLY when nothing was exported. ALPINE_ISO's inherited
# behaviour — a typo'd override silently falling through to another path — is
# flagged above as worth fixing; it is not worth reproducing on the key, where
# "silently used a different one" is the entire defect class.
if [ -z "${SIGNING_KEY_DIR:-}" ]; then
    SIGNING_KEY_DIR="$HOME/.pipeos/keys"
    if [ -d /work/keys/pipeos ]; then
        SIGNING_KEY_DIR=/work/keys/pipeos
    elif [ ! -d "$SIGNING_KEY_DIR" ] && [ -d /work ] && [ -w /work ]; then
        SIGNING_KEY_DIR=/work/keys/pipeos
    fi
fi

# Every candidate store this file knows about, minus the winner. (box3 on #92)
#
# SELECTION IS NOT DETECTION, AND THE TIERS ABOVE SELECT ON `-d`. A directory
# existing is not a key being in it, so the loser of that test can be the store
# that actually holds the fleet key — and it was then invisible: 10-mk-chroot.sh
# censused `$SIGNING_KEY_DIR` and nothing knew the other candidate's name. An
# EMPTY /work/keys/pipeos beside a populated $HOME/.pipeos/keys is not exotic;
# the backup does `mkdir -p` then copies, so any failure between the two leaves
# exactly that, and an operator told "the durable home is /work/keys/pipeos"
# makes the directory first, because that is what people do. The build then
# reported "no signing key — generating a new one", which is #90's headline
# defect reached through the code #92 added to fix it.
#
# So keep the selection dumb and make the census wide: name the paths, let
# 10-mk-chroot.sh look in all of them. Census-before-any-read is the property
# this section is credited with, and it cannot hold over a store it cannot name.
#
# This is NOT conditional on the fallbacks having run. An explicit override says
# where the key SHOULD live; it says nothing about where a key IS, and "the
# operator pointed somewhere else and the old store still holds a key" is the
# same silent-wrong-key class as the rest of this section. The census's answer
# to two populated stores is to stop and make a human choose, which is the right
# answer to a half-finished rotation too.
# An ARRAY, not a space-joined string (pipeOS#111): one candidate lives under
# $HOME, and a $HOME with a space silently split into two nonexistent paths —
# the census then saw nothing there and the two-different-keys refusal was
# disabled exactly where it was needed. Consumers iterate
# "${SIGNING_KEY_DIR_ALT[@]}"; this file is bash (see shebang note above).
SIGNING_KEY_DIR_ALT=()
for _cand in "$HOME/.pipeos/keys" /work/keys/pipeos; do
    if [ "$_cand" != "$SIGNING_KEY_DIR" ]; then
        SIGNING_KEY_DIR_ALT+=("$_cand")
    fi
done
unset _cand

# ── packages built into apks/pipeos ───────────────────────────────────────
# The base three ship on every image. Optional payloads are OFF by default and
# named here rather than in the build scripts, so one list drives all three
# places that must agree: what abuild builds, what the extra-repo fetch must
# NOT look for on the CDN, and what gets added to the image's apk world.
#
# Antigravity was a payload here (2026-08..09-01) and is gone: Google ships
# no musl build, the glibc-sysroot workaround bloated the image past
# GitHub's 2GiB release-asset cap, and Sam pulled it entirely rather than
# keep paying for it. If an optional backend ever returns, pipeOS#195's
# closed branch holds the on-demand install machinery.
# OVERRIDABLE, and that is not decoration: the customer build
# (the customer build's run-root-build.sh) exports PIPEOS_PKGS="pipe claude-code"
# because a customer box ships no hermes. An unconditional assignment here
# would be re-sourced by 30-build-apks.sh and silently put hermes back on a
# stick that is meant not to have it — a fleet-ism reappearing in the factory
# path, which is the exact drift the card work exists to end.
PIPEOS_PKGS="${PIPEOS_PKGS:-pipe claude-code hermes-agent}"
# appended to the staged /etc/apk/world in 40-build-apkovl.sh (empty today)
EXTRA_WORLD=""

PIPE_SRC="${PIPE_SRC:-$HOME/Projects/pipe}"
HERMES_SRC="${HERMES_SRC:-$HOME/.hermes/hermes-agent}"
# on pipeOS itself the checkouts live on the ext4 workspace
[ -d "$PIPE_SRC" ] || { [ -d /work/repos/pipe ] && PIPE_SRC=/work/repos/pipe; }
[ -d "$HERMES_SRC" ] || { [ -d /work/repos/hermes-agent ] && HERMES_SRC=/work/repos/hermes-agent; }

# pipeOS runs these scripts as root with no sudo installed — shim it
if [ "$(id -u)" = 0 ] && ! command -v sudo >/dev/null 2>&1; then
    sudo() { "$@"; }
fi

# Flashable image (per variant, see above)
IMG="$OUT/$IMG_BASENAME"
# Partition starts at 1MiB (sector 2048)
PART_OFFSET_MB=1

# Root login posture (40-build-apkovl.sh writes /etc/shadow from this):
#   locked   — root's password field is '!': no console/password login at all.
#              The generic client image ships this way; first contact is the
#              LAN web wizard (pipeos-web), operator access is AUTH_KEYS ssh.
#   password — root gets DEFAULT_ROOT_PW; provisioning forces a change on
#              first login. Fleet sticks (make stick) build with this.
ROOT_LOGIN="${ROOT_LOGIN:-locked}"
# Default root password for ROOT_LOGIN=password builds.
DEFAULT_ROOT_PW=pipeos

# qemu
OVMF_CODE=/usr/share/edk2/x64/OVMF_CODE.4m.fd
OVMF_VARS_SRC=/usr/share/edk2/x64/OVMF_VARS.4m.fd
VM_SSH_PORT=2222
VM_HTTP_PORT=8080
VM_RAM_MB=8192
