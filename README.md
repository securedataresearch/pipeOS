# pipeOS

Diskless Alpine Linux (x86_64) that runs entirely from RAM, with **claude**
(Claude Code), **hermes** (hermes-agent), and **pipe** preinstalled. Built for
headless ThinkCentre-class boxes with NVMe; developed and tested in qemu.

- The NVMe holds only boot media, an apkovl overlay, a local apk repo, and an
  apk cache. The live system is tmpfs — the disk is never a root filesystem.
- State (claude auth/sessions, pipe identity, hermes config, /etc changes) is
  snapshotted to the NVMe by `lbu commit`, run automatically every 15 minutes
  and at shutdown.
- SSH is enabled out of the box: `root` / `pipeos`. The first interactive
  login runs a provisioning walkthrough (forces a password change, then claude
  login, pipe identity, hermes API keys) and commits.

## Build

```sh
make host-deps   # pacman: qemu-base edk2-ovmf; rustup musl target (once)
make chroot      # Alpine build chroot via apk.static (sudo, once)
make pipe        # cross-compile pipe statically for musl
make apks        # build claude-code/hermes-agent/pipe apks + signed repo
make image       # apkovl + GPT/FAT32 pipeos.img (no root needed)
make vm          # boot the image in qemu (serial console; ctrl-a x to quit)
```

Then `ssh -p 2222 root@localhost` (password `pipeos`).

## Flash to NVMe or USB

```sh
make flash DEV=/dev/nvmeXn1   # internal NVMe
make flash DEV=/dev/sdX       # USB stick (same image — no ISO needed)
```

Safety-checked `dd` of `out/pipeos.img`. The boot media is found by its
`PIPEOS` filesystem label, so the identical image boots from NVMe, USB, or
SATA. Boot the target in UEFI mode with Secure Boot disabled. Reflashing
wipes state; the image itself contains no secrets.

## Layout

- `config.sh` — versions/paths shared by all scripts
- `scripts/NN-*.sh` — build pipeline in order
- `aports/*/APKBUILD` — the three custom packages
- `overlay/etc/` — becomes `pipeos.apkovl.tar.gz` (plus generated shadow,
  runlevels, and repo signing key at build time)
- `out/` — all build products (gitignored)

## Iterating

Config-only change: edit `overlay/`, then `make image` (seconds — apkovl is
re-tarred and re-copied into the image with mtools). Package change: rerun
`make apks` first.
