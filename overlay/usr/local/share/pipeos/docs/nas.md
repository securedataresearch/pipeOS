# Network storage

The box can share folders over SMB — the file-sharing protocol every
laptop and phone already speaks. A shared folder shows up like any network
drive: Finder on a Mac, Explorer on Windows, the Files app on a phone.

## Set up a share

Under **Files → Network storage**:

1. Pick where the folder lives — `work` (the box's own data drive) or an
   attached external drive.
2. Name the share and tick the accounts that may connect. Only accounts
   with unix access (set under Users) can be ticked.
3. Set an **SMB password** for each of those accounts. SMB keeps its own
   password store, so this is separate from the dashboard password — set
   it once here.

Adding a share turns the service on; removing the last one turns it off.
The switch also lives under Services.

## Connect from another device

- macOS: Finder → Go → Connect to Server → `smb://<box>.local/<share>`
- Windows: Explorer address bar → `\\<box>.local\<share>`
- Sign in with the account name and its SMB password. There is no guest
  access — every connection authenticates.

## External drives and reboots

A drive you mount in the Disks card is remembered by its filesystem ID
and re-mounted automatically after a reboot, and shares on it come back
with it. If the drive is unplugged, its shares are skipped (the Files
page shows a grey dot) and return when it is plugged back in. Unmounting
a drive from the dashboard also stops re-mounting it at boot.

## What a share can see

A share serves exactly the folder you picked, as the box (files the
dashboard's own file explorer could touch). Anyone you tick and give an
SMB password to can read and write everything under that folder — share
a subfolder rather than a whole drive when in doubt.
