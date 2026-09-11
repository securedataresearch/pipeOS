# The live disk — flashing, swapping, recovering

The boot media is the box. Partition 1 (vfat, `LABEL=PIPEOS`) carries the
kernel, the modloop, the local apk repos and the apkovl that is this box's
identity; partition 2 (ext4, `LABEL=PIPEWORK`) is `/work`. Everything else
is RAM and is rebuilt from p1 at every boot (`persistence.md` on the box).
This page is how a *released image* reaches a box that is already someone's,
how a stick is replaced, and what to do when a write dies halfway.

Three ways an image lands, in the order an owner meets them:

| path | verb | what it keeps | when |
|------|------|---------------|------|
| in place | `pipeos flash apply` (dashboard: Maintenance → Live disk) | identity, `/work`, the stick | the normal case — a newer release, same hardware |
| second stick | `pipeos flash apply --to /dev/sdX` + the swap | identity; `/work` via `pipeos restore-work` | a bigger or fresher stick, or a stick that is dying |
| generic | `scripts/70-flash.sh` / `make flash DEV=` on any machine | nothing — an unclaimed box | fulfillment, a bench, a brick |

`pipeos-selfupdate` is not on this list: it updates *packages* on the media
through the verified/atomic path (`self-update.md`). A flash replaces the
whole p1, kernel and all.

## In place: `pipeos flash apply`

`pipeos flash check` compares the running image with the latest release at
`UPDATE_RELEASE_URL` (the same key self-update reads). `pipeos flash fetch`
downloads `pipeos-usb.img.xz` into `/work/.pipeos/flash` and verifies it
against the release's `SHA256SUMS` (~2 GB down, ~6 GB free needed for the
decompressed image). `pipeos flash apply` then, in order, and aborting with
nothing written at any failure:

1. Decompresses the image and reads its GPT directly (entry 1 at LBA 2).
2. Finds the box's own p1 by label, asserts it is partition 1 of the disk
   that carries PIPEWORK as partition 2, and that the label resolves to the
   device mounted at `/media/usb` (two pipeOS sticks attached is a coin toss
   for `findfs`; the write refuses rather than guess).
3. **The geometry rule**: both p1s start at sector 2048 — the layout every
   image this repo has ever built uses; anything else is a hand-partitioned
   disk this tool does not understand — and the image's p1 must fit in the
   disk's. Smaller is fine: the GPT on disk is never touched and FAT uses
   what it declares. Refusals name the sector or the two sizes.
4. Loop-mounts the image and looks inside: apkovl, APKINDEX, a kernel, and
   `verify-repo.sh` over its repo.
5. Takes the save lock, packages a fresh identity (`lbu package`) to
   `/work/.pipeos/flash/identity-<ts>.apkovl.tar.gz`, keeps a copy of the
   media's current apkovl as `previous-<ts>`.
6. **Merges** the box's identity with the image's apkovl: the image wins on
   the deployer's `DEPLOY_PATHS` (the overlay), the box keeps everything
   under `etc/pipeos`, `root/.pipe` and its profile env, the image's
   `.overlay-stamp` is taken (it describes the overlay this installs), and
   the two `world` files are unioned — nothing the box had is removed.
7. Proves the merged world resolves against the image's repos
   (`apk --simulate`, against the live root: apk-tools 3 refuses an empty
   `--root`, the one-shot's second catch on 2026-09-01).
8. Asks for the box's hostname, typed.
9. **Quiesces the media**: the modloop — the kernel-module squashfs that is
   loop-mounted *from p1* — is copied to `/run` and re-looped from RAM (RAM
   is checked first), then every mount of the p1 node is unmounted,
   last-mounted first. `fuser` must find nothing holding `/media/usb`.
10. `dd` of exactly the image's p1 bytes through the *partition node*, so
    the kernel bounds the write and p2 cannot be touched. This is the point
    of no return; the state file says so.
11. Verifies the label and the boot files, remounts, installs the merged
    apkovl as **canonical** (`pipeos.apkovl.tar.gz`, via `.new` + rename)
    **and known-good** (`pipeos.known-good.tar.gz`), and removes the old
    rotations — they name packages that are no longer on this media.
12. Stops. Reboot is the owner's move.

Progress is in `/run/pipeos/flash.state` (`step=` one of fetching, staged,
decompressing, packaging, merging, simulating, quiescing, writing, verifying,
installing-identity, done, failed) and `/run/pipeos/flash.progress` (dd's
own); the log is `/work/logs/flash.log`; `/work/.pipeos/flash.applied`
records the image digest a flash last installed, which `pipeos flash check`
and the dashboard's Live disk pill read. The dashboard runs exactly this
(`fetch && apply --yes`) with the typed name checked server-side.

## A second stick: `pipeos flash apply --to`

`pipeos flash apply --to /dev/sdX` takes a **spare whole disk** and makes it
this box's next stick, without touching the running one:

- Guards, all before anything is staged: a whole disk (`/dev/sdX`,
  `/dev/nvmeXn1` — never a partition), nothing on it mounted, not the disk
  behind `/media/usb`, `/work` or a `/dev` root ("system disk" is decided
  by what is *mounted*, not by label — an old, unmounted pipeOS stick is
  exactly the spare this is for, and says so), big enough for the image
  (with a note when less than 8 GB will be left for `/work`). The typed
  confirmation is the **device path**: the thing that can go wrong is the
  target, so the target is what the operator types.
- The whole image is written (not just p1), then the rest of the disk is
  carved as PIPEWORK with `grow.sh`'s own stanza. Carving now rather than
  at first boot matters: `grow.sh`'s gate is "no PIPEWORK label anywhere",
  and the old stick, still attached, would trip it.
- The same merged apkovl (steps 5–7 above) is installed on the new p1 as
  canonical and known-good. `flash.applied` is *not* written — it records
  what this box's own media runs.
- `/work` is **not** copied. That is `pipeos restore-work`.

Then the swap, printed at the end and repeated here:

    pipeos restore-work /work --onto /dev/sdX2     # optional, before the swap
    pipeos save                                    # state as of now, onto the CURRENT stick
    poweroff
    # REMOVE the old stick. Both say PIPEOS/PIPEWORK and the box mounts by
    # label — two attached is a coin toss.
    # Boot from the new stick (UEFI, Secure Boot off).
    pipeos restore-work /dev/<old>2                # if /work is empty: old stick back in

Keep the old stick until the new one has booted and saved once. Two sticks
attached at boot is the one configuration nothing here can make safe;
in-place `apply` refuses it outright (step 2 above), the swap text says
REMOVE for that reason.

### `pipeos restore-work`

`pipeos restore-work SRC [--force] [--onto DEV]` brings `/work` back from a
`pipeos backup` root (its `work/` is used), that `work/` itself, any
directory holding a `/work` tree, or a **device** — the old stick's p2,
mounted read-only for the copy and released after. The destination is the
mounted `/work`, or with `--onto` an *unmounted* ext4 partition: the new
stick's p2, populated before the swap so the new stick boots with its data
in place.

It is **additive**: `rsync -a`, never `--delete`. On the empty `/work` a
swap produces, additive *is* a mirror; on a `--force`d non-empty `/work`,
`--delete` could erase a repo newer than the backup — unrecoverable — while
a stale leftover is visible and fixable. Never restored: `/.pipeos` (this
box's runtime state: flash staging, stamps, clean-shutdown marks), `/logs`
(this boot's history), `/cache`, `lost+found`. One exception:
`.pipeos/users.manifest` is carried over when the destination has none, so
`pipeos-user` re-adopts the same uids. A `/work` with anything beyond
`lost+found`, the directories `workspace.sh` creates and the empty
`claude/projects` it lays down needs `--force`; the refusal names the first
entry it found.

### The vault on a moved stick

The box's secrets ride the apkovl sealed to the chassis (#244). A stick
that boots in a different machine comes up with the dashboard and a locked
vault; the owner types the recovery phrase under Secrets and the vault
re-seals to the new chassis. `pipeos flash apply --to` and `restore-identity`
carry the sealed file unchanged.

## Power loss, and the two apkovls

**Mid-write** (step 10, or the `--to` dd): p1 is unbootable. Nothing else
is lost — identity copies were written to p2 *before* the write
(`/work/.pipeos/flash/identity-<ts>` and `previous-<ts>`), and to any
`pipeos backup` disk. Recovery: flash the generic image from any machine
(the fulfillment recipe below or `make flash DEV=`), boot it, and run
`pipeos flash restore-identity /work/.pipeos/flash/identity-<ts>.apkovl.tar.gz`
(a backup's `identity/pipeos.apkovl.tar.gz`, `.enc` accepted, works the
same) — it integrity-checks the bundle and stages it as the next boot's
apkovl via `pipeos rollback`. For the `--to` case the running box is
untouched: retry.

**Any other time**: the media holds two apkovls. The **canonical** one
(`pipeos.apkovl.tar.gz`) is what boots; `pipeos save` writes it so that it
exists at every instant except inside a single `rename(2)` — plain `lbu
commit` renames the old one away *before* installing the new, and a power
cut in that window boots an unprovisioned box (no ssh keys, no identity),
which is why `pipeos save` replaced it. The **known-good** one
(`pipeos.known-good.tar.gz`) is the last apkovl that booted with nothing
CRITICAL: `pipeos-selfcheck --boot` promotes canonical to known-good after
a healthy boot (warnings do not block promotion — a full-ish disk does not
make the *state* bad, and blocking on it would freeze known-good forever).
`pipeos rollback known-good` stages it; the boot report says when a
promotion happened. A flash installs the merged apkovl as *both*, because
the previous known-good names packages the new media does not carry.

`pipeos verify` must PASS before and after anything that touches the
media; it is the last row of every drill.

## The generic image, from any machine

    curl -fLO https://github.com/securedataresearch/pipeOS/releases/latest/download/pipeos-usb.img.xz
    curl -fLO https://github.com/securedataresearch/pipeOS/releases/latest/download/SHA256SUMS
    sha256sum -c --ignore-missing SHA256SUMS
    xz -dc pipeos-usb.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync

In this repo, `make flash DEV=/dev/sdX` runs `scripts/70-flash.sh`, which
adds the guards the recipe above lacks: whole disk only, nothing mounted,
not the disk behind the host's `/`, a typed device path, and `--box NAME`
against `fleet/serials.txt` for the internal fleet. The released image has
only p1; first boot carves p2 (`grow.sh`), and the first visitor claims the
box (`web-wizard.md`). A stick flashed this way is nobody's: fulfillment's
rule is that a reflash is the only clean unclaim.

## basho_box runbook (historical)

basho_box was the reference box until 2026-09-10, when it was reflashed as
a plain Machine of Sam's cluster (every Machine is GENERIC; the OS carries
no role like "the stream box" — docs/cluster.md). Its card is gone; the
findings below still stand. It ran the first real in-place flash on
2026-09-01. Three things the probes had not caught
(#196): busybox `tar` has no `--owner/--group` (the merge re-tar died,
pre-write, and the gates held); apk-tools 3 refuses to conjure a database
in an empty `--root`, so the world gate simulates against the live root;
and `lsblk` does not exist on the box, which is why every guard reads
`/proc/mounts` and `/sys` directly. The drill that gates a change to any
of this, on basho_box with a spare stick:

1. `pipeos verify` — PASS, before.
2. `pipeos flash check` → `pipeos flash fetch`.
3. `pipeos flash apply --to /dev/sdX` (type the path).
4. `pipeos restore-work /work --onto /dev/sdX2`.
5. `pipeos save`, `poweroff`, swap the sticks, boot.
6. `pipeos verify` — PASS; the boot report DM says all green; `/work` is
   populated; `pipeos status` shows the new image.
7. Swap back to the old stick and boot once more — it must still be a
   valid box (nothing on it was touched).
8. In-place drill, same box: `pipeos flash apply`, reboot, `pipeos verify`
   PASS, boot report green.

Record the timings and anything a stranger would trip on in the PR that
prompted the drill, the way #196 did.
