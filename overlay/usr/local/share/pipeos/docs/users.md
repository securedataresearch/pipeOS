# Users and access

The box has one claim password (the admin from first setup) and, optionally,
more dashboard accounts under **Users**.

## Roles

- **admin** — full control: services, streaming, files, users, saves.
- **user** — everything a viewer sees, plus the file explorer works:
  upload, download, move, rename and delete inside their own folder
  (`/data/home/<name>`, shown as "my files") and on any external drive —
  never the box's own directories. The role for someone who drops files on
  the box without running it.
- **viewer** — sees every page except Files, changes nothing except their
  own password. The server refuses viewer mutations and viewer file reads;
  the greyed-out controls in the dashboard are the affordance, not the
  enforcement.

Changing a password ends that account's other sessions. Every login,
failed login, claim and password change is a line under Overview → Recent
activity (the box's `activity.log`), with the address it came from.

## Unix accounts and terminals

An account can also get a unix login on the box. Its home lives under
`/data/home/<name>`, so it survives a reflash of the boot media. Each such
user can get a personal browser terminal on its own port (7701 and up) —
enable the terminals service and share `http://<box>.local:<port>` with
that user; the terminal asks for their password. In that terminal `claude`
is the Machine's one Claude — the same session as the Assistant terminal —
not a second sign-in. Detach with ctrl-b d. The Assistant terminal itself is
the admin's: it opens from the dashboard at `/terminal/` with the dashboard
session and listens only on the box's loopback.

A **share-only account** (made under Files → Network storage) is the other
kind of unix login: it exists so a network share can authenticate it, and
nothing else — no shell, no ssh key, no terminal, no dashboard sign-in. Its
one credential is its SMB password. Deleting any unix account also removes
it from every share and from samba's password store.

## Lockouts

Disabling an account locks it out immediately. You cannot delete your own
account, and the last admin cannot be demoted — a box always has one way in.
If every dashboard account is lost, the original claim password from
first setup still signs in as the admin.
