#!/usr/bin/env python3
"""Probe for wake-on-lan (#241): pipeos-wake (the magic packet, name
resolution, the refusals), pipeos-wol (arming the right NIC from the card),
and the wiring around them (the fence, the front door, persistence, the
runlevel). No root, no real NIC touched: the packet goes to a loopback UDP
listener through the PIPEOS_WAKE_TARGET seam, ethtool is a stub that logs
its argv, /sys/class/net is a fixture directory.

Exit 0 if every row passes. Controls: check-wake-controls.py.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
WAKE = os.environ.get("CHECK_WAKE_BIN", os.path.join(WEB, "wake.py"))
WOL = os.environ.get("CHECK_WOL_BIN", os.path.join(REPO, "overlay/etc/init.d/pipeos-wol"))
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="ckwake-")
# wake.py imports lanid from its own dir; a control copy must sit beside it
shutil.copy(os.path.join(WEB, "lanid.py"), os.path.join(D, "lanid.py"))
shutil.copy(WAKE, os.path.join(D, "wake.py"))
ROSTER = os.path.join(D, "machines.json")

lis = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
lis.bind(("127.0.0.1", 0))
lis.settimeout(2)
TARGET = "127.0.0.1:%d" % lis.getsockname()[1]


def drain():
    out = []
    lis.settimeout(0.3)
    try:
        while True:
            out.append(lis.recv(4096))
    except socket.timeout:
        pass
    return out


def wake(*args, self_id="7f3a", roster=ROSTER):
    env = dict(os.environ, PIPEOS_WAKE_ROSTER=roster, PIPEOS_WAKE_TARGET=TARGET, PIPEOS_WAKE_SELF=self_id)
    p = subprocess.run([sys.executable, os.path.join(D, "wake.py")] + list(args),
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def seed(rows):
    with open(ROSTER, "w") as f:
        json.dump({"v": 1, "self": "7f3a", "written": 1, "machines": {r["id"]: r for r in rows}}, f)


ROWS = [
    {"id": "9c21", "name": "studio", "host": "studio.local", "ip": "10.0.0.2", "claimed": True,
     "mac": "aa:bb:cc:dd:9c:21", "model": "Test Box", "last_seen": 1700000000},
    {"id": "4d4d", "name": "", "host": "pipeos-4d4d.local", "ip": "10.0.0.4", "claimed": False,
     "mac": "AA:BB:CC:DD:4D:4D", "model": "", "last_seen": 1700000000},
    {"id": "0abc", "name": "legacy", "host": "legacy.local", "ip": "10.0.0.5", "claimed": True,
     "mac": "", "model": "", "last_seen": 1700000000},
    {"id": "7f3a", "name": "", "host": "pipeos-7f3a.local", "ip": "127.0.0.1", "claimed": True,
     "mac": "aa:bb:cc:dd:7f:3a", "model": "", "last_seen": 1700000000},
]
seed(ROWS)

# ── 1. the packet ───────────────────────────────────────────────────────
drain()
rc, out = wake("studio")
pk = drain()
want = b"\xff" * 6 + bytes.fromhex("aabbccdd9c21") * 16
check("1 `pipeos wake studio` sends the magic packet — six 0xff then the MAC sixteen times — more than once, and says so",
      rc == 0 and len(pk) >= 2 and all(p == want for p in pk) and "magic packet sent to studio" in out and "aa:bb:cc:dd:9c:21" in out,
      "rc=%s n=%d out=%s" % (rc, len(pk), out[-200:]))

# ── 2. every spelling of a Machine resolves to the same packet ──────────
same = True
for what in ("STUDIO", "studio.local", "9c21", "pipeos-9c21", "pipeos-9c21.local"):
    drain()
    rc, out = wake(what)
    pk = drain()
    if rc != 0 or not pk or pk[0] != want:
        same = False
        break
drain()
rc4, out4 = wake("pipeos-4d4d")
pk4 = drain()
check("2 name, host, bare id and pipeos-<id> all resolve to the same MAC (any case); an upper-case MAC on record still makes a packet",
      same and rc4 == 0 and pk4 and pk4[0] == b"\xff" * 6 + bytes.fromhex("aabbccdd4d4d") * 16, "same=%s rc4=%s %s" % (same, rc4, out4))

# ── 3-5. the refusals ────────────────────────────────────────────────────
drain()
rc, out = wake("nobody")
check("3 an unknown name is refused with rc 2 and names --list; nothing is sent", rc == 2 and "ever seen" in out and "--list" in out and not drain(), "rc=%s %s" % (rc, out))
rc, out = wake("pipeos-7f3a")
check("4 this Machine is refused (rc 2): it is awake", rc == 2 and "this Machine" in out and not drain(), "rc=%s %s" % (rc, out))
rc, out = wake("legacy")
check("5 a Machine that never advertised a MAC is refused (rc 1) with the reason; nothing is sent", rc == 1 and "no MAC on record" in out and not drain(), "rc=%s %s" % (rc, out))

# ── 6. --all skips self and the MAC-less one ────────────────────────────
drain()
rc, out = wake("--all")
pk = drain()
macs = {p[6:12].hex() for p in pk}
check("6 --all sends to every rostered Machine but this one and the MAC-less one, and counts them",
      rc == 0 and macs == {"aabbccdd9c21", "aabbccdd4d4d"} and "2 Machine(s)" in out, "rc=%s macs=%r %s" % (rc, macs, out))

# ── 7. --list, --forget, no roster ──────────────────────────────────────
rc, out = wake("--list")
lst_ok = rc == 0 and "studio" in out and "aa:bb:cc:dd:9c:21" in out and "(this one)" in out
rc, out = wake("--forget", "legacy")
after = json.load(open(ROSTER))["machines"]
rc2, out2 = wake("legacy")
check("7 --list prints the roster and marks this box; --forget drops a row atomically and the name is unknown afterwards",
      lst_ok and rc == 0 and "0abc" not in after and "9c21" in after and not os.path.exists(ROSTER + ".new") and rc2 == 2,
      "list=%s forget rc=%s after=%r" % (lst_ok, rc, sorted(after)))
rc, out = wake("studio", roster=os.path.join(D, "nope.json"))
check("8 no roster at all is rc 1 and says discovery has not written one", rc == 1 and "no roster" in out, "rc=%s %s" % (rc, out))

# ── 9-10. pipeos-wol arms the primary NIC from the card ─────────────────
SYS = os.path.join(D, "sys")
for n, mac, state in (("eth0", "aa:bb:cc:dd:7f:3a", "down"), ("eth1", "aa:bb:cc:dd:00:01", "up"), ("lo", "00:00:00:00:00:00", "unknown"), ("wlan0", "00:00:00:00:00:00", "down")):
    os.makedirs(os.path.join(SYS, n))
    open(os.path.join(SYS, n, "address"), "w").write(mac + "\n")
    open(os.path.join(SYS, n, "operstate"), "w").write(state + "\n")
ETH = os.path.join(D, "ethtool")
with open(ETH, "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + D + "/ethtool.argv\n")
os.chmod(ETH, 0o755)
CARD = os.path.join(D, "card.conf")


def wol(wol_value):
    open(CARD, "w").write("NICK=\nNAME=\nWOL=%s\n" % wol_value)
    if os.path.exists(D + "/ethtool.argv"):
        os.unlink(D + "/ethtool.argv")
    # run the openrc script's start() under plain sh: stub the openrc verbs
    script = ("ebegin() { :; }; eend() { return $1; }; ewarn() { :; }\n"
              "PIPEOS_WOL_ETHTOOL=%s PIPEOS_WOL_CARD=%s PIPEOS_WOL_SYS=%s\n"
              "export PIPEOS_WOL_ETHTOOL PIPEOS_WOL_CARD PIPEOS_WOL_SYS\n"
              ". %s\nstart\n" % (ETH, CARD, SYS, WOL))
    # the shebang line is openrc's; the rest is POSIX sh
    body = "\n".join(l for l in open(WOL).read().splitlines() if not l.startswith("#!"))
    src = os.path.join(D, "wol.sh")
    open(src, "w").write(body)
    p = subprocess.run(["sh", "-c", script.replace(". %s" % WOL, ". %s" % src)], capture_output=True, text=True)
    argv = open(D + "/ethtool.argv").read().split("\n") if os.path.exists(D + "/ethtool.argv") else []
    return p.returncode, [a for a in argv if a]


rc, argv = wol("")
rc_on, argv_on = wol("on")
check("9 pipeos-wol arms `wol g` on the UP interface with a real MAC (not lo, not the down one) when the card says WOL=on or nothing",
      rc == 0 and argv == ["-s eth1 wol g"] and rc_on == 0 and argv_on == ["-s eth1 wol g"], "rc=%s argv=%r on=%r" % (rc, argv, argv_on))
rc, argv = wol("off")
rc_bad, argv_bad = wol("OFF")
check("10 WOL=off disarms (`wol d`) instead; the card read is case-insensitive",
      rc == 0 and argv == ["-s eth1 wol d"] and argv_bad == ["-s eth1 wol d"], "rc=%s argv=%r bad=%r" % (rc, argv, argv_bad))

# ── 11. the fence, the front door, persistence, the runlevel ────────────
front = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
settings = [open(os.path.join(REPO, p)).read() for p in
            ("overlay/etc/pipeos/pipebox-settings.json", "overlay/usr/local/share/pipeos/card/pipebox-settings.json.tmpl")]
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read()
build = open(os.path.join(REPO, "scripts/40-build-apkovl.sh")).read()
boot_line = next((l for l in build.splitlines() if l.startswith("mk_runlevel boot")), "")
check("11 both settings files deny the verb; `pipeos` dispatches and documents it; the arming service persists (lbu.list) and is in the boot runlevel after networking",
      all('"Bash(pipeos wake*)"' in s and '"Bash(pipeos-wake*)"' in s for s in settings)
      and "wake)        shift; exec /usr/local/bin/pipeos-wake" in front and "pipeos wake NAME" in front
      and "+etc/init.d/pipeos-wol" in lbu
      and "networking pipeos-wol" in boot_line,
      "boot=%r" % boot_line)

lis.close()
shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
