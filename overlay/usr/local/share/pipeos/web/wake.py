#!/usr/bin/env python3
"""pipeos-wake — a magic packet for a Machine this box has seen (#241).

    pipeos wake <name|id|host>     wake one Machine
    pipeos wake --all              wake every rostered Machine but this one
    pipeos wake --forget <id>      drop a Machine from the roster
    pipeos wake --list             the roster, one line each

The roster is what pipeos-mdnsd keeps on /work: every Machine that ever
answered on this LAN, with the MAC it advertised. A Machine that is off is
exactly the one not in the live cache, so the roster — not the cache — is
what this reads. Stdlib only: the packet is six 0xff bytes then the MAC
sixteen times, sent three times to the broadcast address on UDP 9 and once
more to the last address the Machine had, in case the switch is fussy
about broadcast.

Refuses an unknown name (rc 2), this box itself (rc 2), and a Machine that
never sent a MAC — an image before #241 — with a line that says so. rc 1
when there is no roster at all (discovery has not run on /work yet).

Seams (the probe, never production): PIPEOS_WAKE_ROSTER (the file),
PIPEOS_WAKE_TARGET (host:port — replaces the broadcast and the unicast so
a loopback listener receives what would have gone to the LAN),
PIPEOS_WAKE_SELF (this box's id).
"""

import json
import os
import re
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lanid  # noqa: E402

ROSTER = os.environ.get("PIPEOS_WAKE_ROSTER", "/work/pipeos/mdns/machines.json")
TARGET = os.environ.get("PIPEOS_WAKE_TARGET", "")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def roster():
    try:
        with open(ROSTER) as f:
            d = json.load(f)
        m = d.get("machines", {}) if isinstance(d, dict) else {}
        return m, (d.get("self", "") if isinstance(d, dict) else "")
    except (OSError, ValueError):
        return None, ""


def self_id():
    return os.environ.get("PIPEOS_WAKE_SELF") or lanid.mac4()


def resolve(machines, what):
    """A row by name, by pipeos-<id>, by bare id, or by host (with or
    without .local). Case-insensitive; exact, never a prefix guess."""
    w = what.strip().lower().removesuffix(".local")
    if w.startswith("pipeos-") and re.fullmatch(r"pipeos-[0-9a-f]{4}", w):
        w_id = w[7:]
    else:
        w_id = w
    for pid, r in machines.items():
        if pid == w_id:
            return pid, r
    for pid, r in machines.items():
        if (r.get("name") or "").lower() == w or (r.get("host") or "").lower().removesuffix(".local") == w:
            return pid, r
    return None, None


def packet(mac):
    raw = bytes.fromhex(mac.replace(":", ""))
    return b"\xff" * 6 + raw * 16


def send(mac, last_ip):
    pkt = packet(mac)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if TARGET:
            host, _, port = TARGET.rpartition(":")
            dests = [(host, int(port))] * 4
        else:
            dests = [("255.255.255.255", 9)] * 3 + ([(last_ip, 9)] if last_ip else [])
        for d in dests:
            try:
                s.sendto(pkt, d)
            except OSError:
                pass
    finally:
        s.close()


def wake_one(pid, r):
    mac = (r.get("mac") or "").lower()
    label = r.get("name") or r.get("host") or ("pipeos-" + pid)
    if not MAC_RE.match(mac):
        print("%s: no MAC on record — that Machine's image predates wake-on-lan; update it and it will announce one" % label, file=sys.stderr)
        return False
    send(mac, r.get("ip", ""))
    print("magic packet sent to %s (%s) — back in the lobby within a minute if the BIOS allows it" % (label, mac))
    return True


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().split("\n\n")[1])
        return 0 if argv else 2
    machines, me = roster()
    if machines is None:
        print("no roster yet — discovery has not written /work/pipeos/mdns/machines.json on this box (is /work mounted? is pipeos-mdns running?)", file=sys.stderr)
        return 1
    me = self_id() or me
    if argv[0] == "--list":
        for pid, r in sorted(machines.items(), key=lambda kv: (kv[1].get("name") or kv[1].get("host") or "")):
            print("%s  %-16s %-18s %-15s %s" % (pid, r.get("name") or "-", r.get("mac") or "-", r.get("ip") or "-",
                                             "(this one)" if pid == me else ""))
        return 0
    if argv[0] == "--forget":
        if len(argv) < 2:
            print("usage: pipeos wake --forget <id>", file=sys.stderr)
            return 2
        pid, r = resolve(machines, argv[1])
        if pid is None:
            print("%s: not a Machine this box has ever seen" % argv[1], file=sys.stderr)
            return 2
        del machines[pid]
        tmp = ROSTER + ".new"
        with open(tmp, "w") as f:
            json.dump({"v": 1, "self": me, "written": 0, "machines": machines}, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, ROSTER)
        print("forgot %s" % pid)
        return 0
    if argv[0] == "--all":
        n = 0
        for pid, r in machines.items():
            if pid == me:
                continue
            n += 1 if wake_one(pid, r) else 0
        print("%d Machine(s) sent a packet" % n)
        return 0
    pid, r = resolve(machines, argv[0])
    if pid is None:
        print("%s: not a Machine this box has ever seen (pipeos wake --list)" % argv[0], file=sys.stderr)
        return 2
    if pid == me:
        print("%s is this Machine — it is awake" % argv[0], file=sys.stderr)
        return 2
    return 0 if wake_one(pid, r) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
