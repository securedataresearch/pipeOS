#!/usr/bin/env bash
# Boot out/pipeos.img attached as an emulated USB stick (instead of NVMe) —
# proves the image is boot-device-agnostic. Same serial console + ssh forward.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

[ -f "$IMG" ] || { echo "no image — run 50-build-image.sh" >&2; exit 1; }
# Fresh NVRAM every run — see 60-run-vm.sh.
VARS="$OUT/OVMF_VARS-$VARIANT.fd"
cp "$OVMF_VARS_SRC" "$VARS"

exec qemu-system-x86_64 \
    -enable-kvm -machine q35 -cpu host -smp 4 -m "$VM_RAM_MB" \
    -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
    -drive if=pflash,format=raw,file="$VARS" \
    -drive file="$IMG",if=none,format=raw,id=stick \
    -device qemu-xhci -device usb-storage,drive=stick \
    -netdev user,id=n0,hostfwd=tcp::"$VM_SSH_PORT"-:22,hostfwd=tcp::"$VM_HTTP_PORT"-:80 \
    -device virtio-net-pci,netdev=n0 \
    -nographic \
    "$@"
