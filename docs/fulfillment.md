# Fulfillment runbook — from order to shipped box

Scope: turning a paid order into a box in the mail. The order book is the
Stripe dashboard; nothing here depends on any other order system.

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
   For a stick SKU, register the stick serial in `fleet/serials.txt` is NOT
   required — that ledger is for the internal fleet. Note the order id on
   the stick's bag instead.
3. **Burn-in (preloaded box only)**: install media, boot once on the bench,
   wait ~3 minutes, confirm on the bench network:
   - `http://pipeos.local/` answers and reads **unclaimed**;
   - the wizard's boot report (visible after claiming — use a bench claim
     ONLY on a throwaway boot, see below) — simpler: `curl
     http://pipeos.local/api/state` returns `"claimed": false`.
   - **DO NOT claim the box.** The customer's first visit is the claim;
     a box that arrives claimed is a box that arrives owned by us.
   - If you claimed it to debug: reflash before shipping. A reflash is the
     only clean unclaim.
4. **Pack**: box + power lead + the one-page
   [client-onboarding](client-onboarding.md) sheet (printed).
5. **Mark fulfilled** in Stripe with the tracking number; email goes from
   there.

## Batch prep (ahead of orders)

Flash and burn-in a small stock of sticks/boxes after each release; label
with the release tag. Re-flash stock older than two releases — customers
should never unbox an image the update pill immediately flags.
