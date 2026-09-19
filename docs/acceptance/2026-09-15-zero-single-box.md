# Acceptance — zero as a single-box customer, 2026-09-15/16

Release under test: overlay `e15cce7` (#290 live verdict, #294/#295 watchdog) on
zero's image `ddea944` (release repo-2026.09.12-ddea944). Sam's decision
2026-09-15: perfect and optimise for a **single-box owner**; zero taken back to
unclaimed (`pipeos unclaim --yes`, 01:33Z) and the first-boot sheet driven as a
stranger — laptop and box side from the workstation, phone steps by Sam.

| # | section | step | result |
|---|---------|------|--------|
| 1 | stick | image is the release asset (ddea944), not a local build | PASS (the fleet stick, flashed from the release 2026-09-12) |
| 2 | power to the page | `/api/state` answered ~55 s after the reboot; `pipeos.local` and `pipeos-a4e0.local` resolve from the workstation | PASS |
| 3 | power to the page | the unclaimed boot report: **DEGRADED** — `pipeos verify FAIL: canonical apkovl missing`; `lbu commit -d` at the end of unclaim had written `pipeos-a4e0.apkovl.tar.gz` and deleted the canonical and the known-good | **FAIL → fixed** (unclaim ends in one `pipeos-save` in unclaim mode) |
| 4 | power to the page | the unclaimed box was still signed into pipe as `box0` (relay connected), started pipe-daemon + pipebox-listener (no services.conf = the fleet default), and kept cluster.json (still a member of 3762aee1…, CA trusted by the others), 32 NAS share lines, the pinned support port, assistant.conf | **FAIL → fixed** (unclaim is a factory reset; an unprovisioned box wants no pipe/claude) |
| 5 | power to the page | `pipeos.local` on a phone: ____ s (Sam) | owed |
| 6 | power to the page | the FIXED unclaim (#298, afed71e) run on zero: reboot to the page in 41 s; media = canonical + known-good (identical), no hostname file; verify PASS; boot report green, pipe=off claude=off, only web + mDNS up; pipe not authenticated; no cluster; a NEW CA (248c0173…); no Claude credentials left | PASS |
| 7 | power to the page | the one warning an unclaimed box shows read "UNPROVISIONED: autosave is OFF … finish pipebox-setup (or bake a card: make stick)" — fleet-speak on the first screen a stranger sees | **stumble → reworded** ("not claimed yet — open http://<host>.local/ … choose a password") |
| 8 | wizard | **agent-driven, over the API** (Sam: "this whole experience needs to be agent driveable"): `POST /api/claim` → ok, saved, recovery phrase; `POST /api/name solo` → `solo.local` resolves and answers 200 within seconds, cert reloaded | PASS |
| 9 | wizard | services: **no GET** for the declared set (only /api/status carries it); a claim over the API left **no services.conf**, so selfcheck read the fleet default (pipe on) | **stumble → fixed** (GET /api/services; claim writes Claude on, rest off) |
| 10 | wizard | after the claim the dashboard still showed "claimed: no" and the not-claimed warning (the boot report is a snapshot; the live verdict is hourly) | **stumble → fixed** (every save fires `pipeos-selfcheck --live`) |
| 11 | wizard | Claude sign-in: `POST /api/claude-login/start` returns the OAuth link — the one step an agent cannot finish alone (an Anthropic account signs in); the agent path is `POST /api/claude-token` with an API key | PASS as designed; **owed: Sam's code or key** |
| 12 | drills | Vendor support access ON: the key and "waiting for a port" (port "") shown; OFF: gone. The "problems" text said "no relay is configured yet" while the relay is shipped — the port is what is missing | **stumble → reworded** |
| 13 | drills | Repair remote access: "restarted sshd, saved". Reboot the box (API): back in **59 s** with the claim and the name; the browser session did not survive (sessions are tmpfs — sign in again, by design) | PASS |
| 14 | drills | Secrets sealed: `grep -r sk-ant /etc /media/usb` finds nothing; `pipeos verify` says *secrets sealed*; vault open | PASS |
| 15 | drills | Usage: the new owner's Usage view opened on **$2.46 / 14 calls of the old owner's spend** (the ledger lives on /data, which unclaim keeps) | **stumble → fixed** (unclaim drops the ledger, schedule runs, transcripts, sessions, chat, logs; /data/home and /data/repos stay) |
| 16 | drills | Usage cap over the API: `{"cap": 1}` refused with "the cap is a whole number of dollars…" — the field name is not the obvious one and the error does not name it | **stumble → fixed** (`cap` accepted as an alias; the error names `usd`) |
| 17 | drills | Schedule: add + Run now over the API ok (saved); the run itself needs Claude signed in (row 11) | PASS (add/run), owed (the fire) |
| 18 | drills | Usage cap over the API (`usd`): cap $1 under $2.46 spent → paused at once, Run now refused with "scheduled runs are paused — the monthly cap is reached; raise it under Usage"; cap none → `enforce` unpaused and the paused file went — but `/api/status` still said paused for up to a minute (the ledger's 60 s cache) | PASS; **stumble → fixed** (a cap change refreshes the status cache) |

