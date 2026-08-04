# pipeOS — working notes

## Status (2026-08-03)

- `out/pipeos.img` built and verified in qemu **both ways**: attached as NVMe
  and as USB stick. All three tools run (pipe 0.41.3, Claude Code 2.1.221,
  hermes-agent 0.18.2), lbu autosave persists state across reboots.
- SanDisk Ultra 57GB USB flashed with the image — ready to boot the
  ThinkCentre. Verified: single 3.1G FAT32 partition, label `PIPEOS`.
- ThinkCentre hardware boot: **not yet tested** (the one remaining step).

## Booting the ThinkCentre from the USB stick

1. Plug in the stick; boot menu with F12 (or F1 for setup).
2. Must be **UEFI mode, Secure Boot disabled**.
3. Boots diskless into RAM. Get the IP from the router or the console
   (`ip addr`).
4. `ssh root@<ip>`, password `pipeos`. First interactive login runs the
   provisioning walkthrough: forced password change → optional ssh key →
   claude login → pipe identity → hermes API keys → `lbu commit`.
5. After that, state autosaves to the boot media every 15 min and at
   shutdown. Manual save: `lbu commit`.

## Hardware caveats to check on first real boot

- **NIC name**: overlay assumes `eth0`. If `ip link` shows something else,
  edit `overlay/etc/network/interfaces`, rebuild (`make image`), reflash —
  or fix live and `lbu commit`.
- The "backup GPT not at end of disk" warning (image smaller than the media)
  is harmless. Optional tidy: `sudo sgdisk -e /dev/sdX`.
- Since the image is device-agnostic (found by the `PIPEOS` label), a clean
  USB boot on the ThinkCentre proves the same image will behave identically
  flashed to the internal NVMe: `make flash DEV=/dev/nvmeXn1`.

## Flashing

- USB and NVMe use the same image, no ISO exists or is needed:
  `make flash DEV=/dev/sdX` or `make flash DEV=/dev/nvmeXn1`
  (safety-checked dd; refuses mounted disks and the host root disk).
- Reflashing wipes all state. The image contains no secrets — safe to share.

## Rebuild cheat-sheet

- Config-only change (overlay/): `make image` (seconds).
- Package change: `make apks` then `make image`.
- VM test: `make vm` (NVMe attach) or `./scripts/61-run-vm-usb.sh`
  (USB attach); ssh: `ssh -p 2222 root@localhost`, password `pipeos`.
  Serial console quit: Ctrl-a x.
- After VM sessions where anything was committed, rebuild the image before
  flashing — VM lbu commits dirty the image's apkovl
  (`make image` regenerates a pristine one).

## Host cleanup when done building

- Remove the temporary passwordless-sudo rule:
  `sudo rm /etc/sudoers.d/99-temp-pipeos`

## Known quirks baked into the build (see scripts for details)

- Alpine 3.24 ships Python 3.14.5; hermes upstream pins `<3.14` — the build
  relaxes the vendored pyproject ceiling to `<3.15` (works; deps all have
  3.14 wheels).
- Claude Code comes from the official install.sh run inside the Alpine
  chroot (yields the musl build); `~/.local/share/claude/versions/<ver>` is
  the binary itself.
- The `pipe` binary is built from the `pipe-client` crate (not `pipe-cli`).
- The standard ISO's onboard repo lacks python3/ripgrep — runtime deps live
  in the signed `apks/extra` repo on the image.
