# First-boot acceptance — the pass a stranger makes

The checklist for #157 and for every pilot delivery (#159): a released
image, the reference box, a phone and a laptop, **no console at any
point**. Every row has a blank for the measured number or the observed
wording; a pass is the sheet filled in and every row ticked. The first
filled-in copy lives in the PR that ran it; later runs go in
`docs/acceptance/<date>-<box>.md`.

Release under test: `________`   Box: `________`   Date: `________`

## 1. The stick

- [ ] Flashed from the release asset with `make flash DEV=` or the
      fulfillment recipe — never a local build.
- [ ] `sha256sum -c` passed.

## 2. Power to the page

- [ ] Cable in, power on, stopwatch started.
- [ ] `http://pipeos.local/` answers on a **phone** in ____ s.
- [ ] …on a laptop in ____ s.
- [ ] With a second Machine up: appears in its lobby within ____ s; the
      unclaimed one is reachable by its `pipeos-xxxx.local` name.
- [ ] If the phone could not resolve `pipeos.local` (Android without
      mDNS): the IP was findable from the router page in ____ s, and the
      onboarding sheet's wording covered it: yes / no.

## 3. The wizard, as a customer

- [ ] Claim: the password rules were clear; time to the dashboard ____ s.
- [ ] Name: renamed to `____`; `http://<name>.local/` answered within
      ____ s (mdnsd re-reads the hostname per query, no restart).
- [ ] Services: the defaults matched the sheet (Claude on, the rest off).
- [ ] **Claude sign-in**: link shown in ____ s; signed in on the phone;
      code pasted; "Claude answered — connected." in ____ s. Wording that
      confused: ____________________.
- [ ] pipe (optional): skipped / signed in as `____`.

## 4. The dashboard

- [ ] Boot report readable, verdict green.
- [ ] Setup view round-trips (name, Claude pill says "signed in").
- [ ] The TLS certificate offer made sense to a stranger: yes / no.
- [ ] Chat: one question, one answer, ____ s.

## 5. The drills (the sheet's "Something's weird?" and "Need help?")

- [ ] **Reboot the box**: back on the page with state kept in ____ s.
- [ ] **Repair remote access**: visibly did something ("____").
- [ ] Power pulled once mid-run; booted again; the report said power loss
      and state was kept.
- [ ] **Vendor support access** on: the key and "waiting for a port" card
      appeared; key sent; port pinned on the relay; pill went "tunnel up"
      in ____ s; operator reached the box with `ssh -J`; toggle off; the
      tunnel died in ____ s.
- [ ] **Secrets sealed** (#244): after claim the wizard showed the
      recovery phrase once; `grep -r sk-ant /etc /media/usb` on the box
      finds nothing; `pipeos verify` says *secrets sealed*; after a reboot
      Claude is still signed in, the support tunnel and the terminal come
      up. **Rehome drill**: this stick in another chassis → the boot
      report says *chassis mismatch* and *vault is LOCKED*, the dashboard
      is reachable, Secrets → phrase → the services start; `pipeos save`.
- [ ] **Scheduled run** (#242): a job added from Schedule fired at its
      minute with nobody attached; the started/done DMs arrived; the log
      opened from the row; a second Run now while it ran was refused.
- [ ] **Usage** (#246): one dashboard chat and one assistant prompt showed
      as two rows under Usage within a minute, costed; a $1 cap produced
      the 80% DM, then the pause banner and a skipped job; raising it
      lifted the pause.
- [ ] **Wake** (#241, docs/hardware.md): shut one Machine down; on a
      sibling's Network view it went grey ("off · last seen") in ____ s;
      Wake → back in the lobby in ____ s. `pipeos wake <name>` from a
      third Machine did the same. With Wake on LAN off in the BIOS the
      boot report still read green and the packet did nothing (expected).

## First run — 2026-09-09, two Lenovo 10RR Machines, release 35f697b

Timed from the workstation over the API, not from a phone; the phone pass
is still owed.

- Both Machines up and answering `/api/state` within a minute of power.
- **Discovery dead on both**: the responder never started — supervise-daemon
  opens its log after dropping to svc-mdns, and `/work/logs` is root's.
  Fixed in the init script (the log file is made owned by the user).
- **Both booted DEGRADED** with two CRITICALs a customer cannot act on: a
  stale card stamp shipped by the build (the stamp is gitignored; the
  build now regenerates it) and "authorized_keys missing" on an image
  whose root is locked by design (now a note). A fresh Machine must boot
  green; the lobby shows the verdict to a stranger.
- After the responder fix: each Machine appeared in the other's lobby in
  under 5 s; rename to the sibling's name, to its pre-claim name, and to
  an older non-lobby box's name all refused with the right sentence; real
  renames to `alpha` and `beta` answered on the LAN at once.
- The flasher's closing line still said "root / pipeos" for a client
  image. Fixed.

## 6. What a stranger tripped on

Every place the pass stopped, with the wording on screen and what it
should have said. Each becomes a fix in the same PR or an issue.

1. ____________________
2. ____________________
