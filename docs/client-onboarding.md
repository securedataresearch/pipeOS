# Your PipeOS Machine — one page

*(Print or send this with every box.)*

## Setup (about three minutes)

1. **Plug in**: network cable to your router, power on.
2. **Wait one minute**, then on any phone or computer on the same network,
   open: **http://pipeos.local/**. More than one Machine? The page lists
   them; pick the one marked **unclaimed**.
3. **Claim it**: choose an admin password. Whoever sets it owns the box —
   do this before anything else. Keep the password safe; it is the only key.
4. **Name it** (optional): the name becomes its address, e.g.
   `http://studio.local/`.
5. **Pick what it does**: flip the switches. Claude assistant is on by
   default; streaming, pipe messaging, and vendor support access are off
   until you say otherwise.
6. **Connect Claude**: press **Sign in with Claude**. The Machine shows
   a link — open it on your phone or computer, sign in, and paste back the
   code it shows. Your account, your billing, your data. (Have an
   Anthropic API key instead? There is a field for that.)

Done. The same page is your dashboard from now on.

## Day to day

- **Talk to it**: the chat panel on the dashboard.
- **Check on it**: the dashboard shows health, disk, and every log.
- **Updates**: automatic, daily, verified — the dashboard shows the state.
- **Something's weird?** Dashboard → Maintenance → **Repair remote
  access**, and if that doesn't do it, **Reboot the box** — a reboot
  restores the last saved state and is safe to do any time.
- **Need help from us?** Flip **Vendor support access** ON and tell us.
  Flip it OFF when we're done — you're always in control of that door.

## The fine print that matters

Your box saves its state automatically every 15 minutes and at shutdown.
Your credentials live on the box and nowhere else. Nothing about the box
requires an account with us; if we vanished tomorrow, it keeps working.

The Machine's address is `https://<mac>.m.pipe.online/` (the wizard shows it; the box's boot report repeats it). It works on every device with nothing installed; `http://pipeos.local/` gets you there too.
