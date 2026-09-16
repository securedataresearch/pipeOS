# Acceptance — the store switch-on and the watchdog, 2026-09-15

Release under test: pipe `a7b5f2c3` (#923 cluster Buy + install add-on + US
flat shipping + 4xx bodies logged; #924 managed payments off on the box
session; #925 the Machine's specs on the page) on pipe.online; pipeOS overlay
`eeeeb2b` (#294 watchdog card switch, 60 s, boot-report cause, verify regex;
#295 the row reads the pidfile) deployed to zero by `pipeos deploy-overlay`.
one/two/three were powered off and did not answer `pipeos wake --all` (WoL
only on the uncabled onboard port). Driven from the workstation; the browser
pass by Sam.

| # | step | result |
|---|------|--------|
| 1 | live Stripe prices minted (disk $20, machine $799, cluster $7,999, on-site install $500), keys on the relay's live app as SECRET, spec-drift clean | PASS |
| 2 | first Buy after #923: relay log carries Stripe's body — "Shipping parameters cannot be used with Managed Payments, which is enabled by default on your account" (the week of 504s, one cause) | PASS (root cause) |
| 3 | after #924: `POST /stripe/checkout-box?sku=disk|machine|cluster` from the DO origin → 303 to checkout.stripe.com; `sku=rack` → 400 | PASS |
| 4 | the three sessions: `mode=payment`, `managed_payments.enabled=false`, `allowed_countries=[US]`, shipping $5/$25/$250 with delivery estimates, `automatic_tax.enabled`, `metadata.pipe_kind`, cancel → `/hardware/` | PASS |
| 5 | Sam's browser pass on all three Buy pages: shipping line, install toggle on Machine and Cluster, Cancel lands on /hardware/ | PASS (Sam) |
| 6 | California state sales tax registration active in Stripe Tax (`taxreg_1UG2eqBLyiUFnqirz46iJkNr`); the head office had to be re-saved through the Tax Settings API first | PASS |
| 7 | one real $20 order, refunded (proves the webhook's payment-mode path) | not run — Sam trusts the links; watch for the "hardware order paid" INFO line on the first real order |
| 8 | zero: watchdog already live since f0d48ba (30 s); after #294 `pipeos watchdog status` = armed 60 20 /dev/watchdog0, device timeout 60 s | PASS |
| 9 | zero: `pipeos verify` FAIL before (the plaintext-secret check matched `support_key.pub`); PASS after the anchored regex | PASS |
| 10 | zero: selfcheck row said "petter is gone" with `/sbin/watchdog` running (busybox `pgrep -x` matches the whole command line); after #295 the row is silent, verdict green with 1 pre-existing warning | PASS |
| 11 | `pipeos watchdog off` → state off, no petter; `kernel` → re-armed | PASS (in the verb probe; live off/on not exercised on zero) |
| 12 | the panic drill: `echo c > /proc/sysrq-trigger`, back by itself, boot report says "went down: kernel panic — the watchdog rebooted us" | **owed — Sam's call** (it crashes a Machine on purpose) |

## Notes
- Owed by Sam: the CDTFA seller's permit (Stripe collects CA tax as of today), the
  successful-payment email toggle, powering one/two/three so they get `eeeeb2b`.
- The 2026-09-10 `cd.yml` run had failed, so the relay ran a pre-#919 image until today's merges.
