#!/usr/bin/env python3
"""Probe for the LAN lobby's discovery half: lanid.py's wire helpers and
mdnsd.py's advertise/browse/cache, run as two real instances on the
loopback multicast group on a private port (the LOOP seam). No root, no
real interface state touched; the only network is 224.0.0.251 looped back
on this host.

Rows: compressed names parse (and a forward/looping pointer is refused);
the four record types round-trip; the pre-claim name rule; two Machines
find each other with every field right and exclude themselves; the
one-shot A query; a rename keeps one row; goodbye and expiry; the cache is
written atomically. Controls: check-mdns-controls.py.
"""
import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
MDNSD = os.environ.get("CHECK_MDNS_BIN", os.path.join(WEB, "mdnsd.py"))
LANID = os.environ.get("CHECK_LANID", os.path.join(WEB, "lanid.py"))
PORT = 20000 + os.getpid() % 10000
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# lanid is imported by mdnsd via sys.path; put the (possibly control-copied)
# one first so both the in-process import and the child processes agree
LANDIR = tempfile.mkdtemp(prefix="ckmd-lan-")
shutil.copy(LANID, os.path.join(LANDIR, "lanid.py"))
shutil.copy(MDNSD, os.path.join(LANDIR, "mdnsd.py"))
sys.path.insert(0, LANDIR)
lanid = load("lanid", os.path.join(LANDIR, "lanid.py"))
os.environ["PIPEOS_MDNS_PORT"] = str(PORT)
os.environ["PIPEOS_MDNS_LOOP"] = "1"
os.environ["PIPEOS_MDNS_TTL"] = "0"
os.environ["PIPEOS_MDNS_INTERVAL"] = "1"
os.environ["PIPEOS_MDNS_IDENT"] = os.path.join(LANDIR, "idB.json")   # read at import
mdnsd = load("mdnsd", os.path.join(LANDIR, "mdnsd.py"))

# ── 1-2. compressed names ────────────────────────────────────────────────
pkt = lanid.build_query("studio.local", 1)
# a second question whose name is a pointer to the first (offset 12)
pkt = pkt[:4] + b"\x00\x02" + pkt[6:] + b"\xc0\x0c" + b"\x00\x01\x00\x01"
_f, qs, _r = lanid.parse_packet(pkt)
check("1 a compressed question name is followed back to its target",
      len(qs) == 2 and qs[1][0] == "studio.local", repr(qs))
bad_fwd = b"\xc0\x10"
bad_loop = b"\xc0\x00" + b"\x00" * 10
ok2 = True
for b in (bad_fwd, bad_loop + b"\xc0\x00"):
    try:
        lanid.read_name(b, 0)
        ok2 = False
    except ValueError:
        pass
check("2 a forward or looping pointer is refused, not followed", ok2, "")

# ── 3. the four record types round-trip ──────────────────────────────────
identB = {"hostname": "studio", "mac4": "9c21", "claimed": True, "verdict": "all green",
          "commit": "abc1234def01", "built": "2026-09-01T00:00:00Z", "model": "Test Box",
          "mac": "aa:bb:cc:dd:9c:21", "cl": "c1d2e3f4a5b6c7d8", "k": "0123456789abcdef", "h": "fedcba9876543210"}
json.dump(identB, open(os.path.join(LANDIR, "idB.json"), "w"))
iB = mdnsd.read_ident()
rrs = mdnsd.our_records(iB, "10.1.1.2")
_f, _q, recs = lanid.parse_packet(lanid.build_response(rrs))
by = {(r[0], r[1]): r for r in recs}
srv = by.get(("9c21._pipeos._tcp.local", 33))
txt = by.get(("9c21._pipeos._tcp.local", 16))
check("3 PTR, SRV, TXT and A round-trip through the parser with the right fields (the TXT carries the cluster id, key fingerprint and members hash — #211)",
      by.get(("_pipeos._tcp.local", 12), (0, 0, 0, ""))[3] == "9c21._pipeos._tcp.local"
      and srv and srv[3][2] == 80 and srv[3][3] == "studio.local"
      and txt and txt[3].get("id") == "9c21" and txt[3].get("n") == "studio" and txt[3].get("c") == "1"
      and txt[3].get("v") == "all green" and txt[3].get("m") == "Test Box" and txt[3].get("mac") == "aa:bb:cc:dd:9c:21"
      and txt[3].get("cl") == "c1d2e3f4a5b6c7d8" and txt[3].get("k") == "0123456789abcdef" and txt[3].get("h") == "fedcba9876543210"
      and by.get(("studio.local", 1), (0, 0, 0, ""))[3] == "10.1.1.2"
      and by[("_pipeos._tcp.local", 12)][2] == 120,
      repr(recs)[:400])

# ── 4. the pre-claim name rule ───────────────────────────────────────────
def names_for(hostname, claimed):
    json.dump({"hostname": hostname, "mac4": "7f3a", "claimed": claimed}, open(os.path.join(LANDIR, "idB.json"), "w"))
    return mdnsd.our_names(mdnsd.read_ident())
def names_named(name):
    json.dump({"hostname": "pipeos-7f3a", "mac4": "7f3a", "claimed": True, "name": name}, open(os.path.join(LANDIR, "idB.json"), "w"))
    return mdnsd.our_names(mdnsd.read_ident())
check("4 pipeos-<mac4>.local is answered always — unclaimed, claimed-but-unnamed, and named (the name is an alias beside it); pipeos.local always; a legacy name-as-hostname box keeps its name",
      names_for("pipeos", False) == {"pipeos.local", "pipeos-7f3a.local"}
      and names_for("pipeos", True) == {"pipeos.local", "pipeos-7f3a.local"}
      and names_named("") == {"pipeos.local", "pipeos-7f3a.local"}
      and names_named("miura") == {"pipeos.local", "pipeos-7f3a.local", "miura.local"}
      and names_for("studio", True) == {"pipeos.local", "pipeos-7f3a.local", "studio.local"},
      repr((names_for("pipeos", False), names_named("miura"), names_for("studio", True))))
json.dump({"hostname": "pipeos-7f3a", "mac4": "7f3a", "claimed": True, "name": "miura"}, open(os.path.join(LANDIR, "idB.json"), "w"))
_i = mdnsd.read_ident()
check("4b a named box advertises the name as n and serves the web at <name>.local",
      _i["nick"] == "miura" and _i["web_host"] == "miura.local" and mdnsd._txt(_i)["n"] == "miura", repr(_i))
json.dump(identB, open(os.path.join(LANDIR, "idB.json"), "w"))

# ── 5. TTL 0 parses as such ──────────────────────────────────────────────
_f, _q, recs0 = lanid.parse_packet(lanid.build_response(mdnsd.our_records(iB, "10.1.1.2", ttl=0)))
check("5 a goodbye packet parses with TTL 0 on every record", recs0 and all(r[2] == 0 for r in recs0), repr(recs0)[:200])

# ── 6-12. two instances on the loopback group ────────────────────────────
D = tempfile.mkdtemp(prefix="ckmd-")
identA = {"hostname": "pipeos", "mac4": "7f3a", "claimed": False, "verdict": "", "commit": "", "built": "", "model": ""}
json.dump(identA, open(D + "/idA.json", "w"))
json.dump(identB, open(D + "/idB.json", "w"))


def spawn(tag):
    env = dict(os.environ, PIPEOS_MDNS_IDENT=D + "/id%s.json" % tag, PIPEOS_MDNS_CACHE=D + "/cache%s.json" % tag,
               PIPEOS_MDNS_ROSTER=D + "/roster%s/machines.json" % tag)
    return subprocess.Popen([sys.executable, os.path.join(LANDIR, "mdnsd.py")], env=env,
                            stderr=open(D + "/log%s" % tag, "w"))


def cache(tag):
    try:
        return json.load(open(D + "/cache%s.json" % tag)).get("peers", {})
    except (OSError, ValueError):
        return {}


def roster(tag):
    try:
        return json.load(open(D + "/roster%s/machines.json" % tag)).get("machines", {})
    except (OSError, ValueError):
        return {}


def wait_for(cond, secs):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


pA = spawn("A")
time.sleep(0.3)
pB = spawn("B")
found = wait_for(lambda: "9c21" in cache("A") and "7f3a" in cache("B"), 4)
a, b = cache("A"), cache("B")
peerB = a.get("9c21", {})
peerA = b.get("7f3a", {})
check("6 two Machines find each other within two intervals, every field carried; the unnamed one is reachable as pipeos-7f3a.local",
      found and peerB.get("name") == "studio" and peerB.get("host") == "studio.local" and peerB.get("claimed") is True
      and peerB.get("verdict") == "all green" and peerB.get("commit") == "abc1234def01" and peerB.get("model") == "Test Box"
      and peerB.get("ip") == "127.0.0.1" and peerB.get("mac") == "aa:bb:cc:dd:9c:21"
      and peerA.get("name") == "" and peerA.get("host") == "pipeos-7f3a.local" and peerA.get("claimed") is False,
      "A=%r B=%r logA=%s" % (a, b, open(D + "/logA").read()[-300:]))
check("7 each Machine excludes itself from its own cache", "7f3a" not in a and "9c21" not in b, repr((sorted(a), sorted(b))))

# ── 8. the one-shot A query ──────────────────────────────────────────────
got = lanid.query_a("studio.local", 1.0, port=PORT, loop=True)
none = lanid.query_a("nobody.local", 0.7, port=PORT, loop=True)
check("8 the one-shot query hears studio.local and hears nothing for a name nobody owns",
      "127.0.0.1" in got and none == set(), repr((got, none)))

# ── 9. a rename keeps one row, keyed by id ───────────────────────────────
json.dump(dict(identB, hostname="attic"), open(D + "/idB.json", "w"))
renamed = wait_for(lambda: cache("A").get("9c21", {}).get("name") == "attic", 4)
a = cache("A")
check("9 a rename shows up on the sibling as the same row with the new name and host",
      renamed and a["9c21"]["host"] == "attic.local" and len(a) == 1, repr(a))

# ── 10. goodbye on SIGTERM ───────────────────────────────────────────────
pB.send_signal(signal.SIGTERM)
gone = wait_for(lambda: "9c21" not in cache("A"), 2)
pB.wait(timeout=5)
check("10 SIGTERM says goodbye and the sibling drops the row within a second", gone and pB.returncode == 0, "rc=%s a=%r" % (pB.returncode, cache("A")))

# ── 11. expiry after a silent death ──────────────────────────────────────
pB = spawn("B")
wait_for(lambda: "9c21" in cache("A"), 4)
pB.kill(); pB.wait()
t0 = time.time()
still = wait_for(lambda: "9c21" not in cache("A"), 1.5)   # must NOT be gone yet
gone = wait_for(lambda: "9c21" not in cache("A"), 6)
check("11 a Machine that vanishes without goodbye expires after three intervals, not before two",
      (not still) and gone and 2.0 <= time.time() - t0 <= 6.5, "still=%s gone=%s dt=%.1f" % (still, gone, time.time() - t0))

# ── 11b. the roster remembers what the cache forgot (#241) ───────────────
rB = roster("A").get("9c21", {})
check("11b the expired Machine is still on the roster with its MAC, name, last address and last_seen; the roster never lists this box",
      rB.get("mac") == "aa:bb:cc:dd:9c:21" and rB.get("name") == "attic" and rB.get("ip")
      and rB.get("last_seen", 0) >= int(t0) - 10 and "7f3a" not in roster("A")
      and not os.path.exists(D + "/rosterA/machines.json.new"),
      repr(roster("A")))

# ── 12. atomic, world-readable cache ─────────────────────────────────────
st = os.stat(D + "/cacheA.json")
check("12 the cache is written atomically (no .new left) and is 0644",
      not os.path.exists(D + "/cacheA.json.new") and stat.S_IMODE(st.st_mode) == 0o644, oct(st.st_mode))

pA.send_signal(signal.SIGTERM); pA.wait(timeout=5)
shutil.rmtree(D, ignore_errors=True); shutil.rmtree(LANDIR, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
