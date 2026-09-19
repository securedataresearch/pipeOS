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
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mdnsd  # noqa: E402  — the roster of Machines this box has seen

NETGAZE = os.environ.get("PIPEOS_NETGAZE", "netgaze")
CACHE = os.environ.get("PIPEOS_LAN_CACHE", "/run/pipeos/lan.json")
TTL = int(os.environ.get("PIPEOS_LAN_TTL", "60"))
PASS_TIMEOUT = 45          # the sweep is ~6 s on a quiet /24; a gateway that answers PTR slowly stretches it


def collect():
    """One netgaze pass → ({ip: row}, error). A row: mac, reachable, rtt_ms,
    host — whatever the pass learned; absent fields are simply absent."""
    try:
        p = subprocess.run([NETGAZE, "probe", "lan", "--json"], capture_output=True, text=True, timeout=PASS_TIMEOUT)
    except FileNotFoundError:
        return {}, "netgaze is not installed on this image (it ships with the release that carries the network map)"
    except subprocess.TimeoutExpired:
        return {}, "netgaze did not finish in %ds" % PASS_TIMEOUT
    if p.returncode != 0:
        return {}, "netgaze: %s" % (p.stderr.strip().splitlines() or ["exit %d" % p.returncode])[-1]
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
        elif k == "mdns":
            r.setdefault("host", o.get("host") or "")
            if o.get("model"):
                r["model"] = o["model"]
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
    """Say what each row is. Machines come from the mDNS roster (every
    Machine this box has ever seen, with its last address and MAC), members
    from the cluster list; a rostered Machine the sweep did not reach still
    gets a row (awake False) — the map should show the box that is off."""
    mine = set(_self_ips())
    members = _members()
    by_ip = {}
    for pid, m in mdnsd.read_roster().items():
        if m.get("ip"):
            by_ip[m["ip"]] = dict(m, id=pid)
    for ip, r in rows.items():
        m = by_ip.get(ip)
        if ip in mine:
            r["kind"], r["name"] = "self", (m or {}).get("name", "")
            r["id"] = (m or {}).get("id", "")
        elif ip in members:
            r["kind"], r["id"], r["name"] = "member", members[ip][0], members[ip][1]
        elif m:
            r["kind"], r["id"], r["name"] = "machine", m["id"], m.get("name", "")
        else:
            r["kind"] = "other"
        r["awake"] = True
    for ip, m in by_ip.items():
        if ip not in rows:
            rows[ip] = {"ip": ip, "mac": m.get("mac", ""), "id": m["id"], "name": m.get("name", ""), "awake": False,
                        "kind": "member" if ip in members else "machine", "last_seen": m.get("last_seen", 0)}
    out = sorted(rows.values(), key=lambda r: tuple(int(x) for x in r["ip"].split(".")) if r["ip"].count(".") == 3 else (999,))
    return out


def page(refresh=False):
    """The cached map, or a fresh pass when the cache is older than TTL or
    the caller asked. {at, devices, error, source}."""
    if not refresh:
        try:
            with open(CACHE) as f:
                d = json.load(f)
            if time.time() - float(d.get("at", 0)) < TTL:
                return d
        except (OSError, ValueError):
            pass
    rows, err = collect()
    d = {"at": int(time.time()), "devices": mark(rows), "error": err, "source": "netgaze" if not err else "none"}
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, CACHE)
    except OSError:
        pass
    return d


def _table(d):
    if d.get("error"):
        print("network map: %s" % d["error"])
    kinds = {"self": "this Machine", "member": "member", "machine": "a Machine", "other": ""}
    print("%-16s %-18s %-22s %-9s %s" % ("address", "mac", "name / host", "rtt", "what"))
    for r in d.get("devices", []):
        rtt = ("%.1f ms" % r["rtt_ms"]) if r.get("rtt_ms") is not None else ("off" if r.get("awake") is False else "-")
        who = r.get("name") or r.get("host") or ""
        print("%-16s %-18s %-22s %-9s %s" % (r["ip"], r.get("mac", "") or "-", who[:22], rtt, kinds.get(r.get("kind", "other"), "")))
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
