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

# the instance runner: webd with its state redirected into its own dir,
# HTTP and HTTPS both on a free port, the CA/server cert tls-init made
RUNNER = r'''
import importlib.util, os, sys, threading
from http.server import HTTPServer
spec = importlib.util.spec_from_file_location("webd", sys.argv[1]); webd = importlib.util.module_from_spec(spec); spec.loader.exec_module(webd)
d = sys.argv[2]
for k in ("ADMIN_CONF", "SERVICES_CONF", "CARD", "PROVISIONED", "BOOT_REPORT", "USERS_CONF", "SELFUPDATE_CONF", "SUPPORT_CONF", "NAS_CONF"):
    setattr(webd, k, os.path.join(d, k.lower()))
webd.SESS_DIR = os.path.join(d, "sessions"); webd.MDNS_CACHE = os.path.join(d, "peers.json"); webd.MACHINES_ROSTER = os.path.join(d, "machines.json")
webd.FLASH_IMAGE_TXT = os.path.join(d, "image.txt")
webd.SCHEDULE_LOCK = os.path.join(d, "sched.lock")
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
                        PIPEOS_TEST_SAVES=self.saves, PIPEOS_WEB_HTTPS_PORT="0", PIPEOS_WEB_BUNDLE_POLL="0.2", BOX_NAME=name,
                        PIPEOS_REBOOT_CMD="date +%%s.%%N >> %s" % os.path.join(self.dir, "reboots"),
                        PIPEOS_PTS_GLOB=os.path.join(self.dir, "no-pts", "*"))
        r = subprocess.run(["sh", TLS_INIT], env=self.env, capture_output=True, text=True)
        assert r.returncode == 0, "tls-init for %s: %s" % (name, r.stdout + r.stderr)
        self.proc = subprocess.Popen([sys.executable, "-c", RUNNER, os.path.join(WEB, "webd.py"), self.dir],
                                     env=self.env, stdout=subprocess.PIPE, stderr=open(os.path.join(self.dir, "webd.log"), "w"), text=True)
        self.port, self.tls_port = (int(x) for x in self.proc.stdout.readline().split())
        self.addr = "127.0.0.1:%d" % self.tls_port        # cluster.py talks TLS only
        self.env["PIPEOS_WEB_HTTPS_PORT_LOCAL"] = str(self.tls_port)   # `pipeos cluster page` talks to its own :443

    def cli(self, *args, env=None, stdin=None):
        p = subprocess.run([sys.executable, CLUSTER] + list(args), capture_output=True, text=True, env=env or self.env, input=stdin)
        time.sleep(0.6)      # the listeners follow the trust store on disk (bundle_watcher, 0.2 s here)
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
check("5 on one member connection every request is the member's (GET, an admin POST, a GET with a query); the same requests on plain :80 carry no certificate and are 'sign in first'",
      r1.status == 200 and r2.status == 200 and r3.status == 200 and st_p == 401 and body_p["error"] == "sign in first",
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
rc_rmoff, _ = A.cli("remove", "bbbb")
rc_ra, out_ra = A.cli("add", B.addr, stdin="twopassword\n")
A.see(B, C)
st_c, body_c = https(A, "POST", "/api/cluster/members", json.dumps({"cluster": C.doc()}).encode(), cert_of=C)
st_o, body_o = https(A, "POST", "/api/cluster/members", json.dumps({"cluster": C.doc()}).encode(), cert_of=B)
rc_self, out_self = A.cli("remove", "aaaa")
check("8 a re-flashed Machine (new CA, same id) is removed and added again; a list POSTed with a non-member's certificate fails the handshake; a member's list for another cluster id is answered 'other-cluster' and changes nothing; a Machine does not remove itself",
      rc_rmoff == 0 and rc_ra == 0 and sorted(A.doc()["members"]) == ["aaaa", "bbbb"] and A.doc()["members"]["bbbb"]["ca"] == B.ca()
      and st_c == 0 and "handshake" in body_c["error"]
      and st_o == 200 and body_o["result"] == "other-cluster" and sorted(A.doc()["members"]) == ["aaaa", "bbbb"]
      and rc_self == 1 and "does not remove itself" in out_self, repr((rc_rmoff, rc_ra, out_ra[-160:], st_c, body_c, st_o, body_o, rc_self, out_self)))

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

# a member off: grey, and the cluster verdict says so
H.stop()
st_pg2, page2 = json.loads(G.py("st, b, who = cluster.call('local', 'GET', '/api/cluster/page'); print(json.dumps([st, b]))"))
r2 = {r["id"]: r for r in page2["members"]}
check("14 a member that does not answer is a grey row (off, last seen, the reason) and the cluster verdict counts it; the rest of the page still renders",
      st_pg2 == 200 and r2["2222"]["awake"] is False and "unreachable" in r2["2222"].get("error", "") and r2["1111"]["awake"]
      and page2["verdict"] == "1 member off", repr((st_pg2, page2)))
H = Box("2222", "seven-b"); H.claim("sevenpassword")
G.cli("remove", "2222"); G.see(H); G.cli("add", H.addr, stdin="sevenpassword\n"); G.see(H); H.see(G)


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
check("15 'cluster services claude on' with no ids reaches every member: both services confs say on, each box saved its own; 'off 2222 9999' flips only seven and calls 9999 not a member",
      rc_sv == 0 and "1111=ok" in out_sv and "2222=ok" in out_sv and on_g and on_h
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
time.sleep(2.5)
none_yet = not reboots(G) and not reboots(H)
rc_rb, out_rb = G.cli("reboot-all")
time.sleep(3.5)


check("16 'reboot-all' refuses while a member is busy (a job running on six) and names it, without --yes; once idle every member reboots, the others before the box that asked",
      rc_rb0 == 1 and "busy" in out_rb0 and "a job is running" in out_rb0 and none_yet
      and rc_rb == 0 and "2222=rebooting" in out_rb and "1111=rebooting" in out_rb
      and len(reboots(G)) == 1 and len(reboots(H)) == 1 and reboots(H)[0] <= reboots(G)[0],
      repr((rc_rb0, out_rb0[-200:], rc_rb, out_rb[-200:], reboots(G), reboots(H))))

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

for b in (A, B, C, G, H):
    b.stop()
shutil.rmtree(TMPD, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
