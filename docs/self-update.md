# Self-updating pipeOS boxes

A box updates its own OS from a canonical signed repo, on a daily cron, with
the same safety the manual runbook (`docs/fleet-update-runbook.md`) uses —
verified staging, atomic media swap, rollback, and a persistence guard.

This productizes the flow run by hand on 2026-08-09 to take box0/1/2/3 from
pipe 0.41.15 to 0.41.31.

## What it does

`pipeos-selfupdate` (also `pipeos selfupdate`, and the daily cron
`/etc/periodic/daily/pipeos-selfupdate`):

1. Reads `UPDATE_URL` from `/etc/pipeos/selfupdate.conf`. **Empty = disabled**,
   the shipped default — a box does not self-update until pointed at a source.
2. Fetches the remote `APKINDEX.tar.gz`, hashes it, and **exits early if it
   matches the last applied digest** (`/work/.pipeos/selfupdate.applied`), so
   the daily run is nearly free on a current box.
3. On change: downloads the repo's apks into ext4 staging and runs
   `verify-repo.sh` before anything touches media.
4. **Persistence guard.** Asserts the identity paths (`/root/.pipe`,
   `/root/.ssh`, `/root/.abuild`, `/root/.config/gh`, `/etc/ssh`) are in the
   lbu include list, adding any that are missing. This is here because box3's
   older image shipped a list that did not cover `/root/.pipe` or `/root/.ssh`,
   so a post-update reboot reverted its sign-in and root key. An update that
   can strand a box's identity is worse than no update.
5. `pipeos sync-media` — the atomic, verified, self-reverting media swap.
6. `apk update && apk upgrade -a`, then `pipeos save`.
7. `pipeos verify`; if it fails, `pipeos rollback` and exit non-zero. The
   applied digest is recorded only on a verified success.

Trust: the repo is signed and apk verifies its index against the box's trusted
keys at `apk update`. A bad mirror cannot install unsigned packages; the worst
a wrong `UPDATE_URL` does is fail verification and leave the box untouched.

## Turning it on

Point a box at a signed repo (the tree `30-build-apks.sh` builds under
`out/repo/pipeos`, so `<UPDATE_URL>/x86_64/` holds the index and apks):

    # /etc/pipeos/selfupdate.conf
    UPDATE_URL=http://192.168.254.68:8080/pipeos

then `pipeos save`. The daily cron takes it from there, or run `pipeos
selfupdate` once to apply immediately.

## The source

The mechanism needs a canonical origin the boxes can reach. Two options:

- **Pilot (today):** the BUILD box already produces the repo at
  `/work/repos/pipeOS/out/repo/pipeos`. Serve that directory over HTTP
  (`busybox httpd -f -p 8080 -h /work/repos/pipeOS/out/repo`) and it becomes
  the fleet's update origin — a closed loop where the build box publishes and
  the others pull.
- **Endgame (#651):** the netboot server serves this repo as part of the same
  infrastructure that serves the boot image, so "flash over the network" and
  "update itself" share one origin.

Publishing the repo automatically when a build lands is deliberately left to a
follow-up — this change is the box-side consumer, which is the half that was
being done by hand.
