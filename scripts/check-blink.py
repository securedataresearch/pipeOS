#!/usr/bin/env python3
"""check-blink (pipeOS#333): a Machine playing its part in the cluster says so
on its network port LED, in a code that names which Machine it is.

The front power light on these chassis has no software interface (/sys/class/
leds is empty, no LED driver claims the front panel); the network port's LED
does, through ethtool's identify ioctl. So the daemon's whole job is deciding
WHETHER to blink and HOW MANY times, and calling ethtool.

Everything here runs against a stub ethtool that records its calls, a fake
cluster list, roster and health file, through the script's seams — a box
never sets them.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BLINK = os.path.join(REPO, "overlay/usr/local/bin/pipeos-blink")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", desc, "" if ok else "  <- " + detail))


D = tempfile.mkdtemp(prefix="blink-")
BIN = os.path.join(D, "bin"); os.makedirs(BIN)
CALLS = os.path.join(D, "calls")
with open(os.path.join(BIN, "ethtool"), "w") as f:
    f.write("#!/bin/sh\necho \"$*\" >> %s\nexit 0\n" % CALLS)
os.chmod(os.path.join(BIN, "ethtool"), 0o755)
CLUSTER = os.path.join(D, "cluster.json")
ROSTER = os.path.join(D, "roster.json")
HEALTH = os.path.join(D, "health.last")
CARD = os.path.join(D, "card.conf")


def members(*ids):
    json.dump({"v": 2, "id": "cl", "members": {i: {"ca": "x", "name": i} for i in ids}}, open(CLUSTER, "w"))


def roster(**seen):
    now = int(time.time())
    json.dump({"machines": {k: {"last_seen": now - v} for k, v in seen.items()}}, open(ROSTER, "w"))


SYS = os.path.join(D, "net"); os.makedirs(os.path.join(SYS, "lo"))
open(os.path.join(SYS, "lo", "operstate"), "w").write("up\n")


def run(*args, self_id="c2b0", card="", health="verdict: all green", iface="eth0", cycle="2"):
    open(CARD, "w").write(card)
    open(HEALTH, "w").write(health + "\n")
    try:
        os.unlink(CALLS)
    except OSError:
        pass
    env = dict(os.environ, PIPEOS_BLINK_ETHTOOL=os.path.join(BIN, "ethtool"), PIPEOS_BLINK_CLUSTER=CLUSTER,
               PIPEOS_BLINK_ROSTER=ROSTER, PIPEOS_BLINK_HEALTH=HEALTH, PIPEOS_BLINK_CARD=CARD,
               PIPEOS_BLINK_SELF=self_id, PIPEOS_BLINK_IFACE=iface, PIPEOS_BLINK_CYCLE=cycle, PIPEOS_BLINK_SYS=SYS,
               PIPEOS_BLINK_ONCE="1")
    p = subprocess.run(["sh", BLINK] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def calls():
    try:
        return open(CALLS).read().splitlines()
    except OSError:
        return []


# 1. the number is this Machine's place in the member list, sorted by id —
# every member computes the same list, so the numbers are distinct with nothing
# configured
members("a4e0", "c2b0", "c360"); roster(a4e0=5, c360=5)
rc1, out1 = run("status")
rc1b, out1b = run("status", self_id="a4e0")
rc1c, out1c = run("status", self_id="c360")
check("1 the code is this Machine's 1-based place in the member list sorted by id — a4e0 blinks once, c2b0 twice, c360 three times; no configuration and no collisions, because every member sorts the same list",
      rc1 == 0 and "blinking 2 on eth0" in out1 and rc1b == 0 and "blinking 1 on" in out1b and rc1c == 0 and "blinking 3 on" in out1c,
      "a4e0=%r c2b0=%r c360=%r" % (out1b[:60], out1[:60], out1c[:60]))

# 2. it actually blinks that many times, one identify per burst, on the up port
rc2, _ = run("run")
check("2 one cycle is N identify bursts on the cabled port and nothing else — the LED code IS the count",
      rc2 == 0 and calls() == ["-p eth0 1", "-p eth0 1"], "calls=%r" % calls())

# 3. every reason NOT to blink, each said in a sentence an owner can act on
rc3a, out3a = run("status", card="BLINK=off\n")
rc3b, out3b = run("status", health="CRITICAL: /work not mounted")
members("a4e0", "c2b0", "c360"); roster(a4e0=99999, c360=99999)
rc3c, out3c = run("status")
roster(a4e0=5, c360=5)
os.unlink(CLUSTER)
rc3d, out3d = run("status")
members("a4e0", "c2b0", "c360")
rc3e, out3e = run("status", iface="")
check("3 it goes steady for each reason, and says which: the card turned it off, its own health is CRITICAL, no other member has been heard from (it is cut off, or it is the one that is out), it is in no cluster, no port is up",
      rc3a == 1 and "card BLINK=off" in out3a and rc3b == 1 and "CRITICAL" in out3b
      and rc3c == 1 and "no other member has been heard from" in out3c
      and rc3d == 1 and "in no cluster" in out3d and rc3e == 1 and "no network port is up" in out3e,
      "off=%r crit=%r alone=%r nocl=%r noif=%r" % (out3a[:50], out3b[:50], out3c[:50], out3d[:50], out3e[:50]))

# 4. none of those runs touched the LED — a steady port is the whole signal
members("a4e0", "c2b0", "c360"); roster(a4e0=99999, c360=99999)
rc4, _ = run("run")
check("4 a Machine that is not playing its part calls ethtool not once — its port keeps its ordinary link light, and THAT is how the odd one out is spotted",
      rc4 == 0 and calls() == [], "calls=%r" % calls())

# 5. a cluster of one has no peers to miss
members("a4e0"); roster()
rc5, out5 = run("status", self_id="a4e0")
check("5 a cluster of one blinks on its own health — there is no peer to be cut off from",
      rc5 == 0 and "blinking 1 on" in out5, out5[:80])

# 6. the wiring: service, runlevel, lbu.list, card field, verb
init = open(os.path.join(REPO, "overlay/etc/init.d/pipeos-blink")).read()
build = open(os.path.join(REPO, "scripts/40-build-apkovl.sh")).read()
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read()
gen = open(os.path.join(REPO, "overlay/usr/local/bin/pipebox-card")).read()
card = open(os.path.join(REPO, "overlay/etc/pipeos/card.conf")).read()
front = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
default_line = next((l for l in build.splitlines() if l.startswith("mk_runlevel default")), "")
check("6 it is a service in the default runlevel after the network and mDNS, enrolled in lbu.list (a new init script vanishes at reboot without it); BLINK is a card field with an on|off enum; `pipeos blink` is dispatched and in the help",
      "command_background=yes" in init and "after networking pipeos-mdns" in init
      and "pipeos-blink" in default_line and "+etc/init.d/pipeos-blink" in lbu
      and "BLINK) ;;" in gen and "BLINK must be on or off" in gen and "\nBLINK=\n" in card
      and "blink)       shift; cmd_blink" in front and "pipeos blink on|off|status|test" in front, "")

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
