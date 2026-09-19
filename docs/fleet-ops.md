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
| `pipeos-selfcheck --live` | the hourly live verdict (#290) behind every verdict pill | `/run/pipeos/health.last` only | no (tmpfs) |
| `pipeos deploy-overlay [--dry-run] [--yes]` | — (operator only) | overlay files, crontab, runlevels, `.overlay-stamp` | yes, then verifies |
| `pipeos schedule ls\|add\|set\|rm\|enable\|disable\|run\|log` | Schedule view | `/etc/pipeos/schedule.json` | every mutating verb |
| `pipeos usage [totals]` | Usage view | — | — |
| `pipeos usage cap [--box] N\|none` | Usage → monthly cap | card `MONTHLY_CAP_USD`, regenerated; enforced at once | yes |
| `pipeos usage cap --cluster N\|none` | Usage → cluster cap | card `CLUSTER_CAP_USD` on THIS member (set it on each); each member sums every member's month-to-date (an unreachable one at its last figure) and pauses itself; the pause names the cluster cap (#302) | yes |
| `pipeos usage cap --agent NAME N\|none` | Usage → an agent's cap; Schedule → cap field | `cap_usd` on the job in `schedule.json`; enforced at once against that agent's month-to-date; a pause names the agent (#302) | yes |
| `pipeos watchdog kernel\|off\|status` | — (card `WATCHDOG`, no form yet) | card `WATCHDOG`, regenerated; `rc-service watchdog restart` at once | yes |
| `pipeos card set KEY=VALUE…` | every card-backed form | `/etc/pipeos/card.conf` + `pipebox-card generate` | yes |
| `pipeos secrets request NAME [WHY]` | Secrets → Share requests (Approve on a HOLDER's dashboard; Deny anywhere) | the agent's one door (#301): the record on this box (`vault-requests.json`, saved), a notice on every member; the owner's approve, on a Machine that holds it, sends the copy over mutual TLS and this box saves it | yes |
| `pipeos secrets phrase [--ack]` | Secrets → recovery phrase | `--ack` removes the tmpfs copy | no (tmpfs) |
| `pipeos assistant password` (stdin) | Assistant → password | vault `assistant_pass`; restarts `pipeos-assistant` | yes |
| `pipeos cluster init [NAME]\|status\|ca` | Cluster → this Machine's identity (its CA) and member list | `/etc/pipeos/cluster.json` | `init`: yes |
| `pipeos cluster add ID\|NAME\|IP` (its admin password on stdin) | Cluster → Add a Machine | the target joins (takes the list), this list gains its CA, the list is pushed to every member | yes |
| `pipeos cluster remove ID\|NAME` | Cluster → Remove | the list loses it, pushed to the rest (the removed one finds out at its next call and becomes a cluster of one) | yes |
| `pipeos cluster sync` | Cluster → push the list | nothing here; the list to every member | no |
| `pipeos cluster join MEMBER` (that member's password on stdin) | the wizard's Join this cluster | this box asks the member to add it (a one-time token, no password crosses) | yes |
| `pipeos cluster adopt ID\|IP [NAME]` (this Machine's password on stdin) | Network → Adopt on an unclaimed Machine | claims it with the same password, adds it, names it; prints its recovery phrase once | yes |
| `pipeos cluster page` | Cluster → the members card | nothing; every member's two lines and one verdict, gathered over mutual TLS | no |
| `pipeos cluster reboot-all [--yes]` | Cluster → Reboot everything | every member reboots (this one last); refuses while something is busy unless --yes | each box's shutdown hook |
| `pipeos cluster services KEY on\|off [ID...]` | Cluster → a service on several Machines | each member's services.conf through its own /api/services | each box saves |
| `pipeos cluster agents` | Cluster → the agents line under each member | nothing; every member's agents from the page (a grey member's are last-known) | no |
| `pipeos cluster start NAME --on ID\|NAME\|idlest [--prompt TEXT [--cron SPEC\|manual] [--cwd DIR] [--backend B] [--cap N]]` (no --cron = manual: runs now, then only when started) | Cluster → Start an agent on a Machine | the agent (a scheduled job) is written into THAT member's `schedule.json` through its own `/api/agent/start` and run there; a bare name runs one already there | the member saves |
| `pipeos cluster call ID\|NAME\|IP METHOD PATH [JSON]` | — (what the cluster page does box-to-box) | nothing here; a request to a member over mutual TLS, and who answered | no |
| `pipeos selfupdate image on\|off\|status` | System → update automatically | `/etc/pipeos/selfupdate.conf` `IMAGE_UPDATE` | yes |
| `pipeos nas account NAME` (SMB password on stdin) | Files → Network storage → new account for a share | `users.json` (share-only: no sign-in, no shell), `pipeos-user add --nologin`, vault `nas_passdb`; restarts `pipeos-nas` | yes |
| `pipeos vault status\|list\|get\|set\|del\|export\|unlock\|rephrase` | Secrets view | the sealed store | set/del: the store is in `/etc`, save after |
| `pipeos vault share NAME ID\|NAME...` / `unshare` | Secrets → *shared with* ticks | a copy of NAME to each member through ITS `/api/secrets/receive` over mutual TLS (that member saves it as `cluster:<this id>`); this box notes who has it; unshare only forgets — the copy stays that member's own. The one exception to no-propagation, on the owner's tap (#301) | yes |
| `pipeos wake NAME\|ID\|--all\|--list` | Network → Wake | — | — |
| `pipeos lan [--refresh] [--json]` | Network → Everything on this network | netgaze's pass (ICMP sweep, neighbour table, PTR), cached a minute, every row marked this Machine / member / a Machine / other (#217) | no |
| `pipeos work status\|flush\|park\|unpark` | — (operator) | flush: the RAM-staged hot set → the stick; park: remount `/work` read-only | flush is the save |
| `pipeos backup`, `flash`, `restore-work`, `pkg`, `rollback` | Files, System | see each verb's header | yes |
| `pipeos unclaim [--yes]` | — (operator; a resale or a fresh start) | a factory reset: claim, users, name, owner, root ssh key, jobs + job dirs, shares, support port, assistant, vault, pipe + Claude + hermes sign-ins, cluster membership, a NEW CA, the agent's transcripts + memory, the ledger, `/work/backup`; the chassis identity, `/work/home` and `/work/repos` stay; refuses on a parked `/work` it cannot unpark | one save in unclaim mode (canonical + known-good as nobody's), then reboot |

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
