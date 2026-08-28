# fleet/ — sanctioned operator tooling

Scripts for operating the box fleet, promoted here from one-off session scripts
so there is ONE path per operation, in the repo, not improvised each time.

| Script | Runs on | Does |
|---|---|---|
| `flash-box.sh <box> <dev> <img>` | operator host | Writes an image to a box's USB stick, **guarded by serial** (`serials.txt`) so it can't hit the wrong stick or a system disk. |
| `update-box.sh` | a box | Applies the locally-built pipeos repo to boot media (Path B): `pipeos sync-media` → `apk upgrade` → `pipeos save` → verify. |
| `serials.txt` | — | box → USB-stick serial map; the flash guard. Update when a stick is replaced. |

Related, already in the tree:
- `overlay/usr/local/bin/pipeos-selfupdate` — a box updates itself from a remote
  publisher (`UPDATE_URL`). The publisher is the build box serving
  `out/repo/pipeos` over HTTP (`pipeos-repo-httpd`).
- `overlay/usr/local/bin/pipebox-card` — generate a box's identity from its card
  (`docs/cards/<box>.card`); the durable provisioning path (#650).
- `docs/fleet-update-runbook.md`, `docs/self-update.md`, `docs/model-cards.md`.

Provisioning a box, end to end: build image (`make image VARIANT=usb` — NOTE:
the image ships the *unprovisioned default* card from
`overlay/etc/pipeos/card.conf`; nothing bakes a `docs/cards/*.card` in yet, so
the box's card must be installed by hand or via
`pipeos deploy-overlay --install-card` after boot) → `flash-box.sh` → boot →
`pipebox-setup` (card-generate + sign-in + save). Bugs that used to break this — clean-checkout
build (#25) and the provisioned marker (#27) — are fixed.
