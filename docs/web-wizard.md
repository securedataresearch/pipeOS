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

1. **Claim** — set the admin password. This writes
   `/etc/pipeos/web-admin.conf` (the claim credential), sets
   `/etc/pipeos/provisioned`, and runs `pipeos-save` immediately: the claim
   survives a reboot even if the wizard is abandoned right here.
2. **Name** — NICK (+ optional owner) via `card.conf` + `pipebox-card
   generate`, so hostname, motd, and the derived files stay card-verified.
3. **Services** — toggles written to `/etc/pipeos/services.conf` and mirrored
   to `rc-update`/`rc-service`. The listener (pipe→claude bridge) runs only
   when pipe AND claude are both on.
4. **Connect** — Claude: paste a `claude setup-token` token
   (→ `/etc/pipeos/claude-auth.env`, smoke-probed with a real `claude -p`
   call). pipe: paste a one-time key from pipe.online — harmless TTL now,
   a human is live on the page; the box's nick is derived back from
   `pipe status` (#134), never typed.

The dashboard humanizes `/run/pipeos/boot-report` (the pipe owner-DM was the
only delivery channel before; it still works when pipe is on), toggles
services, shows disk/uptime, saves state, changes the password — and, with
Claude enabled, carries **web chat**: `POST /api/chat` feeds the box's Claude
(same fence as the pipe listener, one continued conversation under
`/work/pipebox/webchat`). For a pipe-less box this is the assistant surface.

Two more toggles ride the same services model:
- **Vendor support access** (`pipeos-support`): opt-in reverse tunnel
  (`ssh -R`) OUT to a support relay (`/etc/pipeos/support.conf`); refuses to
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
