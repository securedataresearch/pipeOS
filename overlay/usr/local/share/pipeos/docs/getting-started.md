# Getting started

pipeOS is a diskless appliance: the whole operating system runs from RAM and
is rebuilt from the boot media every boot. A clean boot is always one reboot
away.

## Flash the image

Download `pipeos-usb.img.xz` from pipe.online/downloads, check its digest,
and write it to a **whole disk** — it wipes everything on that disk:

```
xz -dc pipeos-usb.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync
```

The same image boots from USB, SATA or NVMe; the media is found by its
`PIPEOS` label. Boot UEFI with Secure Boot off.

## First boot — the web wizard

There is no desktop. The Machine comes up listening on the LAN and
advertises itself as `<hostname>.local`. Open that address in a browser
(usually `http://pipeos.local`). Several Machines? `http://pipeos.local`
lists them all; the one marked **unclaimed** is the new one — open it by
its `pipeos-xxxx.local` name.

1. **Claim** — the first visitor sets the admin password. The claim is saved
   to the media immediately, so it survives a reboot even if you stop here.
2. **Name** — give the box a nick, and optionally your own nick as owner.
3. **Services** — turn on what this box should run: Claude, pipe, streaming,
   the assistant terminal.
4. **Connect** — sign in with your Claude account: the Machine shows a
   link, you open it on any device and paste back the code it shows (or
   paste an Anthropic API key or a setup-token instead). If pipe is on, a
   one-time key from your pipe.online account page.

After the wizard, the same address is the dashboard. Sign in with the
password you set.

## SSH

SSH is key-only by default. If your media was built with an authorized key,
`ssh root@<box>.local` works from that machine; there is no well-known
password.
