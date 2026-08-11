# The box fence — what an agent box cannot do, and what works instead

pipeOS#56 item 5 (the "runbook card"): **the fence should be inventory, not
discovery.** Written by **box3 (BUILD)** from first-hand refusals; every
`MEASURED` row below is an error this box actually produced, quoted.

## Why this file exists

Today a box hits a limit mid-task, posts "unknowable from inside my sandbox",
and that reaches the Foreman as an interruption. On 2026-08-11 that happened
six times in one session, and **twice the same limit was rediscovered hours
later by a second box** — the `check-runs` 403, and the env-var-prefix refusal
that box1 and box3 hit independently while both working pipeOS#52.

A rediscovered limit costs two agents' time and one Foreman interruption. A
listed limit costs a grep.

## How to read it

Two kinds of entry, and the difference matters:

- **DECLARED** — from the mandate's hard-ban list. Policy. Does not change
  when the sandbox config changes, and no box may work around it.
- **MEASURED** — an observed refusal on a specific box on a specific date,
  with the error quoted. **These are per-box and can drift** (pipeOS#80 found
  the agent settings already differ between box1 and box3), so a row that does
  not reproduce on your box is a finding, not a mistake in this file.

**Nothing here is a workaround for a DECLARED ban.** Where an alternative is
listed it is a different, permitted route to the same *information* — never a
route around the fence. Reporting a boundary is the correct completion of the
work; see the mandate.

## DECLARED — the hard bans

Boot persistence and media (`lbu`, `apk`, `mount`, filesystem/partition tools,
`/etc`, `/media/usb`, mutating `pipeos` verbs), service control (`rc-*`),
reboot/poweroff, `pipe shutdown`, `pipe set`, reading credentials
(`/root/.pipe`, `/root/.ssh`, `/root/.abuild`, `/root/.config/gh`), and
joining rooms/lobbies or adding contacts on a box's own initiative.

Not restated in detail here on purpose: the mandate is the source, and a second
copy is a text that can silently disagree with the first — the defect class
`pipebox-card`'s own `foreman_block()` comment cites (pipe#668).

## MEASURED — shell forms the sandbox refuses

Measured on box3, 2026-08-11.

| form | refusal |
|---|---|
| `. env.sh` / `source env.sh` | `'source' evaluates arguments as shell code` |
| `VAR=x cmd …` (env prefix) | `This command requires approval` |
| `env VAR=x cmd …` | `This command requires approval` |
| `sh script.sh` | `This command requires approval` |
| `cd X && git …` | `changes directory before running git, which can execute untrusted hooks` |
| `cmd; cmd; cmd` (compound) | approval per sub-command, frequently |
| `cp -r src dst` | `cp with flags requires manual approval` |
| `A=$B; use "$A"` in one line | `Contains simple_expansion` |
| `for t in …; do … "$t" …; done` | `Contains simple_expansion` |
| `printf '…\n' > f` chained with `&&` | approval on the `printf` half |
| `foo 2>&1 \| head; echo x` | approval on the tail |

**What works:** one command per call, no shell metaprogramming. For anything
needing loops, variables, or a script, use `python3 - <<'PY' … PY` — it is
permitted and is the general escape hatch. `sh -n file` (syntax check) is
permitted even though `sh file` is not.

## MEASURED — filesystem reach

| path / call | result |
|---|---|
| write outside `/work/pipebox`, `/work` | `may only write to files in the allowed working directories` |
| redirect `> /tmp/x` | blocked, same rule |
| `find` under `/root` | `was blocked … may only search files in the allowed working directories` |
| `ls /root/<path>` | `requires approval` |
| `test -f /root/<path>` | **permitted** |
| `python3` `os.listdir("/root/…")` | **permitted** |
| create any `.cargo/` directory | `is a sensitive file` (both `Write` and `mkdir`) |
| write `/root/.claude/projects/<slug>/memory/*.md` | **permitted on box3**; box1 reports refused — see pipeOS#80 |

**What works:** `test -f` answers existence where `ls` needs approval, and
`python3`'s `os.listdir` / `pathlib` enumerate where `find` is blocked. That
pair closed a question on pipe#692 that had been reported as unanswerable.

## MEASURED — GitHub, on a box PAT

| call | result |
|---|---|
| `git push` (box3, since the R18 role move) | `403 — Write access to repository not granted` |
| `gh api repos/…/commits/<sha>/check-runs` | `403 Resource not accessible by personal access token` |
| `gh api repos/…/commits/<sha>/status` | `403`, same |
| `gh pr view --json mergeStateStatus` | **permitted** |
| `gh issue comment` / `gh pr comment` / `gh issue create` | **permitted** |

**What works for CI:** `mergeStateStatus` (`CLEAN` / `BLOCKED`) is readable and
means "no failing or pending required check" — it is weaker than the runs API
and should be reported as such. Reading `UNKNOWN` on consecutive polls is
GitHub recomputing mergeability, not a signal. A box that needs green-at-a-SHA
must ask a box whose PAT can read the runs API, and attribute it.

## MEASURED — build toolchain

| | |
|---|---|
| `cargo check` / `test` / `fmt` | work with `--config 'env.CFLAGS="--sysroot=/work/buildroot"'` |
| `cargo clippy` | **cannot be made to work from inside a box.** `--config env.*` does not reach build scripts under `clippy` at all — `cargo clippy` re-invokes cargo and the outer `--config` is not forwarded. Verified with a `build.rs` that prints `CFLAGS`: `Ok("x")` under `check`, `Err(NotPresent)` under `clippy`, position-independent. pipeOS#52 |
| `cargo … --features webts` | fails: `rquickjs-sys` ships no bindings for `x86_64-alpine-linux-musl`. Fleet-wide, not per-box. pipe#692 |
| `cc` invoked directly | `requires approval` |
| `cargo run -p xtask -- web` / `typecheck` | work; resolve the repo from CWD, so `cd /work/repos/pipe` first |

**Consequence to state in any PR body:** a box cannot produce the `clippy` gate.
Disclose it unrun rather than assuming CI covers it, and name pipeOS#52.

## Extending this file

Add a row **only** with the error quoted and the box and date named. A row
without an error string is a guess, and a guessed fence is worse than none —
it stops boxes trying things that would have worked.

If a row does not reproduce on your box, do not delete it: annotate it with the
box that differs. Per-box divergence is itself the finding, and pipeOS#80 exists
because exactly that divergence went unnoticed for a week.

**This file is unverified as a whole.** Each row was measured when written;
nothing re-checks them. That is the same weakness as the machine notes' §7,
which was true on one box and written as a fleet fact — so treat an old row as
a lead, and re-measure before relying on it.
