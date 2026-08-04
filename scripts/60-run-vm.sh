#!/usr/bin/env bash
# Boot out/pipeos.img in qemu exactly like the ThinkCentre will see it:
# UEFI (OVMF) + NVMe drive. Serial console on stdio; ssh forwarded to
# localhost:$VM_SSH_PORT. Extra qemu args pass through (e.g. -rtc base=...).
set -euo pipefail
. "$(dirname "$0")/../config.sh"

[ -f "$IMG" ] || { echo "no image — run 50-build-image.sh" >&2; exit 1; }
# Fresh NVRAM every run: the vars file accumulates boot entries across
# sessions, and a stale entry sends OVMF hunting PXE before our media.
VARS="$OUT/OVMF_VARS-$VARIANT.fd"
cp "$OVMF_VARS_SRC" "$VARS"

exec qemu-system-x86_64 \
    -enable-kvm -machine q35 -cpu host -smp 4 -m "$VM_RAM_MB" \
    -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
    -drive if=pflash,format=raw,file="$VARS" \
    -drive file="$IMG",if=none,format=raw,id=nvm \
    -device nvme,serial=pipeos,drive=nvm \
    -netdev user,id=n0,hostfwd=tcp::"$VM_SSH_PORT"-:22 \
    -device virtio-net-pci,netdev=n0 \
    -nographic \
    "$@"
