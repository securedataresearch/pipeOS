# Build your own pipeOS box (and your own fleet)

pipeOS turns any spare UEFI x86_64 machine into a browser-managed agent
appliance. You do not need to build anything to *use* it — download the
released image, flash, boot, claim. This page is for the person who wants
more: building the image themselves, changing it, and running a fleet that
answers to nobody else's keys.

## The two-minute path (no build)

1. Download `pipeos-usb.img.xz` and its `.sha256` from this repo's
   [Releases](../../releases/latest) (also on pipe.online/downloads).
2. Verify and flash — **this wipes the whole target disk**:
   ```sh
   sha256sum -c pipeos-usb.img.xz.sha256
   xz -dc pipeos-usb.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync
   ```
3. Plug the target box into your network, power on (UEFI boot, Secure Boot
   OFF), wait a minute, and open **http://pipeos.local/** from any browser on
   the same network. The first visitor claims the box; the wizard does the
   rest. No console, no ssh, no account required.

Hardware: any 64-bit UEFI x86 machine with 8G+ RAM and a USB port or
internal disk. The reference box is a 1-liter Lenovo ThinkCentre; old
desktops and NUCs work fine. The boot media holds the OS + your saved state;
a second partition (`/work`, created on first boot from free space) holds
bulk data.

## Building the image yourself

```sh
./scripts/00-host-setup.sh   # installs deps (Arch/Debian/Fedora/Alpine),
                             # fetches + sha256-verifies the Alpine ISO
make chroot                  # Alpine build chroot (sudo, once)
make apks                    # build + sign the package repos
make usb                     # out/pipeos-usb.img — flash with dd as above
make vm                      # or boot it in qemu first:
                             #   wizard at http://localhost:8080/
```

Everything the image contains is in this repo: `overlay/` is the filesystem
you can read, `scripts/` is the pipeline in numbered order, and CI gates
every piece. If a stranger can't audit it, we consider that a bug.

## Your fleet, your keys, your origin — stated as a promise

Nothing in a self-built pipeOS phones home to us or trusts us:

- **Signing key**: the first `make chroot` generates a fresh abuild keypair
  (kept outside the repo, in `~/.pipeos/keys` or `/work/keys/pipeos`).
  Every image you build trusts *your* key and only your key. Guard it — the
  build refuses to continue if it ever finds two different keys claiming to
  be yours.
- **Update origin**: `overlay/etc/pipeos/selfupdate.conf` ships pointing at
  this repo's Releases. Blank `UPDATE_RELEASE_URL` and your boxes never
  self-update; point it at your own GitHub Releases (publish with
  `make release`) or any static host serving `SHA256SUMS` +
  `pipeos-repo.tar.gz`, and your fleet updates from you. Updates apply
  through a verified, atomic, self-rolling-back path either way.
- **Support relay**: the "Vendor support access" toggle dials out only to
  the relay in `/etc/pipeos/support.conf` — which ships empty. Your boxes,
  your relay or none.
- **pipe messaging**: entirely optional and off by default. A box without a
  pipe account is a fully functional appliance managed from its dashboard.

## When something goes wrong

- **Box not at pipeos.local**: give it 2 minutes; check your machine
  resolves mDNS (`ping pipeos.local`); or find its IP in your router's DHCP
  table — the wizard answers on plain `http://<ip>/`.
- **Won't boot**: UEFI mode on, Secure Boot OFF (Alpine's GRUB is unsigned).
- **Something's degraded**: the dashboard shows the boot report humanized;
  every service has a log viewer; **Repair remote access** and **Reboot**
  are on the Maintenance card. On a diskless box a reboot is a clean restore
  of the last saved state — it is the correct fix surprisingly often.
- Stuck beyond that: open an issue with the boot report text; the templates
  ask for exactly what helps.
