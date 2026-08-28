# pipeOS — working notes

## Status (2026-08-04)

- Three flavor-matched variants, one Alpine release (3.24.1), one shared
  chroot/apkovl/repo. Payloads: pipe 0.41.5, Claude Code 2.1.221,
  hermes-agent 0.18.2, plus the utility set (operator/network/hardware/fun —
  see `overlay/etc/apk/world`).
- The ThinkCentre's "INVALID SIGNATURE" was **Secure Boot** rejecting
  Alpine's unsigned GRUB — no image can fix that; the firmware setting can.
- **First hardware boot CONFIRMED 2026-08-04**: ThinkCentre (i5-8500T,
  32GB) boots `pipeos-usb.img` from the stick with Secure Boot off —
  tmpfs root, eth0 up via DHCP, ssh reachable, all payloads answering.
  Console keyboards need `usbcore.autosuspend=-1` (now baked into the
  hardware variants) or keystrokes drop.
- Repo: `github.com/securedataresearch/pipeOS` (private). `out/` and
  `vendor/` are reproducible artifacts and stay out of git.

## The variant matrix

| variant | base ISO | kernel | image | for |
|---------|----------|--------|-------|-----|
| vm    | virt     | `-virt` | `out/pipeos-vm.img`    | qemu; serial console, `quiet` |
| usb   | extended | `-lts`  | `out/pipeos-usb.img`   | any real box off a stick; fullest firmware modloop, `waitusb=3`, verbose, nomodeset fallback entry |
| metal | standard | `-lts`  | `out/pipeos-metal.img` | the internal NVMe, once the drives arrive |

Build: `make image` (vm) · `make usb` · `make metal` · `make images` (all).
ISOs live in `~/Downloads/alpine-<flavor>-3.24.1-x86_64.iso`.

## Booting the ThinkCentre from the USB stick

1. **F1 setup → Security → Secure Boot → Disabled** (also set "OS Optimized
   Defaults" to Disabled or it re-enables). This is the fix for
   INVALID SIGNATURE — do it before anything else.
2. Flash: `VARIANT=usb ./scripts/70-flash.sh /dev/sdX`
3. F12 → pick the stick (UEFI entry). GRUB menu shows on the panel; boot is
   verbose on purpose. If the screen blanks when the kernel takes over,
   reboot and pick the "safe graphics: nomodeset" entry.
4. Boots diskless into RAM; get the IP from the console or router;
   `ssh root@<ip>`, password `pipeos`. First interactive login runs the
   provisioning walkthrough and commits.
5. State autosaves (lbu) every 15 min and at shutdown.

## Hardware caveats to check on first real boot

- **NIC name**: overlay assumes `eth0`. If `ip link` disagrees, fix
  `overlay/etc/network/interfaces`, `make usb`, reflash — or fix live and
  `lbu commit`.
- "backup GPT not at end of disk" warning is harmless (image < media).
  Optional tidy: `sudo sgdisk -e /dev/sdX`.
- When the NVMe drives arrive: `make metal`,
  `VARIANT=metal ./scripts/70-flash.sh /dev/nvmeXn1`.

## Rebuild cheat-sheet

- Config-only change (overlay/): `make image` / `make usb` (seconds).
- Package change: edit `overlay/etc/apk/world` (`scripts/30-build-apks.sh`
  derives its `UTILS` set from it), then `make apks` and the image target.
- VM test: `make vm` (NVMe attach) or `VARIANT=usb ./scripts/61-run-vm-usb.sh`
  (USB attach); ssh: `ssh -p 2222 root@localhost`, password `pipeos`.
  Serial console quit: Ctrl-a x.
- After VM sessions where anything was committed, rebuild the image before
  flashing — VM lbu commits dirty the image's apkovl.

## Known quirks baked into the build (see scripts for details)

- **OVMF NVRAM is stateful**: 60/61 copy a fresh vars file per run now. A
  reused vars file accumulates boot entries and can send OVMF to PXE ahead
  of the boot media — that detour is rig state, not an image bug.
- grub.cfg differs per variant on purpose: the vm one drives the qemu UART
  (`serial` + quiet); the hardware ones must NOT run grub's `serial` (can
  hang UART-less firmware) and boot verbose, tty0 last so the panel is
  /dev/console.
- Alpine 3.24 ships Python 3.14.5; hermes pins `<3.14` — the build relaxes
  the vendored ceiling to `<3.15`.
- Claude Code comes from the official install.sh inside the Alpine chroot
  (musl build); the versioned file under
  `~/.local/share/claude/versions/` is the binary itself.
- The `pipe` binary builds from the `pipe-client` crate (not `pipe-cli`).
- Onboard ISO repos lack most packages — every runtime dep ships in the
  signed `apks/extra` repo on the image, so first boot needs no network.

## Host cleanup when done building

- Remove the temporary passwordless-sudo rule:
  `sudo rm /etc/sudoers.d/99-temp-pipeos`
