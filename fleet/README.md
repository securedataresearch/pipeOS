# fleet/ — sanctioned operator tooling

Scripts for operating the box fleet, promoted here from one-off session scripts
so there is ONE path per operation, in the repo, not improvised each time.

| Script | Runs on | Does |
|---|---|---|
| `flash-box.sh <box> <dev> <img>` | operator host | Compatibility wrapper over `scripts/70-flash.sh --box`, the ONE flasher: serial guard (`serials.txt`) **plus** mount/root-disk/whole-disk guards and typed confirmation. An unregistered stick is offered registration interactively instead of an unguarded fallback. |
| `update-box.sh` | a box | Applies the locally-built pipeos repo to boot media (Path B): `pipeos sync-media` → `apk upgrade` → `pipeos save` → verify. |
| `serials.txt` | — | box → USB-stick serial map; the flash guard. Update when a stick is replaced. |

Related, already in the tree:
- `overlay/usr/local/bin/pipeos-selfupdate` — a box updates itself from a remote
  publisher (`UPDATE_URL`). The publisher is the build box serving
  `out/repo/pipeos` over HTTP (`pipeos-repo-httpd`).
- `overlay/usr/local/bin/pipebox-card` — generate a box's identity from its card
  (`docs/cards/<box>.card`); the durable provisioning path (#650).
- `docs/fleet-update-runbook.md`, `docs/self-update.md`, `docs/model-cards.md`.

Provisioning a box, end to end: `make stick CARD=docs/cards/<box>.card`
(bakes the card into the apkovl — identity, derived files and the provisioned
marker ride the image, and the output is named `pipeos-usb-<box>.img` so it
cannot be mistaken for the generic one) → `flash-box.sh` → boot →
`pipebox-setup` (sign-in + owner + save). A plain `make image VARIANT=usb`
still builds the generic image with the unprovisioned default card. Bugs that used to break this — clean-checkout
build (#25) and the provisioned marker (#27) — are fixed.
