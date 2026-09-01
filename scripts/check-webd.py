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
webd.STREAM_CONF = tmp + "/stream.conf"
webd.USERS_CONF = tmp + "/users.json"
webd.TERMINALS_CONF = tmp + "/terminals.conf"
webd.ASSISTANT_CONF = tmp + "/assistant.conf"
webd.SELFUPDATE_CONF = tmp + "/selfupdate.conf"
webd.UPDATE_STAMP = tmp + "/selfupdate.applied"
webd.LOG_ALLOW = {k: tmp + "/" + k + ".log" for k in webd.LOG_ALLOW}
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
req("/api/login", {"password": "hunter22hunter"})
req("/api/logs?name=../../etc/shadow", expect=400)
req("/api/logs?name=nope", expect=400)
ok("logs endpoint refuses names off the allowlist")
r = req("/api/logs?name=selfcheck")
assert "text" in r
ok("logs endpoint serves an allowlisted tail")
# every free-text stream field is shell-sourced — each must refuse quote injection
for bad in ("src", "url", "args", "bitrate"):
    req("/api/stream-config", {bad: "x'; rm -rf /"}, expect=400)
# and inside a provider target (url/key/name)
req("/api/stream-config", {"targets": [{"url": "x'; rm -rf /"}]}, expect=400)
req("/api/stream-config", {"targets": [{"key": "k'; rm -rf /"}]}, expect=400)
req("/api/stream-config", {"targets": [{"br": "loud"}]}, expect=400)
ok("stream config refuses quote injection on every field, targets included")
req("/api/stream-config", {"mode": "browser", "res": "not-a-size"}, expect=400)
req("/api/stream-config", {"mode": "browser", "fps": "abc"}, expect=400)
req("/api/stream-config", {"mode": "browser", "bitrate": "loud"}, expect=400)
ok("stream config validates resolution, fps, and bitrate")
# positive round-trip: a browser-mode multi-provider config persists; keys hidden
r = req("/api/stream-config", {
    "mode": "browser", "url": "https://basho.dev", "res": "1920x1080", "fps": "30",
    "vaapi": True, "bitrate": "3500k",
    "targets": [
        {"name": "YouTube", "url": "rtmp://a.rtmp.youtube.com/live2", "key": "yt-secret", "on": True, "br": "9000k"},
        {"name": "Twitch", "url": "rtmp://live.twitch.tv/app", "key": "tw-secret", "on": True},
    ]})
assert r["ok"]
g = req("/api/stream")
assert g["mode"] == "browser" and g["url"] == "https://basho.dev" and g["vaapi"] is True, g
assert len(g["targets"]) == webd.STREAM_MAX_TARGETS, g
t1, t2 = g["targets"][0], g["targets"][1]
assert t1["name"] == "YouTube" and t1["url"] == "rtmp://a.rtmp.youtube.com/live2" and t1["on"] is True, t1
assert t1["br"] == "9000k" and t2["br"] == "", (t1, t2)  # per-target bitrate round-trips
assert t1["key_set"] is True and t2["key_set"] is True, g["targets"]
assert all("key" not in t for t in g["targets"]), "raw keys must never be returned"
with open(webd.STREAM_CONF) as f:
    conf = f.read()
assert "STREAM_T1_KEY='yt-secret'" in conf and "STREAM_T2_KEY='tw-secret'" in conf
assert "STREAM_T1_NAME='YouTube'" in conf and "STREAM_T2_ON='1'" in conf
ok("stream config round-trips multi-provider targets and hides the keys")
# a blank key with keep_key preserves what was saved for that provider slot
req("/api/stream-config", {"mode": "browser", "url": "https://basho.dev",
    "targets": [{"name": "YouTube", "url": "rtmp://a.rtmp.youtube.com/live2", "on": True, "keep_key": True}]})
g2 = req("/api/stream")
assert g2["targets"][0]["key_set"] is True, g2["targets"]
ok("blank provider key with keep_key preserves the saved key")
# assistant terminal: guards injection + port, and refuses an open terminal
req("/api/assistant-config", {"password": "x'; rm -rf /"}, expect=400)
req("/api/assistant-config", {"port": "notaport", "password": "p"}, expect=400)
req("/api/assistant-config", {"user": "admin"}, expect=400)
ok("assistant config guards injection/port and refuses a passwordless terminal")
r = req("/api/assistant-config", {"password": "hunter2pass", "port": "7681"})
assert r["ok"]
a = req("/api/assistant")
assert a["pass_set"] is True and a["port"] == "7681" and "password" not in a, a
# backend selection: allowlisted, install-checked, and a backend-only flip
# keeps the saved password and port
req("/api/assistant-config", {"backend": "skynet"}, expect=400)
req("/api/assistant-config", {"backend": "agy"}, expect=400)  # not installed here
assert a["backend"] == "claude" and any(b["id"] == "hermes" for b in a["backends"])
if any(b["id"] == "hermes" and b["installed"] for b in a["backends"]):
    req("/api/assistant-config", {"backend": "hermes", "keep_pass": True})
    a2 = req("/api/assistant")
    assert a2["backend"] == "hermes" and a2["pass_set"] is True and a2["port"] == "7681", a2
    req("/api/assistant-config", {"backend": "claude", "keep_pass": True})
ok("assistant backend allowlist + lossless backend-only flips")
with open(webd.ASSISTANT_CONF) as f:
    assert "ASSISTANT_PASS='hunter2pass'" in f.read()
ok("assistant config round-trips and hides the password")
r = req("/api/update")
assert r["state"] in ("self-update disabled", "origin unreachable", "current", "update available", "unknown")
ok("update endpoint reports a coherent state")
# metrics: live numbers and history series come back shaped, never 500
m = req("/api/metrics")
assert "load1" in m and "rx_total" in m
h = req("/api/metrics-history?span=1h")
assert all(k in h for k in ("interval_s", "cpu", "mem_pct", "rx_bps"))
h = req("/api/metrics-history?span=bogus")  # unknown span falls back, not 500
assert "cpu" in h
ok("metrics + history endpoints answer with shaped series")
# files: paths are root-prefixed (work/…, ext/<dev>/…); "" lists the drives
webd.FILES_WORK = tmp + "/work"
webd.FILES_EXT_BASE = tmp + "/ext"
os.makedirs(tmp + "/work/sub", exist_ok=True)
with open(tmp + "/work/hello.txt", "w") as f:
    f.write("hi")
os.symlink("/etc", tmp + "/work/escape")
req("/api/files?path=work/../../etc", expect=400)
req("/api/files?path=work/escape", expect=400)
req("/api/files?path=etc", expect=400)          # unknown root
req("/api/files?path=ext/nope", expect=400)     # unmounted drive
req("/api/file-dl?path=work/../../etc/passwd", expect=400)
req("/api/file-op", {"op": "delete", "path": "work/../hello"}, expect=400)
ok("files endpoints refuse traversal, symlink escapes, unknown roots")
r = req("/api/files?path=")
assert r["roots"] is True and [d["name"] for d in r["dirs"]] == ["work"]
r = req("/api/files?path=work")
assert [d["name"] for d in r["dirs"]] == ["sub"]
assert "hello.txt" in [f["name"] for f in r["files"]]
ok("virtual root lists drives; work listing returns dirs and files")
os.unlink(tmp + "/work/escape")
req("/api/file-op", {"op": "mkdir", "path": "work/sub", "name": "nested"})
req("/api/file-op", {"op": "mkdir", "path": "work", "name": "../up"}, expect=400)
req("/api/file-op", {"op": "mkdir", "path": "", "name": "x"}, expect=400)  # virtual root
req("/api/file-op", {"op": "move", "path": "work/hello.txt", "dest": "work/sub"})
assert req("/api/files?path=work/sub")["files"][0]["name"] == "hello.txt"
req("/api/file-op", {"op": "rename", "path": "work/sub/hello.txt", "dest": "work/sub/hi.txt"})
req("/api/file-op", {"op": "delete", "path": "work/sub"}, expect=400)  # non-empty, no recursive
req("/api/file-op", {"op": "delete", "path": "work/sub", "recursive": True})
assert req("/api/files?path=work")["dirs"] == []
ok("file ops: mkdir/move/rename/delete with guards")
# folder-as-tarball download: jailed like everything else, streams gzip
os.makedirs(tmp + "/work/tardir", exist_ok=True)
with open(tmp + "/work/tardir/f.txt", "w") as f:
    f.write("tarme")
req("/api/file-tar?path=work/../../etc", expect=400)
req("/api/file-tar?path=work/nope", expect=404)
data = req("/api/file-tar?path=work/tardir")
assert data[:2] == b"\x1f\x8b", "not gzip: %r" % data[:8]
req("/api/file-op", {"op": "delete", "path": "work/tardir", "recursive": True})
ok("folder tar.gz download streams and stays jailed")
# health: the read-only re-check runs the selfcheck binary and reports verdict
webd.SELFCHECK_BIN = tmp + "/selfcheck-stub"
with open(webd.SELFCHECK_BIN, "w") as f:
    f.write("#!/bin/sh\necho 'ok all fine'\necho 'verdict: all green'\n")
os.chmod(webd.SELFCHECK_BIN, 0o755)
h = req("/api/health")
assert h["ok"] is True and h["verdict"] == "all green"
ok("health re-check runs selfcheck and extracts the verdict")
# disks: inventory answers, and every system disk refuses ops
r = req("/api/disks")
assert isinstance(r["disks"], list)
req("/api/disk-op", {"op": "mount", "dev": "no/such"}, expect=400)
req("/api/disk-op", {"op": "mount", "dev": "nodev999"}, expect=400)
for d in r["disks"]:
    if d["protected"]:
        req("/api/disk-op", {"op": "format", "dev": d["parts"][0]["dev"]}, expect=400)
        break
ok("disk inventory lists; protected/system devices refuse every op")
# backup: dest validation, then a real rsync round-trip against redirected
# sources and a fake mounted external (ismount patched for the tempdir)
req("/api/backup", {"dest": "work"}, expect=400)
req("/api/backup", {"dest": "ext/ghost"}, expect=400)
os.makedirs(tmp + "/ext/sdx1", exist_ok=True)
os.makedirs(tmp + "/srcwork/sub", exist_ok=True)
with open(tmp + "/srcwork/data.txt", "w") as f:
    f.write("precious")
webd.BACKUP_SRCS = ((tmp + "/srcwork/", "work"),)
_real_ismount = webd.os.path.ismount
webd.os.path.ismount = lambda p: p.startswith(tmp + "/ext/") or _real_ismount(p)
r = req("/api/backup", {"dest": "ext/sdx1"})
assert r["started"]
import select as _sel  # time.sleep is no-op-patched above; select isn't
for _ in range(100):
    if not req("/api/backup")["running"]:
        break
    _sel.select([], [], [], 0.1)
b = req("/api/backup")
assert b["ok"] is True, b
found = False
for root, dirs, files in os.walk(tmp + "/ext/sdx1/pipeos-backup"):
    if "data.txt" in files:
        found = True
assert found, "backup did not copy the source file"
assert b["exts"][0]["last"] is not None
webd.os.path.ismount = _real_ismount
ok("backup validates dest and mirrors sources onto the external")
raw = urllib.request.Request(base + "/api/file-up?path=work&name=up.bin", data=b"x" * 100)
raw.add_header("Cookie", "session=" + cookie["v"])
raw.add_header("Origin", base)
assert json.loads(urllib.request.urlopen(raw).read())["ok"]
assert req("/api/files?path=work")["files"][0]["size"] == 100
raw = urllib.request.Request(base + "/api/file-up?path=work&name=../evil", data=b"x")
raw.add_header("Cookie", "session=" + cookie["v"])
raw.add_header("Origin", base)
try:
    urllib.request.urlopen(raw)
    assert False, "traversal upload accepted"
except urllib.error.HTTPError as e:
    assert e.code == 400
ok("upload streams a raw body and refuses hostile names")
# on-box docs: index lists the seeded pages in display order, a page comes
# back as raw markdown, and hostile slugs bounce before touching the fs
d = req("/api/docs")
slugs = [p["slug"] for p in d["pages"]]
assert slugs and slugs[0] == "getting-started" and "fence" in slugs, slugs
assert all(p["title"] for p in d["pages"]), d
page = req("/api/docs/getting-started")
assert page.lstrip()[:1] == b"#", page[:40]
req("/api/docs/No_Such", expect=400)
req("/api/docs/nope", expect=404)
ok("docs index and pages serve; hostile slugs refused")
# pipe: pref allowlist + nick validation (no daemon here, so only the guards)
req("/api/pipe-set", {"pref": "evil_pref", "value": True}, expect=400)
req("/api/pipe-contact", {"nick": "bad nick!"}, expect=400)
ok("pipe endpoints validate pref names and nicks")
# stream boot flag + configure-implies-enable: posting a config with a live
# target flips services.conf stream=on by itself; boot:false lands as
# STREAM_BOOT='0' and round-trips
with open(webd.SERVICES_CONF) as f:
    assert "SERVICE_STREAM=on" in f.read()  # earlier config post enabled it
r = req("/api/stream-config", {"mode": "browser", "url": "https://basho.dev",
        "boot": False,
        "targets": [{"name": "YouTube", "url": "rtmp://a.rtmp.youtube.com/live2", "on": True, "keep_key": True}]})
assert r["ok"]
with open(webd.STREAM_CONF) as f:
    assert "STREAM_BOOT='0'" in f.read()
assert req("/api/stream")["boot"] is False
ok("configure implies enable; boot opt-out round-trips")
# a pre-multi-user session file (bare timestamp body) must read as the admin,
# not crash the handler — json.loads parses a timestamp fine, as an int
import time as _t
with open(os.path.join(webd.SESS_DIR, "ab" * 32), "w") as f:
    f.write(str(int(_t.time())))
old_cookie = cookie["v"]
cookie["v"] = "ab" * 32
assert req("/api/status")["user"] == "admin"
ok("legacy timestamp-format session reads as admin, not a crash")
cookie["v"] = old_cookie
# users: multi-user login, roles, and the lockout guards
req("/api/login", {"username": "ghost", "password": "hunter22hunter"}, expect=403)
ok("unknown username refused with the same flat error")
req("/api/users/add", {"name": "../x", "password": "hunter22hunter"}, expect=400)
req("/api/users/add", {"name": "Bad Name", "password": "hunter22hunter"}, expect=400)
req("/api/users/add", {"name": "root", "password": "hunter22hunter"}, expect=400)
req("/api/users/add", {"name": "shorty", "password": "short"}, expect=400)
req("/api/users/add", {"name": "sudoer", "password": "hunter22hunter", "sudo": True}, expect=400)
ok("users/add refuses hostile names, short passwords, sudo without unix")
r = req("/api/users/add", {"name": "peek", "password": "peekpassword", "role": "viewer"})
assert r["ok"]
assert "$6$" not in json.dumps(req("/api/users"))
ok("viewer created; /api/users never leaks hashes")
req("/api/users/del", {"name": "admin"}, expect=400)  # self-delete
req("/api/users/set", {"name": "admin", "role": "viewer"}, expect=400)  # last admin
ok("self-delete and last-admin demotion refused")
admin_cookie = cookie["v"]
cookie["v"] = None
req("/api/login", {"username": "peek", "password": "peekpassword"})
req("/api/status", expect=200)
req("/api/services", {"stream": False}, expect=403)
req("/api/users", expect=403)
r = req("/api/password", {"current": "peekpassword", "new": "peekpassword2"})
assert r["ok"]
assert req("/api/docs")["pages"], "viewer must be able to read the docs"
ok("viewer reads but cannot mutate; can change own password")
cookie["v"] = admin_cookie
req("/api/users/set", {"name": "peek", "disabled": True})
saved_admin = cookie["v"]
cookie["v"] = None
req("/api/login", {"username": "peek", "password": "peekpassword2"}, expect=403)
cookie["v"] = saved_admin
req("/api/users/del", {"name": "peek"})
ok("disable locks the account out; delete removes it")
req("/api/logout", {})
req("/api/status", expect=401)
ok("logout revokes the session")

print("check-webd: all %d checks passed" % npass)
sys.exit(0)
