# The support relay — opt-in vendor access, end to end

The relay is one small host that support tunnels dial OUT to. A customer box
never listens on its own network; the owner's "Vendor support access" toggle
is the only thing that opens the path, and turning it off closes it.

**Live relay**: `relay.pipeterminal.com` (DO droplet `pipeos-relay`, sfo3).
sshd accepts the `tunnel` user for reverse forwarding only — no shell, no
TTY, no local forwards (`/etc/ssh/sshd_config.d/60-pipeos-relay.conf`).
Operator admin is root-by-key.

## How a box enrolls

1. Owner flips **Vendor support access** ON in the dashboard. The box
   generates its tunnel identity (`/etc/pipeos/support_key`, ed25519) on
   first enable and the dashboard shows the **public key** and the box's
   assigned port.
2. Owner sends us that public key (any channel — it is public).
3. Operator appends it on the relay, pinned to the box's port so one box
   cannot squat another's:
   ```sh
   ssh root@relay.pipeterminal.com
   echo 'permitlisten="127.0.0.1:42001" ssh-ed25519 AAAA… basho0' \
     >> /home/tunnel/.ssh/authorized_keys
   ```
   Port assignment ledger: keep it in this file — one line per box,
   42001 upward, comment = box name.
4. The box's `pipeos-support` service (already running from the toggle)
   connects within its 30s respawn window: `ssh -R 42001:127.0.0.1:22
   tunnel@relay.pipeterminal.com -N`.

## How the operator reaches the box

```sh
ssh -J root@relay.pipeterminal.com root@127.0.0.1 -p 42001
```
(Box-side sshd is key-only; the operator key must be in the box's
`authorized_keys` — for boxes we shipped, it is baked; for a customer's
self-managed box, they add it or nobody gets in. The toggle grants the
*path*, never the *credential*.)

## Revocation

- Customer side: the toggle. Off = tunnel dies within seconds and cannot
  reconnect.
- Relay side: delete the box's line from `authorized_keys`.

## Ops notes

- The droplet runs stock Ubuntu LTS + unattended upgrades; it holds no
  secrets beyond public keys and never sees tunnel plaintext (end-to-end
  ssh from operator to box).
- Rebuild-from-nothing: create any small droplet, add the sshd dropin and
  the `tunnel` user (cloud-init in the fleet notes), repoint the DNS A
  record, re-enroll keys. Nothing else to restore.
