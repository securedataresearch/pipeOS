# pipeOS roadmap

pipeOS is a client product: diskless Alpine that boots any spare x86_64 box
into an agent appliance, set up and run entirely from a browser
(`http://pipeos.local/` — claim, name, toggle services, paste tokens), with
silent self-update from this repo's GitHub Releases and recovery levers
(reboot / repair access) on the dashboard. The repo is public under
Apache-2.0 (see NOTICE for the trademark policy).

An earlier revision of this file was the internal dev-fleet's operating plan;
that machinery (cohort boards, quorum, the Foreman) is dormant while the
product ships and its issues carry the `fleet` label.

## Phases

1. **Hardening** — the open `product`-labeled issues: key-store robustness,
   CI probe coverage, policy.json gating, selfcheck accuracy, index-format
   truth (#94 #108 #109 #110 #111 #119 #150), and fresh-image agent-memory
   layout (#80 residual).
2. **Dashboard depth** — streaming config page (acceptance: basho0's real
   restream configured through the UI), logs viewer, pipe connect page,
   visible update surface, support-toggle completion.
3. **Self-builders** — build-your-own docs (any UEFI x86_64), the
   sovereignty guarantees in writing (your own signing key, your own update
   origin or none, your own relay), host-portable build scripts, a public
   README front door.
4. **Support + pilots** — a dial-out support relay (opt-in toggle, client
   revocable), pilot deployment kits, the reboot drill on-site.
5. **The store** — one-off Stripe checkout for boot media and preloaded
   hardware on pipe.online (the relay's Stripe integration gains
   `mode=payment`; Stripe's dashboard is the order book), plus the
   fulfillment runbook: flash published image → burn-in selfcheck →
   ship unclaimed.

Later: fleet revival, remote power control, ARM.

## Invariants every phase keeps

- One generic image; identity enters at claim time, never at build time.
- No console, CLI, or pipe account required of an owner — ever.
- Updates apply only through the verified/atomic/rollback path, and a dead
  update origin is loud (selfcheck), never silent.
- The agent is fenced by machine policy, not etiquette; recovery
  (reboot = restore to last saved state) requires no expertise.
- `pipeos verify` PASS before and after anything that touches media.
