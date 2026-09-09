#!/usr/bin/env python3
"""pipeos-mdnsd — answer `<hostname>.local`, and find the other Machines.

A deliberately minimal mDNS responder (RFC 6762/6763 subset): join
224.0.0.251:5353, answer A questions for our names, advertise this
Machine as a `_pipeos._tcp` service (PTR, SRV to <host>.local:80, TXT with
identity and health), and every INTERVAL send one PTR query for that
service so every Machine on the LAN — including this one, which drops
its own answer by id — lands in a peer cache the dashboard's lobby reads.
Not avahi on purpose — avahi drags dbus into an image whose root
filesystem is RAM; this file rides the normal overlay deploy path.

Names answered: <hostname>.local; pipeos.local always (the lobby address,
identical on every Machine); pipeos-<mac4>.local while unclaimed or still
named "pipeos", so a claim lands on a specific Machine. The hostname is
re-read on every query (it changes when the wizard names the box — no
restart choreography). Announce twice on start and on a rename or claim,
goodbye (TTL 0) on SIGTERM, expiry at 3×INTERVAL for a Machine that just
vanished.

Seams for the probe (scripts/check-mdns.py), never set in production:
PIPEOS_MDNS_PORT/_GROUP/_TTL/_INTERVAL/_CACHE, PIPEOS_MDNS_IDENT (a JSON
file standing in for read_ident, re-read each tick), PIPEOS_MDNS_LOOP=1
(SO_REUSEPORT + IP_MULTICAST_LOOP so two instances share a port on
loopback).
"""

import json
import os
import select
import signal
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lanid  # noqa: E402

MDNS_GRP = os.environ.get("PIPEOS_MDNS_GROUP", lanid.MDNS_GRP)
MDNS_PORT = int(os.environ.get("PIPEOS_MDNS_PORT", lanid.MDNS_PORT))
MCAST_TTL = int(os.environ.get("PIPEOS_MDNS_TTL", 255))
INTERVAL = float(os.environ.get("PIPEOS_MDNS_INTERVAL", 10))
EXPIRE = 3 * INTERVAL
CACHE = os.environ.get("PIPEOS_MDNS_CACHE", "/run/pipeos/mdns/peers.json")
IDENT_FILE = os.environ.get("PIPEOS_MDNS_IDENT", "")
LOOP = os.environ.get("PIPEOS_MDNS_LOOP") == "1"
TTL = 120
SERVICE = lanid.SERVICE
PROVISIONED = "/etc/pipeos/provisioned"
BOOT_REPORT = "/run/pipeos/boot-report"
IMAGE_TXT = "/media/usb/pipeos-image.txt"


def log(msg):
    sys.stderr.write("pipeos-mdnsd: %s\n" % msg)
    sys.stderr.flush()


# ---- who we are --------------------------------------------------------------

def read_ident():
    """Everything the TXT record and the A names derive from. Cheap enough
    to call once a tick; the probe overrides it with a JSON file."""
    if IDENT_FILE:
        try:
            with open(IDENT_FILE) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        hn = d.get("hostname", "pipeos")
        m4 = d.get("mac4", "0000")
        ident = {"id": m4, "hostname": hn, "claimed": bool(d.get("claimed")),
                 "verdict": d.get("verdict", ""), "commit": d.get("commit", ""),
                 "built": d.get("built", ""), "model": d.get("model", "")}
    else:
        hn = socket.gethostname().lower()
        img = lanid.image_info(IMAGE_TXT)
        ident = {"id": lanid.mac4(), "hostname": hn, "claimed": os.path.exists(PROVISIONED),
                 "verdict": lanid.verdict_line(BOOT_REPORT), "commit": img["commit"][:12],
                 "built": img["built"], "model": lanid.model()}
    ident["nick"] = "" if ident["hostname"] == "pipeos" else ident["hostname"]
    ident["lan_name"] = lanid.lan_name(ident["id"])
    ident["web_host"] = (ident["hostname"] if ident["hostname"] != "pipeos" else ident["lan_name"]) + ".local"
    ident["instance"] = ident["id"] + "." + SERVICE
    return ident


def our_names(ident):
    names = {ident["hostname"] + ".local", "pipeos.local"}
    # the pre-claim name: while unclaimed, or claimed but never named —
    # either way "<hostname>.local" is pipeos.local and lands anywhere
    if not ident["claimed"] or ident["hostname"] == "pipeos":
        names.add(ident["lan_name"] + ".local")
    return names


def our_records(ident, ip, ttl=TTL):
    txt = _txt(ident)
    rrs = [
        lanid.rr_ptr("_services._dns-sd._udp.local", SERVICE, ttl),
        lanid.rr_ptr(SERVICE, ident["instance"], ttl),
        lanid.rr_srv(ident["instance"], 80, ident["web_host"], ttl),
        lanid.rr_txt(ident["instance"], txt, ttl),
    ]
    for n in sorted(our_names(ident)):
        rrs.append(lanid.rr_a(n, ip, ttl))
    return rrs


def answer_for(questions, ident, ip):
    """The records one incoming packet earns, or None."""
    rrs = []
    names = our_names(ident)
    for name, qtype, _qu in questions:
        if name == SERVICE and qtype in (12, 255):
            rrs.append(lanid.rr_ptr(SERVICE, ident["instance"], TTL))
            rrs.append(lanid.rr_srv(ident["instance"], 80, ident["web_host"], TTL))
            rrs.append(lanid.rr_txt(ident["instance"], _txt(ident), TTL))
            rrs.append(lanid.rr_a(ident["web_host"], ip, TTL))
        elif name == "_services._dns-sd._udp.local" and qtype in (12, 255):
            rrs.append(lanid.rr_ptr("_services._dns-sd._udp.local", SERVICE, TTL))
        elif name == ident["instance"] and qtype in (16, 33, 255):
            rrs.append(lanid.rr_srv(ident["instance"], 80, ident["web_host"], TTL))
            rrs.append(lanid.rr_txt(ident["instance"], _txt(ident), TTL))
            rrs.append(lanid.rr_a(ident["web_host"], ip, TTL))
        elif name in names and qtype in (1, 255):
            rrs.append(lanid.rr_a(name, ip, TTL))
    return lanid.build_response(rrs) if rrs else None


def _txt(ident):
    return {"id": ident["id"], "n": ident["nick"], "c": "1" if ident["claimed"] else "0",
            "v": ident["verdict"][:120], "i": ident["commit"], "b": ident["built"], "m": ident["model"]}


# ---- the peers -----------------------------------------------------------------

def absorb_response(records, src_ip, state):
    """A response naming our service: upsert (or, on TTL 0, drop) the
    Machine it describes. Our own answers come back too — dropped by id."""
    instances = [(ttl, rdata) for name, rtype, ttl, rdata in records if rtype == 12 and name == SERVICE]
    changed = False
    for ttl, inst in instances:
        srv = next((r for r in records if r[1] == 33 and r[0] == inst), None)
        txt = next((r for r in records if r[1] == 16 and r[0] == inst), None)
        kv = txt[3] if txt else {}
        pid = kv.get("id") or inst.split(".")[0]
        if pid == state["ident"]["id"]:
            continue
        if ttl == 0:
            if pid in state["peers"]:
                del state["peers"][pid]
                changed = True
                log("goodbye from %s" % pid)
            continue
        host = srv[3][3] if srv else ""
        a = next((r for r in records if r[1] == 1 and r[0] == host), None)
        entry = {"id": pid, "name": kv.get("n", ""), "host": host, "ip": a[3] if a else src_ip,
                 "claimed": kv.get("c") == "1", "verdict": kv.get("v", ""), "commit": kv.get("i", ""),
                 "built": kv.get("b", ""), "model": kv.get("m", ""), "last_seen": int(time.time())}
        old = state["peers"].get(pid)
        if old is None or any(old.get(k) != v for k, v in entry.items() if k != "last_seen"):
            changed = True
            if old is None:
                log("found %s (%s)" % (pid, host))
        state["peers"][pid] = entry
    return changed


def write_cache(state):
    d = os.path.dirname(CACHE)
    try:
        os.makedirs(d, exist_ok=True)
        tmp = CACHE + ".new"
        with open(tmp, "w") as f:
            json.dump({"v": 1, "self": state["ident"]["id"], "interval": INTERVAL,
                       "written": int(time.time()), "peers": state["peers"]}, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, CACHE)
    except OSError as e:
        log("cannot write %s: %s" % (CACHE, e))


def prune(state):
    now = time.time()
    gone = [p for p, e in state["peers"].items() if now - e["last_seen"] > EXPIRE]
    for p in gone:
        del state["peers"][p]
        log("expired %s" % p)
    return bool(gone)


# ---- the wire ----------------------------------------------------------------

def send(sock, pkt, to=None):
    try:
        sock.sendto(pkt, to or (MDNS_GRP, MDNS_PORT))
    except OSError as e:
        log("send failed: %s" % e)


def announce(sock, ident, ip):
    pkt = lanid.build_response(our_records(ident, ip))
    send(sock, pkt)
    time.sleep(1)
    send(sock, pkt)


def goodbye(sock, ident, ip):
    send(sock, lanid.build_response(our_records(ident, ip, ttl=0)))


def own_ip(sock):
    """The address the kernel would use toward the group — what we put in
    unsolicited records. Loopback when the probe runs us with no route."""
    ip = lanid_addr_toward(MDNS_GRP)
    return ip or "127.0.0.1"


def lanid_addr_toward(peer_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_ip, 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def handle_packet(data, peer, sock, state):
    flags, questions, records = lanid.parse_packet(data)
    if flags & 0x8000:
        if absorb_response(records, peer[0], state):
            write_cache(state)
        return
    if not questions:
        return
    ip = lanid_addr_toward(peer[0]) or own_ip(sock)
    pkt = answer_for(questions, state["ident"], ip)
    if pkt is None:
        return
    if any(qu for _n, _t, qu in questions):
        send(sock, pkt, peer)
    else:
        send(sock, pkt)


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if LOOP:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("0.0.0.0", MDNS_PORT))
    mreq = socket.inet_aton(MDNS_GRP) + socket.inet_aton("0.0.0.0")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MCAST_TTL)
    if LOOP:
        # the probe's arrangement: every packet stays on lo — sent via the
        # loopback interface, group joined there, looped back to every binder
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        socket.inet_aton(MDNS_GRP) + socket.inet_aton("127.0.0.1"))

    state = {"ident": read_ident(), "peers": {}}
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("now", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("now", True))
    log("answering for %s; advertising %s" % (", ".join(sorted(our_names(state["ident"]))), state["ident"]["instance"]))
    ip = own_ip(sock)
    announce(sock, state["ident"], ip)
    write_cache(state)
    send(sock, lanid.build_query(SERVICE, 12))
    next_tick = time.monotonic() + INTERVAL

    while not stop["now"]:
        wait = max(0.0, next_tick - time.monotonic())
        try:
            r, _, _ = select.select([sock], [], [], wait)
        except (OSError, InterruptedError):
            r = []
        if r:
            try:
                data, peer = sock.recvfrom(4096)
                handle_packet(data, peer, sock, state)
            except Exception as e:  # a malformed packet must never kill discovery
                log("ignored bad packet: %s" % e)
        if time.monotonic() >= next_tick:
            next_tick = time.monotonic() + INTERVAL
            try:
                new = read_ident()
                old = state["ident"]
                if our_names(new) != our_names(old) or _txt(new) != _txt(old):
                    ip = own_ip(sock)
                    if our_names(new) != our_names(old):
                        goodbye(sock, old, ip)
                    state["ident"] = new
                    announce(sock, new, ip)
                    log("now %s" % ", ".join(sorted(our_names(new))))
                send(sock, lanid.build_query(SERVICE, 12))
                prune(state)
                write_cache(state)
            except Exception as e:
                log("tick failed: %s" % e)

    goodbye(sock, state["ident"], own_ip(sock))
    write_cache(state)


if __name__ == "__main__":
    main()
