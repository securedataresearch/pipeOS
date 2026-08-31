# pipeOS

Turn any spare x86_64 box into a browser-managed AI agent appliance.
pipeOS is diskless Alpine Linux that runs entirely from RAM — flash it,
boot it, open **http://pipeos.local/**, claim it, and pick what it does:
a Claude assistant, a streaming box, pipe messaging, or all of it. Every
boot is a clean restore of the last saved state; updates are silent,
verified, and self-rolling-back; recovery is two buttons on a web page.

**Fastest start**: grab `pipeos-usb.img.xz` from
[Releases](../../releases/latest) (or [pipe.online/downloads](https://pipe.online/downloads)),
verify, `dd`, boot. Full walkthrough — including building your own image
and running a fleet on your own keys — in
[docs/build-your-own.md](docs/build-your-own.md).

- The NVMe holds only boot media, an apkovl overlay, a local apk repo, and an
  apk cache. The live system is tmpfs — the disk is never a root filesystem.
- State (claude auth/sessions, pipe identity, hermes config, /etc changes) is
  snapshotted to the NVMe by `lbu commit`, run automatically every 15 minutes
  and at shutdown.
- First contact is a browser, not a console: the box serves a setup wizard on
  the LAN (`http://pipeos.local/`) where the owner claims it, names it, and
  turns on services (Claude / streaming / pipe — pipe is off until a key is
  supplied). See docs/web-wizard.md. The generic image ships with root
  LOCKED and key-only ssh; fleet builds (`make stick`, ROOT_LOGIN=password)
  keep the console walkthrough for non-GENERIC roles.

## Build

```sh
make host-deps   # pacman: qemu-base edk2-ovmf; rustup musl target (once)
make chroot      # Alpine build chroot via apk.static (sudo, once)
make pipe        # cross-compile pipe statically for musl
make apks        # build claude-code/hermes-agent/pipe apks + signed repo
make image       # apkovl + GPT/FAT32 pipeos.img (no root needed)
make vm          # boot the image in qemu (serial console; ctrl-a x to quit)
```

Then claim the box at `http://localhost:8080/` (the VM forwards :80 there).
For a shell, build with `AUTH_KEYS=~/.ssh/id_ed25519.pub make apkovl image`
and `ssh -p 2222 root@localhost` (key-only; there is no password anymore).

## Flash to NVMe or USB

```sh
make flash DEV=/dev/nvmeXn1   # internal NVMe
make flash DEV=/dev/sdX       # USB stick (same image — no ISO needed)
```

Safety-checked `dd` of `out/pipeos.img`. The boot media is found by its
`PIPEOS` filesystem label, so the identical image boots from NVMe, USB, or
SATA. Boot the target in UEFI mode with Secure Boot disabled. Reflashing
wipes state; the image itself contains no secrets.

## License

Apache-2.0 (see LICENSE). "pipeOS" is a trademark of Secure Data Research, LLC —
the license grants no rights to the name; see NOTICE for the trademark
policy. Build recipes for third-party software (Claude Code, Antigravity)
ship no binaries and leave those programs under their vendors' own terms.

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
