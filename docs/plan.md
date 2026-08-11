# The Pipeline as a Cuckoo Clock — complete plan

## Context

Sam's standing goal: solve every open issue unless gated by a serious design
decision — under three constraints discovered the hard way today: the shared
agent-session pool is the binding energy source (it capped and stalled the
fleet 100 min), the Foreman is the heaviest single consumer, and board bytes
are multiplied by members × future wakes. Sam asks for the plan drawn as a
mechanical device, and the metaphor is load-bearing: **in a good clock no part
moves unless the mechanism moves it, every motion is bounded, and the
escapement meters a finite spring so the clock runs all day instead of
unwinding in an hour.**

Current state: 29 open cards (10 In progress, 10 Ready, 9 Backlog), zero open
PRs, fleet self-merging on gates, claims discipline holding.

## The mechanism, mapped

| clock part | pipeline part | state |
|---|---|---|
| **Mainspring** (finite energy) | shared Claude session pool | caps early; rewinds on Anthropic reset |
| **Escapement** (meters release) | watcher: wake gating + per-task sessions | half-built — #70 merged, #50/#43 not |
| **Gear train** (transmits work) | card → claim → branch → PR → verdicts → merge | works; one gear (BUILD push funnel) binds |
| **Pendulum** (cadence) | 2-min cron tick; 50-min Foreman sweep | tuned today |
| **Chime** (output only on event) | netgaze events, terse board, PRs | board diet in effect |
| **Plates/frame** (rigid substrate) | model cards, charters, branch protection, gates-as-config | protection added today; cards partial |
| **Winding key** (external energy) | Sam: decisions, PATs, sign-in keys | 3 winds pending (below) |
| **Maintenance** (oiling) | IT automation: GC, toolchain, credentials, markers | GC merged; #51/#52 in flight |
| **The dial** (why it exists) | the product: 671/680/659/672 | revenue model decided; build pending |

## Phase 1 — Finish the escapement (multiplies everything after)

The spring is fixed; only the escapement changes how long it lasts.

1. **pipeOS#50 + #43 as one change** (box0, claimed lane): watcher wakes an
   agent only when `(addressed OR lane non-empty)`; skip is logged. Gate:
   pairs mandatory — relevance gating without standing orders = permanently
   idle boxes.
2. **pipeOS#68 acceptance measurement** (box3): delta-feed already merged via
   PRs #69/#71; remaining scope is the measured before/after wake size
   (<20KB on a 100-reply thread) on a live box.
3. **pipeOS#64** (box0 after #50): evicted-reply delivery gap — now live
   because #69 unmasked it. Fix shape: cursor must not advance past an
   undelivered eviction.
4. **pipeOS#51 residual** (box3, claimed): verify PR#71's fresh-session
   default fully covers the reboot case; close with a reboot test (Foreman
   runs the reboot — operator fence).

**Escapement done when:** a board delta wakes only the boxes that need it,
each in a fresh bounded session, and a full day of normal traffic does not cap
the pool. Measurable in the watcher logs + netgaze agent-health row.

## Phase 2 — Free the binding gear (BUILD funnel)

5. **Sam winds the key: PAT scope change (#60)** — TEST/SHIP get Contents
   write; `main` is already protected (done today, both repos). Then every box
   pushes its own branches; box1 only merges. Retires the format-patch flow.
6. **Required status checks on `main`** (Foreman, careful, separate change):
   make green CI enforced rather than conventional (R3). Context names differ
   per repo; get them from a green run, not from memory.
7. **pipeOS#59 phase 1: self-hosted CI runners** on 2 boxes (box1 specs,
   Foreman registers — runner tokens are operator secrets). Shares the
   idle-and-clean predicate with GC (#41, merged). Cuts gate latency at zero
   pool cost. Mutation-testing/fuzz nightlies are phase 2, after runners
   prove stable.

## Phase 3 — The dial: product wave (revenue model into code)

Order follows Sam's revenue rulings (free acquisition → paid add-ons →
enterprise). All BUILD implementation, spec-first where specs are thin:

8. **pipe#680** (box3 claimed): delete membership gates (they reference a
   subscription that no longer exists) — first-auth nick claim, free;
   lapse sweep stops touching primary nicks (Sam ruling R-recorded).
9. **pipe#671** (box2 spec → box1 build): five-line price table; spec opens
   with "there is no membership subscription". Avatar + bio = new build
   scope on identity surfaces.
10. **pipe#659 slice 4** (box1): interaction layer — last structural slice.
11. **pipe#672** (box2 → Sam picks): mockup variants of the board list per
    `docs/mobile.md`; **Sam's pick is the design gate**; implementation lands
    as slices only after the pick. box0 runs the CVD gate on the chosen
    palette.
12. **pipe#658 finding-4 tail + #649 remainder** (box1): last signalled-cutoff
    work; then 649 closes.
13. **pipe#652** (blocked on **Sam's sign-in key**): Foreman drives the live
    re-key repro with box0's trace points; then fix lands.

## Phase 4 — Plates: declarative substrate (Foreman self-reduction)

14. **pipe#650 item 9** (box1): policy.json generated from card; **external
    audit** (Foreman/CI reads live boxes — they cannot self-report). Closes
    #650. The mandate migration for box0/1/2 rides the same change (box1's
    method proposal).
15. **pipeOS#56 card types** (box1, after 650): Gate card and Lane card first
    (transcription of R3/R6/R13/R16 and the standing lanes); Cohort card
    next; Secrets card with 650-item-9 machinery; Runbook card last. Each one
    deletes a class of Foreman turns — I am the heaviest pool consumer, so
    this is now energy work, not tidiness.
16. **pipeOS#39 generator wiring residual** (verify closed by the merged #45
    + charter; close the issue).

## Phase 5 — Long tail, in order

17. **pipe#655** (greenlit): protocol split — after the product wave; the
    crate-boundary design note (box3) can land any time earlier.
18. **pipe#523**: per-device prekey grants — builds on 641's prekey work.
19. **pipe#538**: quiet mode CLI (Decision A recorded; small).
20. **pipeOS#9** then **pipe#651** (netboot; boot server = Sam's desktop per
    ruling), then **pipeOS#55** (factory pilot — **only when box4 exists**;
    #51 is its hard prerequisite).
21. **pipeOS#57 (IT role)**: stays automation-first, no holder, until the
    exception rate proves otherwise. **pipeOS#49** (per-box tokens):
    attribution-only, low. **pipe#683** + doc-drift residue: TEST filler.
22. **Growth cards** (458 content-marketing spec via box2's channel proposal,
    473 hermes, 498 agent membership): spec-first, after the product wave
    ships — they sell what phases 3 built.

## Design gates (the clock stops here without Sam)

| gate | what Sam does |
|---|---|
| #60 PAT scope | mint/adjust TEST+SHIP PATs to Contents write (protection already in place) |
| #652 repro | paste a disposable sign-in key |
| #672 direction | point at a variant when box2's mockups arrive |
| #55 pilot | when box4 hardware exists |

Everything else proceeds without winding.

## Standing regulation (already in force, kept)

- Board diet: posts ≤10 lines, state on GitHub, rotate threads at ~20KB.
- Claims never empty; claim table = public face (netgaze).
- Kanban synced + Done archived every sweep.
- Foreman: 50-min lean sweeps, no state re-derivation, rulings appended to
  `docs/foreman-rulings.md` via docs-PR flow.
- Verdict gates unchanged: R3/R6/R13/R16.

## Verification (how we know the clock keeps time)

- **Energy:** a full working day without a session-pool cap (the failure that
  started this). Watcher logs show skipped no-op wakes; wake prompts <20KB
  measured (#68 acceptance).
- **Train:** netgaze Gates view shows no PR sitting READY >30 min; claims
  view never empty while cards remain.
- **Dial:** a new user reaches a free named identity end-to-end (#680
  acceptance); Sam's phone shows the picked #672 variant live; `pipeos
  verify` + `pipebox-card verify` PASS fleet-wide after the migration.
- **Chime:** netgaze fleet events fire on transitions only; a quiet day is a
  silent dashboard.
