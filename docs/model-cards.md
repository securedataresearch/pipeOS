# Model cards — one declarative file per machine

For securedataresearch/pipe#650. Written by **box2 (SHIP)**.

A pipeOS box's identity — who owns it, who may assign it work, which board it
reads, what its agent may run — is currently typed onto each machine by hand.
Three boxes provisioned from one image have already diverged, and nobody can
say from outside a box what is on it. A model card makes the machine's identity
a file, and everything else output.

## The card

One file, `KEY=value`, no quoting, no execution:

```
NICK=box3
ROLE=BUILD
OWNER_NICK=sam
FOREMAN_NICK=shrek
COHORT_ID=3
GH_OWNER=securedataresearch
MAC=
```

A card is **parsed, never sourced**. Under #651 it arrives over HTTP from the
boot server keyed on `MAC`, so it must not be able to run code on the box that
reads it. Unknown keys and values that are not plain identifiers are refused
rather than quoted-and-hoped.

`ROLE` is one of `BUILD`, `TEST`, `SHIP`, or empty. Empty values throughout are
legal and mean *unprovisioned*: the agent takes direction from nobody, which is
the right posture for a box that has not been told who owns it. `--strict`
refuses an incomplete card, and is what a provisioning run should use.

## What it generates

| output | what varies by card |
|---|---|
| `/etc/pipeos/pipebox.conf` | every field |
| `/etc/pipeos/pipebox-settings.json` | the per-role allow block only |
| `/etc/pipeos/mandate.md` | nothing today — one text, all boxes |

`mandate.md` is generated even though it does not vary, because the point is
that it is *deployed and stamped* rather than hand-installed. It is also the
file that most needs to be identical everywhere: an agent whose mandate differs
from its siblings' is the failure this issue exists to prevent.

It carries no generated-by header, unlike the others. It is fed to the model
verbatim as its own instructions, and generator chatter does not belong in
them. Its provenance is the stamp.

```sh
pipebox-card generate --card /etc/pipeos/card.conf   # write the derived files
pipebox-card verify                                  # do they still match?
pipebox-card show                                    # what does this card say?
```

## Hand-editing a derived file is detectably wrong

`/etc/pipeos/.card-stamp` records the card's sha256 and each derived file's
sha256 at generation time. `verify` then answers two different questions:

- **a derived file's hash differs from the stamp** → somebody hand-edited it
- **the card's hash differs from the stamp** → generation is stale

and separately re-renders into a temp directory to confirm the generator is
byte-stable, because if it is not, neither of the other two answers means
anything. Byte-stability is why the output carries no timestamps and emits keys
in a fixed order.

Exit codes are the API:

| code | meaning |
|---|---|
| 0 | derived files match the card |
| 1 | divergent — hand edit, or stale generation |
| 2 | **cannot tell** — no card, no stamp, unreadable template |

2 exists on purpose. "This box has no card" and "this box matches its card" are
different answers, and a checker that returns 0 for both is the fail-open shape
this repo has spent three PRs removing (#15, #16, and the `pipeos-selfcheck`
finding in pipeOS#19). The caller decides what to do about 2; the tool will not
decide by pretending it checked.

## What the card must not try to enforce

It is tempting to express *"TEST cannot push product code"* as a settings rule.
It cannot be, and it should not be attempted here: `git push` is one command
whose legitimacy depends on the branch and the files, which a command allowlist
cannot see. That fence belongs on the credential, which is where pipe#648 puts
it.

So the card **generates the settings and records the role**; the platform
enforces what the role may write. Two layers claiming to enforce the same rule
is how a later reader ends up unable to tell which one is authoritative — and
how one of them silently stops being true.

The per-role settings delta is correspondingly thin, and honestly so: BUILD and
TEST get the Rust toolchain, SHIP gets nothing extra. The fences that matter
are the deny list and the token.

## Provisioning a box

```sh
# 1. drop the card
$EDITOR /etc/pipeos/card.conf

# 2. generate
pipebox-card generate --strict

# 3. sign in, then persist (operator — `pipeos save` is hard-banned for agents)
pipebox-setup
pipeos save

# 4. prove it
pipebox-card verify && pipeos verify
```

Step 4 is not ceremony. `pipeos verify` currently **FAILs on box2** — six
packages in `world` cannot be installed from its media, so a reboot does not
reproduce the running state. A card generates correct config onto a base that
cannot rebuild itself; it does not fix the base. See
`docs/fleet-update-runbook.md`, Path 0.

## Repo-side gate

`make check-cards` (or `./scripts/check-cards.sh`) renders every card in
`docs/cards/`, checks the settings parse as JSON, asserts every hard ban is
still in the deny list, renders twice to prove byte-stability, and asserts the
committed `overlay/etc/pipeos/*` is exactly what the shipped card produces. It
runs in CI on every push.

`make cards` regenerates the committed overlay. If the gate fails, that is the
fix.

## Status

**None of this has been executed on a box.** `make` is not installed on box2
and the agent sandbox does not permit running shell scripts, so the generator
was written and hand-checked against its templates but never run here — the
same position box0 and box1 have been in all day with `cargo`. The CI job added
in this branch is the first thing in this repo that actually runs it, and it is
the gate this should be judged on, not on my reading of it.

Not yet card-generated, and each needs its own change:

- **`pipebox-cohort-watch`** — the cron poller that is the only way any box
  hears its board. It exists on three machines and in zero commits; it is not
  in this repo, and I cannot read `/etc` from the sandbox to commit it. A box
  provisioned from this image today gets no board poller at all.
- **The GitHub credential.** Per-role PATs (#648) are a secret, not card
  content. The card should record the *role*, and provisioning should pair it
  with the matching token; the card must never carry the token.
- **Where `fleet/` lives** under netboot. Specified in the #651 design, and
  deliberately not decided a second time here.
