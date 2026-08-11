# Fleet update runbook — getting the boxes onto a current pipe

For securedataresearch/pipe#645. Written by **box2 (SHIP)**; the privileged
half is executed by the Foreman or the owner, never by a box.

## Path 0 — the media is already broken, and this comes first

Found on box2, 2026-08-07, by running `pipeos verify` for the first time:

```
ok    pipeos repo: index matches 5 apk(s)
FAIL  world does not resolve: ERROR: unable to select packages:
FAIL  in world but NOT installed: diffutils findutils gawk github-cli grep sed
ok    canonical apkovl readable (15825844 bytes)
verify: FAIL — a reboot will NOT reproduce this state
```

Six of the 53 packages in `overlay/etc/apk/world` cannot be installed from this
box's media, and the world constraint set as a whole does not resolve. This is
the general form of the `github-cli` hole already noted on #645 — that package
was not special, it was the one somebody happened to need.

**Do this before any version work.** Updating `pipe` on media that cannot
satisfy its own `world` means the next boot still fails to converge, and
`pipeos verify` still says a reboot will not reproduce the running state. The
update would be built on a base that does not rebuild.

`extra-add` is the tool for it — one-shot network fetch into the local extra
repo, re-index, re-sign, install. **Two invocations, not one**, because the six
packages do not all live in the same Alpine repository:

```sh
extra-add diffutils findutils gawk grep sed      # Alpine main
extra-add --repo community github-cli            # community
lbu status && pipeos-save
# then reboot and re-run `pipeos verify` — it must reach PASS
```

Confirm the repo split before running rather than trusting this line: I could
not query the Alpine indexes from the box, so `github-cli` being in community
and the other five in main is from knowledge of Alpine's layout, not from a
check. `extra-add` fails loudly on an unknown package, so the cost of being
wrong is one error message rather than a bad media write.

Three things `extra-add` already handles that a hand-rolled fetch would get
wrong, listed so nobody is tempted to shortcut it:

- **noarch subpackages** (`-openrc`, `-common`, `-doc`) must live in
  `../noarch`, because apk 3.x resolves each package from `<repo>/<pkg-arch>/`.
  It moves them by reading `.PKGINFO`.
- **apk 3.x indexing** — `apk mkndx --sign-key` writing an ADB index. The
  apk-2.x `apk index` + `abuild-sign` recipe from most Alpine documentation
  produces an index apk parses but whose packages fail to install.
- **Free-space floor and rw/ro remount discipline** on the vfat media, with
  the remount restored afterwards.

Every command above is hard-banned for a box — `apk`, `extra-add`, `mount`,
`/media/usb`, `lbu`, `pipeos-save`. This section is written for the operator.

### Why this belongs to #645 rather than its own issue

#645's third acceptance criterion is that the path survives a reboot. On box2
that criterion is failing *today*, before anything is updated, and for a
reason unrelated to the pipe version. Fixing the version without fixing the
media satisfies criteria 1 and 2 and leaves 3 broken — which is the state the
fleet is already in.

**It also gates the #650 pilot.** That issue's acceptance is a factory
ThinkCentre reaching full fleet membership from boot media plus one card plus
two secrets. A box provisioned tomorrow from media whose `world` does not
resolve cannot meet that bar, and the pilot would be testing card tooling
against a base that fails verify — learning nothing clean about either.

## What #645 assumes, and what is actually true

#645 says no musl binary ships, so the only remaining path is manual. That was
true when it was filed against *published* artifacts. It is no longer true of
the tree:

- `crates/pipe-protocol/src/platform.rs` lists `linux-x86_64-musl` in
  `CLIENT_PLATFORMS`, and `detect_client_id()` resolves a musl host to
  `pipe-linux-x86_64-musl` rather than declining. There is a test
  (`a_musl_host_selects_the_musl_asset`) and a second test
  (`client_matrix_covers_every_platform`) that fails CI if the platform table
  and `cd.yml`'s matrix drift apart.
- `.github/workflows/cd.yml` carries all five client legs, musl included.

So the fix #603 called for is **already on main**. The reason the fleet still
sees

    [WARN] auto-update check failed, error=no published release asset for this
    host (OS=linux, ARCH=x86_64, libc=musl) — update through your package manager

is narrower and more fixable than "nobody's job":

| | |
|---|---|
| `v0.41.29` released | 2026-08-06 **01:33:41Z** |
| commit `8ee13a78` *"fix(release): ship a musl client…"* landed | 2026-08-06 **02:11:03Z** |

The musl fix landed **37 minutes after the last release**, and nothing has
released since. Confirmed by the release run itself — run `31062794234` ran
exactly two client legs, `client · Linux x86_64` and `client · macOS aarch64`;
the other three matrix entries did not exist yet. Every published release,
including `v0.41.15` that the boxes run, carries the same three assets:
`pipe-linux-x86_64-gnu`, `pipe-macos-aarch64`, `SHA256SUMS`.

**Consequence: no code change is needed to publish a musl asset. A release is.**

## The reboot constraint, which the acceptance criteria collapse

#645 accepts *either* "a published musl release asset the daemon's auto-update
accepts" *or* "a scripted build-and-sync". **On a pipeOS box those are not
interchangeable, and the first cannot satisfy criterion 3 on its own.**

The root filesystem is tmpfs, rebuilt every boot from the boot media; software
comes from the local apk repos and `/etc/apk/world`. A self-update writes a new
`/usr/bin/pipe` into tmpfs. It works, `pipe --version` reports the new number —
and the next reboot restores whatever the `pipe` apk on the media contains. The
box silently reverts.

So for the fleet:

- **The published asset** removes the nobody's-job failure mode for ordinary
  hosts and makes the pipeOS build reproducible. It does not, by itself, make a
  box current across a reboot.
- **The apk on the media** is the only thing that survives a reboot. Criterion 3
  is a statement about the media repo, not about the running binary.

Both are wanted, for different reasons. Landing only the first would produce a
fleet that reports itself current and reverts overnight — a worse failure than
today's, because today's is at least visible in the log.

## Path A — publish the musl asset (unblocks every host, not reboot-durable here)

Two ways to cut the release that ships it. Neither needs new code.

**A1 — let the next real merge do it (preferred).** `cd.yml` bumps on any
commit touching `crates/`, so the next product merge releases and the musl
asset appears with it. #643 is in the review queue and touches `crates/`;
merging it publishes `pipe-linux-x86_64-musl` as a side effect. Zero extra
risk, zero extra steps.

**A2 — rebuild the existing tag by hand.** `cd.yml`'s `workflow_dispatch` path
skips the bump and rebuilds an existing tag, and the release step uploads with
`--clobber`, so it back-fills assets onto the release that already exists:

    gh workflow run cd.yml --repo securedataresearch/pipe --ref main -f ref=v0.41.29

Read this before running it: the dispatch path does not stop at the client
binaries. It rebuilds the relay image, pushes DOCR `:latest`, and App Platform
redeploys off that push. It is the *same* version, so the redeploy should be a
no-op in behaviour — but it is a production deploy, and the job then blocks for
up to ten minutes waiting for `/version` to answer. **Not a box's call.**

Verify either way:

    gh release view v<version> --repo securedataresearch/pipe \
      --json assets --jq '[.assets[].name] | join(" ")'

Expect five `pipe-*` binaries plus `SHA256SUMS`. Today you get two plus one.

## Path B — the apk on the media (the one criterion 3 is about)

Runs on a **build host**, not on a box: `30-build-apks.sh` needs `abuild` in the
Alpine chroot, and no box has a Rust toolchain (box0 has none, box1 has one
off-`PATH`, box2 has none).

    # 1. sources
    export PIPE_SRC=/path/to/pipe            # or /work/repos/pipe; see config.sh
    git -C "$PIPE_SRC" fetch --tags && git -C "$PIPE_SRC" checkout v<version>

    # 2. chroot, then the musl payload + the three apks
    make chroot
    ./scripts/20-build-pipe.sh               # -> out/payloads/pipe, pipe.version
    ./scripts/30-build-apks.sh               # stamps pkgver, abuild -r, signs the repo

    # 3. gate before anything touches media
    ./scripts/verify-repo.sh

Then, **per box, privileged, operator-executed** — every line below is on the
boxes' hard-ban list and is listed here so the operator has the exact sequence,
not so a box can run it:

    pipeos sync-media                        # verifies, swaps atomically, rolls back
    apk upgrade -a pipe
    pipeos save                              # atomic; not plain `lbu commit`
    pipe --version                           # criterion 2
    # reboot, then re-check `pipe --version`  # criterion 3

`pipeos sync-media` rather than a hand copy: the 2026-08-05 incident in
`CLAUDE.md` was a hand-staged media directory that produced a "v2 package
integrity error" at boot and a box that came back without claude-code, silently.

## A simplification Path A unlocks, worth doing after it lands

`20-build-pipe.sh` cross-compiles the musl binary, which is why a build host
needs `musl-gcc` or a mounted chroot with `rust cargo build-base` in it. Once a
release actually carries `pipe-linux-x86_64-musl`, that step can instead fetch
the published asset and verify it against `SHA256SUMS`.

That is the change that makes "one command per box" honest: it removes the Rust
toolchain from the update path entirely, so the media rebuild becomes download,
verify, `abuild -r`, sync. It also makes the binary on the media byte-identical
to the one every other host self-updates to, which is worth more than it sounds
when a box's verdict on a bug is supposed to mean something.

Not proposed as part of #645 — it depends on Path A having landed, and it is a
change to the build, which wants its own issue and its own review.

## What box2 could not do, and who has to

- **Build anything.** No `cargo`, no `rustc`, no `/work/buildroot` on box2;
  `apk` is hard-banned, so installing a toolchain is not available either. The
  musl apk in Path B is specified here, not produced.
- **Verify the fleet's current version.** `pipe --version` is refused by the
  sandbox on box2 (`pipe status` is permitted, `pipe --version` is not), and
  box0 reported the same refusal. **No box can perform acceptance criterion 2
  on itself.** The `0.41.15` figure in #645 is inherited from the issue, not
  independently confirmed by any box. Whoever runs this should read the version
  from outside the sandbox and not take a box's word for it.
- **Apply any of Path B.** By design — `apk`, `mount`, `/media/usb`,
  `pipeos sync-media`, `pipeos save` are all hard-banned. SHIP prepares, the
  Foreman applies.

## Acceptance, mapped

| # | Criterion | Path | Status |
|---|---|---|---|
| 1 | repeatable documented update path | A + B | this document; A1 needs a merge, B needs a build host |
| 2 | fleet at current release, `pipe --version` | B | **not verifiable by any box** — sandbox refuses the command |
| 3 | survives a reboot | **B only** | A alone reverts at boot; tmpfs root |

## Deploying the cohort-watch fix — run `--seed`, or the first wake is the old cost

Added by **box1 (BUILD)** after pipeOS#61/#62. Same shape as everything above:
the boxes prepared it, only the Foreman or the owner can apply it.

pipeOS#62 changed `pipebox-cohort-watch` to feed the agent only *unseen*
replies instead of the whole changed thread — measured at 207859 bytes versus
7050 for one new reply on thread 74, ~96%, per box per wake. **Merging it to
this repo did not deploy it.** The running script is the installed overlay
copy, so every box keeps paying the old cost until the overlay is installed.

**A box cannot read the installed script, but it CAN tell whether the new one
has run.** Those are different questions and only the first is fenced: the
agent sandbox refuses `/usr/local/bin/pipebox-cohort-watch`, but the new code
writes `/work/pipebox/state/cohort-<id>.cursors` on every tick that has a
changed thread, and `/work` is readable. Absent cursors file on a box whose
`.seen` is recent means the old watcher is still running:

```sh
. /etc/pipeos/pipebox.conf                     # sets COHORT_ID for this box
cid=${COHORT_ID:-3}                            # same fallback the watcher uses
ls -l /work/pipebox/state/cohort-$cid.seen \
      /work/pipebox/state/cohort-$cid.cursors
```

Measured 2026-08-11 on two boxes independently, both with a `.seen` only
minutes old and **no cursors file at all** — box3 (who found this check) and
box1. Both were still on the old whole-thread watcher, observed rather than
assumed.

Do not write this off as unknowable. An earlier draft of this section called
"is the fix live" unanswerable from inside a box, which was wrong in the
direction that matters: it discouraged the check that works, and "unknowable,
therefore accept the risk" is the fail-open shape §5 exists to name.

**After installing the overlay, run this once per box, before the next cron
tick:**

```sh
pipebox-cohort-watch --seed
```

Why it is not optional. The fix keeps per-thread reply cursors in a new file,
`/work/pipebox/state/cohort-<id>.cursors`. A fresh install has no such file,
while `$SEEN` survives (it lives on `/work`, not in the apkovl). So the first
thread that changes after the deploy has no cursor, reads as a first sighting,
and is delivered **whole** — one full-thread wake per box, which is the exact
cost the change exists to remove. `--seed` writes both files together and
suppresses that.

**A second reason, added with the pipeOS#64 fix:** `$SEEN`'s line format
changed from `<id> <count>` to `<id> <count> <last_reply_ts>`. A surviving
two-field `$SEEN` matches nothing, so *every* thread reads as changed on the
first tick — and with no cursors yet, every one of them is delivered whole.
The two reasons compound rather than overlap, and `--seed` is what closes
both.

### Acceptance check — run it, do not assume the deploy took

```sh
. /etc/pipeos/pipebox.conf                       # sets COHORT_ID
cid=${COHORT_ID:-3}                              # same fallback the watcher uses
ls -l /work/pipebox/state/cohort-$cid.cursors    # MUST exist after --seed
```

**Read `COHORT_ID` from the box, never paste a literal.** Every box is cohort 3
today, so a hardcoded `cohort-3` would work right now and fail silently the
first time this runbook is used for its other purpose — provisioning a new
machine, which is exactly pipe#650's acceptance criterion (a factory box
joining from one card). On a box in another cohort the literal path is simply
absent, the operator reads that as a failed deploy, and redoes one that had
worked. A check that reports failure on a success is worse than no check.
(box3, reviewing — flagged as a copy-paste hazard rather than a wording nit.)

The `${COHORT_ID:-3}` fallback is not decoration: the checked-in
`overlay/etc/pipeos/pipebox.conf` ships `COHORT_ID=""`, and
`pipebox-cohort-watch` itself defaults to `3` when it is unset. A bare
`$COHORT_ID` would build `cohort-.cursors` on an unprovisioned box and report
the same false failure by a different route. The check has to agree with the
watcher about which file it writes, so it copies the watcher's fallback.

If the file is missing, the deploy did not take or `--seed` did not run, and
the box is still on the old watcher. **A deploy step with no acceptance check
is how the #58 fix got lost** — it looked landed because CI was green on a
commit that had no route into `main`. Same failure shape, one layer out: an
install looks done because the merge is done.

Reading a *wake* instead is slower and more ambiguous, and worth stating so
nobody substitutes it for the check above: a wake that delivers a whole thread
right after a deploy does **not** prove the fix is absent, because an unseeded
first sighting looks identical. Only a *later* wake delivering just the new
replies is conclusive. (box3 proposed the wake test first, then found the
cursors check — faster and unambiguous — and noted it only exists because the
file's location is documented here.)
