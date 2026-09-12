#!/usr/bin/env python3
"""cluster — membership between Machines over mutual TLS (pipeOS#222, #211,
docs/cluster.md §3).

A cluster is the set of Machines that trust each other's certificate
authority. Every Machine already has one: pipeos-tls-init mints a per-box
CA at first boot (it is what the owner installs on their devices for the
padlock) and a server cert signed by it. That CA IS the Machine's cluster
identity, and its server cert — now also a client cert — is what it
presents when it calls another member. Nothing new is minted here.

    /etc/pipeos/cluster.json   {"v":2, "id":<cluster id>, "created":ts,
                                "members":{<box id>:{"ca":PEM,"name":..,"added":ts}}}
    /run/pipeos/cluster-ca.pem  the members' CAs concatenated: webd's HTTPS
                                listener trusts client certs chaining to it;
                                a call to a member requires the server cert
                                to chain to it too
    /run/pipeos/cluster.status  what this box says about itself to the LAN
                                (mdnsd, as svc-mdns, reads it for the TXT
                                keys cl/k/h): cluster id, CA fingerprint,
                                members hash

THE MECHANISM is the standard one and the stdlib's: webd's :443 asks for a
client certificate (CERT_OPTIONAL — browsers, which hold none from these
CAs, are not prompted); a certificate that chains to a member's CA is a
member, admitted as admin for that connection; anything else that presents
a certificate fails the handshake. Which member: the leaf is verified
against each member CA (`openssl verify`, cached by leaf fingerprint) — the
CA that signed it is the identity, not anything the leaf claims about
itself. A call from this box to a member presents its own server cert and
requires the answering cert to chain to a member CA — so replay, tamper,
clock skew and "who answered" are TLS's problem, not this file's. (The
first version of #222 hand-rolled ed25519 request signatures; the review of
that PR found the query string outside the signature and a keep-alive
connection reusing a verdict — the class of bug a home-made scheme invites,
and the reason this is TLS.)

MEMBERSHIP (#211). Adding a Machine: read its CA (GET /ca.crt — public,
like the padlock download), then POST /api/cluster/join over TLS pinned to
that CA, carrying the target's OWN admin password and this list; the target
takes the list, adds itself, restarts its listener, and answers. This box
adds it and pushes the list (POST /api/cluster/members, mutual TLS) to every
member; a member takes a list from any member (leaderless), and one that
finds itself absent has been removed — a cluster of one again, its CA
untouched. A member that stops answering stays in the list (grey, #212).
One that advertises another cluster id on the LAN has joined elsewhere and
is dropped here by every reader on its own. Being on the LAN with the same
password is not membership; only the list is.

Seams (env, the check-cluster.py probe): PIPEOS_CLUSTER_JSON, PIPEOS_CLUSTER_BUNDLE,
PIPEOS_CLUSTER_STATUS, PIPEOS_CLUSTER_SELF (the box id), PIPEOS_TLS_DIR,
PIPEOS_SAVE_BIN, PIPEOS_MDNS_CACHE, PIPEOS_MDNS_ROSTER.
"""

import hashlib
import http.client
import json
import os
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lanid  # noqa: E402

CLUSTER_JSON = os.environ.get("PIPEOS_CLUSTER_JSON", "/etc/pipeos/cluster.json")
BUNDLE = os.environ.get("PIPEOS_CLUSTER_BUNDLE", "/run/pipeos/cluster-ca.pem")
STATUS = os.environ.get("PIPEOS_CLUSTER_STATUS", "/run/pipeos/cluster.status")
TLS_DIR = os.environ.get("PIPEOS_TLS_DIR", "/etc/pipeos/tls")
CA_CRT = os.path.join(TLS_DIR, "ca.crt")
SRV_CRT = os.path.join(TLS_DIR, "server.crt")
SRV_KEY = os.path.join(TLS_DIR, "server.key")
SAVE_BIN = os.environ.get("PIPEOS_SAVE_BIN", "/usr/local/bin/pipeos-save")
MDNS_CACHE = os.environ.get("PIPEOS_MDNS_CACHE", "/run/pipeos/mdns/peers.json")
MDNS_ROSTER = os.environ.get("PIPEOS_MDNS_ROSTER", "/work/pipeos/mdns/machines.json")
TLS_PORT = 443

_IDENT = {}                  # leaf sha256 -> member id, for this list
_IDENT_LOCK = threading.Lock()
_PORTS = {}                  # id -> (ip, tls port) learned from an IP:PORT target (the probe)
ON_CHANGE = []               # webd registers its listener restart here


class ClusterError(Exception):
    pass


def now():
    return int(time.time())


def self_id():
    return os.environ.get("PIPEOS_CLUSTER_SELF") or lanid.mac4()


# ---- the identity: this box's CA ----------------------------------------------

def _openssl(args, input_bytes=None):
    p = subprocess.run(["openssl"] + args, input=input_bytes, capture_output=True)
    if p.returncode != 0:
        raise ClusterError("openssl %s: %s" % (args[0], p.stderr.decode(errors="replace").strip()[-200:]))
    return p.stdout


def have_identity():
    return os.path.isfile(CA_CRT) and os.path.isfile(SRV_CRT) and os.path.isfile(SRV_KEY)


def self_ca():
    try:
        with open(CA_CRT) as f:
            return f.read()
    except OSError:
        return ""


def fingerprint(pem):
    """sha256 of the certificate's DER, first 16 hex: stable, printable,
    what the TXT record carries (k) and the page shows."""
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
    except (ValueError, TypeError):
        return ""
    return hashlib.sha256(der).hexdigest()[:16]


def _is_ca(pem):
    try:
        text = _openssl(["x509", "-noout", "-text"], pem.encode()).decode(errors="replace")
    except ClusterError:
        return False
    return "CA:TRUE" in text


# ---- the member list -------------------------------------------------------------

def read():
    """The cluster document, or None when this Machine is in no cluster.
    A file that does not parse is an error, not "no cluster": a box never
    joined and one whose membership is broken are different answers."""
    try:
        with open(CLUSTER_JSON) as f:
            d = json.load(f)
    except OSError:
        return None
    except ValueError as e:
        raise ClusterError("%s does not parse (%s)" % (CLUSTER_JSON, e))
    if not isinstance(d, dict) or not isinstance(d.get("members"), dict):
        raise ClusterError("%s does not describe a cluster" % CLUSTER_JSON)
    return d


def write(doc):
    os.makedirs(os.path.dirname(CLUSTER_JSON) or ".", exist_ok=True)
    tmp = CLUSTER_JSON + ".new"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, CLUSTER_JSON)
    bundle(doc)
    publish(doc)
    with _IDENT_LOCK:
        _IDENT.clear()
    for fn in ON_CHANGE:
        try:
            fn()
        except Exception as e:  # a listener that will not restart must not lose the write
            sys.stderr.write("cluster: on-change hook failed: %s\n" % e)


def bundle(doc=None):
    """The members' CAs as one file — what the listener and the caller
    verify against. Removed when there is no cluster: an absent bundle is
    'ask for no client certificate'."""
    d = doc if doc is not None else read()
    try:
        os.makedirs(os.path.dirname(BUNDLE), exist_ok=True)
        if not d or not d["members"]:
            if os.path.exists(BUNDLE):
                os.unlink(BUNDLE)
            return ""
        pems = "".join(m["ca"].rstrip("\n") + "\n" for _, m in sorted(d["members"].items()) if m.get("ca"))
        with open(BUNDLE + ".new", "w") as f:
            f.write(pems)
        os.chmod(BUNDLE + ".new", 0o644)
        os.replace(BUNDLE + ".new", BUNDLE)
        return BUNDLE
    except OSError as e:
        sys.stderr.write("cluster: cannot write %s: %s\n" % (BUNDLE, e))
        return ""


def publish(doc=None):
    try:
        d = doc if doc is not None else read()
    except ClusterError:
        d = None
    st = {"cl": d["id"] if d else "", "k": fingerprint(self_ca()), "h": members_hash(d) if d else "",
          "n": len(d["members"]) if d else 0, "written": now()}
    try:
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        with open(STATUS + ".new", "w") as f:
            json.dump(st, f)
        os.chmod(STATUS + ".new", 0o644)
        os.replace(STATUS + ".new", STATUS)
    except OSError:
        pass
    return st


def members():
    d = read()
    return d["members"] if d else {}


def members_hash(doc=None):
    """One string every member can compare (the TXT h): the sorted member
    ids and CA fingerprints, hashed."""
    d = doc if doc is not None else read()
    if not d:
        return ""
    line = "\n".join("%s %s" % (mid, fingerprint(m.get("ca", ""))) for mid, m in sorted(d["members"].items()))
    return hashlib.sha256(line.encode()).hexdigest()[:16]


def _self_entry(name=""):
    ca = self_ca()
    if not ca:
        raise ClusterError("no certificate authority at %s — pipeos-tls-init has not run" % CA_CRT)
    return {"ca": ca, "name": name, "added": now()}


def init(name="", force=False):
    """A cluster of one. Refuses when a list exists (leaving one is a
    remove from another member), unless forced — which also repairs a
    list that does not parse."""
    try:
        existing = read()
    except ClusterError:
        if not force:
            raise
        existing = None
    if existing is not None and not force:
        raise ClusterError("already in a cluster (%s) — pipeos cluster status" % CLUSTER_JSON)
    doc = {"v": 2, "id": secrets.token_hex(8), "created": now(), "members": {self_id(): _self_entry(name)}}
    write(doc)
    return doc


def _valid_doc(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("members"), dict) or not doc.get("id"):
        raise ClusterError("not a cluster document")
    for mid, m in doc["members"].items():
        if not isinstance(m, dict) or not m.get("ca") or not fingerprint(m["ca"]) or not _is_ca(m["ca"]):
            raise ClusterError("member %s carries no valid certificate authority" % mid)
    return doc


def add_member(box_id, ca_pem, name=""):
    d = read()
    if d is None:
        raise ClusterError("not in a cluster — pipeos cluster init first")
    if not fingerprint(ca_pem) or not _is_ca(ca_pem):
        raise ClusterError("not a certificate authority")
    d["members"][box_id] = {"ca": ca_pem, "name": name, "added": now()}
    write(d)
    return d


def drop_member(box_id):
    d = read()
    if d is None or box_id not in d["members"]:
        raise ClusterError("%s is not a member" % box_id)
    if box_id == self_id():
        raise ClusterError("a Machine does not remove itself — remove it from another member, or 'pipeos cluster init --force' to be a cluster of one")
    del d["members"][box_id]
    write(d)
    return d


def replace(doc):
    """A list from a member (POST /api/cluster/members over mutual TLS).
    Taken as it stands — the list is owner-edited and the same everywhere,
    so the last edit wins; a list in which we are absent means we were
    removed: a cluster of one again. Returns taken | same | removed |
    other-cluster."""
    _valid_doc(doc)
    mine = read()
    me = self_id()
    if mine is not None and doc["id"] != mine["id"]:
        return "other-cluster"
    if me not in doc["members"]:
        keep = (mine["members"].get(me) if mine else None) or _self_entry()
        write({"v": 2, "id": secrets.token_hex(8), "created": now(), "members": {me: keep}})
        return "removed"
    if mine is not None and members_hash(mine) == members_hash(doc) \
            and all(mine["members"][k].get("name") == v.get("name") for k, v in doc["members"].items()):
        return "same"
    write({"v": 2, "id": doc["id"], "created": doc.get("created", now()), "members": doc["members"]})
    return "taken"


def join(doc, name=""):
    """This Machine joins the cluster `doc` describes (POST /api/cluster/join,
    authorised by our own admin password). A Machine already in a cluster
    of more than one refuses — remove it there first; a cluster of one is
    simply left."""
    _valid_doc(doc)
    try:
        mine = read()
    except ClusterError:
        mine = None
    if mine is not None and mine["id"] != doc["id"] and len(mine["members"]) > 1:
        raise ClusterError("already a member of cluster %s with %d others — remove it there first"
                           % (mine["id"], len(mine["members"]) - 1))
    me = self_id()
    ms = dict(doc["members"])
    ms[me] = _self_entry(name)
    write({"v": 2, "id": doc["id"], "created": doc.get("created", now()), "members": ms})
    return {"id": me, "name": name, "ca": ms[me]["ca"]}


def reconcile():
    """§3: a member seen in ANOTHER cluster on the LAN is dropped here.
    Every reader runs this on its own; nothing propagates."""
    d = read()
    if d is None:
        return []
    dropped = []
    for pid, p in _peers().items():
        if pid in d["members"] and pid != self_id() and p.get("cl") and p["cl"] != d["id"]:
            del d["members"][pid]
            dropped.append(pid)
    if dropped:
        write(d)
    return dropped


# ---- who is on the other end of a TLS connection -----------------------------------

def member_of(leaf_der):
    """The member whose CA signed this leaf, or None. Verified with openssl
    against each member CA — the signer is the identity, not the leaf's
    own subject — and cached by leaf fingerprint until the list changes."""
    if not leaf_der:
        return None
    key = hashlib.sha256(leaf_der).hexdigest()
    with _IDENT_LOCK:
        if key in _IDENT:
            return _IDENT[key]
    who = None
    try:
        mem = members()
    except ClusterError:
        mem = {}
    if mem:
        leaf_pem = ssl.DER_cert_to_PEM_cert(leaf_der)
        with tempfile.TemporaryDirectory() as td:
            lp = os.path.join(td, "leaf.pem")
            with open(lp, "w") as f:
                f.write(leaf_pem)
            for mid, m in sorted(mem.items()):
                cp = os.path.join(td, "ca.pem")
                with open(cp, "w") as f:
                    f.write(m.get("ca", ""))
                p = subprocess.run(["openssl", "verify", "-CAfile", cp, lp], capture_output=True)
                if p.returncode == 0:
                    who = mid
                    break
    with _IDENT_LOCK:
        _IDENT[key] = who
    return who


def server_context():
    """webd's listener context: our cert, and — when there is a list —
    a request for a client certificate chaining to a member CA."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(SRV_CRT, SRV_KEY)
    if os.path.isfile(BUNDLE):
        ctx.load_verify_locations(cafile=BUNDLE)
        ctx.verify_mode = ssl.CERT_OPTIONAL
    return ctx


def _client_context(cafile, present=True):
    """cafile: what the answering cert must chain to (None: not verified —
    the one step that LEARNS a CA). present: offer our own cert. The two
    bootstrap calls (reading a CA, the join) present none: the other side
    does not trust our CA yet, and a presented cert that does not verify
    fails the handshake even under CERT_OPTIONAL."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False      # members are reached by address; the CA is the identity
    if cafile:
        ctx.load_verify_locations(cafile=cafile)
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        ctx.verify_mode = ssl.CERT_NONE
    if present and have_identity():
        ctx.load_cert_chain(SRV_CRT, SRV_KEY)
    return ctx


# ---- reaching a member ------------------------------------------------------------

def _peers():
    try:
        with open(MDNS_CACHE) as f:
            c = json.load(f)
        if now() - int(c.get("written", 0)) > 60:
            return {}
        return c.get("peers", {}) or {}
    except (OSError, ValueError):
        return {}


def _roster():
    try:
        with open(MDNS_ROSTER) as f:
            return json.load(f).get("machines", {}) or {}
    except (OSError, ValueError):
        return {}


def resolve(target):
    """An id, a name, or an address → (ip, tls port), from what the
    responder knows (the live cache first, then the roster on /work). An
    address may carry a port (the probe's loopback instances do)."""
    if target in _PORTS:
        return _PORTS[target]
    port = TLS_PORT
    host, _, p = target.partition(":")
    if p.isdigit():
        port, target = int(p), host
    if target.count(".") == 3 and target.replace(".", "").isdigit():
        return target, port
    t = target.lower()
    for rows in (_peers(), _roster()):
        for pid, r in rows.items():
            if t in (pid, (r.get("name") or "").lower(), (r.get("host") or "").lower(),
                     (r.get("host") or "").lower().removesuffix(".local")) and r.get("ip"):
                return r["ip"], int(r.get("tls_port") or port)
    raise ClusterError("%s: not a Machine this box knows — pipeos wake --list" % target)


def _https(ip, port, method, path, body, cafile, timeout, present=True):
    """One request over TLS. Returns (status, parsed body, the answering
    leaf's DER or None)."""
    data = json.dumps(body).encode() if body is not None else None
    conn = http.client.HTTPSConnection(ip, port, timeout=timeout, context=_client_context(cafile, present))
    try:
        hdrs = {"Content-Type": "application/json"} if data is not None else {}
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        raw = r.read()
        leaf = conn.sock.getpeercert(binary_form=True) if conn.sock else None
    except ssl.SSLError as e:
        raise ClusterError("%s:%d refused the TLS handshake — %s (this Machine is not a member there, or that one is not a member here)"
                           % (ip, port, getattr(e, "reason", e)))
    except (OSError, http.client.HTTPException) as e:
        raise ClusterError("%s:%d unreachable — %s" % (ip, port, e))
    finally:
        conn.close()
    try:
        parsed = json.loads(raw) if raw[:1] in (b"{", b"[") else raw
    except ValueError:
        parsed = raw
    return r.status, parsed, leaf


def call(target, method, path, body=None, timeout=10):
    """A request to a member over mutual TLS, and who answered. Returns
    (status, parsed body, the answering member's id or None). The answer
    is required to chain to a member CA — anything else is a handshake
    failure, not a reply."""
    if not os.path.isfile(BUNDLE):
        raise ClusterError("not in a cluster")
    ip, port = resolve(target)
    st, parsed, leaf = _https(ip, port, method, path, body, BUNDLE, timeout)
    return st, parsed, member_of(leaf)


def fetch_ca(target, timeout=10):
    """A Machine's CA, from its public /ca.crt — over TLS, unverified,
    because this is the step that LEARNS the CA (the padlock download does
    the same). What comes back is pinned for the join that follows."""
    ip, port = resolve(target)
    st, parsed, _ = _https(ip, port, "GET", "/ca.crt", None, None, timeout, present=False)
    if st != 200 or not isinstance(parsed, bytes) or b"BEGIN CERTIFICATE" not in parsed:
        raise ClusterError("%s: no certificate authority there (%d) — is it a Machine?" % (target, st))
    return parsed.decode()


def push(doc=None, only=None):
    """The list to every member but us (or to `only`). Returns
    {id: taken|same|removed|<error>} — a member that is off is an error
    line, not a failure of the push: it takes the list from whoever it
    hears next (#212 shows it grey and out of sync)."""
    d = doc if doc is not None else read()
    if d is None:
        raise ClusterError("not in a cluster")
    out = {}
    for mid in sorted(only or d["members"]):
        if mid == self_id():
            continue
        try:
            st, body, who = call(mid, "POST", "/api/cluster/members", {"cluster": d})
            if st == 200 and who:
                out[mid] = body.get("result", "ok")
            else:
                out[mid] = "%d %s" % (st, (body.get("error") if isinstance(body, dict) else "") or ("answered by %s" % who if who else "not a member's answer"))
        except ClusterError as e:
            out[mid] = str(e)
    return out


def add(target, password, name=""):
    """Mark a Machine out of the lobby (#211): learn its CA, have it join
    with ITS admin password over TLS pinned to that CA, add it here, push
    the list. Returns (the new member's id, the push report)."""
    d = read()
    if d is None:
        d = init()
    ca = fetch_ca(target)
    if fingerprint(ca) == fingerprint(self_ca()):
        raise ClusterError("that is this Machine")
    if any(fingerprint(m.get("ca", "")) == fingerprint(ca) for m in d["members"].values()):
        raise ClusterError("%s is already a member" % target)
    ip, port = resolve(target)
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(ca)
        pin = f.name
    try:
        st, ans, leaf = _https(ip, port, "POST", "/api/cluster/join",
                               {"password": password, "cluster": d, "name": name}, pin, 20, present=False)
    finally:
        os.unlink(pin)
    if st != 200:
        raise ClusterError("%s refused the join: %s" % (target, ans.get("error", "%d" % st) if isinstance(ans, dict) else st))
    if not isinstance(ans, dict) or not ans.get("id") or fingerprint(ans.get("ca", "")) != fingerprint(ca):
        raise ClusterError("%s answered the join with a different certificate authority" % target)
    d = add_member(ans["id"], ca, ans.get("name") or name)
    _PORTS[ans["id"]] = (ip, port)
    return ans["id"], push(d)


def remove(box_id):
    """Drop a member and push the list to the rest. The removed one is
    not told — its CA left our bundle, so it could no longer complete a
    handshake with us anyway; it learns the other way round: its next
    call to any member fails the handshake, its reader shows the members
    hash differing, and the owner sees it grey and out of sync (#212).
    Returns the push report."""
    d = read()
    if d is None:
        raise ClusterError("not in a cluster")
    was = set(d["members"])
    d = drop_member(box_id)
    return push(d, only=was - {self_id(), box_id})


def sync():
    return push()


# ---- the view ----------------------------------------------------------------------

def view():
    """The member list as the page shows it (#212 fills the rows in): each
    member with whether it answers on the LAN now, whether its advertised
    list agrees with ours, and — for the Add form — every claimed Machine
    on the LAN that is not a member."""
    try:
        d = read()
        err = ""
    except ClusterError as e:
        d, err = None, str(e)
    me = self_id()
    peers, roster = _peers(), _roster()
    h = members_hash(d) if d else ""
    rows = []
    for mid, m in sorted((d or {"members": {}})["members"].items()):
        p = peers.get(mid) or {}
        r = roster.get(mid) or {}
        awake = mid == me or bool(p)
        rows.append({"id": mid, "name": m.get("name", "") or p.get("name", "") or r.get("name", ""),
                     "self": mid == me, "awake": awake, "ip": p.get("ip") or r.get("ip", ""),
                     "host": p.get("host") or r.get("host", ""), "last_seen": p.get("last_seen") or r.get("last_seen", 0),
                     "in_sync": (mid == me) or (bool(p) and p.get("h", "") == h),
                     "verdict": p.get("verdict", ""), "fingerprint": fingerprint(m.get("ca", ""))})
    candidates = [{"id": pid, "name": p.get("name", ""), "host": p.get("host", ""), "ip": p.get("ip", ""),
                   "cluster": p.get("cl", ""), "tls_port": int(p.get("tls_port") or TLS_PORT)}
                  for pid, p in sorted(peers.items())
                  if p.get("claimed") and not (d and pid in d["members"])]
    return {"self": me, "identity": have_identity(), "fingerprint": fingerprint(self_ca()),
            "cluster": d["id"] if d else None, "members_hash": h, "members": rows,
            "candidates": candidates, "error": err}


# ---- the operator verb (pipeos cluster …) ------------------------------------------

def _save():
    p = subprocess.run([SAVE_BIN], capture_output=True, text=True)
    if p.returncode != 0:
        print("saved: NO — %s" % (p.stdout + p.stderr).strip()[-300:], file=sys.stderr)
        return 1
    print("saved")
    return 0


def _report(r):
    return ", ".join("%s=%s" % kv for kv in sorted(r.items())) or "(no other members)"


def main(argv):
    verb = argv[0] if argv else ""
    try:
        if verb == "init":
            name = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else ""
            d = init(name=name, force="--force" in argv)
            print("cluster %s: a cluster of one — %s (%s)" % (d["id"], self_id(), fingerprint(self_ca())))
            return _save()
        if verb == "status":
            vw = view()
            if vw["error"]:
                print("cluster: BROKEN — %s" % vw["error"]); return 1
            if not vw["identity"]:
                print("cluster: no certificate authority yet (pipeos-tls-init runs at boot)"); return 1
            print("self      %s  %s" % (vw["self"], vw["fingerprint"]))
            if vw["cluster"] is None:
                print("cluster   none — a Machine of one, not yet a cluster of one (pipeos cluster init)"); return 0
            print("cluster   %s  members-hash %s" % (vw["cluster"], vw["members_hash"]))
            for r in vw["members"]:
                print("member    %s  %s  %-12s %s%s" % (r["id"], r["fingerprint"], r["name"],
                                                     "self" if r["self"] else ("up" if r["awake"] else "off"),
                                                     "" if r["in_sync"] else "  LIST DIFFERS — pipeos cluster sync"))
            for c in vw["candidates"]:
                print("lobby     %s  %-12s %s" % (c["id"], c["name"], "in cluster %s" % c["cluster"] if c["cluster"] else "not in a cluster"))
            return 0
        if verb == "ca":
            print(self_ca(), end=""); return 0
        if verb == "add":
            if len(argv) < 2:
                print("usage: pipeos cluster add ID|NAME|IP [NAME]   (the target's admin password on stdin)", file=sys.stderr); return 2
            pw = sys.stdin.readline().rstrip("\n")
            if not pw:
                print("cluster: the target's admin password is read from stdin and was empty", file=sys.stderr); return 2
            mid, report = add(argv[1], pw, argv[2] if len(argv) > 2 else "")
            print("added %s; list pushed: %s" % (mid, _report(report)))
            return _save()
        if verb == "remove":
            if len(argv) < 2:
                print("usage: pipeos cluster remove ID", file=sys.stderr); return 2
            report = remove(argv[1])
            print("removed %s; list pushed: %s" % (argv[1], _report(report)))
            return _save()
        if verb == "sync":
            report = sync()
            print("list pushed: %s" % _report(report))
            return 0 if all(v in ("taken", "same", "removed") for v in report.values()) else 1
        if verb == "call":
            if len(argv) < 4:
                print("usage: pipeos cluster call ID|NAME|IP METHOD PATH [JSON]", file=sys.stderr); return 2
            body = json.loads(argv[4]) if len(argv) > 4 else None
            st, out, who = call(argv[1], argv[2].upper(), argv[3], body)
            print("%d %s" % (st, "answered by member %s" % who if who else "NOT a member's answer"))
            print(json.dumps(out, indent=1) if not isinstance(out, bytes) else out.decode(errors="replace"))
            return 0 if (st < 400 and who) else 1
    except ClusterError as e:
        print("cluster: %s" % e, file=sys.stderr)
        return 1
    print("usage: pipeos cluster init [NAME] [--force] | status | ca | add ID|NAME|IP [NAME] | remove ID | sync | call ID|NAME|IP METHOD PATH [JSON]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
