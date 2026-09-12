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

→ #217

## 5. Sign-in and users — keys, not passwords; no propagation

- Management prefers **ssh keys**; passwords are the fallback, and each
  Machine keeps its own admin password for now. One-login-everywhere is
  not a goal while keys are the plan.
- **Nothing propagates.** Adding a user on one Machine adds it there only.
  The cluster page shows users **cluster-wide** (every box's list, side by
  side) but editing is per box. The rule generalises: this network avoids
  propagation wherever it can.
- Sign-in is per box for now; with keys everywhere the question goes away.

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

- The bulk volume is renamed **`/data`**; `/work` is reserved for
  something else. (Today `/work` is `LABEL=PIPEWORK`, `restore-work`,
  `/work/repos`, 79 files mention it — the rename is its own PR and
  needs a compatibility symlink for a release or two.)
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
  member's boot report in one place.
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

→ #213

The second box's secrets are its own: claiming it mints its own vault and
recovery phrase (#244). A stick moved between Machines needs the phrase
once, then belongs to the chassis it is in.

## Order of work

1. §1 identity (hostname = id, alias = name, suggester) — small, unblocks
   re-claiming the bench boxes.
2. §3 membership + §10 the page — the cluster exists once these land.
3. §11 join/adopt — the customer path.
4. §6 roles, §5 users view, §9 rolling updates.
5. §4 netgaze map, §2 labels, §8 `/data` rename and file index.
