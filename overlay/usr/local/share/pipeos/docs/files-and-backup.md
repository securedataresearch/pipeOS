# Files, disks and backup

## The Files view

A browser over the box's bulk storage. The virtual root lists the drives:
`work` (the box's own data partition) and any mounted external disk.

- **Upload** with the button or by dragging files onto the folder card.
- **Download** a file directly, or any folder as a `.tar.gz`.
- **Move** with the move bar (or drag a row onto a folder), rename, make
  folders, delete (deleting a non-empty folder asks for the recursive
  confirmation).

Everything stays jailed to those roots — the server refuses any path that
would escape them.

## External disks

The System/Files disk panel lists every disk the box can see. External
drives can be mounted, unmounted, or formatted; the boot media and the work
partition are protected and refuse every operation.

## Backup

The Backup card mirrors the box's persistent data — `/work` and the boot
media state — onto a mounted external drive under `pipeos-backup/`, with a
timestamp of the last run per drive. Plug in a drive, mount it, back up,
unmount, and the copy is cold storage. Backups are rsync mirrors: a second
run only copies what changed.

## Identity backup

The Backup card also writes the box's **identity**: a fresh bundle of the
saved state — `/etc`, the pipe keys, SSH, the Claude credential, users, the
box's configuration — the same thing a boot restores. On the drive it lands
under `pipeos-backup/<name>/identity/` as `pipeos.apkovl.tar.gz` (the
previous run kept as `pipeos.apkovl.prev.tar.gz`), plain copies of the pipe
keys under `pipe/`, and a `MANIFEST` naming the box, the date, the checksum
and the image it was taken on. **Identity only** skips the `/work` and boot
media mirrors and takes seconds.

Whoever holds the drive holds the keys. The bundle is plaintext by default,
because a passphrase the owner forgets is a lost identity, which is worse.
A passphrase seals it (and writes no plain key copies); an ext4 drive keeps
file permissions, a FAT drive cannot — the card says which it found.

To put an identity back: Maintenance → Live disk → **Restore identity**,
or from a shell `pipeos flash restore-identity <bundle>`. The bundle is
checked and staged as the next boot's state; reboot to apply it. This is
how a box comes back on fresh media after a dead stick or a write that
lost power halfway.

## Flashing a new live disk

The **Live disk** row under Maintenance shows the image the box is running
and whether the latest release is newer. **Flash a new live disk** rewrites
the boot partition in place with the released image: the box's identity
and `/work` are kept, the image is downloaded and checked first, the merge
of the box's state with the new image is proved before a byte is written,
and you type the box's name to confirm. It never reboots — you do, when
ready. Back up first (Files → Backup). A power loss mid-write leaves the
box unbootable until it is reflashed from another machine; the identity is
safe on the work partition and on any backup drive, and Restore identity
brings it back.

A **second stick** — bigger, fresher, or replacing one that is dying — is a
shell verb today: `pipeos flash apply --to /dev/sdX` writes the released
image and this box's identity onto a spare disk and prints the swap. One
rule matters: remove the old stick before booting the new one. Both carry
the same labels, and the box mounts by label. `pipeos restore-work` copies
`/work` onto the new stick, before the swap or after.
