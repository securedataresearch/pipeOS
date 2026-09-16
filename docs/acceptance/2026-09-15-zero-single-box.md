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

