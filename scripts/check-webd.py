#!/usr/bin/env python3
"""check-webd: exercise the web wizard's API against a temp-dir fake box.

Runs the real webd.py (state paths redirected into a tempdir) and walks the
claim/session/services surface from outside, over HTTP — the same calls the
browser makes. No root, no network beyond loopback, no box state touched.
CI runs this (a probe nobody runs gates nothing — pipeOS#109).
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBD = os.path.join(REPO, "overlay/usr/local/share/pipeos/web/webd.py")

spec = importlib.util.spec_from_file_location("webd", WEBD)
webd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(webd)

tmp = tempfile.mkdtemp(prefix="check-webd.")
webd.ADMIN_CONF = tmp + "/web-admin.conf"
webd.SERVICES_CONF = tmp + "/services.conf"
webd.CLAUDE_AUTH = tmp + "/claude-auth.env"
webd.CARD = tmp + "/card.conf"
webd.PROVISIONED = tmp + "/provisioned"
webd.SESS_DIR = tmp + "/sessions"
webd.BOOT_REPORT = tmp + "/boot-report"
with open(webd.CARD, "w") as f:
    f.write("NICK=\nROLE=GENERIC\nOWNER_NICK=\n")
with open(webd.BOOT_REPORT, "w") as f:
    f.write("pipeos boot report [test]\nverdict: all green\n")

srv = HTTPServer(("127.0.0.1", 0), webd.Handler)
base = "http://127.0.0.1:%d" % srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

npass = 0
cookie = {}


def req(path, body=None, expect=200, origin=None):
    r = urllib.request.Request(base + path)
    if body is not None:
        r.data = json.dumps(body).encode()
        r.add_header("Content-Type", "application/json")
    if cookie.get("v"):
        r.add_header("Cookie", "session=" + cookie["v"])
    if origin:
        r.add_header("Origin", origin)
    try:
        resp = urllib.request.urlopen(r)
        code, data, hdrs = resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        code, data, hdrs = e.code, e.read(), e.headers
    assert code == expect, "%s -> %d (wanted %d): %s" % (path, code, expect, data[:200])
    sc = hdrs.get("Set-Cookie", "")
    if "session=" in sc and "Max-Age=0" not in sc:
        cookie["v"] = sc.split("session=")[1].split(";")[0]
    return json.loads(data) if data[:1] in (b"{", b"[") else data


def ok(label):
    global npass
    npass += 1
    print("  ok   " + label)


s = req("/api/state")
assert s["claimed"] is False and s["authed"] is False
ok("fresh box reports unclaimed")
assert b"<title>pipeOS</title>" in req("/")
ok("/ serves the app shell")
req("/api/status", expect=401)
ok("status requires a session")
req("/api/claim", {"password": "short"}, expect=400)
ok("short claim password refused")
req("/api/claim", {"password": "hunter22hunter"}, origin="http://evil.example", expect=403)
ok("cross-origin claim refused")
r = req("/api/claim", {"password": "hunter22hunter"})
assert r["ok"] and os.path.exists(webd.PROVISIONED)
ok("claim sets the provisioned marker")
req("/api/claim", {"password": "another-pass"}, expect=403)
ok("second claim refused")
st = req("/api/status")
assert "verdict: all green" in st["boot_report"]
ok("status returns the boot report")
r = req("/api/services", {"claude": True, "pipe": False})
assert r["services"]["claude"] is True and r["services"]["pipe"] is False
with open(webd.SERVICES_CONF) as f:
    assert "SERVICE_CLAUDE=on" in f.read()
ok("service toggle lands in services.conf")
req("/api/name", {"nick": "bad name!"}, expect=400)
ok("hostile nick refused")
cookie["v"] = "0" * 64
req("/api/status", expect=401)
ok("bogus session refused")
cookie["v"] = None
webd.time.sleep = lambda _s: None  # skip the wrong-password tax in CI
req("/api/login", {"password": "wrong"}, expect=403)
ok("wrong password refused")
req("/api/login", {"password": "hunter22hunter"})
req("/api/status", expect=200)
ok("login grants a working session")
req("/api/logout", {})
req("/api/status", expect=401)
ok("logout revokes the session")

print("check-webd: all %d checks passed" % npass)
sys.exit(0)
