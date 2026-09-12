#!/usr/bin/env python3
"""check-tls-public: the Machine's public https address (pipeOS#286), end to end.

The shipped pipeos-tls-public and dns_pipe.sh run against a fake relay (a
loopback HTTP server that verifies every signature the way pipe's relay does
— same canonical bytes, same domain tag — and records what it was asked), a
fake acme.sh on PATH (records its argv, calls the hook the way the real one
does, installs a self-signed certificate on --install-cert and runs the
reloadcmd), a fake vault and a fake pipeos-save. Then the real webd on a
free port: the public name gets the public certificate by SNI and every
other name keeps the box CA's (the cluster's identity, #284); plain http
lands on the public name, except where it must not.

No root, no network beyond loopback, no box state touched. Controls in
check-tls-public-controls.py put the bugs back.
"""
import http.server
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
SCRIPT = os.path.join(REPO, "overlay/usr/local/bin/pipeos-tls-public")
HOOK = os.path.join(REPO, "overlay/usr/local/share/pipeos/acme/dns_pipe.sh")
TLS_INIT = os.path.join(REPO, "overlay/usr/local/bin/pipeos-tls-init")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="tlspub-")
BIN = os.path.join(D, "bin")
os.makedirs(BIN)
MAC = "e86a645ca4e0"
NAME = MAC + ".m.pipe.online"
TLS = os.path.join(D, "tls")
KEY = os.path.join(D, "machine_key")
CONF = os.path.join(D, "public.conf")
STATUS = os.path.join(D, "public.status")
ACME_HOME = os.path.join(D, "acme")
SAVES = os.path.join(D, "saves")
ARGV = os.path.join(D, "acme.argv")

# ── the fakes on PATH ────────────────────────────────────────────────────
with open(os.path.join(BIN, "pipeos-save"), "w") as f:
    f.write("#!/bin/sh\necho save >> %s\n" % SAVES)
with open(os.path.join(BIN, "pipeos-vault"), "w") as f:
    # `set machine_key --bytes CONSUMER` on stdin → the exported file; `export` is a no-op
    f.write("#!/bin/sh\ncase \"$1\" in set) cat > %s; chmod 600 %s;; export) :;; esac\n" % (KEY, KEY))
with open(os.path.join(BIN, "dig"), "w") as f:
    # the hook's propagation poll and the resolver row: answer from the env
    f.write("#!/bin/sh\nfor a; do case \"$a\" in TXT) echo \"\\\"${FAKE_TXT:-}\\\"\";; A) echo \"${FAKE_A:-}\";; esac; done\n")
with open(os.path.join(BIN, "acme.sh"), "w") as f:
    # what the real one does, observably: records argv; on --issue sources the
    # --dns hook and calls dns_<name>_add then _rm like acme.sh's dns mode;
    # on --install-cert writes a self-signed cert for -d to the given paths
    # and runs --reloadcmd; --list names the cert; --cron records
    f.write(r'''#!/bin/sh
echo "$*" >> "$ACME_ARGV"
[ -n "${FAKE_ACME_FAIL:-}" ] && exit 1
verb=""; d=""; hook=""; kf=""; cf=""; reload=""
while [ $# -gt 0 ]; do
  case "$1" in
    --issue|--install-cert|--cron|--list) verb=$1;;
    -d) d=$2; shift;;
    --dns) hook=$2; shift;;
    --key-file) kf=$2; shift;;
    --fullchain-file) cf=$2; shift;;
    --reloadcmd) reload=$2; shift;;
  esac; shift
done
case "$verb" in
  --issue)
    . "$hook"; fn=$(basename "$hook" .sh)
    "${fn}_add" "_acme-challenge.$d" "TOKEN-$$" || exit 1
    "${fn}_rm" "_acme-challenge.$d" "TOKEN-$$";;
  --install-cert)
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 90 -subj "/CN=$d" \
      -addext "subjectAltName=DNS:$d" -keyout "$kf" -out "$cf" >/dev/null 2>&1 || exit 1
    [ -n "$reload" ] && sh -c "$reload";;
  --list) echo "$d";;
  --cron) :;;
esac
exit 0
''')
for b in os.listdir(BIN):
    os.chmod(os.path.join(BIN, b), 0o755)


# ── the fake relay: verifies signatures like pipe's, records calls ───────
class Relay(http.server.BaseHTTPRequestHandler):
    calls = []
    pubkeys = {}
    mode = {"register": 200}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        try:
            j = json.loads(body)
        except ValueError:
            j = {}
        mac = self.headers.get("x-pipe-machine", "")
        pub = j.get("pubkey") if self.path == "/machines/register" else Relay.pubkeys.get(mac)
        ok = pub is not None and verify(pub, self.path, mac, self.headers, body)
        rec = {"path": self.path, "mac": mac, "sig_ok": ok, "body": j}
        Relay.calls.append(rec)
        if not ok:
            return self.answer(401, {"error": "signature does not verify"})
        if self.path == "/machines/register":
            code = Relay.mode["register"]
            if code != 200:
                return self.answer(code, {"error": "this MAC is registered under another key" if code == 409 else "no dns"})
            ip = j.get("lan_ip", "")
            if not ip.startswith(("10.", "192.168.", "172.")):
                return self.answer(400, {"error": "lan_ip must be a private IPv4 address"})
            Relay.pubkeys[mac] = pub
            return self.answer(200, {"name": "%s.m.pipe.online" % j.get("mac", "").lower().replace(":", ""), "mac": mac, "lan_ip": ip})
        if self.path == "/machines/dns":
            allowed = "_acme-challenge.%s.m" % mac
            name = j.get("name", "").rstrip(".")
            if name.endswith(".pipe.online"):
                name = name[: -len(".pipe.online")]
            if j.get("rr") != "TXT" or name != allowed:
                return self.answer(403, {"error": "not your record"})
            return self.answer(200, {"ok": True})
        self.answer(404, {"error": "no"})

    def answer(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def verify(pub_hex, path, mac, headers, body):
    """pipe-protocol's signing_bytes_in(domain, path, subject, ts, nonce, body), ed25519."""
    try:
        raw = bytes.fromhex(pub_hex)
    except ValueError:
        return False
    if len(raw) != 32:
        return False
    der = bytes.fromhex("302a300506032b6570032100") + raw
    pem = ssl.DER_cert_to_PEM_cert(der).replace("CERTIFICATE", "PUBLIC KEY")
    msg = b"\n".join([b"pipe-machine-v1", path.encode(), mac.encode(), headers.get("x-pipe-ts", "").encode(),
                      headers.get("x-pipe-nonce", "").encode()]) + b"\n" + body
    with tempfile.TemporaryDirectory() as td:
        open(td + "/pub.pem", "w").write(pem)
        open(td + "/msg", "wb").write(msg)
        try:
            open(td + "/sig", "wb").write(bytes.fromhex(headers.get("x-pipe-sig", "")))
        except ValueError:
            return False
        p = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", td + "/pub.pem", "-rawin",
                            "-in", td + "/msg", "-sigfile", td + "/sig"], capture_output=True)
    return p.returncode == 0


relay = http.server.HTTPServer(("127.0.0.1", 0), Relay)
RELAY = "http://127.0.0.1:%d" % relay.server_address[1]
threading.Thread(target=relay.serve_forever, daemon=True).start()

ENV = dict(os.environ, PATH=BIN + ":" + os.environ.get("PATH", ""),
           PIPEOS_PUBLIC_CONF=CONF, PIPEOS_TLS_DIR=TLS, PIPEOS_ACME_HOME=ACME_HOME, PIPEOS_ACME_BIN="acme.sh",
           PIPEOS_MACHINE_KEY=KEY, PIPEOS_VAULT_BIN=os.path.join(BIN, "pipeos-vault"),
           PIPEOS_SAVE_BIN=os.path.join(BIN, "pipeos-save"), PIPEOS_PUBLIC_STATUS=STATUS, PIPEOS_PUBLIC_HOOK=HOOK,
           PIPEOS_PUBLIC_MAC=MAC, PIPEOS_PUBLIC_IP="192.168.254.77", PIPEOS_PUBLIC_LOCK=os.path.join(D, "lock"),
           PIPEOS_PUBLIC_LOG=os.path.join(D, "tls.log"), ACME_ARGV=ARGV, FAKE_TXT="TOKEN", FAKE_A="192.168.254.77")
os.makedirs(TLS)
open(CONF, "w").write("PUBLIC=on\nPUBLIC_RELAY=%s\nPUBLIC_ACME=letsencrypt_test\nPUBLIC_NAME=\n" % RELAY)


def tls(*args, env=None):
    p = subprocess.run(["sh", SCRIPT] + list(args), capture_output=True, text=True, env=env or ENV)
    return p.returncode, p.stdout + p.stderr


def conf():
    return dict(l.split("=", 1) for l in open(CONF).read().split("\n") if "=" in l and not l.startswith("#"))


def status():
    try:
        return json.load(open(STATUS))
    except (OSError, ValueError):
        return {}


def nsaves():
    try:
        return len(open(SAVES).read().split())
    except OSError:
        return 0


# ── 1. issue, end to end ─────────────────────────────────────────────────
rc, out = tls("issue")
argv = open(ARGV).read().split("\n") if os.path.exists(ARGV) else []
reg = [c for c in Relay.calls if c["path"] == "/machines/register"]
dns = [c for c in Relay.calls if c["path"] == "/machines/dns"]
check("1 'issue': the machine key is minted into the vault (0600), the registration is signed with it (the relay verifies the pipe-machine-v1 bytes: MAC, ts, nonce, body), the name lands in public.conf, acme.sh is asked for that name with the hook and the ACME server from the conf, the hook's add/rm reach the relay signed and for this Machine's own challenge record only, the certificate is installed, the status says ready, one save",
      rc == 0 and os.path.exists(KEY) and oct(os.stat(KEY).st_mode & 0o777) == "0o600"
      and len(reg) == 1 and reg[0]["sig_ok"] and reg[0]["mac"] == MAC and reg[0]["body"]["lan_ip"] == "192.168.254.77"
      and conf().get("PUBLIC_NAME") == NAME
      and any(("--issue" in a and "-d " + NAME in a and "--dns " + HOOK in a and "--server letsencrypt_test" in a) for a in argv)
      and [c["body"]["op"] for c in dns] == ["add", "rm"] and all(c["sig_ok"] for c in dns)
      and all(c["body"]["name"] == "_acme-challenge." + NAME for c in dns)
      and os.path.exists(TLS + "/public.crt") and oct(os.stat(TLS + "/public.key").st_mode & 0o777) == "0o600"
      and status().get("ready") is True and status().get("name") == NAME and status().get("days", 0) > 80
      and nsaves() == 1 and "is live" in out,
      repr((rc, out[-300:], reg, dns, argv, conf(), status(), nsaves())))

# ── 2. status, renew ─────────────────────────────────────────────────────
rc_s, out_s = tls("status")
n0 = len(reg)
rc_r, out_r = tls("renew")
argv2 = open(ARGV).read().split("\n")
reg2 = [c for c in Relay.calls if c["path"] == "/machines/register"]
check("2 'status' prints the name, the certificate's end and days left, whether the LAN resolves us, and the address; 'renew' re-registers (the A record follows the lease) and runs acme.sh --cron, saving nothing itself",
      rc_s == 0 and NAME in out_s and "days left" in out_s and "LAN resolves: yes" in out_s and "https://" + NAME + "/" in out_s
      and rc_r == 0 and len(reg2) == n0 + 1 and any("--cron" in a for a in argv2) and nsaves() == 1,
      repr((rc_s, out_s, rc_r, out_r, len(reg2) - n0, nsaves())))

# ── 3. the LAN that will not resolve us ─────────────────────────────────
rc_n, out_n = tls("status", env=dict(ENV, FAKE_A="1.2.3.4"))
res_no = status().get("resolves")
tls("status")   # back to yes
check("3 when this LAN's resolver answers the name with another address (rebind protection), status says so and the status file carries resolves=no (the redirect reads it)",
      rc_n == 0 and "LAN resolves: no" in out_n and res_no == "no" and status().get("resolves") == "yes", repr((out_n, res_no)))

# ── 4. refusals: another key, an unreachable relay, acme failing ────────
Relay.mode["register"] = 409
n_argv = len(open(ARGV).read().split("\n"))
rc_9, out_9 = tls("issue")
Relay.mode["register"] = 200
n_after_409 = len(open(ARGV).read().split("\n"))
open(CONF + ".unreach", "w").write("PUBLIC=on\nPUBLIC_RELAY=http://127.0.0.1:1\nPUBLIC_ACME=letsencrypt_test\nPUBLIC_NAME=%s\n" % NAME)
rc_ur, out_ur = tls("issue", env=dict(ENV, PIPEOS_PUBLIC_CONF=CONF + ".unreach", PIPEOS_PUBLIC_STATUS=STATUS + ".u"))
rc_af, out_af = tls("issue", env=dict(ENV, FAKE_ACME_FAIL="1", PIPEOS_PUBLIC_STATUS=STATUS + ".a"))
check("4 a MAC the relay has under another key: rc 1, the reason, acme.sh never called; a relay that is down: rc 1 'unreachable'; acme.sh failing: rc 1 with the log's name — and the last error is in the status file each time",
      rc_9 == 1 and "another key" in out_9 and n_after_409 == n_argv
      and rc_ur == 1 and "unreachable" in out_ur and "unreachable" in json.load(open(STATUS + ".u")).get("error", "")
      and rc_af == 1 and "could not get a certificate" in out_af and "certificate" in json.load(open(STATUS + ".a")).get("error", ""),
      repr((rc_9, out_9[-200:], rc_ur, out_ur[-200:], rc_af, out_af[-200:], open(STATUS + ".u").read() if os.path.exists(STATUS + ".u") else "no .u", open(STATUS + ".a").read() if os.path.exists(STATUS + ".a") else "no .a", len(open(ARGV).read().split("\n")), n_argv)))

# ── 5. on / off ─────────────────────────────────────────────────────────
s0 = nsaves()
rc_off, out_off = tls("off")
off_conf, off_status = conf().get("PUBLIC"), status().get("on")
rc_i_off, out_i_off = tls("issue")
rc_on, out_on = tls("on")
check("5 'off' writes PUBLIC=off, the status says off, saves; 'issue' refuses while off; 'on' restores and saves",
      rc_off == 0 and off_conf == "off" and off_status is False and rc_i_off == 1 and "off" in out_i_off
      and rc_on == 0 and conf().get("PUBLIC") == "on" and status().get("on") is True and nsaves() == s0 + 2,
      repr((rc_off, out_off, off_conf, off_status, rc_i_off, out_i_off, rc_on, conf(), nsaves() - s0)))

# ── 6-9. webd: SNI, the redirect, the reader, hot reload ────────────────
BOX = os.path.join(D, "box")
os.makedirs(BOX)
subprocess.run(["sh", TLS_INIT], env=dict(ENV, PIPEOS_TLS_DIR=TLS, PIPEOS_TLS_HOST="pipeos-a4e0"), capture_output=True)
RUNNER = r'''
import importlib.util, os, sys, threading, json
from http.server import HTTPServer
spec = importlib.util.spec_from_file_location("webd", sys.argv[1]); webd = importlib.util.module_from_spec(spec); spec.loader.exec_module(webd)
d = sys.argv[2]
for k in ("SERVICES_CONF", "CARD", "PROVISIONED", "BOOT_REPORT", "USERS_CONF", "SELFUPDATE_CONF", "SUPPORT_CONF", "NAS_CONF"):
    setattr(webd, k, os.path.join(d, k.lower()))
webd.ADMIN_CONF = os.path.join(d, "admin_conf")
webd.SESS_DIR = os.path.join(d, "sessions"); webd.MDNS_CACHE = os.path.join(d, "peers.json"); webd.MACHINES_ROSTER = os.path.join(d, "machines.json")
webd.FLASH_IMAGE_TXT = os.path.join(d, "image.txt")
webd.TLS_DIR = os.environ["PIPEOS_TLS_DIR"]; webd.CA_CRT = webd.TLS_DIR + "/ca.crt"; webd.SRV_CRT = webd.TLS_DIR + "/server.crt"; webd.SRV_KEY = webd.TLS_DIR + "/server.key"
webd.PUB_CRT = webd.TLS_DIR + "/public.crt"; webd.PUB_KEY = webd.TLS_DIR + "/public.key"
webd.PUBLIC_CONF = os.environ["PIPEOS_PUBLIC_CONF"]; webd.PUBLIC_STATUS = os.environ["PIPEOS_PUBLIC_STATUS"]
webd.TLS_PUBLIC_BIN = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "../../../bin/pipeos-tls-public")
webd.lanid.mac4 = lambda iface=None: "a4e0"
webd.box_hostname = lambda: "pipeos-a4e0"
open(webd.CARD, "w").write("NICK=\nNAME=zero\nROLE=GENERIC\n")
srv = HTTPServer(("127.0.0.1", 0), webd.Handler)
https = webd.start_https(init=False)
print(srv.server_address[1], https.server_address[1], flush=True)
srv.serve_forever()
'''
proc = subprocess.Popen([sys.executable, "-c", RUNNER, os.path.join(WEB, "webd.py"), BOX],
                        env=dict(ENV, PIPEOS_CLUSTER_JSON=os.path.join(BOX, "cluster.json"), PIPEOS_CLUSTER_BUNDLE=os.path.join(BOX, "bundle.pem"),
                                 PIPEOS_CLUSTER_STATUS=os.path.join(BOX, "cluster.status"), PIPEOS_WEB_HTTPS_PORT="0", PIPEOS_WEB_BUNDLE_POLL="0.2"),
                        stdout=subprocess.PIPE, stderr=open(os.path.join(BOX, "webd.log"), "w"), text=True)
PORT, TLS_PORT = (int(x) for x in proc.stdout.readline().split())


def served_cert(sni):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", TLS_PORT), timeout=5) as s:
        with ctx.wrap_socket(s, server_hostname=sni) as ss:
            return ssl.DER_cert_to_PEM_cert(ss.getpeercert(binary_form=True))


def fp(path):
    return open(path).read()


def http_(method, path, host="zero.local", body=None):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=body, method=method)
    r.add_header("Host", host)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(r, timeout=5)
        return resp.status, resp.headers.get("Location", ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", ""), e.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


urllib.request.install_opener(urllib.request.build_opener(NoRedirect))

# unclaimed first: no redirect, whatever the certificate says
st_u, loc_u, _ = http_("GET", "/")
open(os.path.join(BOX, "admin_conf"), "w").write("HASH='x'\n")
time.sleep(0.6)
pub_pem, box_pem = served_cert(NAME), served_cert(None)
local_pem = served_cert("pipeos-a4e0.local")
check("6 SNI: the public name is answered with the public certificate; no SNI, and the chassis .local name, get the box CA's certificate (the cluster identity, untouched)",
      pub_pem == fp(TLS + "/public.crt") and box_pem == fp(TLS + "/server.crt") and local_pem == box_pem and pub_pem != box_pem,
      repr((pub_pem[:60], box_pem[:60])))

st_r, loc_r, _ = http_("GET", "/?x=1")
st_p, loc_p, _ = http_("POST", "/api/save", body=b"{}")
st_s, _, body_s = http_("GET", "/api/state")
st_l, _, _ = http_("GET", "/api/lobby")
st_t, _, body_t = http_("GET", "/api/tls-public")
st_c, _, _ = http_("GET", "/ca.crt")
st_h, _, _ = http_("GET", "/", host=NAME)
check("7 the redirect: on plain http a claimed Machine sends the dashboard to https://<name>/ with the path (302; 307 for a POST); the lobby's JSON, /api/state, /api/tls-public and the CA download stay on http; a request that already names the public host is not redirected; an unclaimed Machine is never redirected; /api/state carries public_host",
      st_u == 200 and st_r == 302 and loc_r == "https://%s/?x=1" % NAME and st_p == 307 and loc_p.startswith("https://" + NAME + "/api/save")
      and st_s == 200 and json.loads(body_s)["public_host"] == NAME and st_l == 200 and st_t == 200 and json.loads(body_t)["ready"] is True
      and st_c == 200 and st_h == 200,
      repr((st_u, st_r, loc_r, st_p, loc_p, st_s, st_l, st_t, st_c, st_h)))

# the LAN that cannot resolve us, and PUBLIC=off: no redirect either way
st = status(); st["resolves"] = "no"; json.dump(st, open(STATUS, "w"))
st_no, _, _ = http_("GET", "/")
st["resolves"] = "yes"; json.dump(st, open(STATUS, "w"))
_c = open(CONF).read(); open(CONF, "w").write(_c.replace("PUBLIC=on", "PUBLIC=off"))
time.sleep(0.6)
st_off, _, body_off = http_("GET", "/api/state")
st_off2, _, _ = http_("GET", "/")
_c = open(CONF).read(); open(CONF, "w").write(_c.replace("PUBLIC=off", "PUBLIC=on"))
time.sleep(0.6)
check("7b no redirect while this LAN's resolver refuses the name, and none with public https off (public_host is then empty)",
      st_no == 200 and st_off == 200 and json.loads(body_off)["public_host"] == "" and st_off2 == 200, repr((st_no, st_off, body_off[:80], st_off2)))

# hot reload: a renewed certificate is served without anyone restarting webd
old = fp(TLS + "/public.crt")
subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "90",
                "-subj", "/CN=" + NAME, "-addext", "subjectAltName=DNS:" + NAME, "-keyout", TLS + "/public.key.new", "-out", TLS + "/public.crt.new"],
               capture_output=True)
# the way acme.sh's --install-cert and reload land it: key first, then the cert
os.replace(TLS + "/public.key.new", TLS + "/public.key"); os.replace(TLS + "/public.crt.new", TLS + "/public.crt")
time.sleep(1.5)
new = served_cert(NAME)
check("8 a renewed public certificate is picked up from disk within a second — the daily renewal never restarts anything the owner notices",
      new == fp(TLS + "/public.crt") and new != old, repr((new[:40], old[:40], open(os.path.join(BOX, "webd.log")).read())))

# mdns: the public name rides the TXT record from the status file
sys.path.insert(0, WEB)
os.environ["PIPEOS_PUBLIC_STATUS"] = STATUS
os.environ["PIPEOS_MDNS_IDENT"] = os.path.join(D, "ident.json")
json.dump({"hostname": "pipeos-a4e0", "mac4": "a4e0", "claimed": True, "name": "zero", "p": NAME}, open(os.path.join(D, "ident.json"), "w"))
import importlib.util as _iu
_spec = _iu.spec_from_file_location("mdnsd", os.path.join(WEB, "mdnsd.py")); mdnsd = _iu.module_from_spec(_spec); _spec.loader.exec_module(mdnsd)
txt = mdnsd._txt(mdnsd.read_ident())
check("9 the responder advertises the public name as TXT p (the lobby links to it); public_name() reads the status file",
      txt.get("p") == NAME and mdnsd.public_name() == NAME, repr((txt, mdnsd.public_name())))

# ── 10. identity coverage and the wiring ───────────────────────────────
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read().split("\n")
su = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfupdate")).read()
world = open(os.path.join(REPO, "overlay/etc/apk/world")).read().split("\n")
sc = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
disp = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
vault = open(os.path.join(WEB, "vault.py")).read()
daily = os.path.join(REPO, "overlay/etc/periodic/daily/pipeos-tls-public")
check("10 persistence and wiring: public.conf, acme.sh's home and the daily job are in lbu.list and IDENTITY_PATHS; acme.sh is in world; the daily job is executable; `pipeos tls public` dispatches; the vault exports machine_key as a 0600 file; selfcheck has the rows (no name, no cert, expiring, expired, resolver, lbu coverage)",
      all(x in lbu for x in ("+etc/pipeos/public.conf", "+etc/pipeos/acme", "+etc/periodic/daily/pipeos-tls-public"))
      and "etc/pipeos/public.conf etc/pipeos/acme" in su and "acme.sh" in world
      and os.access(daily, os.X_OK) and 'exec /usr/local/bin/pipeos-tls-public "$@"' in disp
      and '"machine_key": "tls-public"' in vault and 'files["machine_key"]' in vault
      and all(x in sc for x in ("no name yet", "no certificate is installed", "expires in", "has EXPIRED", "rebind protection", "is not in the lbu include list")),
      "")

proc.kill(); proc.wait()
relay.shutdown()
shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
