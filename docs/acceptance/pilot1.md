# Acceptance — pilot1, the pilot delivery (#159)

Release under test: `________` (cut after #219 lands; never a local build).
Box: card `docs/cards/pilot1.card`, NICK=pilot1; claimed by the client on their premises.
Date: `________`   Support port: `42003` (docs/support-relay.md ledger, pinned on the day).
The rule from docs/fulfillment.md § Pilot delivery: the client's hands, not ours —
we watch and fill this in; we do not touch the keyboard.

| # | section | step | result |
|---|---------|------|--------|
| 1 | The stick | Flashed from the release asset with `make flash DEV=` or the fulfillment recipe — never a local build. |  |
| 2 | The stick | `sha256sum -c` passed. |  |
| 3 | Power to the page | Cable in, power on, stopwatch started. |  |
| 4 | Power to the page | `http://pipeos.local/` answers on a **phone** in ____ s — the dashboard, no warning page (http is the front door by decision, 2026-09-12; never https on the LAN). |  |
| 5 | Power to the page | …on a laptop in ____ s. |  |
| 6 | Power to the page | With a second Machine up: appears in its lobby within ____ s; the unclaimed one is reachable by its `pipeos-xxxx.local` name. |  |
| 7 | Power to the page | If the phone could not resolve `pipeos.local` (Android without mDNS): the IP was findable from the router page in ____ s, and the onboarding sheet's wording covered it: yes / no. |  |
| 8 | The wizard, as a customer | Claim: the password rules were clear; time to the dashboard ____ s. |  |
| 9 | The wizard, as a customer | Name: renamed to `____`; `http://<name>.local/` answered within ____ s (mdnsd re-reads the hostname per query, no restart). |  |
| 10 | The wizard, as a customer | Services: the defaults matched the sheet (Claude on, the rest off). |  |
| 11 | The wizard, as a customer | **Claude sign-in**: link shown in ____ s; signed in on the phone; code pasted; "Claude answered — connected." in ____ s. Wording that confused: ____________________. |  |
| 12 | The wizard, as a customer | pipe (optional): skipped / signed in as `____`. |  |
| 13 | The dashboard | Boot report readable, verdict green. |  |
| 14 | The dashboard | Setup view round-trips (name, Claude pill says "signed in"). |  |
| 15 | The dashboard | The TLS certificate offer made sense to a stranger: yes / no. |  |
| 16 | The dashboard | Chat: one question, one answer, ____ s. |  |
| 17 | The drills | **Reboot the box**: back on the page with state kept in ____ s. |  |
| 18 | The drills | **Repair remote access**: visibly did something ("____"). |  |
| 19 | The drills | Power pulled once mid-run; booted again; the report said power loss and state was kept. |  |
| 20 | The drills | **Watchdog** (#247, docs/hardware.md): `echo c > /proc/sysrq-trigger` on the bench; the box came back by itself in ____ s and the report said "went down: kernel panic — the watchdog rebooted us". |  |
| 21 | The drills | **Vendor support access** on: the key and "waiting for a port" card appeared; key sent; port pinned on the relay; pill went "tunnel up" in ____ s; operator reached the box with `ssh -J`; toggle off; the tunnel died in ____ s. |  |
| 22 | The drills | **Secrets sealed** (#244): after claim the wizard showed the recovery phrase once; `grep -r sk-ant /etc /media/usb` on the box finds nothing; `pipeos verify` says *secrets sealed*; after a reboot Claude is still signed in, the support tunnel and the terminal come up. **Rehome drill**: this stick in another chassis → the boot report says *chassis mismatch* and *vault is LOCKED*, the dashboard is reachable, Secrets → phrase → the services start; `pipeos save`. |  |
| 23 | The drills | **Scheduled run** (#242): a job added from Schedule fired at its minute with nobody attached; the started/done DMs arrived; the log opened from the row; a second Run now while it ran was refused. |  |
| 24 | The drills | **Usage** (#246): one dashboard chat and one assistant prompt showed as two rows under Usage within a minute, costed; a $1 cap produced the 80% DM, then the pause banner and a skipped job; raising it lifted the pause. |  |
| 25 | The drills | **Wake** (#241, docs/hardware.md): shut one Machine down; on a sibling's Network view it went grey ("off · last seen") in ____ s; Wake → back in the lobby in ____ s. `pipeos wake <name>` from a third Machine did the same. With Wake on LAN off in the BIOS the boot report still read green and the packet did nothing (expected). |  |

## What a stranger tripped on

1. ____________________
2. ____________________

## Sign-off

- [ ] every row above ticked or struck with a dated reason
- [ ] the support door proven both ways (row: Vendor support access); port 42003 pinned; `ssh -J` worked; toggle off, tunnel gone
- [ ] `pipeos verify` PASS on the box before we leave
- [ ] this file committed with the release tag and the port
