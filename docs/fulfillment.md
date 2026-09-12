# Fulfillment runbook — from order to shipped box

Scope: turning a paid order into a box in the mail. The order book is the
Stripe dashboard; nothing here depends on any other order system.

## Turning the store on (once)

The Buy buttons on pipe.online/hardware post to the relay's
`/stripe/checkout-box?sku=disk|machine`. The endpoint has shipped since the
pricing pivot; what was missing on 2026-09-09 was the two prices — every
Buy answered a bare 504 because the platform in front of the relay
rewrites the relay's "not for sale yet" 503. Now an unpriced SKU sends the
visitor back to the page's note; to sell:

1. Mint the products and prices, in the Stripe account the relay bills to
   (`stripe login` first; live mode):
   ```sh
   stripe products create --name "PipeOS Live Disk" --description "pipeOS on a stick, in a ring box"
   stripe prices create --product prod_… --unit-amount 2000 --currency usd --tax-behavior exclusive
   stripe products create --name "PipeOS Machine" --description "a 1-liter box with pipeOS ready, unclaimed"
   stripe prices create --product prod_… --unit-amount 79900 --currency usd --tax-behavior exclusive
   ```
   The unit amounts are the page's ($20, $799); the page is the contract.
   Automatic tax needs a registration in the Stripe dashboard (Tax →
   Registrations) or checkout fails at the tax step.
2. Paste the two `price_…` ids into the relay's console env as
   `STRIPE_DISK_PRICE_ID` and `STRIPE_MACHINE_PRICE_ID` (SECRET), then
   `scripts/do-apply.sh prod` in the pipe repo — the spec declares both keys
   and the apply carries the values through.
3. Prove it without paying: press Buy, reach Stripe's page, cancel — it
   returns to pricing. A test-mode purchase with card 4242… lands in the
   dashboard's order book with shipping, phone and the `pipe-live-disk` /
   `pipe-machine` label.

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
   there.

## Batch prep (ahead of orders)

Flash and burn-in a small stock of sticks/boxes after each release; label
with the release tag. Re-flash stock older than two releases — customers
should never unbox an image the update pill immediately flags.

Our own Machines (the operator's cluster) are flashed from a **local** build
of the released commit with the workstation key baked in
(`AUTH_KEYS=~/.ssh/id_ed25519.pub make usb`, then `scripts/70-flash.sh
--image out/pipeos-usb.img /dev/sdX`). That image is never published: a
release asset with an operator key in it is an operator key on every
customer box. `make release` enforces it: `scripts/verify-image-generic.sh`
reads the image's apkovl and refuses an ssh key or a box card (the build
prints `kind=operator` and a banner for such an image). Reused sticks get
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
