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
