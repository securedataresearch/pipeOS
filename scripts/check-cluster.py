#!/usr/bin/env python3
"""check-cluster: the cross-box auth primitive (pipeOS#222), end to end.

Two real webd instances on loopback, each a subprocess with its own key,
its own member list and its own stub pipeos-save — the check-webd.py
pattern, twice, because the property is between two Machines: a member's
signature is a session on the other box, a stranger's is a 401 that names
the check that refused it, and the answer comes back signed. Every row
runs the shipped cluster.py and the shipped webd.py, not a copy.

No root, no network beyond loopback, no box state touched. Controls in
check-cluster-controls.py put each refusal's bug back.
"""
import http.client as _hc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
CLUSTER = os.path.join(WEB, "cluster.py")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="cluster-")
BIN = os.path.join(D, "bin")
os.makedirs(BIN)
with open(os.path.join(BIN, "pipeos-save"), "w") as f:
    f.write("#!/bin/sh\necho save >> \"$PIPEOS_TEST_SAVES\"\n")
os.chmod(os.path.join(BIN, "pipeos-save"), 0o755)

# the instance runner: webd with its state redirected into its own dir
RUNNER = r'''
import importlib.util, os, sys, threading
from http.server import HTTPServer
spec = importlib.util.spec_from_file_location("webd", sys.argv[1]); webd = importlib.util.module_from_spec(spec); spec.loader.exec_module(webd)
d = sys.argv[2]
for k in ("ADMIN_CONF", "SERVICES_CONF", "CARD", "PROVISIONED", "BOOT_REPORT", "USERS_CONF", "SELFUPDATE_CONF", "SUPPORT_CONF", "NAS_CONF"):
    setattr(webd, k, os.path.join(d, k.lower()))
webd.SESS_DIR = os.path.join(d, "sessions"); webd.MDNS_CACHE = os.path.join(d, "peers.json"); webd.MACHINES_ROSTER = os.path.join(d, "machines.json")
webd.FLASH_IMAGE_TXT = os.path.join(d, "image.txt")
webd.lanid.mac4 = lambda iface=None: os.environ["PIPEOS_CLUSTER_SELF"]
webd.box_hostname = lambda: "pipeos-" + os.environ["PIPEOS_CLUSTER_SELF"]
open(webd.CARD, "w").write("NICK=\nNAME=" + os.environ.get("BOX_NAME", "") + "\nROLE=GENERIC\n")
srv = HTTPServer(("127.0.0.1", 0), webd.Handler)
print(srv.server_address[1], flush=True)
srv.serve_forever()
'''


class Box:
    def __init__(self, bid, name):
        self.id, self.name = bid, name
        self.dir = os.path.join(D, name)
        os.makedirs(self.dir)
        self.cdir = os.path.join(self.dir, "cluster")
        self.cjson = os.path.join(self.dir, "cluster.json")
        self.saves = os.path.join(self.dir, "saves")
        self.env = dict(os.environ, PATH=BIN + ":" + os.environ.get("PATH", ""),
                        PIPEOS_CLUSTER_DIR=self.cdir, PIPEOS_CLUSTER_JSON=self.cjson,
                        PIPEOS_CLUSTER_SELF=bid, PIPEOS_SAVE_BIN=os.path.join(BIN, "pipeos-save"),
                        PIPEOS_TEST_SAVES=self.saves, BOX_NAME=name)
        self.proc = subprocess.Popen([sys.executable, "-c", RUNNER, os.path.join(WEB, "webd.py"), self.dir],
                                     env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.port = int(self.proc.stdout.readline().strip())
        self.addr = "127.0.0.1:%d" % self.port

    def cli(self, *args, env=None):
        p = subprocess.run([sys.executable, CLUSTER] + list(args), capture_output=True, text=True, env=env or self.env)
        return p.returncode, p.stdout + p.stderr

    def py(self, code, env=None):
        """Run a line of cluster.py's API in this box's identity."""
        p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import cluster, json\n%s" % (WEB, code)],
                           capture_output=True, text=True, env=env or self.env)
        if p.returncode != 0:
            raise RuntimeError(p.stderr[-400:])
        return p.stdout.strip()

    def headers(self, method, path, body=b"", env=None):
        return json.loads(self.py("print(json.dumps(cluster.sign_headers(%r, %r, %r)))" % (method, path, body), env=env))

    def pub(self):
        return open(os.path.join(self.cdir, "key.pub")).read()

    def nsaves(self):
        try:
            return len(open(self.saves).read().split())
        except OSError:
            return 0

    def stop(self):
        self.proc.kill()


def http(box, method, path, headers=None, body=None):
    r = urllib.request.Request("http://%s%s" % (box.addr, path), data=body, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        return resp.status, json.loads(resp.read() or b"{}"), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


A = Box("aaaa", "zero")
B = Box("bbbb", "two")

# ── 1. init: a cluster of one on each ───────────────────────────────────
rc_a, out_a = A.cli("init", "zero")
rc_b, out_b = B.cli("init", "two")
ja, jb = json.load(open(A.cjson)), json.load(open(B.cjson))
check("1 cluster init on each box mints a 0600 key, writes a cluster of one with self named, and saves once",
      rc_a == 0 and rc_b == 0 and oct(os.stat(os.path.join(A.cdir, "key.pem")).st_mode & 0o777) == "0o600"
      and list(ja["members"]) == ["aaaa"] and ja["members"]["aaaa"]["name"] == "zero" and ja["id"] != jb["id"]
      and A.nsaves() == 1 and B.nsaves() == 1, repr((rc_a, out_a[-200:], rc_b, out_b[-200:], ja, A.nsaves())))

# ── 2. the identity is public; the reader is not ────────────────────────
st, ident, _ = http(B, "GET", "/api/cluster/identity")
st2, body2, _ = http(B, "GET", "/api/cluster")
check("2 /api/cluster/identity is public (id, public key, fingerprint, cluster id); /api/cluster wants a session",
      st == 200 and ident["id"] == "bbbb" and ident["pub"] == B.pub() and ident["cluster"] == jb["id"] and len(ident["fingerprint"]) == 16
      and st2 == 401 and body2["error"] == "sign in first", repr((st, ident, st2, body2)))

# ── 3. a signature from a box the other does not know ───────────────────
st, body, _ = http(B, "GET", "/api/cluster", A.headers("GET", "/api/cluster"))
check("3 a signed request from a Machine not in the member list is 401 'not a member', not a cookie fallback",
      st == 401 and body["error"] == "cluster: aaaa is not a member of this cluster", repr((st, body)))

# ── 4. admit each other, then a member's signature is a session ─────────
A.py("cluster.add_member('bbbb', %r, 'two')" % B.pub())
B.py("cluster.add_member('aaaa', %r, 'zero')" % A.pub())
st, body, hdrs = http(B, "GET", "/api/cluster", A.headers("GET", "/api/cluster"))
resp_ok = A.py("print(cluster.check(%r, '200', '/api/cluster', %r, replay=False))"
               % ({k: hdrs[k] for k in ("X-Pipeos-Id", "X-Pipeos-Ts", "X-Pipeos-Nonce", "X-Pipeos-Sig") if k in hdrs},
                  json.dumps(body).encode()))
check("4 once admitted, zero's signed GET on two is 200 with two's member list, and the answer comes back signed by two (verified against two's key with the status and path in the string)",
      st == 200 and sorted(m["id"] for m in body["members"]) == ["aaaa", "bbbb"] and body["self"] == "bbbb"
      and hdrs.get("X-Pipeos-Id") == "bbbb" and resp_ok.startswith("('bbbb'"), repr((st, body, dict(hdrs), resp_ok)))
ha, hb = json.load(open(A.cjson)), json.load(open(B.cjson))
check("4b both boxes compute the same members hash from the same two keys (what #211 puts in the TXT record)",
      A.py("print(cluster.members_hash())") == B.py("print(cluster.members_hash())") != "", "")

# ── 5. replay ───────────────────────────────────────────────────────────
h = A.headers("GET", "/api/cluster")
st1, _, _ = http(B, "GET", "/api/cluster", h)
st2, body2, _ = http(B, "GET", "/api/cluster", h)
st3, _, _ = http(B, "GET", "/api/cluster", A.headers("GET", "/api/cluster"))
check("5 the same signature twice is a replay (second is 401 'replay'); a fresh signature a moment later is fine (the nonce is why)",
      st1 == 200 and st2 == 401 and body2["error"] == "cluster: replay" and st3 == 200, repr((st1, st2, body2, st3)))

# ── 6. clock skew ───────────────────────────────────────────────────────
old = dict(A.env, PIPEOS_CLUSTER_NOW=str(int(time.time()) - 300))
st, body, _ = http(B, "GET", "/api/cluster", A.headers("GET", "/api/cluster", env=old))
edge = dict(A.env, PIPEOS_CLUSTER_NOW=str(int(time.time()) - 100))
st_e, _, _ = http(B, "GET", "/api/cluster", A.headers("GET", "/api/cluster", env=edge))
check("6 a signature 300 s old is 401 'clock skew' naming the seconds and the limit; 100 s inside the window passes",
      st == 401 and body["error"].startswith("cluster: clock skew 30") and "limit 120s" in body["error"] and st_e == 200,
      repr((st, body, st_e)))

# ── 7. the body is covered ──────────────────────────────────────────────
signed = json.dumps({"note": "signed"}).encode()
h = A.headers("POST", "/api/save", signed)
st, body, _ = http(B, "POST", "/api/save", h, json.dumps({"note": "tampered"}).encode())
s0 = B.nsaves()
st_ok, body_ok, _ = http(B, "POST", "/api/save", A.headers("POST", "/api/save", signed), signed)
check("7 a signed POST whose body was changed in flight is 401 'bad signature'; the untouched one is 200 and runs as admin (the save happened on two)",
      st == 401 and body["error"] == "cluster: bad signature" and st_ok == 200 and B.nsaves() == s0 + 1,
      repr((st, body, st_ok, body_ok, B.nsaves() - s0)))

# ── 8. impersonation: the right id, the wrong key ───────────────────────
C = Box("cccc", "stranger")
C.cli("init", "stranger")
forged = C.headers("GET", "/api/cluster", env=dict(C.env, PIPEOS_CLUSTER_SELF="aaaa"))
st, body, _ = http(B, "GET", "/api/cluster", forged)
check("8 a third box signing as zero's id with its own key is 401 'bad signature'",
      forged["X-Pipeos-Id"] == "aaaa" and st == 401 and body["error"] == "cluster: bad signature", repr((st, body)))

# ── 9. the operator verb: pipeos cluster call ───────────────────────────
rc, out = A.cli("call", B.addr, "GET", "/api/cluster")
rc_s, out_s = C.cli("call", B.addr, "GET", "/api/cluster")
check("9 'pipeos cluster call' from a member reports the status and 'signed by a member' (rc 0) and prints the answer; from a stranger it is rc 1 with the 401 and the reason",
      rc == 0 and out.startswith("200 signed by a member") and '"self": "bbbb"' in out
      and rc_s == 1 and out_s.startswith("401") and "not a member" in out_s, repr((rc, out[:120], rc_s, out_s[:200])))

# ── 10. removed → refused; a broken list → refused and said so ──────────
B.py("cluster.drop_member('aaaa')")
st, body, _ = http(B, "GET", "/api/cluster", A.headers("GET", "/api/cluster"))
open(B.cjson, "w").write("{not json")
st2, body2, _ = http(B, "GET", "/api/cluster", A.headers("GET", "/api/cluster"))
rc_st, out_st = B.cli("status")
check("10 a dropped member is 'not a member' again; a member list that does not parse refuses everyone with 'cluster.json:' and 'pipeos cluster status' says BROKEN (rc 1)",
      st == 401 and "not a member" in body["error"] and st2 == 401 and body2["error"].startswith("cluster: cluster.json:")
      and rc_st == 1 and "BROKEN" in out_st, repr((st, body, st2, body2, rc_st, out_st)))

# ── 12. keep-alive: every request on a connection is checked on its own ──
# The review of #283: protocol_version is HTTP/1.1, one Handler instance
# serves a whole connection, and the member verdict was cached on it — the
# second request on a member's socket would have been admitted unsigned.
# row 10 left two's list unparseable; put a good one back with both keys
open(B.cjson, "w").write(json.dumps({"v": 1, "id": jb["id"], "created": 1, "members": {
    "bbbb": {"pub": B.pub(), "name": "two", "added": 1},
    "aaaa": {"pub": A.pub(), "name": "zero", "added": 1}}}))
conn = _hc.HTTPConnection("127.0.0.1", B.port, timeout=10)
h = A.headers("GET", "/api/cluster")
conn.request("GET", "/api/cluster", headers=h); r1 = conn.getresponse(); b1 = r1.read()
forged = dict(h, **{"X-Pipeos-Sig": "AAAA" * 21 + "AA==", "X-Pipeos-Nonce": "ff" * 16})
conn.request("GET", "/api/cluster", headers=forged); r2 = conn.getresponse(); b2 = json.loads(r2.read())
conn.request("GET", "/api/cluster"); r3 = conn.getresponse(); b3 = json.loads(r3.read())
conn.close()
check("12 on one keep-alive connection: a valid signed request, then a garbage signature (401 'bad signature', not admitted on the first request's verdict), then an unsigned one (401 'sign in first', not the previous refusal)",
      r1.status == 200 and r2.status == 401 and b2["error"] == "cluster: bad signature"
      and r3.status == 401 and b3["error"] == "sign in first" and "X-Pipeos-Sig" not in r3.headers,
      repr((r1.status, r2.status, b2, r3.status, b3)))

# ── 13. the query string is inside the signature, both ways ─────────────
st_q, body_q, hdrs_q = http(B, "GET", "/api/cluster?x=1", A.headers("GET", "/api/cluster?x=1"))
resp_q = A.py("print(cluster.check(%r, '200', '/api/cluster?x=1', %r, replay=False))"
              % ({k: hdrs_q[k] for k in ("X-Pipeos-Id", "X-Pipeos-Ts", "X-Pipeos-Nonce", "X-Pipeos-Sig") if k in hdrs_q},
                 json.dumps(body_q).encode()))
st_qt, body_qt, _ = http(B, "GET", "/api/cluster?x=2", A.headers("GET", "/api/cluster?x=1"))
rc_off, out_off = A.cli("call", "127.0.0.1:1", "GET", "/api/cluster")
check("13 a signed GET with a query string is accepted and its answer verifies over the same target; the same signature over a changed query is 'bad signature'; a call to a Machine that is off is one 'unreachable' line, rc 1, not a traceback",
      st_q == 200 and resp_q.startswith("('bbbb'") and st_qt == 401 and body_qt["error"] == "cluster: bad signature"
      and rc_off == 1 and "unreachable" in out_off and "Traceback" not in out_off,
      repr((st_q, resp_q, st_qt, body_qt, rc_off, out_off[-200:])))

# ── 14. --force repairs a list that does not parse ──────────────────────
open(B.cjson, "w").write("{not json")
rc_f0, out_f0 = B.cli("init", "two")
rc_f, out_f = B.cli("init", "two", "--force")
check("14 'cluster init' on an unparseable list refuses (rc 1) and '--force' repairs it to a cluster of one with the SAME key (the members' copy of our public key stays valid)",
      rc_f0 == 1 and rc_f == 0 and list(json.load(open(B.cjson))["members"]) == ["bbbb"]
      and json.load(open(B.cjson))["members"]["bbbb"]["pub"] == B.pub(), repr((rc_f0, out_f0[-120:], rc_f, out_f[-120:])))

# ── 11. identity coverage ───────────────────────────────────────────────
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read().split("\n")
su = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfupdate")).read()
sc = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
check("11 the key dir and the member list are identity: in lbu.list, in pipeos-selfupdate's IDENTITY_PATHS, and selfcheck has the rows (parse, key present, key mode, lbu coverage)",
      "+etc/pipeos/cluster" in lbu and "+etc/pipeos/cluster.json" in lbu
      and "etc/pipeos/cluster etc/pipeos/cluster.json" in su
      and "cluster.json does not parse" in sc and "key.pem is mode" in sc and "no key at /etc/pipeos/cluster/key.pem" in sc
      and "is not in the lbu include list" in sc, "")

for b in (A, B, C):
    b.stop()
shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
