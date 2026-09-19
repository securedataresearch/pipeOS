---
name: pipeos-fleet
description: Drive Sam's pipeOS Machines from this workstation — reach a box, deploy a merged commit, run the drills (schedule, cap, vault, wake, reboot), read the recovery phrase, set the assistant password — through the box's own verbs, never a hand edit. Use for "deploy to the boxes", "run the drills", "is zero up to date", "set the cap on two", "what's pending on the fleet".
---

# pipeos-fleet — driving the Machines

Everything here is `ssh root@<ip> '<pipeos verb>'`. The verbs are the
dashboard's forms as commands: same file, same refusals, same `pipeos save`.
The rules that make this safe are in `docs/fleet-ops.md` and CLAUDE.md;
the short form: **no hand edits over ssh** (`cp`, `printf > /etc/...`,
`rc-update`, `sed -i`). If a task has no verb, the fix is a verb + a probe
row in `scripts/check-operator-verbs.py`, then a deploy — not a one-off.

## Reaching a box

| name  | id          | ip              |
|-------|-------------|-----------------|
| zero  | pipeos-a4e0 | 192.168.254.77  |
| one   | pipeos-b010 | 192.168.254.79  |
| two   | pipeos-c2b0 | 192.168.254.78  |
| three | pipeos-c360 | 192.168.254.80  |

Root key is baked into the sticks. Host keys changed at the 2026-09-10
reflash and Sam's `~/.ssh/known_hosts` is stale, so use a scratch file:

```sh
KH=$SCRATCH/known_hosts
ssh -o BatchMode=yes -o UserKnownHostsFile=$KH -o StrictHostKeyChecking=accept-new root@192.168.254.77 '...'
```

A box that does not answer is off (see wake) or mid-reboot. Run the same
command on several boxes in parallel with `&`/`wait`, one output file each.

## First look at a box

```sh
pipeos status            # overlay commit + how far behind origin/main, save state ("N uncommitted change(s)" = what a save would change vs the canonical apkovl, #307)
pipeos verify            # PASS = a reboot reproduces this state (run before AND after any change)
pipeos-selfcheck         # verdict + every WARN/CRITICAL; the boot-report DM says the same
pipeos-selfcheck --live  # the hourly live verdict (#290): same rows minus the heavy ones; writes /run/pipeos/health.last, which the page and `pipeos status` prefer when newer
pipebox-card verify      # derived files match the card? deploy-overlay regenerates them after a template change (#281); FAIL otherwise -> pipebox-card generate; pipeos save
pipeos cluster status    # this Machine's cluster identity (its CA) + member list (#222); `pipeos cluster call two GET /api/cluster` is a box-to-box call over mutual TLS
pipeos cluster adopt ID  # (this box's password on stdin) claim an unclaimed Machine + add it in one step (#213); join MEMBER from the new box
pipeos cluster page      # the pilot's one page (#212): every member's two lines + one verdict; reboot-all [--yes]; services KEY on|off [ID...]
pipeos cluster add two   # (two's admin password on stdin) marks two out of the lobby into this cluster; remove ID / sync push the list (#211)
pipeos cluster agents    # every member's agents and what each last did; start NAME --on two|idlest [--prompt TEXT --cron SPEC] places one ON a member — it lives there, a grey box's agents are grey (#300)
```

## Deploy a merged commit

```sh
pipeos deploy-overlay --dry-run    # what would change
pipeos deploy-overlay --yes        # install, restart changed services, enrol new init scripts, save, verify
```

- **Run it twice when the deployer itself changed** (`pipeos-deploy-overlay`,
  `pipeos-flash`): the first run executes the old copy. The output says
  "N changed" — if the changed file is the deployer, run again.
- It carries `etc/crontabs/root` and enrols new `etc/init.d/*` where
  `scripts/40-build-apkovl.sh` puts them (since #254/#255). It never
  touches `etc/pipeos/*` or `root/.pipe/policy.json`.
- After a **template** change (`usr/local/share/pipeos/card/*.tmpl`) it
  regenerates the card outputs itself, before the save (#281; `--dry-run`
  says "would regenerate"). A box generate has never run on is left alone
  and told so. `pipeos card set` regenerates when you change a card field.
- Then: `pipeos verify` PASS, `pipeos-selfcheck` green, and the stamp
  (`head -1 /etc/pipeos/.overlay-stamp`) names the commit.

## The verbs

```sh
pipeos schedule ls
pipeos schedule add NAME --prompt "..." [--cron "0 2 * * *"|manual] [--cwd /work/...] [--backend claude|hermes] [--notify on|off] [--session fresh|continue] [--cap N]   # no --cron = manual: runs only when started
pipeos schedule set NAME --notify off           # only the given flags change
pipeos schedule rm|enable|disable|run|log NAME  # run = Run now (detached); log NAME [N]
pipeos usage                                    # totals today/7d/30d/month, by actor, the cap
pipeos usage cap 40 | cap none                  # card MONTHLY_CAP_USD, regenerated, saved, enforced now
pipeos usage cap --agent NAME 5 | none          # that agent's own monthly cap (cap_usd on the job); most restrictive wins, every pause names its cap (#302)
pipeos schedule set NAME --cap 5                # the same cap, from the schedule side
pipeos card set KEY=VALUE ...                   # any card field; regenerate + save
pipeos secrets phrase [--ack]                   # the vault's pending recovery phrase (tmpfs); --ack forgets it
printf '%s' PW | pipeos assistant password      # -> vault assistant_pass, pipeos-assistant restarted, saved
pipeos watchdog kernel|off|status               # the hardware watchdog (card WATCHDOG, default kernel, 60 s): regenerated, saved, restarted now
pipeos selfupdate image on|off|status           # automatic image updates (default on): hourly check, apply in place, reboot — held while a job/terminal is live
printf '%s' PW | pipeos nas account NAME        # share-only account (no sign-in, no shell) + its SMB password; tick it on a share in Files → Network storage
pipeos vault status|list|get|set|export         # the sealed store; set reads stdin: printf '%s' V | pipeos vault set NAME [CONSUMER]
pipeos wake NAME|ID|--all|--list                # magic packet to a Machine this box has seen
pipeos work status|flush|park|unpark            # the RAM-staged hot set; park = flush + /work read-only so the stick idles
```

The stick is `/work` on these Machines (no internal disk yet). `pipeos work
park` between operations keeps it cool; a job or session unparks as needed.
Before pulling a stick: `pipeos work flush` (or `pipeos save`, which flushes).

Box clocks are UTC; cron expressions are box-local, so UTC.

## The drills (what "done" looks like)

- **Scheduled job fires unattended:** `pipeos schedule add drill --cron "$M $H * * *" --prompt "Reply with exactly: drill ok"` three minutes out; after it, `pipeos schedule ls` shows `ok`, `pipeos schedule log drill` has the reply, `/work/.pipeos/schedule/runs.log` has the row, the owner got the DMs (notify on). Then `pipeos schedule rm drill`.
- **Cap DM / pause:** `pipeos usage` for this month's spend; `pipeos usage cap N` with N below it → `enforce` prints paused, `/work/.pipeos/ledger/paused` exists, the owner gets the 100% DM, `pipeos schedule run X` is refused and the tick logs "paused". `pipeos usage cap none` (or a higher N) lifts it at once.
- **Vault first boot / rehome:** `rc-service pipeos-vault status`, `pipeos vault status` (open), `pipeos vault list`; `pipeos secrets phrase` shows the migration's phrase until acked — give it to Sam, then `--ack`. Rehome = the stick in another chassis: dashboard Secrets → recovery phrase.
- **Wake a sibling:** from a member, `pipeos wake one`. **Only the onboard 1GbE (eth1) supports WoL; the SFP+ eth0 does not.** Uncabled eth1 = no wake. selfcheck says which.
- **Watchdog drill (#247):** `pipeos watchdog status` says armed 60 20; `echo c > /proc/sysrq-trigger` (a real panic — the box is gone for ~90 s); back up, the boot report says `went down: kernel panic — the watchdog rebooted us`, `pipeos-selfcheck` green, known-good untouched. `pipeos watchdog off` → status says off, no petter; `kernel` re-arms.
- **Reboot drill:** `pipeos verify` PASS → `reboot` → wait ~90 s → `pipeos-selfcheck` green and `pipeos status` says known-good matches.

## Pilot day (#159) — the opposite rule

A pilot Machine is a customer's, not the fleet's: nothing above applies on
site. The stick comes from the **released** image (`gh release download`,
`sha256sum -c`, `scripts/verify-image-generic.sh` exit 0, `scripts/70-flash.sh
--image`), burned in on the bench and **never claimed** (`curl
http://pipeos.local/api/state` → `"claimed": false` before it goes in the
bag). On site **the client's hands**: claim, name, services, Claude sign-in
from `docs/client-onboarding.md`; we read `docs/first-boot-acceptance.md`
and fill in `docs/acceptance/pilot0.md` / `pilot1.md`. The support door is
proven both ways (key → pinned to the port in `docs/support-relay.md`'s
ledger → `ssh -J` → toggle off → tunnel gone) and then stays off. No ssh to
a pilot box unless the owner has the toggle on and asked.

## What is NOT yours to do

- Sign a box into pipe or Claude (OAuth/credentials: Sam, via the wizard).
- Cable eth1. Physical.
- Merge without the review pass; deploy without `pipeos verify` PASS before and after.
- Anything the auto-mode classifier refuses (raw writes, secret-store writes): stop and say exactly which verb was refused; the fix is a rule in `.claude/settings.json`, not a workaround.

## Memory

`~/.claude/projects/-home-s-Projects-pipeOS/memory/lan-fleet-2026-09-09.md`
holds the per-box state (overlay commit, what is pending). Update it when
a deploy lands or a drill passes.
