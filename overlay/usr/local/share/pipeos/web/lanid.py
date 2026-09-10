"""lanid — what a pipeOS Machine is on the LAN, and the mDNS wire it speaks.

Shared by mdnsd.py (advertise, browse) and webd.py (the lobby, the rename
check). Stdlib only, IPv4 only, like everything else in this directory.
Identity comes from sysfs so it reads the same as root (webd) and as
svc-mdns (the responder), and needs no subprocess.

The wire half is the RFC 6762/6763 subset a fleet lobby needs: read a
name with compression pointers (real responders compress), parse a packet
into questions and records with A/PTR/SRV/TXT decoded, and build the same
four record types. Nothing here is a general DNS library.
"""

import os
import re
import select
import socket
import struct
import subprocess
import time

MDNS_GRP = "224.0.0.251"
MDNS_PORT = 5353
SERVICE = "_pipeos._tcp.local"
SYS_NET = "/sys/class/net"
DMI = "/sys/class/dmi/id"

# ---- identity ---------------------------------------------------------------


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def primary_iface():
    """The interface a Machine is known by: sysfs only, deterministic —
    not lo, has a real MAC, up beats down, then first by name."""
    try:
        names = sorted(os.listdir(SYS_NET))
    except OSError:
        return ""
    cands = []
    for n in names:
        if n == "lo":
            continue
        mac = _read(os.path.join(SYS_NET, n, "address")).lower()
        if not mac or mac == "00:00:00:00:00:00":
            continue
        up = _read(os.path.join(SYS_NET, n, "operstate")) == "up"
        cands.append((0 if up else 1, n))
    if not cands:
        return ""
    return sorted(cands)[0][1]


def mac4(iface=None):
    """Last four hex digits of the primary MAC, lowercase; never raises.
    When the pipeos-identity service has already stamped the id into the
    hostname (pipeos-xxxx), that wins: it was computed before any link was
    up, and the two must never disagree on a box with more than one NIC."""
    if iface is None:
        m = re.fullmatch(r"pipeos-([0-9a-f]{4})", _hostname().lower())
        if m:
            return m.group(1)
    n = iface or primary_iface()
    mac = _read(os.path.join(SYS_NET, n, "address")).lower() if n else ""
    hexs = re.sub(r"[^0-9a-f]", "", mac)
    return hexs[-4:] if len(hexs) >= 4 else "0000"


def _hostname():
    try:
        import socket
        return socket.gethostname()
    except OSError:
        return ""


def lan_name(m4=None):
    """The pre-claim name every unclaimed Machine answers to."""
    return "pipeos-" + (m4 or mac4())


_OEM = ("to be filled by o.e.m.", "system product name", "default string",
        "system manufacturer", "system version", "not specified", "none")


def model():
    """Vendor + product from DMI (0444 in sysfs — no dmidecode, no root);
    blank when the firmware left the OEM placeholders."""
    vendor = _read(os.path.join(DMI, "sys_vendor"))
    product = _read(os.path.join(DMI, "product_name"))
    parts = []
    for p in (vendor, product):
        if p and p.lower() not in _OEM:
            parts.append(p)
    if len(parts) == 2 and parts[0].lower() in parts[1].lower():
        parts = parts[1:]
    return " ".join(" ".join(parts).split())


def image_info(path="/media/usb/pipeos-image.txt"):
    """variant/commit/built from the media's pipeos-image.txt; empty strings
    when it is missing or unreadable (a vfat umask can hide it from an
    unprivileged reader)."""
    out = {"variant": "", "commit": "", "built": ""}
    for line in _read(path).splitlines():
        k, _, v = line.partition("=")
        if k in out:
            out[k] = v.strip()
    return out


def verdict_line(path="/run/pipeos/boot-report"):
    m = re.search(r"^verdict: (.*)$", _read(path), re.M)
    return m.group(1).strip() if m else ""


# ---- wire -------------------------------------------------------------------


def encode_name(name):
    out = b""
    for label in name.strip(".").split("."):
        raw = label.encode("ascii", "replace")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def read_name(data, off):
    """Read a possibly-compressed name at off. Returns (name, next_off).
    Pointers must point backwards and are followed at most 16 deep — a
    forward or looping pointer is a malformed packet, not a name."""
    labels = []
    end = None
    hops = 0
    while True:
        if off >= len(data):
            raise ValueError("name runs past the packet")
        n = data[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            if off + 1 >= len(data):
                raise ValueError("truncated pointer")
            ptr = ((n & 0x3F) << 8) | data[off + 1]
            if ptr >= off:
                raise ValueError("forward pointer")
            hops += 1
            if hops > 16:
                raise ValueError("pointer loop")
            if end is None:
                end = off + 2
            off = ptr
            continue
        if n & 0xC0:
            raise ValueError("unsupported label type")
        labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
        off += 1 + n
    return ".".join(labels).lower(), (end if end is not None else off)


def _txt_kv(rdata):
    kv = {}
    off = 0
    while off < len(rdata):
        n = rdata[off]
        s = rdata[off + 1:off + 1 + n].decode("utf-8", "replace")
        off += 1 + n
        k, sep, v = s.partition("=")
        if k:
            kv[k] = v if sep else ""
    return kv


def parse_packet(data):
    """(flags, questions, records): questions = [(name, qtype, qu)],
    records = [(name, rtype, ttl, rdata)] over AN+NS+AR with rdata decoded
    for A (ip str), PTR (name), SRV ((prio, weight, port, target)), TXT
    ({k: v}); other types keep raw bytes. Stops at the first malformed
    piece and returns what it has."""
    if len(data) < 12:
        return 0, [], []
    (_tid, flags, qd, an, ns, ar) = struct.unpack("!6H", data[:12])
    off = 12
    questions, records = [], []
    try:
        for _ in range(qd):
            name, off = read_name(data, off)
            qtype, qclass = struct.unpack("!2H", data[off:off + 4])
            off += 4
            questions.append((name, qtype, bool(qclass & 0x8000)))
        for _ in range(an + ns + ar):
            name, off = read_name(data, off)
            rtype, _rclass, ttl, rdlen = struct.unpack("!2HIH", data[off:off + 10])
            off += 10
            raw = data[off:off + rdlen]
            if len(raw) < rdlen:
                break
            if rtype == 1 and rdlen == 4:
                rdata = socket.inet_ntoa(raw)
            elif rtype == 12:
                rdata, _ = read_name(data, off)
            elif rtype == 33 and rdlen >= 7:
                prio, weight, port = struct.unpack("!3H", raw[:6])
                target, _ = read_name(data, off + 6)
                rdata = (prio, weight, port, target)
            elif rtype == 16:
                rdata = _txt_kv(raw)
            else:
                rdata = raw
            off += rdlen
            records.append((name, rtype, ttl, rdata))
    except (ValueError, struct.error):
        pass
    return flags, questions, records


def _rr(name, rtype, ttl, rdata, flush=True):
    return encode_name(name) + struct.pack("!2HIH", rtype, 0x8001 if flush else 0x0001, ttl, len(rdata)) + rdata


def rr_a(name, ip, ttl):
    return _rr(name, 1, ttl, socket.inet_aton(ip))


def rr_ptr(name, target, ttl):
    # shared record: no cache-flush bit (other instances answer the same name)
    return _rr(name, 12, ttl, encode_name(target), flush=False)


def rr_srv(name, port, target, ttl):
    return _rr(name, 33, ttl, struct.pack("!3H", 0, 0, port) + encode_name(target))


def rr_txt(name, kv, ttl):
    out = b""
    for k, v in kv.items():
        s = ("%s=%s" % (k, v)).encode("utf-8")[:255]
        out += bytes([len(s)]) + s
    return _rr(name, 16, ttl, out or b"\x00")


def build_response(rrs):
    return struct.pack("!6H", 0, 0x8400, 0, len(rrs), 0, 0) + b"".join(rrs)


def build_query(name, qtype, qu=False):
    return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + encode_name(name) + struct.pack("!2H", qtype, 0x8001 if qu else 1)


def query_a(name, timeout=1.0, port=MDNS_PORT, group=MDNS_GRP, loop=False):
    """One-shot: who answers A for name? Returns the set of IPs seen within
    timeout. The socket binds the mDNS port itself so the multicast replies
    every responder sends reach it alongside the resident responder's
    socket (Linux delivers a joined group to every SO_REUSEADDR binder)."""
    name = name.lower()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if loop:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("0.0.0.0", port))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(group) + socket.inet_aton("0.0.0.0"))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255 if not loop else 0)
        if loop:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         socket.inet_aton(group) + socket.inet_aton("127.0.0.1"))
        s.sendto(build_query(name, 1), (group, port))
        seen = set()
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            r, _, _ = select.select([s], [], [], left)
            if not r:
                break
            try:
                data, _peer = s.recvfrom(4096)
            except OSError:
                continue
            flags, _q, recs = parse_packet(data)
            if not flags & 0x8000:
                continue
            for rname, rtype, _ttl, rdata in recs:
                if rtype == 1 and rname == name:
                    seen.add(rdata)
        return seen
    except OSError:
        return set()
    finally:
        s.close()


def local_ips():
    """Every IPv4 address this host holds (ip(8) — webd-side only)."""
    ips = {"127.0.0.1"}
    try:
        p = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5)
        for m in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)/", p.stdout):
            ips.add(m.group(1))
    except (OSError, subprocess.SubprocessError):
        pass
    return ips
