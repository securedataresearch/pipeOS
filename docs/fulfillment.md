# Fulfillment runbook — from order to shipped box

Scope: turning a paid order into a box in the mail. The order book is the
Stripe dashboard; nothing here depends on any other order system.

## Turning the store on (done 2026-09-15)

The Buy buttons on pipe.online/hardware post to the relay's
`/stripe/checkout-box?sku=disk|machine|cluster`. An unpriced SKU sends the
visitor back to the page's `#not-yet` note; a priced one goes straight to
Stripe. The switch-on happened 2026-09-15 and is recorded here so the next
person can see what exists rather than re-mint it:

| Product (live mode) | Price | Env key on the relay |
|---|---|---|
| PipeOS Live Disk | $20, `price_1UG05IBLyiUFnqirog8UYWiG` | `STRIPE_DISK_PRICE_ID` |
| PipeOS Machine | $799, `price_1UG0CBBLyiUFnqir9DXiStA4` | `STRIPE_MACHINE_PRICE_ID` |
| PipeOS Cluster | $7,999, `price_1UG0D1BLyiUFnqirT9ncKTce` | `STRIPE_CLUSTER_PRICE_ID` |
| PipeOS on-site install | $500, `price_1UG0D2BLyiUFnqirw9fjbaJz` | `STRIPE_CLUSTER_INSTALL_PRICE_ID` |

All four are one-off, USD, tax **exclusive**; the three goods carry the
general tangible-goods tax code and are marked shippable, the install is a
service. The install is not a SKU of its own: it is a Checkout *optional
item* on the Machine and Cluster sessions (pipe#923), shown unticked on
Stripe's page. The page's amounts are the contract — a price change is a
new Price object and a new id in the console, never an edit in place.

**Shipping (pipe#923, Sam 2026-09-15): US only, flat per SKU** — $5 stick,
$25 machine, $250 cluster freight — written into each session inline as
`shipping_rate_data` with a delivery estimate (3–5 business days; 7–10 for
the cluster). No Shipping Rate object exists in the dashboard and no env
key carries it: the amounts sit in the relay's SKU table
(`crates/pipe-relay/src/web_stripe.rs`, `checkout_box_handler`) beside the
page's prices, and a change is a code change like a price on the page.
Canada left `allowed_countries` on the same day: customs paperwork and a
GST registration the store does not have. Add it back on the first ask.

How the values reached the relay: added to the **live** app spec on the
relay's service with `doctl apps update` (the live spec fetched first, the
four entries appended as `type: SECRET` with plaintext values, DO encrypts
on apply). `scripts/do-apply.sh prod` in the pipe repo then carries them
forever (its merge keeps every live SECRET); `scripts/check-spec-drift.py`
against a fresh `doctl apps spec get` must say "no spec drift".

Two things the dashboard still owns:

- **Tax registrations: none.** Stripe Tax is active with a Thousand Oaks
  head office, so checkout computes tax but collects $0 everywhere until a
  registration exists (Tax → Registrations). A California business shipping
  goods to California addresses wants the CA registration before real
  orders; a filing decision, not a build step.
- **Proving it without paying:** press Buy, reach Stripe's page, cancel — it
  returns to /hardware/. A test-mode purchase with card 4242… needs
  test-mode prices, which do not exist; the live cancel is the drill.

## Per order

1. **Pull the order** from Stripe: name, shipping address, email, SKU
   (boot media only, or preloaded box).
2. **Flash** the current released image (never a local build for a
   customer):
   ```sh
   curl -fLO https://github.com/securedataresearch/pipeOS/releases/latest/download/pipeos-usb.img.xz
   curl -fLO https://github.com/securedataresearch/pipeOS/releases/latest/download/pipeos-usb.img.xz.sha256
   sha256sum -c pipeos-usb.img.xz.sha256
   xz -dc pipeos-usb.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync
   ```
   (`make flash DEV=/dev/sdX` from this repo adds the whole-disk, unmounted
   and not-the-host-root guards; [live-disk](live-disk.md) has the three
   ways an image lands.) For a stick SKU, registering the serial in
   `fleet/serials.txt` is NOT required — that ledger is for the internal
   fleet. Note the order id on the stick's bag instead.
3. **Burn-in (preloaded box only)**: install media, boot once on the bench,
   wait ~3 minutes, confirm on the bench network:
   - `http://pipeos.local/` answers and reads **unclaimed**;
   - the wizard's boot report (visible after claiming — use a bench claim
     ONLY on a throwaway boot, see below) — simpler: `curl
     http://pipeos.local/api/state` returns `"claimed": false`.
   - **DO NOT claim the box.** The customer's first visit is the claim;
     a box that arrives claimed is a box that arrives owned by us.
   - If you claimed it to debug: reflash before shipping. A reflash is the
     only clean unclaim ([live-disk](live-disk.md): the generic image is
     nobody's; identity enters at the customer's first visit).
4. **Pack**: box + power lead + the one-page
   [client-onboarding](client-onboarding.md) sheet (printed).
5. **Mark fulfilled** in Stripe with the tracking number; email goes from
   there. The order's shipping line is what the buyer paid for postage
   (\$5 / \$25 / \$250) — buy the label to match, not above it.

## Batch prep (ahead of orders)

Flash and burn-in a small stock of sticks/boxes after each release; label
with the release tag. Re-flash stock older than two releases — customers
should never unbox an image the update pill immediately flags.

Our own Machines (the operator's cluster) are flashed from a **local** build
of the released commit with the workstation key baked in
(`AUTH_KEYS=~/.ssh/id_ed25519.pub make usb`, then `scripts/70-flash.sh
--image out/pipeos-usb.img /dev/sdX`). That image is never published: a
release asset with an operator key in it is an operator key on every
customer box. `make release` enforces it: it decompresses the `.xz` it is
about to attach and `scripts/verify-image-generic.sh` refuses an ssh key, a
root password or a box card in its apkovl (the build prints `kind=operator`
and a banner for such an image). Compress with `xz -T0 -k
out/pipeos-usb.img` — `-k` keeps the `.img` a flash still needs. Reused sticks get
`wipefs -a` first — a leftover `PIPEWORK` label stops `grow.sh` from
carving `/work`.

## Pilot delivery

A pilot is an order we walk through the door with. Per pilot:

1. **Stick**: from the released image, labelled with the release tag
   (`gh release view --json tagName`), burned in per step 3 — never
   claimed. `curl http://pipeos.local/api/state` → `"claimed": false`
   before it goes in the bag.
2. **The sheet**: [client-onboarding](client-onboarding.md), printed.
3. **On site, the client's hands, not ours**: claim, name, services, the
   Claude sign-in. We watch and fill in
   [first-boot-acceptance](first-boot-acceptance.md); we do not touch the
   keyboard.
4. **The reboot drill**, from the sheet: Reboot the box → back with state.
5. **The support door, both ways**: client flips **Vendor support access**
   on; the Services view shows the box's key and "waiting for a port";
   the client sends us the key (any channel — it is public); we pin it on
   the relay to the next free port (`docs/support-relay.md`, the ledger in
   `authorized_keys`) and tell them the number; `SUPPORT_PORT=` goes in
   `/etc/pipeos/support.conf` on the box and the pill goes "tunnel up"; we
   prove it with `ssh -J`; the client flips it **off** and we show the
   tunnel is gone. Then it stays off unless they need us.
6. **Sign-off**: the filled acceptance sheet in `docs/acceptance/`, one
   per pilot, with the release tag and the port assigned.

The two pilot boxes get cards (`docs/cards/pilot0.card`, `pilot1.card`)
only for our records; nothing on a card reaches a customer box — identity
enters at the claim, on their premises.
