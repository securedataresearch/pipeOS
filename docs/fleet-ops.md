# Fleet operations — driving a Machine without the dashboard

The dashboard is the owner's surface. An operator over ssh — a human, or
the owner's agent on a workstation — drives the same Machine through
**verbs**: `pipeos <verb>` on the box. A verb is the dashboard's form as a
command: the same file, the same refusals, the same `pipeos save` at the
end. That is the whole design, and it is enforced twice: the resident
agent's fence (`pipebox-settings.json`) only lets it run the read-only
verbs, and `scripts/check-operator-verbs.py` holds every verb to the
dashboard's shape so the two never disagree.

**Why verbs and not `ssh … 'printf > /etc/pipeos/x'`:** the 2026-08-05
incident (CLAUDE.md) and pipeOS#97 are both "a hand change nobody could
find later". A verb validates, persists and says what it did; a hand edit
does none of that. On 2026-09-11 the first deploy of the standard set
needed a crontab line, two runlevel entries, a card regeneration and a
vault restart by hand — every one of those is now either a verb or a step
`pipeos deploy-overlay` takes itself (#254–#257, #259).

The operator's skill for a Claude Code session is
`.claude/skills/pipeos-fleet/SKILL.md`; this page is the reference behind it.

## Reaching the boxes

`docs/cluster.md` names them. Root ssh with the key baked at flash time;
`ssh -o UserKnownHostsFile=<scratch> -o StrictHostKeyChecking=accept-new`
when host keys have changed (every reflash). Clocks are UTC.

## The verbs

| verb | what it is in the dashboard | writes | saves |
|------|-----------------------------|--------|-------|
| `pipeos status` / `verify` / `diff` | Overview, System | — | — |
| `pipeos deploy-overlay [--dry-run] [--yes]` | — (operator only) | overlay files, crontab, runlevels, `.overlay-stamp` | yes, then verifies |
| `pipeos schedule ls\|add\|set\|rm\|enable\|disable\|run\|log` | Schedule view | `/etc/pipeos/schedule.json` | every mutating verb |
| `pipeos usage [totals]` | Usage view | — | — |
| `pipeos usage cap N\|none` | Usage → monthly cap | card `MONTHLY_CAP_USD`, regenerated; enforced at once | yes |
| `pipeos card set KEY=VALUE…` | every card-backed form | `/etc/pipeos/card.conf` + `pipebox-card generate` | yes |
| `pipeos secrets phrase [--ack]` | Secrets → recovery phrase | `--ack` removes the tmpfs copy | no (tmpfs) |
| `pipeos assistant password` (stdin) | Assistant → password | vault `assistant_pass`; restarts `pipeos-assistant` | yes |
| `pipeos cluster init [NAME]\|status\|pub` | Cluster (#212) → this Machine's key and member list | `/etc/pipeos/cluster/key.pem` (0600), `/etc/pipeos/cluster.json` | `init`: yes |
| `pipeos cluster call ID\|NAME\|IP METHOD PATH [JSON]` | — (what the cluster page does box-to-box) | nothing here; a signed request to a member, its answer checked | no |
| `pipeos selfupdate image on\|off\|status` | System → update automatically | `/etc/pipeos/selfupdate.conf` `IMAGE_UPDATE` | yes |
| `pipeos nas account NAME` (SMB password on stdin) | Files → Network storage → new account for a share | `users.json` (share-only: no sign-in, no shell), `pipeos-user add --nologin`, vault `nas_passdb`; restarts `pipeos-nas` | yes |
| `pipeos vault status\|list\|get\|set\|del\|export\|unlock\|rephrase` | Secrets view | the sealed store | set/del: the store is in `/etc`, save after |
| `pipeos wake NAME\|ID\|--all\|--list` | Network → Wake | — | — |
| `pipeos work status\|flush\|park\|unpark` | — (operator) | flush: the RAM-staged hot set → the stick; park: remount `/work` read-only | flush is the save |
| `pipeos backup`, `flash`, `restore-work`, `pkg`, `rollback`, `unclaim` | Files, System | see each verb's header | yes |

`pipeos` with no verb prints the list; each verb refuses with rc 2 and
one line when the input is wrong, exactly as the dashboard answers 400.

## Deploying

1. Merge to `main` (CI green: the probes and their controls).
2. On each box: `pipeos deploy-overlay --dry-run`, then `--yes`.
   The deployer installs the ref's overlay, restarts the supervised
   services whose code changed, ships `etc/crontabs/root`, enrols any
   `etc/init.d/<svc>` that no runlevel holds in the level
   `scripts/40-build-apkovl.sh` names for it (and starts it once), saves,
   verifies, and stamps the commit.
3. **If the deployer itself changed, run it again**: a running script keeps
   its old inode on purpose, so the first run executes the old copy.
4. After a template change under `usr/local/share/pipeos/card/` the deploy
   regenerates the card outputs itself before saving (#281); `--dry-run`
   says "would regenerate". A box generate has never run on is left alone
   and told so. (`pipeos card set` regenerates when a field changes.)
5. `pipeos verify` PASS, `pipeos-selfcheck` green, and the boot-report DM
   after the next reboot says the same.

## The drills

Each shipped feature has a drill an operator runs on a real Machine once,
and records in `docs/acceptance/`. The skill lists the commands; the
acceptance sheet (`docs/first-boot-acceptance.md`) lists the rows.

- **Scheduled runs (#242):** a job added three minutes out fires with
  nobody attached; `schedule ls` says `ok`, the log has the reply, the
  owner had the DMs.
- **Usage cap (#246):** `usage cap` below this month's spend pauses jobs at
  once (marker, DM, `schedule run` refused, tick logs "paused"); raising it
  lifts the pause.
- **Vault (#244):** first start migrates plaintext secrets and leaves the
  recovery phrase in tmpfs until `secrets phrase --ack`; the phrase rehomes
  the stick in another chassis.
- **Wake-on-LAN (#241):** `pipeos wake NAME` from a sibling. **Only the
  onboard 1GbE port supports it on the M920q; the SFP+ port's driver does
  not.** selfcheck WARNs when the wakeable port is not the one in use.
- **Reboot:** `verify` PASS → `reboot` → selfcheck green, known-good
  matches.

## The stick is /work: heat, RAM staging, park

Until a Machine has an internal disk the boot stick is also `/work`. The
churn — `logs`, `pipeos/mdns`, `.pipeos/ledger`, `.pipeos/schedule`
(`usr/local/share/pipeos/hot.list`) — is staged in a tmpfs by `pipeos-hot`
at boot and written back by `pipeos work flush`: hourly, before every
`pipeos save`, at shutdown, before `park`. `pipeos work park` flushes and
remounts `/work` read-only so the stick takes nothing until `unpark`; a
scheduled run unparks itself and re-parks. If park says something holds
`/work`, that is a session or a job: wait, or stop it. `/work` mounts
`commit=120,lazytime`. Add a path to `hot.list` when something new churns;
never one that is the only copy of anything.

## What stays human

Signing a box into pipe or Claude (the wizard; OAuth), cabling, and the
judgement calls in `docs/foreman-rulings.md`. An agent that hits a refusal
— the box's fence, or its own harness — stops and names the verb it
needed; the answer is a rule or a verb, never a workaround.
