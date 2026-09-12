#!/usr/bin/env python3
"""cluster — the cross-box auth primitive (pipeOS#222, docs/cluster.md §3).

A cluster is a set of public keys every member holds. This module is the
whole of how one Machine proves to another that it is a member: a keypair
per Machine, a member list, and a signature over each request and each
response. Membership editing (mark out of the lobby, adopt, remove) is
#211's; this file is what #211, the cluster page (#212) and rolling updates
(#216) build on, and it is deliberately small enough to read in one sitting.

    /etc/pipeos/cluster/key.pem   this Machine's ed25519 private key, 0600
    /etc/pipeos/cluster/key.pub   its public key (SubjectPublicKeyInfo PEM)
    /etc/pipeos/cluster.json      {"v":1,"id":<cluster id>,"created":ts,
                                   "members":{<box id>:{"pub":PEM,"name":..,"added":ts}}}

Both are identity: lbu.list persists them, pipeos-selfupdate's
IDENTITY_PATHS re-adds them, and the apkovl carries them through a backup
and a flash apply like every other NEVER path.

THE WIRE. A signed request carries four headers:

    X-Pipeos-Id     the sender's box id (mac4)
    X-Pipeos-Ts     unix seconds, the sender's clock
    X-Pipeos-Nonce  16 random bytes, hex
    X-Pipeos-Sig    base64 ed25519 signature over the canonical string

    canonical = id "\\n" ts "\\n" nonce "\\n" METHOD "\\n" path "\\n" sha256hex(body)

A response is signed the same way by the box that answered, with the HTTP
status code in the METHOD slot and the request's path — so the caller
knows both who answered and that the body is theirs. The nonce is there
because ed25519 is deterministic: two identical GETs in the same second
would carry the same signature, and a replay set would refuse the second
legitimate one. With a nonce every signature is unique, and the replay set
is a set of signatures.

THE CHECKS, in order, each with its own refusal so the caller's log says
which one fired: unsigned; not a member; clock skew beyond SKEW_S (120 s —
chronyd may not have synced on a LAN without WAN, and a member whose clock
is off by more than two minutes is a box to fix, not to talk to); replay;
bad signature. A request that fails any of them is refused before the
session check, and never falls back to cookies.

WHY openssl AND NOT A PURE-PYTHON ED25519. The stdlib has none; openssl 3 is
in world for the vault (#244) and the TLS init; a copied-in curve
implementation would be the one piece of crypto in this repo nobody can
audit against a known-good. One subprocess per request is nothing at the
scale the design names (four on a switch, #209).

Seams (env, the check-cluster.py probe): PIPEOS_CLUSTER_DIR, PIPEOS_CLUSTER_JSON,
PIPEOS_CLUSTER_SELF (the box id), PIPEOS_SAVE_BIN, PIPEOS_CLUSTER_NOW (a
fixed clock, for the skew rows).
"""

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lanid  # noqa: E402

DIR = os.environ.get("PIPEOS_CLUSTER_DIR", "/etc/pipeos/cluster")
KEY = os.path.join(DIR, "key.pem")
PUB = os.path.join(DIR, "key.pub")
CLUSTER_JSON = os.environ.get("PIPEOS_CLUSTER_JSON", "/etc/pipeos/cluster.json")
SAVE_BIN = os.environ.get("PIPEOS_SAVE_BIN", "/usr/local/bin/pipeos-save")
SKEW_S = 120
H_ID, H_TS, H_NONCE, H_SIG = "X-Pipeos-Id", "X-Pipeos-Ts", "X-Pipeos-Nonce", "X-Pipeos-Sig"

_REPLAY = {}            # sig -> expiry; every signature seen inside the window
_REPLAY_LOCK = threading.Lock()


class ClusterError(Exception):
    pass


def now():
    fixed = os.environ.get("PIPEOS_CLUSTER_NOW")
    return int(fixed) if fixed else int(time.time())


def self_id():
    return os.environ.get("PIPEOS_CLUSTER_SELF") or lanid.mac4()


# ---- the key ---------------------------------------------------------------

def _openssl(args, input_bytes=None):
    p = subprocess.run(["openssl"] + args, input=input_bytes, capture_output=True)
    if p.returncode != 0:
        raise ClusterError("openssl %s: %s" % (args[0], p.stderr.decode(errors="replace").strip()[-200:]))
    return p.stdout


def have_key():
    return os.path.isfile(KEY) and os.path.isfile(PUB)


def mint(force=False):
    """This Machine's keypair. Once: a second mint would orphan the public
    key every other member holds, so it refuses unless forced."""
    if have_key() and not force:
        return self_pub()
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    os.chmod(DIR, 0o700)
    pem = _openssl(["genpkey", "-algorithm", "ed25519"])
    pub = _openssl(["pkey", "-pubout"], pem)
    fd = os.open(KEY + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    os.chmod(KEY + ".new", 0o600)
    os.replace(KEY + ".new", KEY)
    with open(PUB + ".new", "wb") as f:
        f.write(pub)
    os.replace(PUB + ".new", PUB)
    return pub.decode()


def self_pub():
    try:
        with open(PUB) as f:
            return f.read()
    except OSError:
        return ""


def key_mode_ok():
    try:
        return (os.stat(KEY).st_mode & 0o777) == 0o600
    except OSError:
        return False


def fingerprint(pub_pem):
    """Short, stable, safe to print and to put in a TXT record (#211): the
    sha256 of the DER, first 16 hex."""
    try:
        der = _openssl(["pkey", "-pubin", "-outform", "DER"], pub_pem.encode())
    except ClusterError:
        return ""
    return hashlib.sha256(der).hexdigest()[:16]


# ---- the member list -------------------------------------------------------

def read():
    """The cluster document, or None when this Machine is in no cluster.
    A file that does not parse is an error, not "no cluster": the
    difference is a box that was never joined versus one whose membership
    is broken, and selfcheck says which."""
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


def members():
    d = read()
    return d["members"] if d else {}


def members_hash(doc=None):
    """One string every member can compare (#211's TXT h): the sorted
    member ids and their fingerprints, hashed."""
    d = doc if doc is not None else read()
    if not d:
        return ""
    line = "\n".join("%s %s" % (mid, fingerprint(m.get("pub", ""))) for mid, m in sorted(d["members"].items()))
    return hashlib.sha256(line.encode()).hexdigest()[:16]


def init(name="", force=False):
    """A cluster of one: mint the key if needed, write the document with
    this Machine as its only member. Refuses when a cluster document
    already exists (leaving one is #211's business), unless forced."""
    if read() is not None and not force:
        raise ClusterError("already in a cluster (%s) — pipeos cluster status" % CLUSTER_JSON)
    pub = mint()
    ts = now()
    doc = {"v": 1, "id": secrets.token_hex(8), "created": ts,
           "members": {self_id(): {"pub": pub, "name": name, "added": ts}}}
    write(doc)
    return doc


def add_member(box_id, pub_pem, name=""):
    """The one edit #211 needs from here: a public key joins the set. The
    document must exist; an unknown key format is refused by openssl."""
    d = read()
    if d is None:
        raise ClusterError("not in a cluster — pipeos cluster init first")
    if not fingerprint(pub_pem):
        raise ClusterError("not a public key")
    d["members"][box_id] = {"pub": pub_pem, "name": name, "added": now()}
    write(d)
    return d


def drop_member(box_id):
    d = read()
    if d is None or box_id not in d["members"]:
        raise ClusterError("%s is not a member" % box_id)
    del d["members"][box_id]
    write(d)
    return d


# ---- sign / verify ---------------------------------------------------------

def canonical(box_id, ts, nonce, method, path, body):
    body = body if isinstance(body, bytes) else (body or "").encode()
    return "\n".join([str(box_id), str(ts), str(nonce), str(method), str(path),
                      hashlib.sha256(body).hexdigest()]).encode()


def sign(msg):
    if not have_key():
        raise ClusterError("no cluster key — pipeos cluster init")
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(msg)
        mpath = f.name
    try:
        sig = _openssl(["pkeyutl", "-sign", "-inkey", KEY, "-rawin", "-in", mpath])
    finally:
        os.unlink(mpath)
    return base64.b64encode(sig).decode()


def verify_sig(pub_pem, msg, sig_b64):
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except (ValueError, TypeError):
        return False
    if len(sig) != 64:
        return False
    with tempfile.NamedTemporaryFile(delete=False) as fm, tempfile.NamedTemporaryFile(delete=False) as fs, \
            tempfile.NamedTemporaryFile(delete=False) as fp:
        fm.write(msg); fs.write(sig); fp.write(pub_pem.encode())
        mpath, spath, ppath = fm.name, fs.name, fp.name
    try:
        p = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", ppath, "-rawin",
                            "-in", mpath, "-sigfile", spath], capture_output=True)
        return p.returncode == 0 and b"Signature Verified Successfully" in p.stdout
    finally:
        for x in (mpath, spath, ppath):
            os.unlink(x)


def sign_headers(method, path, body=b"", box_id=None):
    """The four headers for a request this Machine makes (or, with the
    status code as METHOD, a response it gives)."""
    box_id = box_id or self_id()
    ts, nonce = now(), secrets.token_hex(16)
    return {H_ID: box_id, H_TS: str(ts), H_NONCE: nonce,
            H_SIG: sign(canonical(box_id, ts, nonce, method, path, body))}


def _replay_seen(sig, ts):
    with _REPLAY_LOCK:
        t = now()
        for k in [k for k, exp in _REPLAY.items() if exp < t]:
            del _REPLAY[k]
        if sig in _REPLAY:
            return True
        _REPLAY[sig] = ts + 2 * SKEW_S
        return False


def check(headers, method, path, body=b"", replay=True):
    """(member id, "") when the headers prove a member sent this; (None,
    reason) otherwise. `headers` is anything with .get (an http.server
    message, a dict). A response is checked with replay=False — the caller
    matched it to its own nonce already, and a slow answer is not an attack."""
    box_id = headers.get(H_ID)
    if not box_id:
        return None, "unsigned"
    try:
        mem = members()
    except ClusterError as e:
        return None, "cluster.json: %s" % e
    m = mem.get(box_id)
    if not m or not m.get("pub"):
        return None, "%s is not a member of this cluster" % box_id
    try:
        ts = int(headers.get(H_TS) or "x")
    except ValueError:
        return None, "bad timestamp"
    skew = abs(now() - ts)
    if skew > SKEW_S:
        return None, "clock skew %ds (limit %ds) — the two Machines disagree on the time" % (skew, SKEW_S)
    nonce, sig = headers.get(H_NONCE) or "", headers.get(H_SIG) or ""
    if not nonce or not sig:
        return None, "missing nonce or signature"
    if not verify_sig(m["pub"], canonical(box_id, ts, nonce, method, path, body), sig):
        return None, "bad signature"
    if replay and _replay_seen(sig, ts):
        return None, "replay"
    return box_id, ""


# ---- a call to another member ------------------------------------------------

def resolve(target):
    """An id, a name, or an address → (ip, port), from what the responder
    knows (the live cache first, then the roster on /work). An address may
    carry a port (the probe's loopback instances do)."""
    port = 80
    host, _, p = target.partition(":")
    if p.isdigit():
        port, target = int(p), host
    if target.count(".") == 3 and target.replace(".", "").isdigit():
        return target, port
    t = target.lower()
    for path in ("/run/pipeos/mdns/peers.json", "/work/pipeos/mdns/machines.json"):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        rows = d.get("peers") or d.get("machines") or {}
        for pid, r in rows.items():
            if t in (pid, (r.get("name") or "").lower(), (r.get("host") or "").lower(),
                     (r.get("host") or "").lower().removesuffix(".local")) and r.get("ip"):
                return r["ip"], port
    raise ClusterError("%s: not a Machine this box knows — pipeos wake ls" % target)


def call(target, method, path, body=None, timeout=10):
    """A signed request to a member, and a check of its signed answer.
    Returns (status, parsed body or bytes, verified) — verified is False
    when the answer carries no member signature, which a caller treats as
    an unauthenticated reply (a lobby page, an error from something else
    on that port)."""
    ip, port = resolve(target)
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request("http://%s:%d%s" % (ip, port, path), data=data or None, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in sign_headers(method, path, data).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        status, raw, hdrs = resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        status, raw, hdrs = e.code, e.read(), e.headers
    who, _ = check(hdrs, str(status), path, raw, replay=False)
    try:
        parsed = json.loads(raw) if raw[:1] in (b"{", b"[") else raw
    except ValueError:
        parsed = raw
    return status, parsed, bool(who)


# ---- the operator verb (pipeos cluster …) ------------------------------------

def _save():
    p = subprocess.run([SAVE_BIN], capture_output=True, text=True)
    if p.returncode != 0:
        print("saved: NO — %s" % (p.stdout + p.stderr).strip()[-300:], file=sys.stderr)
        return 1
    print("saved")
    return 0


def status_doc():
    try:
        d = read()
        err = ""
    except ClusterError as e:
        d, err = None, str(e)
    return {"self": self_id(), "key": have_key(), "key_mode_ok": key_mode_ok(),
            "fingerprint": fingerprint(self_pub()) if have_key() else "",
            "cluster": d["id"] if d else None, "members": d["members"] if d else {},
            "members_hash": members_hash(d) if d else "", "error": err}


def main(argv):
    verb = argv[0] if argv else ""
    try:
        if verb == "init":
            name = argv[1] if len(argv) > 1 else ""
            d = init(name=name, force="--force" in argv)
            print("cluster %s: a cluster of one — %s (%s)" % (d["id"], self_id(), fingerprint(self_pub())))
            return _save()
        if verb == "status":
            s = status_doc()
            if s["error"]:
                print("cluster: BROKEN — %s" % s["error"]); return 1
            if not s["key"]:
                print("cluster: no key (pipeos cluster init)"); return 0
            print("self      %s  %s%s" % (s["self"], s["fingerprint"], "" if s["key_mode_ok"] else "  KEY MODE IS NOT 0600"))
            if s["cluster"] is None:
                print("cluster   none — a Machine of one, not yet a cluster of one (pipeos cluster init)"); return 0
            print("cluster   %s  members-hash %s" % (s["cluster"], s["members_hash"]))
            for mid, m in sorted(s["members"].items()):
                print("member    %s  %s  %s%s" % (mid, fingerprint(m.get("pub", "")), m.get("name", ""),
                                                  "  (self)" if mid == s["self"] else ""))
            return 0
        if verb == "call":
            if len(argv) < 4:
                print("usage: pipeos cluster call ID|NAME|IP METHOD PATH [JSON]", file=sys.stderr); return 2
            body = json.loads(argv[4]) if len(argv) > 4 else None
            st, out, ok = call(argv[1], argv[2].upper(), argv[3], body)
            print("%d %s" % (st, "signed by a member" if ok else "UNSIGNED — not a member's answer"))
            print(json.dumps(out, indent=1) if not isinstance(out, bytes) else out.decode(errors="replace"))
            return 0 if (st < 400 and ok) else 1
        if verb == "pub":
            print(self_pub(), end=""); return 0
    except ClusterError as e:
        print("cluster: %s" % e, file=sys.stderr)
        return 1
    print("usage: pipeos cluster init [NAME] [--force] | status | pub | call ID|NAME|IP METHOD PATH [JSON]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
