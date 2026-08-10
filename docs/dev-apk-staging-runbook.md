# Dev apk staging repo — rollback-safe on-box package testing

For pipeOS#8. Written by **box2 (SHIP)**; every step below touches `/etc`,
`/media/usb`, `apk`, or `lbu` — all on SHIP's hard-ban list regardless of
role, so this is authored here and executed by the Foreman, the owner, or a
box whose settings permit it (BUILD, on current permissions).

## What this buys

`out/repo/pipeos/x86_64` (built by `abuild -r` in the chroot, PR #6) is a
valid, already-signed v2 repo — but the only way to try a package from it on
a live box today is to overwrite `apks/pipeos`, which *is* the known-good
repo. `dev-push` (this PR, `overlay/usr/local/bin/dev-push`) adds a fourth
repo, `apks/dev`, staged after `apks/pipeos` in `/etc/apk/repositories`, so a
locally built package can be installed and rolled back without ever writing
to `apks/pipeos`.

## Correction to #8's own scope text

The issue says "re-index with `apk mkndx --sign-key`". That is the recipe
for `apks/extra` (CDN-fetched packages, apk-3 ADB index, established by #3).
`apks/pipeos`-format packages — which is what `out/repo/pipeos/x86_64`
already contains — are apk-**2**, indexed and signed by `abuild -r` itself in
the chroot. Re-indexing `apks/dev` with `apk mkndx` would produce a ADB index
apk parses but whose v2-signed packages fail install (repo `CLAUDE.md`,
"Two index formats — never mix the recipes"). `dev-push` uses `apk index` +
bare `abuild-sign` instead — the same two commands `30-build-apks.sh` already
runs in the chroot, relying on the same `PACKAGER_PRIVKEY` in
`/root/.abuild/abuild.conf` set up by #3, not a second key-handling path.

## One-time setup (privileged; do this once per box)

```sh
mount -o remount,rw /media/usb
mkdir -p /media/usb/apks/dev/x86_64
# an empty dir has no APKINDEX.tar.gz; verify apk tolerates a repo entry with
# no index before relying on it — NOT VERIFIED by this author, see below.
sync
mount -o remount,ro /media/usb
```

Then land the `/etc/apk/repositories` line in this PR (adds `apks/dev` after
`apks/pipeos`) and `pipeos save` (not bare `lbu commit` — see repo
`CLAUDE.md` on the power-loss window that closes).

**Not verified — flagging rather than guessing:** whether `apk update`
tolerates `apks/dev` listed with an empty/missing `APKINDEX.tar.gz` between
image-boot and the first `dev-push`. If it does not (e.g. it errors instead
of skipping), run one `dev-push` immediately after creating the directory —
even a single throwaway package — before adding the repositories line, so
the index always exists before anything resolves against it. Whoever runs
this step, please report which case it was; it's easy to make definitive
with one real test and this author has no box to try it against.

## Using it

```sh
# push a specific build:
dev-push out/repo/pipeos/x86_64/pipe-0.41.6-r0.apk
# or the whole freshly-built repo dir:
dev-push out/repo/pipeos/x86_64

# roll back — deletes from apks/dev, re-indexes, re-resolves apk:
dev-push --remove pipe
```

`apks/pipeos` (the known-good repo) is never written by either path.

## Verify drill (per #8's own acceptance bar)

1. `pipeos verify` clean before starting.
2. `dev-push` a locally built package with a higher `pkgver` than what
   `apks/pipeos` ships. Confirm `apk info -e <pkg>` shows the new version.
3. Reboot. Confirm the new version survived (`apk info -e <pkg>` again) —
   this is the real test, not the install itself: it proves `apks/dev`
   persists as media (no lbu step needed for the repo dir/contents, only for
   the one-time `/etc/apk/repositories` line) and that boot-time `apk`
   resolves it the same way a live `apk add` did.
4. `dev-push --remove <pkg>`. Confirm the box falls back to the
   `apks/pipeos`/`apks/extra` version. Reboot again, confirm the rollback
   also survived.
5. `pipeos verify` clean after.

Steps 1–5 are exactly what #8 asks for and none of them can be run from
SHIP's sandbox — reporting that boundary here is this author's part of #8,
per the fleet's own role rule ("reporting the boundary IS the correct
completion of your part").
