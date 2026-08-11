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
