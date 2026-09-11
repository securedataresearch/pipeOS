# Hardware notes — the reference Machine

The reference Machine is a Lenovo ThinkCentre M920q (type 10RR): Intel
I219 gigabit on board, one NVMe or SATA disk for `/work`, the boot stick
on a USB port. This page is the firmware side of things the OS cannot do
from inside — the settings an operator makes once with a keyboard plugged
in, and how to tell from the box that they were made.

## Wake on LAN (#241)

A Machine that is off has no row in the live lobby, but its siblings
remember it: the **Wake** button on the Network view (and `pipeos wake
<name>` from any member) sends a magic packet to the MAC it last
advertised. For that packet to start the box, two switches have to be on:

1. **The NIC side** — the OS arms it at every boot (`pipeos-wol`, boot
   runlevel, `ethtool -s eth0 wol g`). The root filesystem is RAM, so this
   is a service, not a setting. `WOL=off` in the card disarms it.
2. **The BIOS side** — the firmware decides whether a packet may start
   the machine from soft-off (S5). The OS cannot flip this.

### M920q: turning it on in the BIOS

Power on with a keyboard attached, press **F1** at the Lenovo splash:

- **Power** → **Automatic Power On** → **Wake on LAN** → *Automatic*
  (or *Primary*: only the on-board NIC). *Disabled* is the factory value
  on some batches.
- **Power** → **After Power Loss** → *Power On*. Not the same setting,
  but the companion: a box that lost mains comes back on its own instead
  of waiting for a packet.
- Some firmware revisions also carry **Advanced** → **Power** → *Wake on
  LAN from S5*; it must not be *Disabled*.

**F10** saves. The box reboots.

### Checking from the box

```
ethtool eth0 | grep Wake-on
```

should read `Supports Wake-on: pumbg` (a `g` in there) and `Wake-on: g`.
The boot report carries the same check as a row: *wake-on-lan not armed
on eth0* names the NIC side (`rc-service pipeos-wol restart` re-arms it);
*cannot wake on a magic packet (driver)* means the hardware will never do
it. The BIOS side does not show in `ethtool` — the only test is the
drill below.

### Limits

- Wake works from **soft-off** (the box was shut down, standby light on).
  A box whose **mains was pulled** wakes only if *After Power Loss* is
  *Power On*; a magic packet alone does nothing to a cold chassis.
- The packet is a LAN broadcast (UDP 9) plus one unicast to the last
  address. Across a router it needs a directed-broadcast relay; the
  sibling Machines are on the same segment, which is the case this is
  built for.
- The sender is any Machine that has ever seen the target — the roster is
  per Machine, on its `/work`. A freshly flashed box has an empty roster
  until the others announce (about ten seconds).

### The drill (first-boot acceptance §5)

Shut a Machine down from the dashboard. On a sibling's Network view it
turns grey within about 40 seconds (*off · last seen …*). Click **Wake**.
It is back in the lobby within a minute. If it is not, the BIOS side is
off: the ethtool line above reads `g`, and the packet was sent (the note
under the list says so).
