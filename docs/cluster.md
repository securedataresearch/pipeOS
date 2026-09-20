# PipeOS Cluster — design decisions

The answers Sam gave on 2026-09-10 to the cluster interview, written down so
the work that follows does not re-ask them. Product names: the stick is the
**PipeOS Live Disk**, the box is a **PipeOS Machine**, several Machines an
owner has joined are a **PipeOS Cluster**. Owner-facing copy says "the
Machine" and "the cluster".

Nothing here is built yet. Each numbered section ends with the issue that
carries it. Things that exist today (the lobby, `pipeos-xxxx.local`, cards,
ROLE) are named where the design reuses them.

## 1. Identity — a number underneath, a name on top

- Every Machine has a **chassis id** it can never lose: the four hex
  characters of its MAC that already make `pipeos-a4e0.local`. The id is
  the hostname. It is never an ordinal — the system never numbers boxes
  0, 1, 2, because a number stops being true the day one box is replaced.
- The owner gives it a **name** at claim time, on top of the id. The name
  is an mDNS alias for the same host (`corvette.local` and
  `pipeos-a4e0.local` answer the same box); renaming never changes the
  hostname, so nothing that talks to the box by id breaks.
- The wizard **suggests a name** from a pool of classic cars (Corvette,
  Mustang, Giulia, Miura, 2CV, …); the pool skips names already in the
  lobby. The owner can type their own.
- Identity is fluid on purpose: a reflash is a new box; the three boxes on
  the bench today get re-claimed under this scheme rather than migrated.

→ #210

## 2. Labels — the box says on its side what the screen says

- A `pipeos label` utility (and a Labels view in the dashboard) renders a
  sticker: name, chassis id, release tag, a QR to `http://<id>.local/`,
  with a **visual preview** before printing. Output is a PDF/PNG at the
  common label sizes; printing goes through whatever the workstation has.
- A phone photo of the sticker is enough for support to know which box.

→ #218

## 3. What a cluster is — a decision, not a fact

- The lobby (`pipeos.local`, #206) stays: it is the **fact** — every
  Machine that answers on this LAN, claimed or not. A cluster is a
  **decision** the owner makes in the web interface by marking Machines
  out of the lobby.
- Each Machine belongs to exactly one cluster, or is a cluster of one.
- Joining mints a **cluster secret** (a keypair per Machine, the cluster
  is the set of public keys each member holds). Being on the LAN with the
  same admin password is not membership.
- **Leaderless.** No head node, no election. Every member holds the same
  member list; the cluster page is served by whichever box you opened.
  The network is meant to be elastic: boxes come and go and nothing has
  to fail over.
- **Leaving.** A member that stops answering goes **grey** and stays in
  the list until it shows up again, shows up in *another* cluster (then
  it is dropped here), or the owner removes it by hand. No automatic
  expiry.
- Cluster size the UI is designed for: **four on one high-speed switch**,
  plus whatever else is kludged onto the wired network. A list, not a
  search box.
- **The primitive** (#222, `web/cluster.py`) is mutual TLS with what every
  Machine already has: its own CA (the padlock's) is its cluster identity,
  its server cert doubles as the client cert it presents to a member, and
  the member list is the set of member CAs webd's :443 accepts client
  certificates from. No new keys, no home-made signatures; the stdlib does
  the verifying. `pipeos cluster init|status|ca|call`.
- **Membership** (#211): every edit is pushed to every member over mutual
  TLS and a member takes the list from any member (leaderless). A Machine
  finding itself absent becomes a cluster of one, CA kept. Adding needs the
  target's own admin password once, over TLS pinned to its CA; a member
  advertising another cluster id on the LAN (TXT `cl`) is dropped by every
  member on its own. Dashboard → Cluster; `pipeos cluster add|remove|sync`.

→ #211

## 4. The network map

- The cluster page needs the whole LAN, not only pipeOS boxes: what is on
  the switch, what the router leases, what just appeared. **netgaze**
  (Sam's Rust TUI, `netgaze probe <source>` gives one collector pass as
  text, and it already has a fleet view) becomes the collector; it ships
  in the image and the dashboard renders its output.
- The map is where the owner draws the cluster boundary (§3).

- A Machine that is playing its part says so **on its own body**: while it is
  a healthy member that can still see another member, it blinks its place in
  the member list on its cabled network port's LED — one flash for the first
  member, two for the second, a lighthouse characteristic. The Machine that is
  NOT blinking is the one to walk over to: off, out of the cluster, unhealthy,
  or cut off. The numbers need no configuring, because every member sorts the
  same list, and `pipeos cluster status` prints them beside the members. The
  front power light cannot do this: on these chassis it has no software
  interface at all (`/sys/class/leds` is empty), so the light is the port's,
  at the back. `pipeos blink status` says why this Machine is or is not
  blinking; the card's `BLINK=off` turns it off.

→ #217, #333

## 5. Sign-in and users — keys, not passwords; no propagation

- Management prefers **ssh keys**; passwords are the fallback, and each
  Machine keeps its own admin password for now. One-login-everywhere is
  not a goal while keys are the plan.
- **Nothing propagates.** Adding a user on one Machine adds it there only.
  The cluster page shows users **cluster-wide** (every box's list, side by
  side) but editing is per box. The rule generalises: this network avoids
  propagation wherever it can.
- Sign-in is per box for now; with keys everywhere the question goes away.

- Built (#215): `pipeos cluster users` and the Cluster page's *Who can sign
  in, across the cluster* card gather every member's list live over mutual
  TLS — who exists, at what level, with a unix login or a browser terminal,
  and whether the account is disabled. Never a hash, a key or a terminal
  port: reading who exists is what makes the list possible, not a way to
  become them. A member's certificate may read ONE Machine's list; only the
  owner's session (or a Machine asking its own listener) gathers the
  cluster's. Nothing is cached — unlike the agent list, a sign-in list that
  is quietly out of date is the kind of thing an owner acts on. Editing
  stays per Machine, on that Machine's own dashboard.

→ #215

## 6. Roles

- ROLE (today a card field: TEST, BUILD, SHIP, GENERIC) becomes an
  owner-visible, owner-editable property shown in the lobby and the
  cluster page. The default for any joined box is **GENERIC**.
- Roles are a permissions surface: an operator can adjust a box's role,
  gate what a role may do, and define new roles.

→ #214

## 7. Services — per box

- Claude sign-in, NAS, stream, support tunnel, assistant, terminals are
  all **per Machine**. The cluster page is "very capable": it toggles a
  service on several boxes in one action, but each toggle is still that
  box's own setting.

→ #212

## 8. Data — `/data`, with a cluster-wide index

- The bulk volume's name is **`/data`**; `/work` is reserved for something
  else. It lands in two releases, because a live box cannot change where a
  mounted volume is without a window where half its paths are wrong
  (pipeOS#219). **This release: the name works.** The volume still mounts at
  `/work`, `/data` is a symlink to it laid at every boot, and both paths
  reach the same bytes — a job's working dir may be `/data/repos/x`, and
  selfcheck says so when the link is missing or is something else. **The
  next: the flip** — the volume mounts at `/data` and `/work` becomes the
  symlink, with the tree's own text renamed. By then every box already
  answers to both names, so the flip is a reboot and nothing else. The
  filesystem label stays `PIPEWORK` throughout: the owner never sees a
  label, and relabelling sticks in the field could only lose a volume.
- The **file index** of every member is visible to the cluster (the Files
  view grows a box selector); bytes stay where they are.
- An agent on one box reaching another box's files may use **any of
  SMB, pipe, or ssh between boxes**. All three stay available.
- Agents on a cluster whose owner is on pipe are wired together
  **automatically** (a cohort, in pipe's terms); nothing to configure.

→ #219, #220

## 9. Updates and health

- **Rolling updates, at least one member green at all times.** A member
  will not apply an update while another member is mid-update or not
  green. Leaderless: each box checks the member list before it goes.
- The cluster page shows **one verdict for the cluster** and every
  member's boot report in one place. Since #290 the verdict is **live**: an
  hourly `pipeos-selfcheck --live` writes `/run/pipeos/health.last` and every
  reader (the page, the lobby, `pipeos status`) prefers it when it is newer
  than the boot report, and says which one it shows.
- Alerts stay **one DM per box** for now (too much is changing to design
  a digest).
- A **Reboot everything** button exists. It warns when something like a
  stream or a build is running, and still lets the owner do it.

→ #216, #212

## 10. The cluster page — the pilot's one page

- **Dots**: one row per Machine, two lines each, so four to six are seen
  at a glance: name and id and role on the first line; verdict, what it is
  doing (stream live, agent running, idle), disk use, release on the
  second. A richer visualisation is welcome if it stays glanceable.
- Without clicking into a box you can: see health, see files, see roles,
  change roles, shut everything down.

- Built (#212): `/api/cluster/page` gathers every member's
  `/api/cluster/summary` over mutual TLS from whichever box was opened;
  `Reboot everything` warns with the page's busy list (a live stream, a
  running job, an open terminal, a pending image) and reboots the others
  first, this box last; one service switch fans out to the ticked members,
  each applying and saving its own. `pipeos cluster page|reboot-all|services`.
  Roles show as a pill until #214.

→ #212

## 11. Onboarding the second box

- Box two's first page, when a cluster already exists on the LAN, offers
  **join this cluster** with the existing members listed. Joining
  inherits nothing that would count as propagation (§5): it gets the
  minted secret and the cluster's member list, and its own password.
- Box two can also be **adopted from box one's dashboard**, so the owner
  never types a second password to claim it.
- Association is entirely client-side: a shipped generic box carries
  nothing that ties it to any customer or cluster.

- Built (#213): the wizard's **Join this cluster** step after the claim
  (the owner types one member's password; the new box mints a one-time
  token, the member runs its ordinary add against it — no password crosses
  between boxes); **Adopt** on an unclaimed Machine in a member's Network
  view (claimed with this member's password, typed once to confirm; added;
  named; its recovery phrase shown once). `pipeos cluster join|adopt`.

→ #213

The second box's secrets are its own: claiming it mints its own vault and
recovery phrase (#244). A stick moved between Machines needs the phrase
once, then belongs to the chassis it is in.

## 12. What a cluster does together — the modes (2026-09-18)

Sections 3–11 are plumbing: membership, the page, join. This section is
the answer to *what several Machines do that one cannot*. Sam's answers
of 2026-09-18; the modes are all wanted, in this order.

- **Fleet view first.** One page: health, usage, release, one update
  button, one reboot button. It is the pilot deliverable and §10 already
  carries most of it.
- **Agent pool second — a placement rule, not a scheduler.** An agent is
  *started on* a member and lives there for its whole life. Its work stays
  on that box's `/data` (there is no cluster NAS today; §8's index is a
  view, not a home), its secrets stay in that box's vault. The one new
  primitive is "start this on box X, or on the idlest member", plus a
  cluster-wide list of where every agent lives. No migration. **A grey
  box's agents are grey too**: they are not restarted elsewhere from a
  snapshot; they come back when the box does.
- **Role split third.** The service set learns that a role is *filled
  elsewhere* ("streaming is on Miura"); the cluster page shows the role
  map. This is §6 and §7 knowing about other members, nothing more.
- **Distributed compute last, and reframed.** Of the workloads named
  (local inference, batch jobs agents emit, many parallel agents, a
  capability on the spec sheet), the one today's hardware carries is
  **batch jobs the agents emit** — builds, tests, transcodes fanned out to
  idle members. That is the agent pool plus a `run` verb, not a second
  scheduler; slurm is not wanted. Local inference is a hardware decision
  before it is a cluster feature: the reference Machine (M920q, integrated
  GPU) does not add up to useful inference at any count, so the honest
  shape is *a GPU box joins the cluster and advertises an inference role*
  — role split again. The spec sheet may say "distributed compute" as long
  as it says which of these it means.

**Caps: every level, most restrictive wins.** The owner may set a cap on
the cluster, on a box, and on an agent or role. Any exhausted cap pauses
the agent. Because the owner can then see room on the cluster meter next
to a paused agent, **every pause names the cap that caused it** — on the
status page, in the DM, in `pipeos usage`.

**Vault: per box, copy on demand — the one exception to no propagation.**
Secrets live in the vault of the box they were entered on. They reach
another member two ways, both wanted:

- *Pre-share*: each secret on the vault page carries a member list.
- *Request*: an agent on box two needs a key held on box one; the
  dashboard (whichever box the owner has open) shows a share request; the
  owner taps approve once; the copy travels over the existing mutual TLS
  (§3). This is the single case of one member writing state into another,
  and it happens only on the owner's tap. §5 stands otherwise.

**Hardware: 10 GbE is a Cluster part, not a Machine part.** The 10 GbE
NIC is fitted only in Machines sold as a pipe Cluster; it is part of why a
Cluster costs more than four Machines. A self-assembled cluster of four
Machines runs on the on-board gigabit and every mode above works the same,
slower. The no-wake-over-SFP+ caveat (docs/hardware.md) is therefore an
owner-facing fact for Cluster buyers.

- Built (#319): a job can say which secrets it needs — `needs` on the job
  (Schedule form, `pipeos schedule add|set NAME --needs jobs.a,jobs.b`,
  carried by a placement). Before a run the runner checks each name in the
  vault's export; the first missing one is asked for through the agent's
  door (`pipeos secrets request`, #301) and the run waits — exit 75, the
  Schedule row reads *waiting* with the reason, one DM per new ask — until
  the owner approves on a holder and the copy lands: a cron job runs at
  its next match, a manual job is run the moment the copy arrives. A
  denied ask is not repeated for a day. Only `jobs.*` names (the export
  the runner sources). An agent placed on a member that lacks its secrets
  asks at once instead of failing its first run.
- Built (#300): an agent is a scheduled job, and it is placed by being
  written into one member's `schedule.json` through that member's own
  `/api/agent/start` and run there — `pipeos cluster start NAME --on
  ID|NAME|idlest`, Cluster → *Start an agent on a Machine*. Explicit is the
  default; `idlest` is the awake member with nothing busy and the least
  load per cpu, then the fewest agents running. Every member's agents ride
  its summary, so the page and `pipeos cluster agents` list them all; a
  grey member's row shows the agents it had at its last answer, marked
  last-known, from a per-member note under `/work/pipeos/cluster/last` —
  nothing is ever restarted from it. Job placement writes no state on the
  box that asked. A placement without a schedule is a `manual` job: it
  runs now and then only when started (Run now, `pipeos schedule run`,
  another placement) — "run once, now" needs no invented cron.

- Built (#301, pre-share half): a secret's vault entry carries `shared`
  — the members it was copied to. `pipeos vault share NAME ID…` / the
  *shared with* ticks on the Secrets page copy a text secret (never a
  Machine's own: terminal password, support key, SMB db, stream keys) to
  each member through that member's `POST /api/secrets/receive` over
  mutual TLS; the member stores it as `cluster:<holder>`, exports it, and
  saves. A copy never overwrites a secret the member set itself. Unshare
  only forgets. Every other secrets handler now takes an admin *session*
  only — a member's certificate may ask `have` and hand over `receive`,
  nothing else.
- Built (#301, the request half): `pipeos secrets request NAME [WHY]` is
  the resident agent's one door (allowed in the fence; every `vault` verb
  stays denied). It asks every member `have` (existence only), writes the
  record in `/etc/pipeos/vault-requests.json` on the requester (saved; it
  rides the apkovl and is re-offered at boot) and offers a tmpfs notice
  to every member, so the request shows on whichever dashboard the owner
  has open (Secrets → *Share requests*, and an alert). **Approve on a
  Machine that holds the secret** (Sam, 2026-09-19: the review of the
  first cut showed that approving elsewhere means the holder acts on
  another member's word, which a compromised member can forge): the
  holder's own admin session flips the request to approved under a lock
  (a second tap finds it decided), sends its copy to the requester's
  `receive` with the request id — the requester takes it only from a
  holder the request named — stores the note, and every member closes
  it. A dashboard on any other Machine shows the request with a link to
  each holder's page. Deny closes it everywhere. Requests expire after a
  day, unseen.

→ #300 (placement + the agent list), #301 (vault copy on demand),
#302 (caps at every level, the pause names its cap)

## Order of work

1. §1 identity (hostname = id, alias = name, suggester) — small, unblocks
   re-claiming the bench boxes.
2. §3 membership + §10 the page — the cluster exists once these land.
3. §11 join/adopt — the customer path.
4. §6 roles, §5 users view, §9 rolling updates.
5. §4 netgaze map, §2 labels, §8 `/data` rename and file index.
6. §12 the modes, in the order given there: fleet view is the pilot
   deliverable; then placement + the agent list; then roles filled
   elsewhere; then `run` on idle members.
