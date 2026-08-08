# Fleet update runbook — getting the boxes onto a current pipe

For securedataresearch/pipe#645. Written by **box2 (SHIP)**; the privileged
half is executed by the Foreman or the owner, never by a box.

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
