#!/usr/bin/env python3
"""pipeos-mdnsd — answer `<hostname>.local` so a customer can find the box.

A deliberately minimal mDNS responder (RFC 6762 subset): join 224.0.0.251:5353,
parse incoming queries, and answer A questions for our own name. Not avahi on
purpose — avahi drags dbus into an image whose root filesystem is RAM, and a
world change never reaches already-flashed boxes; this file rides the normal
overlay deploy path like every other script.

The hostname is re-read on every query (it changes when the wizard names the
box — no restart choreography), and we answer with the address of the
interface that faces the asker, so multi-homed boxes answer usefully.
"""

import socket
import struct
import sys

MDNS_GRP = "224.0.0.251"
MDNS_PORT = 5353
TTL = 120


def our_names():
    hn = socket.gethostname().lower()
    names = {hn + ".local"}
    # the shipped default answers too, until the box is named
    names.add("pipeos.local")
    return names


def addr_toward(peer_ip):
    """The source address the kernel would use toward the asker."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_ip, 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def parse_queries(data):
    """Yield (name, qtype, unicast_requested) for each question. Handles the
    uncompressed names queries actually use; bails quietly on anything odd."""
    if len(data) < 12:
        return
    (_tid, flags, qd, _an, _ns, _ar) = struct.unpack("!6H", data[:12])
    if flags & 0x8000:  # a response, not a query
        return
    off = 12
    for _ in range(qd):
        labels = []
        while True:
            if off >= len(data):
                return
            n = data[off]
            if n == 0:
                off += 1
                break
            if n & 0xC0:  # compression pointer — not worth following in a QD
                off += 2
                break
            labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
            off += 1 + n
        if off + 4 > len(data):
            return
        qtype, qclass = struct.unpack("!2H", data[off:off + 4])
        off += 4
        yield ".".join(labels).lower(), qtype, bool(qclass & 0x8000)


def encode_name(name):
    out = b""
    for label in name.split("."):
        raw = label.encode("ascii", "replace")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_answer(name, ip):
    # mDNS response: QR|AA, no questions, one answer, cache-flush class bit.
    hdr = struct.pack("!6H", 0, 0x8400, 0, 1, 0, 0)
    rr = (
        encode_name(name)
        + struct.pack("!2HIH", 1, 0x8001, TTL, 4)
        + socket.inet_aton(ip)
    )
    return hdr + rr


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", MDNS_PORT))
    mreq = socket.inet_aton(MDNS_GRP) + socket.inet_aton("0.0.0.0")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sys.stderr.write("pipeos-mdnsd: answering for %s\n" % ", ".join(sorted(our_names())))

    while True:
        try:
            data, (peer_ip, peer_port) = sock.recvfrom(4096)
        except OSError:
            continue
        try:
            for name, qtype, unicast in parse_queries(data):
                if qtype not in (1, 255):  # A or ANY
                    continue
                if name not in our_names():
                    continue
                ip = addr_toward(peer_ip)
                if not ip:
                    continue
                pkt = build_answer(name, ip)
                if unicast:
                    sock.sendto(pkt, (peer_ip, peer_port))
                else:
                    sock.sendto(pkt, (MDNS_GRP, MDNS_PORT))
        except Exception as e:  # a malformed packet must never kill discovery
            sys.stderr.write("pipeos-mdnsd: ignored bad packet: %s\n" % e)


if __name__ == "__main__":
    main()
