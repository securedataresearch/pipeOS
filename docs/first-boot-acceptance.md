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

## 6. What a stranger tripped on

Every place the pass stopped, with the wording on screen and what it
should have said. Each becomes a fix in the same PR or an issue.

1. ____________________
2. ____________________
