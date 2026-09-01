# Users and access

The box has one claim password (the admin from first setup) and, optionally,
more dashboard accounts under **Users**.

## Roles

- **admin** — full control: services, streaming, files, users, saves.
- **user** — everything a viewer sees, plus the file explorer works:
  upload, download, move, rename and delete under `/work` and any shared
  drive. The role for someone who drops files on the box without running it.
- **viewer** — sees every page, changes nothing except their own password.
  The server refuses viewer mutations; the greyed-out controls in the
  dashboard are the affordance, not the enforcement.

## Unix accounts and terminals

An account can also get a unix login on the box. Its home lives under
`/work/home/<name>`, so it survives a reflash of the boot media. Each such
user can get a personal browser terminal on its own port (7701 and up) —
enable the terminals service and share `https://<box>.local:<port>` with
that user; the terminal asks for their password.

## Lockouts

Disabling an account locks it out immediately. You cannot delete your own
account, and the last admin cannot be demoted — a box always has one way in.
If every dashboard account is lost, the original claim password from
first setup still signs in as the admin.
