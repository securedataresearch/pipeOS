#!/usr/bin/env python3
"""lan.py — the network map (pipeOS#217, docs/cluster.md §4).

One collector pass by netgaze (`netgaze probe lan --json`: an ICMP sweep of
the /24, the neighbour table for MACs, reverse DNS for what answered) merged
with what this Machine already knows — the mDNS roster of Machines and the
cluster's member list — so every row says what it is: this Machine, a
member, a Machine that is not a member, or something else on the wire.

The pass takes a few seconds and asks 254 addresses a question, so it is
cached (CACHE, TTL seconds) and every reader — the dashboard's Network view,
`pipeos lan` — shares one. A missing netgaze is a row on the page, not an
error: the map says it cannot see and why.

Seams (the probe's; a box never sets them): PIPEOS_NETGAZE, PIPEOS_LAN_CACHE,
PIPEOS_LAN_TTL, PIPEOS_MDNS_ROSTER (mdnsd's), PIPEOS_LAN_SELF_IPS.
"""
import errno
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mdnsd  # noqa: E402  — the roster of Machines this box has seen

NETGAZE = os.environ.get("PIPEOS_NETGAZE", "netgaze")
CACHE = os.environ.get("PIPEOS_LAN_CACHE", "/run/pipeos/lan.json")
TTL = int(os.environ.get("PIPEOS_LAN_TTL", "60"))
PASS_TIMEOUT = 45          # the sweep is ~6 s on a quiet /24; a gateway that answers PTR slowly stretches it
# The ICMP sweep uses SOCK_DGRAM ping sockets, which the kernel grants by gid
# (net.ipv4.ping_group_range — Alpine ships 999-59999, the `ping` group). webd
# and `pipeos lan` are root, which is OUTSIDE that range, so the collector
# runs in the ping group: Alpine's own hardening, no sysctl of ours.
PING_GROUP = os.environ.get("PIPEOS_LAN_GROUP", "ping")
LABEL = {"self": "this Machine", "member": "member", "machine": "a Machine", "other": ""}


def collect():
    """One netgaze pass → ({ip: row}, error). A row: mac, reachable, rtt_ms,
    host — whatever the pass learned; absent fields are simply absent."""
    kw = {}
    try:
        import grp
        grp.getgrnam(PING_GROUP)
        kw = {"group": PING_GROUP} if os.geteuid() == 0 else {}
    except (KeyError, ImportError):
        pass
    try:
        p = subprocess.run([NETGAZE, "probe", "lan", "--json"], capture_output=True, text=True, timeout=PASS_TIMEOUT, **kw)
    except subprocess.TimeoutExpired:
        return {}, "netgaze did not finish in %ds" % PASS_TIMEOUT
    except OSError as e:
        if e.errno == errno.ENOENT and not os.path.exists(NETGAZE):
            return {}, "netgaze is not installed on this image (it ships with the release that carries the network map)"
        return {}, "netgaze could not run: %s" % (e.strerror or e)
    if p.returncode != 0:
        last = (p.stderr.strip().splitlines() or ["exit %d" % p.returncode])[-1]
        if "ermission denied" in last:
            last += " (the ICMP sweep needs a ping socket: the collector runs in the %s group, net.ipv4.ping_group_range must include it)" % PING_GROUP
        return {}, "netgaze: %s" % last
    rows = {}
    for line in p.stdout.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        ip = o.get("ip")
        if not ip:
            continue
        r = rows.setdefault(ip, {"ip": ip})
        k = o.get("kind")
        if k == "ping":
            r["rtt_ms"] = round(float(o.get("rtt_ms") or 0), 2)
        elif k == "neigh":
            r["mac"] = o.get("mac", "")
            r["reachable"] = bool(o.get("reachable"))
        elif k == "ptr":
            r["host"] = o.get("host", "")
    return rows, ""


def _self_ips():
    env = os.environ.get("PIPEOS_LAN_SELF_IPS")
    if env is not None:
        return [x for x in env.split(",") if x]
    try:
        import lanid
        return list(lanid.local_ips())
    except Exception:
        return []


def _members():
    """{ip: (id, name)} of the cluster's members, {} outside a cluster."""
    try:
        import cluster
        return {r.get("ip"): (r["id"], r.get("name") or "") for r in cluster.view()["members"] if r.get("ip")}
    except Exception:
        return {}


def mark(rows):
    """Say what each row is, and label it for the page. Machines come from
    the mDNS roster (every Machine this box has ever seen, with its last
    address and MAC — never pruned), members from the cluster list. A live
    row is claimed as a rostered Machine only when the MACs agree (or one
    side has none): the roster's address is a LAST address, and a router
    re-leases it — a phone at one's old address is not one. A rostered
    Machine the sweep did not reach still gets a row (awake False): the map
    should show the box that is off."""
    mine = set(_self_ips())
    members = _members()
    by_ip = {}
    for pid, m in mdnsd.read_roster().items():
        if m.get("ip"):
            by_ip.setdefault(m["ip"], []).append(dict(m, id=pid))
    claimed = set()
    for ip, r in rows.items():
        cands = by_ip.get(ip, [])
        mac = (r.get("mac") or "").lower()
        m = next((c for c in cands if not mac or not c.get("mac") or c["mac"].lower() == mac), None)
        if ip in mine:
            r["kind"], r["name"], r["id"] = "self", (m or {}).get("name", ""), (m or {}).get("id", "")
        elif ip in members:
            r["kind"], r["id"], r["name"] = "member", members[ip][0], members[ip][1]
        elif m:
            r["kind"], r["id"], r["name"] = "machine", m["id"], m.get("name", "")
        else:
            r["kind"] = "other"
            if cands:
                r["note"] = "address last used by %s" % (cands[-1].get("name") or cands[-1]["id"])
        if m:
            claimed.add((ip, m["id"]))
        r["awake"] = True
    for ip, cands in by_ip.items():
        for m in cands:
            if (ip, m["id"]) in claimed or (ip in mine) or (ip in members and ip in rows):
                continue
            rows[ip + "/" + m["id"]] = {"ip": ip, "mac": m.get("mac", ""), "id": m["id"], "name": m.get("name", ""), "awake": False,
                                        "kind": "member" if ip in members else "machine", "last_seen": m.get("last_seen", 0)}
    for r in rows.values():
        r["label"] = LABEL.get(r.get("kind", "other"), "")
    return sorted(rows.values(), key=lambda r: (tuple(int(x) for x in r["ip"].split(".")) if r["ip"].count(".") == 3 else (999,), r.get("awake") is False))


def _read_cache():
    try:
        with open(CACHE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_cache(d):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".lan.", dir=os.path.dirname(CACHE))
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def page(refresh=False):
    """The cached map, or a fresh pass when the cache is older than TTL or
    the caller asked. One pass at a time (a lock file): the page polls, the
    CLI runs, several tabs open — every reader past the TTL shares the pass
    one of them is running, and a reader that finds the lock held gets the
    stale cache at once rather than a second sweep of the /24.
    {at, devices, error, source}."""
    d = _read_cache()
    if d and not refresh and time.time() - float(d.get("at", 0)) < TTL:
        return d
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        lock = open(CACHE + ".lock", "w")
    except OSError:
        lock = None
    if lock is not None:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if d and not refresh:
                return d                                   # someone is sweeping now; the stale page is the answer
            fcntl.flock(lock, fcntl.LOCK_EX)               # a refresh, or no cache at all: wait for that pass
            d2 = _read_cache()
            if d2 and (not refresh or float(d2.get("at", 0)) > float((d or {}).get("at", 0))):
                lock.close()
                return d2
    try:
        rows, err = collect()
        d = {"at": int(time.time()), "devices": mark(rows), "error": err, "source": "netgaze" if not err else "none"}
        _write_cache(d)
    finally:
        if lock is not None:
            lock.close()
    return d


def _table(d):
    if d.get("error"):
        print("network map: %s" % d["error"])
    print("%-16s %-18s %-22s %-9s %s" % ("address", "mac", "name / host", "rtt", "what"))
    for r in d.get("devices", []):
        rtt = ("%.1f ms" % r["rtt_ms"]) if r.get("rtt_ms") is not None else ("off" if r.get("awake") is False else "-")
        who = r.get("name") or r.get("host") or ""
        print("%-16s %-18s %-22s %-9s %s" % (r["ip"], r.get("mac", "") or "-", who[:22], rtt, (r.get("label", "") + (" — " + r["note"] if r.get("note") else "")).strip()))
    print("%d device(s) · %s" % (len(d.get("devices", [])), time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(d.get("at", 0)))))


def main(argv):
    refresh = "--refresh" in argv
    d = page(refresh=refresh)
    if "--json" in argv:
        print(json.dumps(d, indent=1))
    else:
        _table(d)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
