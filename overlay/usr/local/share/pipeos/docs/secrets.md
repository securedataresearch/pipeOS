# Secrets

Everything this Machine has to keep to itself — the Claude token, the
vendor-support key, the streaming keys, the assistant terminal's password,
the network-storage logins — lives in **one sealed file** on the boot
stick. The services read it through the box's memory only; the stick and
every clone of it carry the sealed file and nothing in the clear.

## Sealed to this Machine

The file opens in the chassis it was claimed in. Move the stick to a
different box and the dashboard still comes up, but every secret is
unavailable: the boot report says *vault is LOCKED*, and the services that
need a secret wait. That is on purpose — a stick on a desk, or in the
wrong box, tells nobody your keys.

To open it elsewhere you need the **recovery phrase**: eight groups of
four characters, shown **once**, at claim (or under Secrets the first time
this Machine boots with the vault). Write it down. It is not stored
anywhere on the box, and there is no other way in.

Under **Secrets**:

- **Locked?** Type the phrase. The vault re-seals itself to the Machine it
  is in now, the services start, the phrase keeps working.
- **New phrase** — the old one stops working the moment the new one shows.
- **Reveal** — a value, once, after re-typing your own password.
- **Add** — anything else you want the box to keep: a key a scheduled job
  needs, a token for a service. Named `jobs.something` it reaches jobs as
  `SOMETHING`.

The Claude token, support key, stream keys, terminal password and SMB
logins are set from their own cards (Setup, Services, Streaming, Files);
Secrets lists them and can delete them, nothing more.

## What stays outside

Your admin password (a hash) and the dashboard's own certificate are not
in the vault — they are what lets you reach the page to type the phrase.
The pipe sign-in keys are pipe's own and stay where pipe keeps them.

## Backups and clones

A backup or a second stick carries the sealed file as-is. It opens in the
same chassis, or anywhere with the phrase. A backup you hand to someone is
still just a file to them.
