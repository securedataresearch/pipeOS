#!/usr/bin/env python3
"""check-cluster: membership between Machines over mutual TLS (pipeOS#222,
#211), end to end.

Real webd instances on loopback, each a subprocess with its own CA and
server cert (the shipped pipeos-tls-init makes them), its own member list
and its own stub pipeos-save — the check-webd.py pattern, several times,
because the property is between Machines: a member's client certificate
is a session on the other box, a stranger's fails the handshake, the
listener picks up a changed list, and the list reaches every member.
Every row runs the shipped cluster.py, webd.py and pipeos-tls-init.

No root, no network beyond loopback, no box state touched. Controls in
check-cluster-controls.py put the bugs back.
"""
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
CLUSTER = os.path.join(WEB, "cluster.py")
TLS_INIT = os.path.join(REPO, "overlay/usr/local/bin/pipeos-tls-init")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


TMPD = tempfile.mkdtemp(prefix="cluster-")
BIN = os.path.join(TMPD, "bin")
os.makedirs(BIN)
with open(os.path.join(BIN, "pipeos-save"), "w") as f:
    f.write("#!/bin/sh\necho save >> \"$PIPEOS_TEST_SAVES\"\n")
os.chmod(os.path.join(BIN, "pipeos-save"), 0o755)
for stub in ("pipebox-card", "pipeos-tls-init"):
    with open(os.path.join(BIN, stub), "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(os.path.join(BIN, stub), 0o755)
# the schedule runner an agent start launches (#300): records which job, on which box
with open(os.path.join(BIN, "pipeos-schedule-run"), "w") as f:
    f.write("#!/bin/sh\necho \"$1\" >> \"$PIPEOS_TEST_RAN\"\n")
os.chmod(os.path.join(BIN, "pipeos-schedule-run"), 0o755)

# the instance runner: webd with its state redirected into its own dir,
# HTTP and HTTPS both on a free port, the CA/server cert tls-init made
RUNNER = r'''
import importlib.util, os, sys, threading
from http.server import HTTPServer
spec = importlib.util.spec_from_file_location("webd", sys.argv[1]); webd = importlib.util.module_from_spec(spec); spec.loader.exec_module(webd)
d = sys.argv[2]
for k in ("ADMIN_CONF", "SERVICES_CONF", "CARD", "PROVISIONED", "BOOT_REPORT", "HEALTH_LAST", "USERS_CONF", "SELFUPDATE_CONF", "SUPPORT_CONF", "NAS_CONF"):
    setattr(webd, k, os.path.join(d, k.lower()))
webd.SESS_DIR = os.path.join(d, "sessions"); webd.MDNS_CACHE = os.path.join(d, "peers.json"); webd.MACHINES_ROSTER = os.path.join(d, "machines.json")
webd.FLASH_IMAGE_TXT = os.path.join(d, "image.txt")
webd.SCHEDULE_LOCK = os.path.join(d, "sched.lock")
webd.SCHEDULE_CONF = os.path.join(d, "schedule.json"); webd.SCHEDULE_STATE_DIR = os.path.join(d, "sched-state"); os.makedirs(webd.SCHEDULE_STATE_DIR, exist_ok=True)
webd.SCHEDULE_RUN_BIN = os.path.join(os.environ["PIPEOS_BIN"], "pipeos-schedule-run"); webd.SCHEDULE_LOGDIR = d; webd.LEDGER_PAUSED = os.path.join(d, "paused"); webd.LEDGER_PAUSED_JSON = os.path.join(d, "paused.json"); webd.LEDGER_DIR = os.path.join(d, "ledger")
webd.TLS_INIT = os.path.join(os.environ["PIPEOS_BIN"], "pipeos-tls-init")
webd.VAULT = webd.vault.VAULT_FILE = os.path.join(d, "vault.sealed"); webd.SECRETS_DIR = webd.vault.RUN_DIR = os.path.join(d, "secrets")
webd.vault.ETC = d; webd.vault.ITER = 1500; webd.vault.ident = lambda: {"mac": "aa:bb:cc:dd:" + os.environ["PIPEOS_CLUSTER_SELF"][:2] + ":" + os.environ["PIPEOS_CLUSTER_SELF"][2:], "serial": "PC", "product": "Test Box"}
webd.VAULT_PHRASE = os.path.join(d, "vault-phrase"); webd.VAULT_STATUS = os.path.join(d, "vault.status")
webd.VAULT_REQUESTS = os.path.join(d, "vault-requests.json"); webd.VAULT_NOTICES = os.path.join(d, "vault-notices.json")
open(webd.FLASH_IMAGE_TXT, "w").write("variant=usb\nbuilt=2026-09-12T00:00:00Z\ncommit=abc123def456789\n")
webd.TLS_DIR = os.path.join(d, "tls"); webd.CA_CRT = webd.TLS_DIR + "/ca.crt"; webd.SRV_CRT = webd.TLS_DIR + "/server.crt"; webd.SRV_KEY = webd.TLS_DIR + "/server.key"
webd.lanid.mac4 = lambda iface=None: os.environ["PIPEOS_CLUSTER_SELF"]
webd.box_hostname = lambda: "pipeos-" + os.environ["PIPEOS_CLUSTER_SELF"]
open(webd.CARD, "w").write("NICK=\nNAME=" + os.environ.get("BOX_NAME", "") + "\nROLE=GENERIC\n")
webd.cluster.bundle(); webd.cluster.publish()
srv = HTTPServer(("127.0.0.1", 0), webd.Handler)
https = webd.start_https(init=False)
print(srv.server_address[1], https.server_address[1], flush=True)
srv.serve_forever()
'''


class Box:
    def __init__(self, bid, name):
        self.id, self.name = bid, name
        self.dir = os.path.join(TMPD, name)
        os.makedirs(self.dir)
        self.tls = os.path.join(self.dir, "tls")
        self.cjson = os.path.join(self.dir, "cluster.json")
        self.saves = os.path.join(self.dir, "saves")
        self.env = dict(os.environ, PATH=BIN + ":" + os.environ.get("PATH", ""),
                        PIPEOS_CLUSTER_JSON=self.cjson, PIPEOS_TLS_DIR=self.tls, PIPEOS_TLS_HOST="pipeos-" + bid,
                        PIPEOS_CLUSTER_BUNDLE=os.path.join(self.dir, "cluster-ca.pem"),
                        PIPEOS_CLUSTER_STATUS=os.path.join(self.dir, "cluster.status"),
                        PIPEOS_MDNS_CACHE=os.path.join(self.dir, "peers.json"),
                        PIPEOS_MDNS_ROSTER=os.path.join(self.dir, "machines.json"),
                        PIPEOS_CLUSTER_SELF=bid, PIPEOS_SAVE_BIN=os.path.join(BIN, "pipeos-save"),
                        PIPEOS_TEST_SAVES=self.saves, PIPEOS_WEB_HTTPS_PORT="0", PIPEOS_WEB_BUNDLE_POLL="0.05", BOX_NAME=name,
                        PIPEOS_REBOOT_CMD="date +%%s.%%N >> %s" % os.path.join(self.dir, "reboots"),
                        PIPEOS_PTS_GLOB=os.path.join(self.dir, "no-pts", "*"), PIPEOS_BIN=BIN,
                        PIPEOS_JOIN_TOKEN=os.path.join(self.dir, "join-token"),
                        PIPEOS_CLUSTER_LAST=os.path.join(self.dir, "last"), PIPEOS_TEST_RAN=os.path.join(self.dir, "ran"),
                        PIPEOS_WEB_AUTH_DELAY="0")
        r = subprocess.run(["sh", TLS_INIT], env=self.env, capture_output=True, text=True)
        assert r.returncode == 0, "tls-init for %s: %s" % (name, r.stdout + r.stderr)
        self.proc = subprocess.Popen([sys.executable, "-c", RUNNER, os.path.join(WEB, "webd.py"), self.dir],
                                     env=self.env, stdout=subprocess.PIPE, stderr=open(os.path.join(self.dir, "webd.log"), "w"), text=True)
        self.port, self.tls_port = (int(x) for x in self.proc.stdout.readline().split())
        self.addr = "127.0.0.1:%d" % self.tls_port        # cluster.py talks TLS only
        self.env["PIPEOS_WEB_HTTPS_PORT_LOCAL"] = str(self.tls_port)   # `pipeos cluster page` talks to its own :443

    MEMBERSHIP = ("init", "add", "remove", "sync", "join", "adopt")

    def cli(self, *args, env=None, stdin=None):
        p = subprocess.run([sys.executable, CLUSTER] + list(args), capture_output=True, text=True, env=env or self.env, input=stdin)
        if args and args[0] in self.MEMBERSHIP:
            time.sleep(0.45)     # the listeners follow the trust store on disk (bundle_watcher, 0.05 s here)
        return p.returncode, p.stdout + p.stderr

    def py(self, code, env=None):
        p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import cluster, json\n%s" % (WEB, code)],
                           capture_output=True, text=True, env=env or self.env)
        if p.returncode != 0:
            raise RuntimeError(p.stderr[-400:])
        return p.stdout.strip()

    def claim(self, password):
        h = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n", capture_output=True, text=True).stdout.strip()
        open(os.path.join(self.dir, "admin_conf"), "w").write("HASH='%s'\n" % h)
        open(os.path.join(self.dir, "users_conf"), "w").write(json.dumps({"version": 1, "users": [{"name": "admin", "role": "admin", "hash": h}]}))
        open(os.path.join(self.dir, "provisioned"), "w").close()

    def see(self, *others, **over):
        """What this box's responder would know about the others."""
        peers = {}
        for o in others:
            st = {}
            try:
                st = json.load(open(os.path.join(o.dir, "cluster.status")))
            except (OSError, ValueError):
                pass
            peers[o.id] = dict({"id": o.id, "name": o.name, "host": o.name + ".local", "ip": "127.0.0.1", "port": o.port,
                                "tls_port": o.tls_port, "claimed": True, "verdict": "all green", "cl": st.get("cl", ""),
                                "k": st.get("k", ""), "h": st.get("h", ""), "last_seen": int(time.time())}, **over.get(o.id, {}))
        json.dump({"v": 1, "self": self.id, "interval": 10, "written": int(time.time()), "peers": peers},
                  open(os.path.join(self.dir, "peers.json"), "w"))

    def ca(self):
        return open(os.path.join(self.tls, "ca.crt")).read()

    def doc(self):
        try:
            return json.load(open(self.cjson))
        except (OSError, ValueError):
            return None

    def nsaves(self):
        try:
            return len(open(self.saves).read().split())
        except OSError:
            return 0

    def stop(self):
        self.proc.kill()
        self.proc.wait()


def http(box, method, path, body=None):
    """Plain :80 — what a browser or a stranger without a certificate gets."""
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (box.port, path), data=body, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login(box, password):
    """An admin session cookie on plain :80 — the owner's browser."""
    r = urllib.request.Request("http://127.0.0.1:%d/api/login" % box.port, data=json.dumps({"password": password}).encode(), method="POST")
    r.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(r, timeout=10)
    sc = resp.headers.get("Set-Cookie", "")
    return sc.split("session=")[1].split(";")[0]


def sess(box, cookie, method, path, body=None):
    """A request as the signed-in owner (plain :80 + the session cookie)."""
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (box.port, path), data=json.dumps(body).encode() if body is not None else None, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    r.add_header("Cookie", "session=" + cookie)
    try:
        resp = urllib.request.urlopen(r, timeout=20)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def https(box, method, path, body=None, cert_of=None):
    """:443 straight from here, optionally presenting another box's cert."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    if cert_of is not None:
        ctx.load_cert_chain(os.path.join(cert_of.tls, "server.crt"), os.path.join(cert_of.tls, "server.key"))
    r = urllib.request.Request("https://127.0.0.1:%d%s" % (box.tls_port, path), data=body, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(r, timeout=10, context=ctx)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        return 0, {"error": "handshake: %s" % getattr(e, "reason", e)}


A = Box("aaaa", "zero"); B = Box("bbbb", "two"); C = Box("cccc", "stranger")

# ── 1. the identity is the CA tls-init made; init is a cluster of one ────
eku = subprocess.run(["openssl", "x509", "-in", os.path.join(A.tls, "server.crt"), "-noout", "-text"], capture_output=True, text=True).stdout
rc_a, out_a = A.cli("init", "zero")
rc_b, out_b = B.cli("init", "two")
rc_a2, out_a2 = A.cli("init")
ja = A.doc()
check("1 tls-init issues a server cert with clientAuth; 'cluster init' writes a cluster of one whose only member carries this box's CA, writes the trust bundle, saves once; a second init refuses (rc 1) without saving",
      "TLS Web Client Authentication" in eku and rc_a == 0 and rc_b == 0 and ja and ja["v"] == 2
      and list(ja["members"]) == ["aaaa"] and ja["members"]["aaaa"]["ca"] == A.ca() and ja["members"]["aaaa"]["name"] == "zero"
      and open(os.path.join(A.dir, "cluster-ca.pem")).read() == A.ca()
      and A.nsaves() == 1 and rc_a2 == 1 and "already in a cluster" in out_a2 and A.nsaves() == 1,
      repr((rc_a, out_a[-200:], rc_b, out_b[-120:], rc_a2, out_a2[-120:], ja and list(ja["members"]), A.nsaves())))

# ── 2. public identity; no certificate is no session; a stranger's certificate is no handshake
st_i, ident = http(B, "GET", "/api/cluster/identity")
st_n, body_n = https(B, "GET", "/api/cluster")
st_s, body_s = https(B, "GET", "/api/cluster", cert_of=C)
rc_ac, out_ac = A.cli("call", C.addr, "GET", "/api/cluster")     # C has no cluster yet: its listener asks for no certificate
rc_c, out_c = C.cli("init", "stranger")
rc_cc, out_cc = C.cli("call", B.addr, "GET", "/api/cluster")
check("2 /api/cluster/identity is public (id, CA fingerprint, cluster id — the CA itself is /ca.crt); over TLS with no client certificate /api/cluster is 'sign in first'; a certificate from a CA not in the list fails the handshake; a call TO a Machine whose cert chains to no member CA is refused by the caller; 'cluster call' says so (rc 1)",
      st_i == 200 and ident["id"] == "bbbb" and len(ident["fingerprint"]) == 16 and ident["cluster"] == B.doc()["id"]
      and st_n == 401 and body_n["error"] == "sign in first"
      and st_s == 0 and "handshake" in body_s["error"]
      and rc_ac == 1 and "refused the TLS handshake" in out_ac and "CERTIFICATE_VERIFY_FAILED" in out_ac
      and rc_cc == 1 and "refused the TLS handshake" in out_cc,
      repr((st_i, ident, st_n, body_n, st_s, body_s, rc_ac, out_ac[-160:], rc_cc, out_cc[-200:])))

# ── 3. add: the target's password, then mutual TLS both ways ─────────────
B.claim("twopassword")
A.see(B, C)
rc_w, out_w = A.cli("add", B.addr, stdin="wrongpassword\n")
b_before, b_saves_before = B.doc(), B.nsaves()
rc_add, out_add = A.cli("add", B.addr, stdin="twopassword\n")
A.see(B, C)      # the responder would now hear two's new cluster id within a tick
da, db = A.doc(), B.doc()
same_hash = A.py("print(cluster.members_hash())") == B.py("print(cluster.members_hash())")
rc_ab, out_ab = A.cli("call", B.addr, "GET", "/api/cluster")
rc_ba, out_ba = B.cli("call", "127.0.0.1:%d" % A.tls_port, "GET", "/api/cluster")
check("3 'cluster add' with the wrong password is refused by the target (nothing changes there, no save); with its password the target takes the list (its own CA added), both lists carry both CAs, same hash, one save each; then each calls the other over mutual TLS and the answer is attributed to the right member",
      rc_w == 1 and "refused the join" in out_w and b_before is not None and list(b_before["members"]) == ["bbbb"] and b_saves_before == 1
      and rc_add == 0 and "added bbbb" in out_add and sorted(da["members"]) == ["aaaa", "bbbb"] == sorted(db["members"])
      and da["id"] == db["id"] and db["members"]["aaaa"]["ca"] == A.ca() and da["members"]["bbbb"]["ca"] == B.ca() and same_hash
      and A.nsaves() == 2 and B.nsaves() == 2
      and rc_ab == 0 and out_ab.startswith("200 answered by member bbbb") and '"self": "bbbb"' in out_ab
      and rc_ba == 0 and out_ba.startswith("200 answered by member aaaa"),
      repr((rc_w, out_w[-160:], b_saves_before, rc_add, out_add[-200:], sorted(da["members"]), sorted(db["members"]), same_hash, A.nsaves(), B.nsaves(), rc_ab, out_ab[:80], rc_ba, out_ba[:80])))

# ── 4. the listener follows the list: removed → handshake refused; re-added → admitted
rc_rm, out_rm = A.cli("remove", "bbbb")
st_r, body_r = https(A, "GET", "/api/cluster", cert_of=B)
rc_brm, out_brm = B.cli("call", "127.0.0.1:%d" % A.tls_port, "GET", "/api/cluster")
db_after = B.doc()
rc_re, out_re = A.cli("add", B.addr, stdin="twopassword\n")
A.see(B, C)
st_r2, body_r2 = https(A, "GET", "/api/cluster", cert_of=B)
check("4 after 'cluster remove' the removed Machine's certificate fails the handshake at once (the listener restarted with the new trust store); its own call says so; its list still names both (it finds out that way); adding it back admits it again — its CA never changed",
      rc_rm == 0 and st_r == 0 and "handshake" in body_r["error"]
      and rc_brm == 1 and "refused the TLS handshake" in out_brm
      and sorted(db_after["members"]) == ["aaaa", "bbbb"]
      and rc_re == 0 and st_r2 == 200 and sorted(m["id"] for m in body_r2["members"]) == ["aaaa", "bbbb"]
      and B.doc()["members"]["bbbb"]["ca"] == B.ca(),
      repr((rc_rm, out_rm[-160:], st_r, body_r, rc_brm, out_brm[-160:], rc_re, out_re[-160:], st_r2, body_r2.get("error"))))

# ── 5. the identity is the connection's: keep-alive, and :80 ─────────────
import http.client as _hc
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
ctx.load_cert_chain(os.path.join(B.tls, "server.crt"), os.path.join(B.tls, "server.key"))
conn = _hc.HTTPSConnection("127.0.0.1", A.tls_port, timeout=10, context=ctx)
class _R: status = 0
r1 = r2 = r3 = _R()
try:
    conn.request("GET", "/api/cluster"); r1 = conn.getresponse(); r1.read()
    conn.request("POST", "/api/save", body=b"{}", headers={"Content-Type": "application/json"}); r2 = conn.getresponse(); r2.read()
    conn.request("GET", "/api/cluster?x=1"); r3 = conn.getresponse(); r3.read()
except (OSError, ssl.SSLError, _hc.HTTPException):
    pass
conn.close()
st_p, body_p = http(A, "POST", "/api/cluster/members", json.dumps({"cluster": B.doc()}).encode())
check("5 on one member connection every request is the member's (GET, the member list POST, a GET with a query) — and a member's certificate is NOT an owner: an admin-only POST like /api/save is refused by the allowlist; the same requests on plain :80 carry no certificate and are 'sign in first'",
      r1.status == 200 and r2.status == 403 and r3.status == 200 and st_p == 401 and body_p["error"] == "sign in first",
      repr((r1.status, r2.status, r3.status, st_p, body_p)))

# ── 6. the view: off, out of sync, candidates; status prints it ──────────
A.see(B, C, bbbb={"h": "0000000000000000"})
v1 = json.loads(A.py("print(json.dumps(cluster.view()))"))
A.see(B, C)
v2 = json.loads(A.py("print(json.dumps(cluster.view()))"))
A.see(C)
v3 = json.loads(A.py("print(json.dumps(cluster.view()))"))
rc_st, out_st = A.cli("status")
m = lambda v, i: next(r for r in v["members"] if r["id"] == i)
check("6 the view: a member advertising a different members hash is 'list differs'; the same hash is in sync; one that stopped answering is off; the lobby's claimed non-members are the Add candidates with their TLS port; status prints the same",
      not m(v1, "bbbb")["in_sync"] and m(v1, "bbbb")["awake"]
      and m(v2, "bbbb")["in_sync"] and m(v2, "bbbb")["awake"]
      and not m(v3, "bbbb")["awake"]
      and [c["id"] for c in v2["candidates"]] == ["cccc"] and v2["candidates"][0]["tls_port"] == C.tls_port
      and rc_st == 0 and "member    bbbb" in out_st and "lobby     cccc" in out_st,
      repr((v1["members"], v2["candidates"], out_st)))

# ── 7. sync, and a member that is off ────────────────────────────────────
A.see(B, C)
rc_s, out_s = A.cli("sync")
B.stop()
rc_s2, out_s2 = A.cli("sync")
check("7 'cluster sync' pushes the list to every member ('same' when it already has it); with that member off the report says unreachable and rc 1 — it takes the list when it is back",
      rc_s == 0 and "bbbb=same" in out_s and rc_s2 == 1 and "bbbb=" in out_s2 and "unreachable" in out_s2, repr((rc_s, out_s, rc_s2, out_s2)))

# ── 8. the list endpoint: from a non-member; a list for another cluster; self-removal
B = Box("bbbb", "two-b"); B.claim("twopassword")            # the same Machine back: a re-flash, a NEW CA
A.see(B, C)
rc_rmoff, out_rmoff = A.cli("remove", "PIPEOS-BBBB.local")   # the mDNS host form, any case (#317 polish); the push set must not name it
rc_ra, out_ra = A.cli("add", B.addr, stdin="twopassword\n")
A.see(B, C)
st_c, body_c = https(A, "POST", "/api/cluster/members", json.dumps({"cluster": C.doc()}).encode(), cert_of=C)
st_o, body_o = https(A, "POST", "/api/cluster/members", json.dumps({"cluster": C.doc()}).encode(), cert_of=B)
rc_self, out_self = A.cli("remove", "aaaa")
check("8 a re-flashed Machine (new CA, same id) is removed (by its pipeos-ID.local host form; the push report does not name the removed one) and added again; a list POSTed with a non-member's certificate fails the handshake; a member's list for another cluster id is answered 'other-cluster' and changes nothing; a Machine does not remove itself",
      rc_rmoff == 0 and "removed bbbb" in out_rmoff and "bbbb=" not in out_rmoff and rc_ra == 0 and sorted(A.doc()["members"]) == ["aaaa", "bbbb"] and A.doc()["members"]["bbbb"]["ca"] == B.ca()
      and st_c == 0 and "handshake" in body_c["error"]
      and st_o == 200 and body_o["result"] == "other-cluster" and sorted(A.doc()["members"]) == ["aaaa", "bbbb"]
      and rc_self == 1 and "does not remove itself" in out_self, repr((rc_rmoff, out_rmoff[-120:], rc_ra, out_ra[-160:], st_c, body_c, st_o, body_o, rc_self, out_self)))

# ── 9. §3: a member seen in ANOTHER cluster is dropped on read, after its own request was admitted
A.see(B, C, bbbb={"cl": "someothercluster0"})
rc_r, out_r = B.cli("call", "127.0.0.1:%d" % A.tls_port, "GET", "/api/cluster")
check("9 a member advertising another cluster id on the LAN is dropped by the reader (its own request is admitted first — it WAS a member when it asked) and the answer says so",
      rc_r == 0 and '"dropped": [\n  "bbbb"\n ]' in out_r and list(A.doc()["members"]) == ["aaaa"], repr((rc_r, out_r[-300:], A.doc())))

# ── 10. join refuses a Machine already in a cluster of more than one ───
C.see(B)
rc_j, out_j = C.cli("add", B.addr, stdin="twopassword\n")
check("10 adding a Machine that is in another cluster with other members is refused by that Machine ('remove it there first'); a cluster of one is simply left",
      rc_j == 1 and "already a member of cluster" in out_j and "remove it there first" in out_j, repr((rc_j, out_j[-200:])))

# ── 11. a broken list; --force repairs it, the CA untouched ─────────────
A.see(B, C)
A.cli("add", B.addr, stdin="twopassword\n") if "bbbb" not in A.doc()["members"] else None
A.see(B, C)
open(A.cjson, "w").write("{not json")
rc_bs, out_bs = A.cli("status")
rc_bc, out_bc = B.cli("call", "127.0.0.1:%d" % A.tls_port, "GET", "/api/cluster")
rc_f0, out_f0 = A.cli("init", "zero")
rc_f, out_f = A.cli("init", "zero", "--force")
check("11 a member list that does not parse: status says BROKEN (rc 1), a member's call is refused 'sign in first' (no list, no members); init refuses (rc 1); init --force repairs it to a cluster of one with the SAME CA",
      rc_bs == 1 and "BROKEN" in out_bs and rc_bc == 1 and "401" in out_bc
      and rc_f0 == 1 and rc_f == 0 and list(A.doc()["members"]) == ["aaaa"] and A.doc()["members"]["aaaa"]["ca"] == A.ca(),
      repr((rc_bs, out_bs, rc_bc, out_bc[:120], rc_f0, out_f0[-120:], rc_f, out_f[-120:])))

# ── 13-16. the cluster page (#212): two fresh Machines ──────────────────
G = Box("1111", "six"); H = Box("2222", "seven")
for b in (G, H):
    b.claim(b.name + "password")
G.cli("init", "six"); G.see(H)
rc_gh, _ = G.cli("add", H.addr, stdin="sevenpassword\n"); G.see(H); H.see(G)
rc_sum, out_sum = G.cli("call", H.addr, "GET", "/api/cluster/summary")
summ = json.loads(out_sum.split("\n", 1)[1]) if rc_sum == 0 else {}
rc_pg, out_pg = G.cli("page")
st_pg, page = json.loads(G.py("st, b, who = cluster.call('local', 'GET', '/api/cluster/page'); print(json.dumps([st, b]))"))
rows = {r["id"]: r for r in page.get("members", [])}
check("13 a member's /api/cluster/summary over mutual TLS carries the two lines (name, id, role, verdict, activity, disk, release, boot report); the page gathers every member's, marks itself, and gives one verdict; 'pipeos cluster page' prints it",
      rc_gh == 0 and rc_sum == 0 and summ.get("id") == "2222" and summ.get("name") == "seven" and summ.get("role") == "GENERIC"
      and "commit" in summ and "work_pct" in summ and "busy" in summ and "boot_report" in summ
      and st_pg == 200 and sorted(rows) == ["1111", "2222"] and rows["1111"]["self"] and rows["2222"]["awake"]
      and rows["2222"]["commit"] == "abc123def456" and page["verdict"] == "all green"
      and rc_pg == 0 and "member    seven" in out_pg and "member    six" in out_pg and "abc123def456" in out_pg,
      repr((rc_gh, rc_sum, summ, st_pg, page, rc_pg, out_pg[-300:])))

# the live verdict (#290): a member whose boot report went DEGRADED but whose
# hourly check says green rolls up green and the row says so; an older live
# file loses to the boot report
open(os.path.join(H.dir, "boot_report"), "w").write("pipeos boot report [seven]\nverdict: DEGRADED — 1 critical\nCRITICAL: the morning's template divergence\n")
open(os.path.join(H.dir, "health_last"), "w").write("pipeos health [live] [seven]\nverdict: all green\n")
now = time.time()
os.utime(os.path.join(H.dir, "boot_report"), (now - 7200, now - 7200)); os.utime(os.path.join(H.dir, "health_last"), (now - 600, now - 600))
st_l, page_l = json.loads(G.py("st, b, who = cluster.call('local', 'GET', '/api/cluster/page'); print(json.dumps([st, b]))"))
rl = {r["id"]: r for r in page_l.get("members", [])}
rc_pl, out_pl = G.cli("page")
os.utime(os.path.join(H.dir, "health_last"), (now - 9000, now - 9000))
st_o, page_o = json.loads(G.py("st, b, who = cluster.call('local', 'GET', '/api/cluster/page'); print(json.dumps([st, b]))"))
ro = {r["id"]: r for r in page_o.get("members", [])}
check("13b a member's newer live verdict (health.last) wins over a DEGRADED boot report — the page rolls up green, the row says live and how old; an older live file loses to the boot report",
      st_l == 200 and rl["2222"]["verdict"] == "all green" and rl["2222"]["verdict_source"] == "live" and 500 <= rl["2222"]["verdict_age_s"] <= 700
      and page_l["verdict"] == "all green" and rc_pl == 0 and "(live, 10m ago)" in out_pl
      and st_o == 200 and ro["2222"]["verdict_source"] == "boot" and "DEGRADED" in ro["2222"]["verdict"] and page_o["verdict"].startswith("DEGRADED"),
      repr((st_l, rl.get("2222", {}).get("verdict"), rl.get("2222", {}).get("verdict_source"), rl.get("2222", {}).get("verdict_age_s"), page_l.get("verdict"), out_pl[-200:], ro.get("2222", {}).get("verdict_source"), page_o.get("verdict"))))
os.unlink(os.path.join(H.dir, "health_last")); open(os.path.join(H.dir, "boot_report"), "w").write("pipeos boot report [seven]\nverdict: all green\n")

# a member off: grey, and the cluster verdict says so
H.stop()
st_pg2, page2 = json.loads(G.py("st, b, who = cluster.call('local', 'GET', '/api/cluster/page'); print(json.dumps([st, b]))"))
r2 = {r["id"]: r for r in page2["members"]}
check("14 a member that does not answer is a grey row (off, last seen, the reason) and the cluster verdict counts it; the rest of the page still renders",
      st_pg2 == 200 and r2["2222"]["awake"] is False and "unreachable" in r2["2222"].get("error", "") and r2["1111"]["awake"]
      and page2["verdict"] == "1 member off", repr((st_pg2, page2)))
H = Box("2222", "seven-b"); H.claim("sevenpassword")
rc_rm7, out_rm7 = G.cli("remove", "seven"); gone7 = "2222" not in G.doc()["members"]; G.see(H); G.cli("add", H.addr, stdin="sevenpassword\n"); G.see(H); H.see(G)    # removed by NAME (#317 polish)


def svc_on(box, key):
    try:
        t = open(os.path.join(box.dir, "services_conf")).read()
    except OSError:
        return None
    return ("SERVICE_%s=on" % key.upper()) in t or '"%s": true' % key in t


# ── 15. one service on several Machines ─────────────────────────────────
rc_sv, out_sv = G.cli("services", "claude", "on")
on_g, on_h = svc_on(G, "claude"), svc_on(H, "claude")
rc_sv2, out_sv2 = G.cli("services", "claude", "off", "2222", "9999")
check("15 'cluster remove seven' (by NAME) dropped 2222 and said so; 'cluster services claude on' with no ids reaches every member: both services confs say on, each box saved its own; 'off 2222 9999' flips only seven and calls 9999 not a member",
      rc_rm7 == 0 and gone7 and "removed 2222" in out_rm7 and rc_sv == 0 and "1111=ok" in out_sv and "2222=ok" in out_sv and on_g and on_h
      and "2222=ok" in out_sv2 and "9999=not a member" in out_sv2 and svc_on(G, "claude") and not svc_on(H, "claude"),
      repr((rc_sv, out_sv, on_g, on_h, rc_sv2, out_sv2, svc_on(G, "claude"), svc_on(H, "claude"))))

def reboots(box):
    try:
        return [float(x) for x in open(os.path.join(box.dir, "reboots")).read().split()]
    except OSError:
        return []


# ── 16. reboot everything: the warning, then the order ──────────────────
import fcntl as _f
lockf = open(os.path.join(G.dir, "sched.lock"), "w")
_f.flock(lockf, _f.LOCK_EX)
rc_rb0, out_rb0 = G.cli("reboot-all")
_f.flock(lockf, _f.LOCK_UN); lockf.close()
time.sleep(2.5)      # schedule_reboot's own 2 s delay: had the refusal been ignored, the files would exist by now
none_yet = not reboots(G) and not reboots(H)
rc_rb, out_rb = G.cli("reboot-all")
for _ in range(80):  # the stub fires after schedule_reboot's 2 s; poll rather than wait a fixed 3.5 s
    if reboots(G) and reboots(H):
        break
    time.sleep(0.05)


check("16 'reboot-all' refuses while a member is busy (a job running on six) and names it, without --yes; once idle every member reboots, the others before the box that asked",
      rc_rb0 == 1 and "busy" in out_rb0 and "a job is running" in out_rb0 and none_yet
      and rc_rb == 0 and "2222=rebooting" in out_rb and "1111=rebooting" in out_rb
      and len(reboots(G)) == 1 and len(reboots(H)) == 1 and reboots(H)[0] <= reboots(G)[0],
      repr((rc_rb0, out_rb0[-200:], rc_rb, out_rb[-200:], reboots(G), reboots(H))))

# ── 19-21. agents on a cluster (#300): started on a member, they live there ──
def jobs_of(box):
    try:
        return [j["name"] for j in json.load(open(os.path.join(box.dir, "schedule.json")))["jobs"]]
    except (OSError, ValueError, KeyError):
        return []


def ran_on(box):
    try:
        return open(os.path.join(box.dir, "ran")).read().split()
    except OSError:
        return []


def page_rows(box):
    st, pg = json.loads(box.py("st, b, who = cluster.call('local', 'GET', '/api/cluster/page'); print(json.dumps([st, b]))"))
    return st, pg, {r["id"]: r for r in pg.get("members", [])}


h_saves0 = H.nsaves()
rc_st1, out_st1 = G.cli("start", "nightly", "--on", "2222", "--prompt", "say good night", "--cron", "@daily")
ran1, saves1 = ran_on(H), H.nsaves()
rc_st2, out_st2 = G.cli("start", "nightly", "--on", "seven-b")                       # by name; the agent is already there, so it just runs
rc_st3, out_st3 = G.cli("start", "nightly", "--on", "9999")
rc_st4, out_st4 = G.cli("start", "ghost", "--on", "1111")                            # a name alone must already live there
rc_st5, out_st5 = G.cli("start", "bad", "--on", "2222", "--prompt", "x", "--cron", "every tuesday")
rc_ag, out_ag = G.cli("agents")
st_p19, pg19, r19 = page_rows(G)
h_agents = [a["name"] for a in r19.get("2222", {}).get("agents", [])]
check("19 'cluster start NAME --on ID' places an agent on that member: the job is in ITS schedule.json (not this box's), ITS runner ran it, it saved; --on by name runs the agent already there; 9999 is not a member; a bare name that lives nowhere is refused; a bad schedule is the member's own refusal; every member's agents ride the page and 'cluster agents' lists them",
      rc_st1 == 0 and "started nightly on 2222" in out_st1 and jobs_of(H) == ["nightly"] and jobs_of(G) == [] and ran1 == ["nightly"] and saves1 == h_saves0 + 1
      and rc_st2 == 0 and ran_on(H) == ["nightly", "nightly"] and H.nsaves() == h_saves0 + 1
      and rc_st3 == 1 and "not a member" in out_st3 and rc_st4 == 1 and "no agent named ghost" in out_st4
      and rc_st5 == 1 and "2222: schedule:" in out_st5 and jobs_of(H) == ["nightly"]
      and st_p19 == 200 and h_agents == ["nightly"] and r19["1111"]["agents"] == [] and "load1" in r19["2222"] and "ncpu" in r19["2222"]
      and "spend_month_usd" in r19["2222"] and r19["2222"].get("month") == time.strftime("%Y-%m", time.gmtime())
      and json.load(open(os.path.join(G.dir, "last", "2222.json"))).get("month") == time.strftime("%Y-%m", time.gmtime())
      and rc_ag == 0 and "nightly" in out_ag and "2222" in out_ag,
      repr((rc_st1, out_st1[-160:], jobs_of(H), jobs_of(G), ran1, ran_on(H), saves1 - h_saves0, H.nsaves() - h_saves0, rc_st2, out_st2[-120:], rc_st3, out_st3[-120:], rc_st4, out_st4[-160:], rc_st5, out_st5[-160:], h_agents, rc_ag, out_ag[-200:])))

import fcntl as _f
# 19b. a placement refused at run time (the member is mid-job) still wrote the job — so it is saved, and said so
lh = open(os.path.join(H.dir, "sched.lock"), "w"); _f.flock(lh, _f.LOCK_EX)
h_saves2 = H.nsaves()
rc_rf, out_rf = G.cli("start", "later", "--on", "2222", "--prompt", "later then", "--cron", "@weekly")
_f.flock(lh, _f.LOCK_UN); lh.close()
check("19b a placement the member refuses at run time (another job is running there) still wrote the job into its schedule — and the member saved it, so the next boot keeps what the owner placed; the refusal names the member",
      rc_rf == 1 and "2222: another job is running" in out_rf and "later" in jobs_of(H) and H.nsaves() == h_saves2 + 1 and "later" not in ran_on(H),
      repr((rc_rf, out_rf[-200:], jobs_of(H), H.nsaves() - h_saves2, ran_on(H))))
H.cli("call", "local", "POST", "/api/schedule/del", json.dumps({"name": "later"}))

lg = open(os.path.join(G.dir, "sched.lock"), "w"); _f.flock(lg, _f.LOCK_EX)          # six is busy: a job is running
rc_i1, out_i1 = G.cli("start", "pick", "--on", "idlest", "--prompt", "pick me", "--cron", "@hourly")
g_jobs1, h_jobs1 = jobs_of(G), jobs_of(H)
_f.flock(lg, _f.LOCK_UN); lg.close()
lh = open(os.path.join(H.dir, "sched.lock"), "w"); _f.flock(lh, _f.LOCK_EX)          # now seven is the busy one
rc_i2, out_i2 = G.cli("start", "pick", "--on", "idlest", "--prompt", "pick me", "--cron", "@hourly")
lg = open(os.path.join(G.dir, "sched.lock"), "w"); _f.flock(lg, _f.LOCK_EX)          # both busy
rc_i3, out_i3 = G.cli("start", "pick", "--on", "idlest", "--prompt", "pick me", "--cron", "@hourly")
_f.flock(lg, _f.LOCK_UN); lg.close(); _f.flock(lh, _f.LOCK_UN); lh.close()
check("20 '--on idlest' lands on the awake member with nothing busy: seven while six runs a job, six while seven does, and a refusal (nothing started anywhere) when both are busy — explicit placement is the default, idlest the option",
      rc_i1 == 0 and "on 2222 (the idlest member)" in out_i1 and "pick" in h_jobs1 and "pick" not in g_jobs1
      and rc_i2 == 0 and "on 1111 (the idlest member)" in out_i2 and jobs_of(G) == ["pick"] and ran_on(G) == ["pick"]
      and rc_i3 == 1 and "no member is idle" in out_i3 and ran_on(G) == ["pick"] and ran_on(H) == ["nightly", "nightly", "pick"],
      repr((rc_i1, out_i1[-160:], g_jobs1, h_jobs1, rc_i2, out_i2[-160:], rc_i3, out_i3[-160:], jobs_of(G), jobs_of(H), ran_on(G), ran_on(H))))

# ── 20b. run once, now: a placement without a schedule is a manual job ────
ran_before = list(ran_on(H))
rc_once, out_once = G.cli("start", "once", "--on", "2222", "--prompt", "just this once")
once_cron = next((j.get("cron") for j in json.load(open(os.path.join(H.dir, "schedule.json")))["jobs"] if j["name"] == "once"), None)
H.cli("call", "local", "POST", "/api/schedule/del", json.dumps({"name": "once"}))
check("20b 'cluster start' with a prompt and no --cron places a MANUAL job: it ran once on the member and will never fire from its tick; the schedule needs no invented cron",
      rc_once == 0 and "started once on 2222" in out_once and once_cron == "manual" and ran_on(H) == ran_before + ["once"] and "once" not in jobs_of(H),
      repr((rc_once, out_once[-120:], once_cron, ran_on(H), jobs_of(H))))

# ── 22-26. a secret reaches a member only on the owner's tap (#301, pre-share) ──
cg, ch = login(G, "sixpassword"), login(H, "sevenpassword")
sess(G, cg, "POST", "/api/secrets/init", {}); sess(H, ch, "POST", "/api/secrets/init", {})
sess(G, cg, "POST", "/api/secrets/phrase-ack", {}); sess(H, ch, "POST", "/api/secrets/phrase-ack", {})
st_set, _ = sess(G, cg, "POST", "/api/secrets/set", {"name": "jobs.x", "value": "tok-1"})
h_saves3 = H.nsaves()
st_sh, out_sh = sess(G, cg, "POST", "/api/secrets/share", {"name": "jobs.x", "to": ["2222", "9999"]})
st_hl, out_hl = sess(H, ch, "GET", "/api/secrets")
h_rows = {r["name"]: r for r in out_hl.get("secrets", [])}
st_rv, out_rv = sess(H, ch, "POST", "/api/secrets/reveal", {"name": "jobs.x", "password": "sevenpassword"})
st_gl, out_gl = sess(G, cg, "GET", "/api/secrets")
g_rows = {r["name"]: r for r in out_gl.get("secrets", [])}
st_un, out_un = sess(G, cg, "POST", "/api/secrets/unshare", {"name": "jobs.x", "to": ["seven-b"]})     # by name, as the docs say
st_gl2, out_gl2 = sess(G, cg, "GET", "/api/secrets")
st_hl2, out_hl2 = sess(H, ch, "GET", "/api/secrets")
check("22 pre-share: the owner ticks a member on jobs.x → that member's vault holds it, marked as a copy from this box (by cluster:1111), the member saved once, the owner's list says who has it; 9999 is not a member; un-tick forgets here and the copy stays there",
      st_set == 200 and st_sh == 200 and out_sh["results"].get("2222") == "ok" and out_sh["results"].get("9999") == "not a member"
      and st_hl == 200 and h_rows.get("jobs.x", {}).get("by") == "cluster:1111" and st_rv == 200 and out_rv.get("value") == "tok-1" and H.nsaves() == h_saves3 + 1
      and g_rows.get("jobs.x", {}).get("shared", {}).get("2222") and st_un == 200 and out_un["results"].get("seven-b") == "forgotten"
      and not {r["name"]: r for r in out_gl2["secrets"]}["jobs.x"]["shared"] and "jobs.x" in {r["name"] for r in out_hl2["secrets"]},
      repr((st_set, st_sh, out_sh, st_hl, h_rows.get("jobs.x"), st_rv, out_rv, H.nsaves() - h_saves3, g_rows.get("jobs.x"), st_un, out_un)))

st_ml, _ = https(H, "GET", "/api/secrets", cert_of=G)
st_ms, _ = https(H, "POST", "/api/secrets/set", json.dumps({"name": "jobs.q", "value": "v"}).encode(), cert_of=G)
st_mr, _ = https(H, "POST", "/api/secrets/reveal", json.dumps({"name": "jobs.x", "password": "sevenpassword"}).encode(), cert_of=G)
st_md, _ = https(H, "POST", "/api/secrets/del", json.dumps({"name": "jobs.x"}).encode(), cert_of=G)
sess(H, ch, "POST", "/api/secrets/set", {"name": "jobs.z", "value": "mine"})
st_ov, out_ov = https(H, "POST", "/api/secrets/receive", json.dumps({"name": "jobs.z", "value": "theirs"}).encode(), cert_of=G)
st_rz, out_rz = sess(H, ch, "POST", "/api/secrets/reveal", {"name": "jobs.z", "password": "sevenpassword"})
st_pb, _ = https(H, "POST", "/api/secrets/receive", json.dumps({"name": "assistant_pass", "value": "x"}).encode(), cert_of=G)
st_str, out_str = https(H, "POST", "/api/secrets/receive", json.dumps({"name": "jobs.x", "value": "evil"}).encode(), cert_of=C)
st_sh2, out_sh2 = https(H, "POST", "/api/secrets/share", json.dumps({"name": "jobs.z", "to": ["1111"]}).encode(), cert_of=G)
st_ua, out_ua = https(H, "POST", "/api/users/add", json.dumps({"name": "x", "role": "admin", "password": "12345678abc"}).encode(), cert_of=G)   # the front door (the #314 review)
st_cs, _ = https(H, "POST", "/api/card", json.dumps({"NAME": "pwned"}).encode(), cert_of=G)
st_fw, out_fw = sess(H, ch, "POST", "/api/secrets/share", {"name": "jobs.x", "to": ["1111"]})                                                   # forwarding a copy from H (it is G's)
check("25 the guards: a member's certificate may not list, set, reveal or delete this Machine's secrets (403); a copy never overwrites a secret this Machine set itself (409, value intact); a per-box secret is never taken; a stranger's certificate fails the handshake; a member cannot make this box share; a member's certificate cannot add a user or touch the card either (the front door is shut); a copy is never forwarded onward",
      st_ml == 403 and st_ms == 403 and st_mr == 403 and st_md == 403
      and st_ov == 409 and "set on this Machine itself" in out_ov.get("error", "") and st_rz == 200 and out_rz.get("value") == "mine"
      and st_pb == 400 and st_str == 0 and st_sh2 == 403 and st_ua == 403 and st_cs in (403, 404) and st_fw == 400 and "is a copy from 1111" in out_fw.get("error", ""),
      repr((st_ml, st_ms, st_mr, st_md, st_ov, out_ov, out_rz, st_pb, st_str, out_str, st_sh2, out_sh2, st_ua, out_ua, st_cs, st_fw, out_fw)))

# ── 23-24. a share request (#301 part 2): the agent asks, the owner taps once, the copy travels ──
st_have, out_have = https(G, "POST", "/api/secrets/have", json.dumps({"name": "jobs.x"}).encode(), cert_of=H)
h_saves4 = H.nsaves()
rc_rq, out_rq = H.cli("call", "local", "POST", "/api/secrets/request", json.dumps({"name": "jobs.y", "why": "the nightly job needs it"}))   # H's own cert: the verb's path
rc_rq2, out_rq2 = H.cli("call", "local", "POST", "/api/secrets/request", json.dumps({"name": "jobs.x", "why": "already held"}))          # a name nobody holds → 404, nothing written
sess(G, cg, "POST", "/api/secrets/set", {"name": "jobs.y", "value": "tok-y"})
rc_rq3, out_rq3 = H.cli("call", "local", "POST", "/api/secrets/request", json.dumps({"name": "jobs.y", "why": "the nightly job needs it"}))
req_id = json.loads(out_rq3.split("\n", 1)[1]).get("request", {}).get("id") if rc_rq3 == 0 else None
st_stg, out_stg = sess(G, cg, "GET", "/api/status")
st_sth, out_sth = sess(H, ch, "GET", "/api/status")
st_ma, _ = https(G, "POST", "/api/secrets/requests/approve", json.dumps({"id": req_id}).encode(), cert_of=H)   # a member cert cannot approve
st_ap, out_ap = sess(G, cg, "POST", "/api/secrets/requests/approve", {"id": req_id})                           # the owner, on the HOLDER's dashboard (local send)
st_rvy, out_rvy = sess(H, ch, "POST", "/api/secrets/reveal", {"name": "jobs.y", "password": "sevenpassword"})
h_rec = next((r for r in json.load(open(os.path.join(H.dir, "vault-requests.json")))["requests"] if r["id"] == req_id), {})
st_stg2, out_stg2 = sess(G, cg, "GET", "/api/status")
st_sth2, out_sth2 = sess(H, ch, "GET", "/api/status")
check("23 request → approve → copy: the requester asks its own listener (the verb's path); a name nobody holds is 404; with a holder the record is written and saved on the requester and offered to every member's status; a member's certificate cannot approve; the owner's tap on the holder's dashboard copies it to the requester (reveal matches, by cluster:holder), the record is done with who decided, and nobody shows it pending",
      st_have == 200 and out_have.get("have") is True and rc_rq == 1 and "no member holds" in out_rq and rc_rq2 == 1 and "already holds" in out_rq2
      and rc_rq3 == 0 and req_id and H.nsaves() >= h_saves4 + 1
      and any(q["id"] == req_id and q.get("can_approve") is True for q in out_stg.get("share_requests", [])) and any(q["id"] == req_id and q.get("mine") and not q.get("can_approve") for q in out_sth.get("share_requests", []))
      and st_ma == 403 and st_ap == 200 and out_ap.get("holder") == "1111" and out_ap.get("to") == "2222"
      and st_rvy == 200 and out_rvy.get("value") == "tok-y" and h_rec.get("state") == "done" and h_rec.get("decided_by", "").endswith("@1111")
      and not out_stg2.get("share_requests") and not out_sth2.get("share_requests"),
      repr((st_have, out_have, rc_rq, out_rq[-160:], rc_rq2, out_rq2[-160:], rc_rq3, out_rq3[-300:], H.nsaves() - h_saves4, out_stg.get("share_requests"), out_sth.get("share_requests"), st_ma, st_ap, out_ap, st_rvy, out_rvy, h_rec, out_stg2.get("share_requests"), out_sth2.get("share_requests"))))

st_ap2, out_ap2 = sess(G, cg, "POST", "/api/secrets/requests/approve", {"id": req_id})
# a second request, approved from the REQUESTER's dashboard (the holder is remote: send over mTLS), and one that expires unseen
sess(G, cg, "POST", "/api/secrets/set", {"name": "jobs.w", "value": "tok-w"})
rc_rq4, out_rq4 = H.cli("call", "local", "POST", "/api/secrets/request", json.dumps({"name": "jobs.w"}))
req2 = json.loads(out_rq4.split("\n", 1)[1]).get("request", {}).get("id") if rc_rq4 == 0 else None
st_apx, out_apx = sess(H, ch, "POST", "/api/secrets/requests/approve", {"id": req2})      # the requester's own dashboard: it does not hold it — pointed at a holder
st_ap3, out_ap3 = sess(G, cg, "POST", "/api/secrets/requests/approve", {"id": req2})      # the holder's dashboard, on its own session
st_rvw, out_rvw = sess(H, ch, "POST", "/api/secrets/reveal", {"name": "jobs.w", "password": "sevenpassword"})
st_dn0, out_dn0 = sess(G, cg, "POST", "/api/secrets/requests/deny", {"id": req2})
rp = os.path.join(H.dir, "vault-requests.json"); doc = json.load(open(rp))
doc["requests"].append({"id": "deadbeefdeadbeef", "name": "jobs.old", "why": "", "from": "2222", "holders": ["1111"], "asked_at": 1, "expires_at": 2, "state": "pending", "decided_by": "", "decided_at": 0})
open(rp, "w").write(json.dumps(doc))
st_ex, out_ex = sess(H, ch, "POST", "/api/secrets/requests/approve", {"id": "deadbeefdeadbeef"})
st_sth3, out_sth3 = sess(H, ch, "GET", "/api/status")
st_rcv, out_rcv = https(H, "POST", "/api/secrets/receive", json.dumps({"id": "nope", "name": "jobs.v", "value": "x"}).encode(), cert_of=G)
sess(G, cg, "POST", "/api/secrets/set", {"name": "jobs.d", "value": "tok-d"})
rc_rq5, out_rq5 = H.cli("call", "local", "POST", "/api/secrets/request", json.dumps({"name": "jobs.d"}))
req3 = json.loads(out_rq5.split("\n", 1)[1]).get("request", {}).get("id") if rc_rq5 == 0 else None
st_dn, out_dn = sess(G, cg, "POST", "/api/secrets/requests/deny", {"id": req3})
h_rec3 = next((r for r in json.load(open(rp))["requests"] if r["id"] == req3), {})
st_ap4, out_ap4 = sess(H, ch, "POST", "/api/secrets/requests/approve", {"id": req3})
check("24 once and only once: a second approve is refused as already done; the requester's own dashboard cannot approve (it points at a holder) and the holder's can, on its own session; an expired request is refused and gone from status; a copy with an unknown request id is refused and writes nothing; deny on any dashboard closes it on the requester (saved) and a later approve is refused",
      st_ap2 == 409 and "already done" in out_ap2.get("error", "")
      and rc_rq4 == 0 and st_apx == 409 and "approve on one that holds" in out_apx.get("error", "") and st_ap3 == 200 and out_ap3.get("holder") == "1111" and out_ap3.get("state") == "done" and out_ap3.get("saved") is True
      and st_rvw == 200 and out_rvw.get("value") == "tok-w" and st_dn0 == 409
      and st_ex == 409 and "expired" in out_ex.get("error", "") and not any(q["id"] == "deadbeefdeadbeef" for q in out_sth3.get("share_requests", []))
      and st_rcv == 404 and "jobs.v" not in {r["name"] for r in sess(H, ch, "GET", "/api/secrets")[1].get("secrets", [])}
      and rc_rq5 == 0 and st_dn == 200 and h_rec3.get("state") == "denied" and st_ap4 == 409 and "denied" in out_ap4.get("error", ""),
      repr((st_ap2, out_ap2, rc_rq4, out_rq4[-160:], st_apx, out_apx, st_ap3, out_ap3, st_rvw, out_rvw, st_dn0, st_ex, out_ex, st_rcv, out_rcv, rc_rq5, st_dn, out_dn, h_rec3, st_ap4, out_ap4)))

tmpl = open(os.path.join(REPO, "overlay/usr/local/share/pipeos/card/pipebox-settings.json.tmpl")).read()
vsrc = open(os.path.join(WEB, "vault.py")).read()
lbul = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read()
check("26 the fence denies the resident agent every vault verb (share included) and the side doors to the same listener (pipeos cluster*, python3 on the web dir), and allows exactly `pipeos secrets request`; the share/unshare/request verbs go through this Machine's own listener; the requester's records ride the apkovl",
      '"Bash(pipeos vault*)"' in tmpl and '"Bash(pipeos cluster*)"' in tmpl and '"Bash(python3 /usr/local/share/pipeos/web/*)"' in tmpl and '"Bash(pipeos secrets request *)"' in tmpl
      and 'cluster.call("local", "POST", "/api/secrets/" + verb' in vsrc and 'cluster.call("local", "POST", "/api/secrets/request"' in vsrc and "+etc/pipeos/vault-requests.json" in lbul, "")

H.stop()
st_p21, pg21, r21 = page_rows(G)
rc_ag2, out_ag2 = G.cli("agents")
grey = r21.get("2222", {})
check("21 a grey box's agents are grey too: the row of a member that stopped answering still lists the agents it had at its last answer, marked last-known with nothing claimed running, and nothing restarts them anywhere; 'cluster agents' says grey (box off)",
      st_p21 == 200 and grey.get("awake") is False and sorted(a["name"] for a in grey.get("agents", [])) == ["nightly", "pick"]
      and grey.get("agents_stale") is True and all(a.get("running") is None for a in grey["agents"]) and isinstance(grey.get("agents_seen"), int)
      and ran_on(G) == ["pick"] and jobs_of(G) == ["pick"]
      and rc_ag2 == 0 and "grey (box off)" in out_ag2 and "nightly" in out_ag2,
      repr((st_p21, grey.get("awake"), grey.get("agents"), grey.get("agents_stale"), grey.get("agents_seen"), ran_on(G), jobs_of(G), rc_ag2, out_ag2[-240:])))
H = Box("2222", "seven-c"); H.claim("sevenpassword")
G.cli("remove", "2222"); G.see(H); G.cli("add", H.addr, stdin="sevenpassword\n"); G.see(H); H.see(G)

# ── 17-18. onboarding the second box (#213): join from the new box, adopt from a member
J = Box("3333", "nine"); J.claim("ninepassword")
J.env["PIPEOS_CLUSTER_ADVERTISE"] = J.addr
G.see(H, J); H.see(G, J); J.see(G, H)
rc_jw, out_jw = J.cli("join", G.addr, stdin="wrongpassword\n")
j_before = J.doc()
rc_j, out_j = J.cli("join", G.addr, stdin="sixpassword\n")
G.see(H, J); H.see(G, J); J.see(G, H)
tok_gone = not os.path.exists(os.path.join(J.dir, "join-token"))
hashes = {b.name: b.py("print(cluster.members_hash())") for b in (G, H, J)}
check("17 the wizard's join: the wrong member password is refused (rc 1, nothing changes); the right one has the member add this box with a one-time token — all three lists agree; the token is gone afterwards (single use)",
      rc_jw == 1 and "refused" in out_jw and j_before is None
      and rc_j == 0 and "joined via" in out_j and sorted(G.doc()["members"]) == ["1111", "2222", "3333"] == sorted(J.doc()["members"]) == sorted(H.doc()["members"])
      and len(set(hashes.values())) == 1 and tok_gone,
      repr((rc_jw, out_jw[-160:], j_before, rc_j, out_j[-200:], hashes, tok_gone)))

# ── 27. a member the request did not name cannot answer it or close it as done (#301) ──
cj = login(J, "ninepassword"); ch = login(H, "sevenpassword")           # H was re-made after row 21: a new box, a new vault, a new session
sess(J, cj, "POST", "/api/secrets/init", {}); sess(J, cj, "POST", "/api/secrets/phrase-ack", {})
sess(H, ch, "POST", "/api/secrets/init", {}); sess(H, ch, "POST", "/api/secrets/phrase-ack", {})
sess(G, cg, "POST", "/api/secrets/set", {"name": "jobs.j2", "value": "tok-j2"})
rc_rq6, out_rq6 = H.cli("call", "local", "POST", "/api/secrets/request", json.dumps({"name": "jobs.j2"}))
req6 = json.loads(out_rq6.split("\n", 1)[1]).get("request", {}).get("id") if rc_rq6 == 0 else None
st_jr, out_jr = https(H, "POST", "/api/secrets/receive", json.dumps({"id": req6, "name": "jobs.j2", "value": "forged"}).encode(), cert_of=J)   # J saw the offer; it is not a holder
st_jc, out_jc = https(H, "POST", "/api/secrets/requests/close", json.dumps({"id": req6, "state": "done"}).encode(), cert_of=J)
st_ja, out_ja = sess(J, cj, "POST", "/api/secrets/requests/approve", {"id": req6})                                                              # J's owner: does not hold it
still_open = any(q["id"] == req6 for q in sess(H, ch, "GET", "/api/status")[1].get("share_requests", []))
st_ga, out_ga = sess(G, cg, "POST", "/api/secrets/requests/approve", {"id": req6})
st_rv6, out_rv6 = sess(H, ch, "POST", "/api/secrets/reveal", {"name": "jobs.j2", "password": "sevenpassword"})
check("27 a member the request did not name can neither answer it with its own value nor close it as done (403 both; the request stays open); its owner's dashboard cannot approve what it does not hold; the holder's owner can, and the copy is the holder's",
      rc_rq6 == 0 and req6 and st_jr == 403 and "not a holder" in out_jr.get("error", "") and st_jc == 403 and st_ja == 409 and still_open
      and st_ga == 200 and st_rv6 == 200 and out_rv6.get("value") == "tok-j2",
      repr((rc_rq6, out_rq6[-120:], st_jr, out_jr, st_jc, out_jc, st_ja, out_ja, still_open, st_ga, out_ga, st_rv6, out_rv6)))

K = Box("4444", "ten-box")            # unclaimed: no admin conf, no users
G.see(H, J, K)
rc_aw, out_aw = G.cli("adopt", K.addr, "ten", stdin="wrongpassword\n")
k_unclaimed = not os.path.exists(os.path.join(K.dir, "admin_conf"))
rc_a, out_a = G.cli("adopt", K.addr, "ten", stdin="sixpassword\n")
G.see(H, J, K)
k_card = open(os.path.join(K.dir, "card")).read() if os.path.exists(os.path.join(K.dir, "card")) else ""
rc_a2, out_a2 = G.cli("adopt", K.addr, stdin="sixpassword\n")
check("18 adopt from a member: the wrong (member) password is refused before anything touches the new box; the right one claims it with that same password, adds it, names it, prints its recovery phrase once, saves; adopting a claimed Machine is refused",
      rc_aw == 1 and "not this Machine's admin password" in out_aw and k_unclaimed
      and rc_a == 0 and "adopted 4444" in out_a and "recovery phrase" in out_a
      and os.path.exists(os.path.join(K.dir, "admin_conf")) and "4444" in G.doc()["members"] and K.doc() and "1111" in K.doc()["members"]
      and "NAME=ten" in k_card
      and rc_a2 == 1 and "add it with its own password instead" in out_a2,
      repr((rc_aw, out_aw[-160:], k_unclaimed, rc_a, out_a[-300:], k_card, rc_a2, out_a2[-160:])))

# ── 12. identity coverage ───────────────────────────────────────────────
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read().split("\n")
su = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfupdate")).read()
sc = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
ti = open(TLS_INIT).read()
check("12 the member list and the TLS dir are identity: in lbu.list, in pipeos-selfupdate's IDENTITY_PATHS; tls-init re-issues a serverAuth-only cert; selfcheck has the rows (parse, CA present, clientAuth, lbu coverage, members hash vs peers)",
      "+etc/pipeos/cluster.json" in lbu and "+etc/pipeos/tls" in lbu
      and "etc/pipeos/tls etc/pipeos/cluster.json" in su
      and 'grep -q "TLS Web Client Authentication" || need=1' in ti and "serverAuth,clientAuth" in ti
      and "cluster.json does not parse" in sc and "no CA/server cert" in sc and "has no clientAuth" in sc
      and "is not in the lbu include list" in sc and "member list differs on" in sc, "")

for b in (A, B, C, G, H, J, K):
    b.stop()
shutil.rmtree(TMPD, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
