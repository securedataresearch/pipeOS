# The dashboard

Everything the box can do is on this page, one view per item in the side
panel (bottom tab bar on a phone).

## Overview

The cockpit: last boot verdict, uptime, disk, CPU load and temperature, plus
an alerts strip when something needs attention. **re-check now** runs a
read-only health check on demand. The full boot report is under Services.

## Streaming

Configure the video pipeline and its providers. See the Streaming page in
these docs for the details and the settled bitrates.

## Assistant

Pick the assistant backend the box runs (Claude or Hermes),
chat with the box, and manage the browser terminal.

## Files

A file explorer over `/work` and any mounted external drives: upload
(drag & drop works), download files or whole folders as `.tar.gz`, move,
rename, mkdir, delete. A chat pane rides along when Claude is enabled.

## Services

One switch per service. Switches write the persistent config and start or
stop the service immediately; what is on here is what starts at boot.

## Users

Admin only. Add dashboard accounts with the **admin** or **viewer** role,
reset passwords, disable or delete accounts. Viewers see everything and can
change nothing — the server enforces it, the grey controls are just the
honest signal.

## Secrets

Everything the Machine keeps to itself, in one sealed file: listed by
name, never by value. Add a secret a job needs; reveal one after re-typing
your password; delete one. If the stick booted in a different box the page
says *locked* and asks for the recovery phrase. The manual's Secrets page
has the whole story.

## Network and System

**Machines on this network** lists every Machine that answers on the LAN,
with a **Claim** link on the unclaimed ones. A Machine this box has seen
before but that is not answering now shows grey — *off · last seen …* —
with its last address and MAC and a **Wake** button, which sends a
magic packet (wake-on-lan). The box is back in the list within a minute
if its BIOS allows waking; the manual's hardware page has the switch.

Live metrics: addresses, throughput, CPU/memory/disk history as 24-hour
charts. **Save state now** (System) writes the current config to the boot
media without waiting for the 15-minute autosave.
