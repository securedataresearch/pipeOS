# pipeOS shared build configuration. Sourced by every script in scripts/.
# All paths are absolute so scripts can run from anywhere.

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

# Default root password baked into the overlay; provisioning forces a change.
DEFAULT_ROOT_PW=pipeos

# qemu
OVMF_CODE=/usr/share/edk2/x64/OVMF_CODE.4m.fd
OVMF_VARS_SRC=/usr/share/edk2/x64/OVMF_VARS.4m.fd
VM_SSH_PORT=2222
VM_RAM_MB=8192
