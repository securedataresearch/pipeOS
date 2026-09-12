# The web wizard — first contact and management for client boxes

A client box is set up in a browser, not a console. The box boots listening
on `:80` (`pipeos-web`), advertises `<hostname>.local` over mDNS
(`pipeos-mdns`), and the first visitor **claims** it by setting an admin
password. Everything else — naming the box, choosing services, connecting
Claude and pipe — is wizard steps behind that password.

This replaced the pipe-first design after basho0's first boot (2026-08-29):
the baked one-time pipe key expires ~15 minutes after minting, so it was dead
on every box not booted at the flashing desk, and the fallback needed a
console that clients don't have. pipe is now a *service the wizard turns on*,
default off; the pipe-first flow (`pipebox-setup`) remains for fleet boxes.

## Flow

0. **Find** — every Machine answers `pipeos.local`, and on a LAN with more
   than one that address is the **lobby**: a page every Machine serves
   identically, listing each Machine the responder has heard (name or
   pre-claim name, claimed?, boot verdict, address, link). A lone
   unclaimed Machine skips the lobby and opens its wizard; `/lobby` shows
   it regardless, and a claimed Machine's login page links to it. The
   pre-claim name is `pipeos-<last 4 hex of the primary MAC>.local`, so a
   claim lands on the Machine you meant. Discovery is `mdnsd.py`
   advertising `_pipeos._tcp` (PTR/SRV/TXT/A) and asking for it every
   10 s; peers land in `/run/pipeos/mdns/peers.json`, which `GET
   /api/lobby` (public, like `/api/state`) reads. No leader: a view of the
   network needs none. Renaming refuses a name a sibling uses or that
   already answers on the LAN (one mDNS question, one second).
1. **Claim** — set the admin password. This writes
   `/etc/pipeos/web-admin.conf` (the claim credential), sets
   `/etc/pipeos/provisioned`, and runs `pipeos-save` immediately: the claim
   survives a reboot even if the wizard is abandoned right here.
2. **Name** — the owner's alias (+ optional owner nick), NAME= in
   `card.conf` + `pipebox-card generate`, so motd, issue and the derived
   files stay card-verified. The hostname is NOT the name (docs/cluster.md
   §1): it is the chassis id `pipeos-<mac4>`, written from hardware at
   every boot by the `pipeos-identity` service, and `pipeos-<mac4>.local`
   is answered for life beside `<name>.local`. The wizard offers five
   classic-car names (`GET /api/name-suggest`, seeded by the id, skipping
   what the lobby already shows); `pipeos-xxxx` names are refused. NICK
   stays the pipe identity. `pipeos unclaim` (root) is the way back to an
   unclaimed Machine — claim, users, name and owner go, the box reboots
   into this wizard.
3. **Services** — toggles written to `/etc/pipeos/services.conf` and mirrored
   to `rc-update`/`rc-service`. The listener (pipe→claude bridge) runs only
   when pipe AND claude are both on.
4. **Connect** — Claude, two ways, no terminal on either side (#192):
   **sign in** — the box runs `claude auth login` with its browser
   suppressed (`--console` when the owner picks Console billing), hands
   the page the sign-in URL it prints, and the owner signs in on any
   device and pastes back the code the page shows; the code goes to the
   waiting process's stdin and the credential lands where `claude` keeps
   and refreshes it (`/root/.claude/.credentials.json`, in lbu's list; the
   15-minute autosave persists a refresh). Or **paste** an Anthropic
   Console key (`sk-ant-api…` → `ANTHROPIC_API_KEY=`) or a `claude
   setup-token` (→ `CLAUDE_CODE_OAUTH_TOKEN=`) into
   `/etc/pipeos/claude-auth.env`. Either way one real `claude -p` call
   proves it. The env file wins over the sign-in (claude reads the
   variable every session), so a successful sign-in deletes it. Anthropic's
   SDK docs reserve claude.ai login in third-party products for approved
   partners; the Console-key path is the one those docs point at, and the
   card offers both — the owner's call. pipe: paste a one-time key from
   pipe.online — harmless TTL now, a human is live on the page; the box's
   nick is derived back from `pipe status` (#134), never typed.

The dashboard humanizes `/run/pipeos/boot-report` (the pipe owner-DM was the
only delivery channel before; it still works when pipe is on), toggles
services, shows disk/uptime, saves state, changes the password — and, with
Claude enabled, carries **web chat**: `POST /api/chat` feeds the box's Claude
(same fence as the pipe listener, one continued conversation under
`/work/pipebox/webchat`). For a pipe-less box this is the assistant surface.

Two more toggles ride the same services model:
- **Vendor support access** (`pipeos-support`): opt-in reverse tunnel
  (`ssh -R`) OUT to a support relay (`/etc/pipeos/support.conf`; the key is
  made on first enable and `GET /api/support` shows it, #159); refuses to
  start unconfigured, off by default, one switch for the customer.
- **Updates**: silent daily self-update once `UPDATE_RELEASE_URL` points at a
  published release (`make release` → GitHub Release with SHA256SUMS +
  pipeos-repo.tar.gz); applies through the existing verify → atomic swap →
  save → verify-or-rollback path, and selfcheck warns when the update path
  is configured but stale or erroring.

## Security posture (deliberate, owner-approved)

- **LAN listener, claim-on-first-visit.** The generic image ships unclaimed;
  whoever reaches the page first owns the box. That is the standard appliance
  posture (routers, printers, Home Assistant) and it is accepted here.
  No packet filter in MVP; the bind is wide, the LAN is the boundary.
- **Root has no password on the client image** (`ROOT_LOGIN=locked`, the
  default — shadow field `*`: unmatchable, but not sshd-"locked", so key auth
  still works): no baked well-known password anymore. sshd is key-only
  (`prohibit-password`); operator sticks bake a key with
  `make stick AUTH_KEYS=...`. Fleet sticks build with `ROOT_LOGIN=password`.
- Sessions are random tokens in `/run/pipeos/web-sessions` (tmpfs — a reboot
  signs everyone out). Cookies are `HttpOnly; SameSite=Strict`; cross-origin
  POSTs are refused; failed logins cost a flat 2 s.

## Files

| File | Role |
|---|---|
| `usr/local/share/pipeos/web/webd.py` | the daemon (python3 stdlib, single-threaded) |
| `usr/local/share/pipeos/web/mdnsd.py` | minimal mDNS responder |
| `usr/local/share/pipeos/web/static/` | the UI (no framework, no build step) |
| `usr/local/bin/pipeos-webd`, `pipeos-mdnsd` | shell launchers (CI shellchecks bin/) |
| `usr/local/share/pipeos/web/lanid.py` | LAN identity (primary MAC, model) and the mDNS wire, shared by webd and mdnsd |
| `/run/pipeos/mdns/peers.json` | the responder's peer cache — what the lobby lists |
| `usr/local/bin/pipebox-claude-trust` | shared headless-claude trust helper |
| `etc/init.d/pipeos-web`, `pipeos-mdns` | always in the default runlevel |
| `etc/init.d/pipeos-stream` | ffmpeg restream, toggled via the UI |
| `/etc/pipeos/web-admin.conf` | claim credential (absent = unclaimed) |
| `/etc/pipeos/services.conf` | declarative enabled-services record |
| `/etc/pipeos/stream.conf` | streaming parameters (Phase B page) |

All runtime state files carry `+` lines in `protected_paths.d/lbu.list`.

## Users (multi-user login, non-root accounts, terminals)

One "box user" record (`/etc/pipeos/users.json`, mode 600) grants any subset
of: web login (role `admin` or `viewer` — viewers read, every mutating POST
403s), an SSH account, a browser terminal, and doas. The claim still writes
`web-admin.conf`; it stays the claim marker AND the lockout escape hatch — if
users.json is missing or corrupt, auth falls back to the original admin
password, so the dashboard is always reachable.

- Unix accounts are created by `usr/local/bin/pipeos-user` (dashboard shells
  out; also usable over ssh). Homes are real paths on `/work/home/<name>`
  (ext4): they survive a media reflash even though the accounts (apkovl) do
  not — recreating the user re-adopts the surviving home's uid. Shadow gets
  `*`, never busybox's `!` (which blocks even pubkey auth); sshd_config is
  never touched.
- doas policy is `permit persist :wheel` (`etc/doas.d/pipeos.conf`); it
  checks the user's own password, so sudo-flagged users get their web
  password hash synced into shadow. No `nopass`, ever. The doas package
  rides `world` — boxes on older media get a graceful warning until their
  next image update.
- Browser terminals: one ttyd per terminal-enabled user (ports 7701+, own
  password), each running `su -l <user>` — a real non-root shell in their
  /work home. `etc/init.d/pipeos-terminals` supervises the set;
  `/etc/pipeos/terminals.conf` is generated from users.json.
- Guards: you cannot delete yourself, nor delete/disable the last enabled
  admin; deleting keeps `/work/home/<name>` unless purge is chosen; every
  /etc/shadow edit is awk → temp → atomic rename.

**Future work (deliberately out of scope):** the claude agent, pipe daemon,
and assistant terminal still run as root — `/root` IS the agent's identity
(.claude, .pipe, gh auth) and lbu.list is built around those paths. Moving
the agent to its own user is a separate project.

## Testing

VM: `make vm` forwards `:8080 → :80` (and ssh on 2222). Claim at
`http://localhost:8080/`, toggle, `reboot`, confirm everything survives and
the boot report is not DEGRADED with pipe off. mDNS cannot traverse QEMU
user-mode networking — test discovery on a real LAN.

## Public https (#286)

After the claim the wizard shows "Getting your secure address": the Machine
registers `<mac>.m.pipe.online` at the relay (its own ed25519 key, kept in
the vault), gets a Let's Encrypt certificate itself with `acme.sh` over
DNS-01 (the relay writes the one TXT record), and the finish button lands on
`https://<mac>.m.pipe.online/` — a padlock on every phone and laptop with
nothing installed. From then on every plain-http way in (`pipeos.local`,
`<name>.local`, the IP) redirects there; the lobby's JSON, the CA downloads
and the unclaimed wizard stay on http.

What can go wrong, and what the box does: no internet → the wizard says so
and continues on the local address; the daily job retries. A router that
refuses to resolve the name to a private address (DNS rebind protection —
pfSense, dnsmasq's `stop-dns-rebind`) → selfcheck WARNs with the setting to
change (allow `m.pipe.online`), the redirect stops, the local address keeps
working with the box CA's certificate as before. `pipeos tls public
status|issue|renew|on|off` is the verb; the Network view's Secure access
card shows the same.

The box CA's own certificate stays for the `.local` names and the IP (and
is the cluster identity, #284); the "install this box's certificate" path
is still there under a details block for a Machine that will never have
internet.
