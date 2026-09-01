# What persists

The root filesystem is RAM, rebuilt from the boot media every boot. That is
the appliance's whole trick: a clean boot is always one reboot away, and
nothing can rot in place.

Three things survive a reboot:

- **Saved state** — the box's configuration (`/etc`) and its identities
  (pipe, SSH, agent credentials). Saved automatically every 15 minutes and
  at shutdown; **Save state now** in the dashboard does it on demand.
- **`/work`** — an ordinary disk partition: repos, agent memory, logs,
  uploads, user homes. It is real storage, it is finite, and it fills; the
  Overview disk tile is watching it.
- **Installed packages** — software added with `pipeos pkg add` is fetched
  into the local mirror on the media, so the next boot has it without a
  network. A plain `apk add` on the console is gone at reboot — that is by
  design.

Anything else written outside `/work` — a file in `/tmp`, a hand-edited
system file that is not part of saved state — evaporates at reboot. If a
change must stick, it either belongs in `/work` or it goes through the
dashboard (which saves what it changes).
