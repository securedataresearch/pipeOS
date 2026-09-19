#!/usr/bin/env python3
"""check-lan (pipeOS#217): the network map — lan.py runs `netgaze probe lan
--json`, parses one JSON object per line, caches the pass for TTL seconds,
and marks every row from what the box already knows (the mDNS roster, the
cluster list, its own addresses). A stub netgaze answers a fixed pass and
counts its calls; a roster file names one Machine that answered and one
that did not.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
LAN = os.path.join(WEB, "lan.py")
PIPEOS = os.path.join(REPO, "overlay/usr/local/bin/pipeos")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", desc, "" if ok else "  <- " + detail))


D = tempfile.mkdtemp(prefix="lan-")
BIN = os.path.join(D, "bin"); os.makedirs(BIN)
CALLS = os.path.join(D, "calls")
PASS = [
    {"ip": "192.168.9.1", "kind": "ping", "rtt_ms": 1.5}, {"ip": "192.168.9.1", "kind": "neigh", "mac": "54:47:cc:00:00:01", "reachable": True},
    {"ip": "192.168.9.1", "kind": "ptr", "host": "router.lan"},
    {"ip": "192.168.9.77", "kind": "ping", "rtt_ms": 2.2}, {"ip": "192.168.9.77", "kind": "neigh", "mac": "24:8a:07:ba:a4:e0", "reachable": False},
    {"ip": "192.168.9.78", "kind": "ping", "rtt_ms": 2.3}, {"ip": "192.168.9.78", "kind": "neigh", "mac": "24:8a:07:ba:c2:b0", "reachable": False},
    {"ip": "192.168.9.20", "kind": "ping", "rtt_ms": 11.7}, {"ip": "192.168.9.20", "kind": "neigh", "mac": "8c:49:62:13:62:2e", "reachable": True},
    {"ip": "192.168.9.20", "kind": "mdns", "host": "laptop.local", "service": "_ssh._tcp", "model": "MacBookPro"},
    "not json",
]
with open(os.path.join(BIN, "netgaze"), "w") as f:
    f.write("#!/bin/sh\necho \"$*\" >> %s\n[ \"$1 $2 $3\" = 'probe lan --json' ] || { echo 'stub: unexpected args' >&2; exit 2; }\ncat <<'EOJ'\n%s\nEOJ\n"
            % (CALLS, "\n".join(json.dumps(x) if not isinstance(x, str) else x for x in PASS)))
os.chmod(os.path.join(BIN, "netgaze"), 0o755)
ROSTER = os.path.join(D, "machines.json")
json.dump({"machines": {"pipeos-a4e0": {"name": "solo", "ip": "192.168.9.77", "mac": "24:8a:07:ba:a4:e0", "claimed": True, "last_seen": 1700000000},
                        "pipeos-c2b0": {"name": "two", "ip": "192.168.9.78", "mac": "24:8a:07:ba:c2:b0", "claimed": True, "last_seen": 1700000000},
                        "pipeos-b010": {"name": "one", "ip": "192.168.9.79", "mac": "24:8a:07:ba:b0:10", "claimed": True, "last_seen": 1600000000}}}, open(ROSTER, "w"))
CACHE = os.path.join(D, "run", "lan.json")
ENV = dict(os.environ, PIPEOS_NETGAZE=os.path.join(BIN, "netgaze"), PIPEOS_LAN_CACHE=CACHE, PIPEOS_LAN_TTL="60",
           PIPEOS_MDNS_ROSTER=ROSTER, PIPEOS_LAN_SELF_IPS="192.168.9.77", PIPEOS_LAN=LAN)


def run(*args, env=None):
    p = subprocess.run([sys.executable, LAN] + list(args), capture_output=True, text=True, env=env or ENV)
    return p.returncode, p.stdout + p.stderr


def calls():
    try:
        return open(CALLS).read().splitlines()
    except OSError:
        return []


rc1, out1 = run("--json")
d1 = json.loads(out1) if rc1 == 0 else {}
by = {r["ip"]: r for r in d1.get("devices", [])}
check("1 one pass is parsed into one row per address — rtt from ping, mac + reachable from neigh, host from ptr, model from mdns; a non-JSON line is skipped; rows sorted by address",
      rc1 == 0 and d1.get("source") == "netgaze" and not d1.get("error") and [r["ip"] for r in d1["devices"]][:2] == ["192.168.9.1", "192.168.9.20"]
      and by["192.168.9.1"]["rtt_ms"] == 1.5 and by["192.168.9.1"]["mac"] == "54:47:cc:00:00:01" and by["192.168.9.1"]["host"] == "router.lan" and by["192.168.9.1"]["reachable"] is True
      and by["192.168.9.20"]["model"] == "MacBookPro" and by["192.168.9.20"]["host"] == "laptop.local", "rc=%s out=%r" % (rc1, out1[:400]))
check("2 every row is marked from what the box knows: its own address is 'self' (with its name from the roster), a rostered Machine is 'machine' with its id and name, the router is 'other'; a rostered Machine the sweep did not reach is a row too, awake False, with its last mac",
      by["192.168.9.77"]["kind"] == "self" and by["192.168.9.77"]["name"] == "solo" and by["192.168.9.78"]["kind"] == "machine" and by["192.168.9.78"]["id"] == "pipeos-c2b0"
      and by["192.168.9.1"]["kind"] == "other" and by.get("192.168.9.79", {}).get("awake") is False and by["192.168.9.79"]["mac"] == "24:8a:07:ba:b0:10" and by["192.168.9.79"]["name"] == "one",
      repr({k: (v.get("kind"), v.get("name"), v.get("awake")) for k, v in by.items()}))
n_before = len(calls())
rc2, out2 = run("--json")
d2 = json.loads(out2)
n_after_cached = len(calls())
rc3, out3 = run("--json", "--refresh")
check("3 the pass is cached: a second read within the TTL runs no netgaze and answers the same 'at'; --refresh runs it now and the cache file is replaced atomically",
      n_before == 1 and rc2 == 0 and n_after_cached == 1 and d2["at"] == d1["at"] and rc3 == 0 and len(calls()) == 2 and os.path.isfile(CACHE) and not os.path.exists(CACHE + ".tmp"),
      "before=%d cached=%d after_refresh=%d" % (n_before, n_after_cached, len(calls())))
rc4, out4 = run()
check("4 the CLI prints a table — address, mac, name/host, rtt, what — and a count with the time; the marks read 'this Machine' / 'a Machine'",
      rc4 == 0 and "192.168.9.1" in out4 and "router.lan" in out4 and "this Machine" in out4 and "a Machine" in out4 and "4 device(s)" not in out4 and "5 device(s)" in out4, out4[-400:])
rc5, out5 = run("--json", env=dict(ENV, PIPEOS_NETGAZE=os.path.join(BIN, "no-such-netgaze"), PIPEOS_LAN_CACHE=os.path.join(D, "run2", "lan.json")))
d5 = json.loads(out5) if rc5 == 0 else {}
check("5 a missing netgaze is a sentence on the page, not an error: rc 0, source none, the rostered Machines still listed (from the roster), error names the release that carries it",
      rc5 == 0 and d5.get("source") == "none" and "not installed" in d5.get("error", "") and {r["ip"] for r in d5["devices"]} == {"192.168.9.77", "192.168.9.78", "192.168.9.79"}, out5[:300])
p = subprocess.run(["sh", PIPEOS, "lan"], capture_output=True, text=True, env=dict(ENV, PIPEOS_LAN_CACHE=CACHE))
check("6 `pipeos lan` is the same table through the verb (PIPEOS_LAN seam); help and fleet-ops name it",
      p.returncode == 0 and "router.lan" in p.stdout and "pipeos lan" in open(PIPEOS).read().split("case")[0] and "`pipeos lan" in open(os.path.join(REPO, "docs/fleet-ops.md")).read(),
      "rc=%s out=%r" % (p.returncode, (p.stdout + p.stderr)[-300:]))
webd = open(os.path.join(WEB, "webd.py")).read()
check("7 webd serves GET /api/lan to a signed-in reader (a reader in the table, not in PEER_GETS: a member's certificate may not read another Machine's map), ?refresh=1 runs the pass; the Network view has the card",
      '"/api/lan": self.api_lan' in webd and '"/api/lan"' not in webd.split("PEER_GETS = ")[1].split("\n")[0] and "lan.page(refresh=" in webd
      and 'id="lanrows"' in open(os.path.join(WEB, "static/app.js")).read(), "")
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
