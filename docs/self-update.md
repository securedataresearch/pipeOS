# Self-updating pipeOS boxes

A box updates its own OS from a canonical signed repo, on an hourly cron, with
the same safety the manual runbook (`docs/fleet-update-runbook.md`) uses —
verified staging, atomic media swap, rollback, and a persistence guard.

This productizes the flow run by hand on 2026-08-09 to take box0/1/2/3 from
pipe 0.41.15 to 0.41.31.

## What it does

`pipeos-selfupdate` (also `pipeos selfupdate`, and the daily cron
`/etc/periodic/daily/pipeos-selfupdate`):

1. Reads `UPDATE_RELEASE_URL` (the product path, shipped pointing at this
   repo's Releases) and `UPDATE_URL` (the pilot/fleet path) from
   `/etc/pipeos/selfupdate.conf`. Release URL takes precedence; **both empty
   = disabled**.
2. Probes for change cheaply — release mode hashes `SHA256SUMS`, URL mode
   the remote `APKINDEX.tar.gz` — and **exits early if it matches the last
   applied digest** (`/work/.pipeos/selfupdate.applied`), so the daily run
   is nearly free on a current box.
3. On change: fetches the repo (release mode: `pipeos-repo.tar.gz`, checked
   against `SHA256SUMS`; URL mode: each apk) into ext4 staging and runs
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

## The origin — `UPDATE_RELEASE_URL`

The shipped default (owner decision, 2026-08-30: silent daily self-update
is the client posture):

    # /etc/pipeos/selfupdate.conf
    UPDATE_RELEASE_URL=https://github.com/securedataresearch/pipeOS/releases/latest/download
    UPDATE_URL=

A release is a flat asset directory: `SHA256SUMS`, `pipeos-repo.tar.gz`
(the signed repo, `APKINDEX.tar.gz` at its root) and, when the image is
fresh, `pipeos-usb.img.xz` — published by `make release`
(`scripts/80-publish-release.sh`, run on the build workstation because the
signing key never enters CI). `SHA256SUMS` is the change probe; the daily
run on a current box fetches only that. The same key is what `pipeos flash`
and the dashboard's Live disk row read, so one origin answers both "is
there a newer package set" and "is there a newer image" — but the *image*
is a flash, not an update; see `live-disk.md`.

Blank `UPDATE_RELEASE_URL` and the box never self-updates
(`build-your-own.md` — your own origin, or none). `pipeos-selfcheck` warns
when the origin has never applied, is more than 14 days stale, or last
errored; a dead origin is loud, never silent.

## The pilot path — `UPDATE_URL`

For a fleet fed from a dev box: point `UPDATE_URL` at a signed repo (the
tree `30-build-apks.sh` builds under `out/repo/pipeos`, so
`<UPDATE_URL>/x86_64/` holds the index and apks), blank the release URL,
`pipeos save`. The daily cron takes it from there, or run `pipeos
selfupdate` once to apply immediately.

    # /etc/pipeos/selfupdate.conf
    UPDATE_RELEASE_URL=
    UPDATE_URL=http://192.168.254.68:8080/pipeos

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

## The image, too — automatic (#275)

Sam, 2026-09-12: "any time there is an update boxes need to update themselves
like windows" — reboot window: "frickin whenever". So the hourly run's first
step is the **image**: if the latest release's tag names a commit other than
the one in the running `pipeos-image.txt` (or its image digest differs from
what a flash last applied), the box `pipeos flash fetch`es it (verified),
`apply --yes` in place (identity merged, saves fenced until the reboot —
docs/live-disk.md step 12), writes `/work/.pipeos/image-updated`, and
**reboots**. The boot report then says "this boot is a self-applied image
update". It holds, and retries next hour, while a scheduled run holds the
schedule lock or any terminal session is live. Default **on**, client boxes
included; `pipeos selfupdate image off` (System → update automatically) turns
it off — packages still update, a new image then waits for `pipeos flash
apply`. Packages-only step is unchanged and runs when no image was applied.
