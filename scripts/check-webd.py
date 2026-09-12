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
webd.CARD = tmp + "/card.conf"
webd.PROVISIONED = tmp + "/provisioned"
webd.SESS_DIR = tmp + "/sessions"
webd.BOOT_REPORT = tmp + "/boot-report"
webd.STREAM_CONF = tmp + "/stream.conf"
webd.USERS_CONF = tmp + "/users.json"
webd.TERMINALS_CONF = tmp + "/terminals.conf"
webd.ASSISTANT_CONF = tmp + "/assistant.conf"
webd.SELFUPDATE_CONF = tmp + "/selfupdate.conf"
webd.NAS_CONF = tmp + "/nas.conf"
webd.SUPPORT_CONF = tmp + "/support.conf"
# the vault (#244): the sealed file, its export dir and the hardware
# identity all live in the tempdir; a small PBKDF2 count for speed
webd.VAULT = webd.vault.VAULT_FILE = tmp + "/vault.sealed"
webd.SECRETS_DIR = webd.vault.RUN_DIR = tmp + "/secrets"
webd.vault.ETC = tmp
webd.vault.ITER = 1500
webd.vault.ident = lambda: {"mac": "aa:bb:cc:dd:7f:3a", "serial": "PC1", "product": "Test Box"}
webd.VAULT_PHRASE = tmp + "/vault-phrase"
# scheduled runs (#242): the job list, the runtime state, a stub runner
webd.SCHEDULE_CONF = tmp + "/schedule.json"
webd.SCHEDULE_STATE_DIR = tmp + "/sched"
webd.SCHEDULE_LOCK = tmp + "/schedule.lock"
webd.SCHEDULE_LOGDIR = tmp + "/logs"
webd.LEDGER_PAUSED = tmp + "/paused"
# the usage ledger (#246): rows, transcripts, rates, the conf with the cap,
# and the dashboard chat's own session id, all in the tempdir
webd.LEDGER_DIR = tmp + "/ledger"
webd.LEDGER_PAUSED = tmp + "/ledger/paused"
webd.LEDGER_TRANSCRIPTS = tmp + "/projects"
webd.LEDGER_CONF = tmp + "/pipebox.conf"
webd.LEDGER_SESSIONS = tmp + "/psessions"
webd.WEBCHAT_DIR = tmp + "/webchat"
webd.WEBCHAT_SID = tmp + "/webchat/.dashboard-sid"
os.makedirs(tmp + "/ledger"); os.makedirs(tmp + "/projects"); os.makedirs(tmp + "/webchat")
with open(webd.LEDGER_CONF, "w") as f:
    f.write('NICK=""\nOWNER_NICK=""\nMONTHLY_CAP_USD=""\n')
webd.SCHEDULE_RUN_BIN = tmp + "/sched-run-stub"
with open(webd.SCHEDULE_RUN_BIN, "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + tmp + "/sched.argv\n")
os.chmod(webd.SCHEDULE_RUN_BIN, 0o755)
os.makedirs(tmp + "/logs", exist_ok=True)
webd.VAULT_STATUS = tmp + "/vault.status"
webd.CLAUDE_AUTH = tmp + "/secrets/claude.env"
webd.CLAUDE_AUTH_LEGACY = tmp + "/claude-auth.env"
webd.SUPPORT_KEY = tmp + "/secrets/support_key"
webd.SUPPORT_PUB = tmp + "/support_key.pub"
# the LAN lobby: mdnsd's cache stands in as a file; identity and the
# one-shot LAN question are stubbed on the lanid module webd imported
webd.MDNS_CACHE = tmp + "/peers.json"
webd.lanid.mac4 = lambda iface=None: "7f3a"
webd.box_hostname = lambda: "pipeos"
webd.lanid.query_a = lambda name, *a, **k: {"10.0.0.9"} if name == "printer.local" else set()
webd.lanid.local_ips = lambda: {"127.0.0.1"}


def seed_peers(peers, written=None):
    import time as _t
    now = int(_t.time())
    with open(webd.MDNS_CACHE, "w") as f:
        json.dump({"v": 1, "self": "7f3a", "interval": 10, "written": written if written is not None else now,
                   "peers": {p["id"]: dict(p, last_seen=now) for p in peers}}, f)


PEERS = [
    {"id": "9c21", "name": "studio", "host": "studio.local", "ip": "10.0.0.2", "claimed": True,
     "verdict": "all green", "commit": "abc1234", "built": "2026-09-01T00:00:00Z", "model": "Test Box",
     "mac": "aa:bb:cc:dd:9c:21"},
    {"id": "1a2b", "name": "", "host": "pipeos-1a2b.local", "ip": "10.0.0.3", "claimed": False,
     "verdict": "", "commit": "", "built": "", "model": "", "mac": ""},
]
# the roster (#241): every Machine ever seen, on /work; a rostered id that is
# not in the live cache is a grey row with a Wake button
webd.MACHINES_ROSTER = tmp + "/machines.json"
webd.WAKE_BIN = tmp + "/wake-stub"
with open(webd.WAKE_BIN, "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + tmp + "/wake.argv\n"
            "case \"$1\" in 4d4d) echo 'magic packet sent to attic (aa:bb:cc:dd:4d:4d)';; "
            "ffff) echo 'ffff: not a Machine this box has ever seen' >&2; exit 2;; "
            "*) echo 'no MAC on record' >&2; exit 1;; esac\n")
os.chmod(webd.WAKE_BIN, 0o755)


def seed_roster(rows):
    with open(webd.MACHINES_ROSTER, "w") as f:
        json.dump({"v": 1, "self": "7f3a", "written": 1, "machines": {r["id"]: r for r in rows}}, f)


ROSTER = [
    {"id": "9c21", "name": "studio", "host": "studio.local", "ip": "10.0.0.2", "claimed": True,
     "mac": "aa:bb:cc:dd:9c:21", "model": "Test Box", "last_seen": 1700000000},
    {"id": "4d4d", "name": "attic", "host": "attic.local", "ip": "10.0.0.4", "claimed": True,
     "mac": "aa:bb:cc:dd:4d:4d", "model": "Old Box", "last_seen": 1700000000},
    {"id": "7f3a", "name": "", "host": "pipeos-7f3a.local", "ip": "127.0.0.1", "claimed": False,
     "mac": "", "model": "", "last_seen": 1700000000},
]
webd.MOUNTS_CONF = tmp + "/mounts.conf"
webd.UPDATE_STAMP = tmp + "/selfupdate.applied"
webd.BACKUP_STATE = tmp + "/backup.state"
# the copying is pipeos-backup's (check-backup.py has its rows); here a stub
# stands in, records its argv, and lands what the card looks for
webd.BACKUP_BIN = tmp + "/backup-stub"
with open(webd.BACKUP_BIN, "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + tmp + "/backup.argv\n"
            "for d; do :; done\nn=$(hostname)\nmkdir -p \"$d/pipeos-backup/$n/work\"\n"
            "echo precious > \"$d/pipeos-backup/$n/work/data.txt\"\n"
            "date +%s > \"$d/pipeos-backup/$n/.last\"\necho step=done > " + tmp + "/backup.state\n")
os.chmod(webd.BACKUP_BIN, 0o755)
webd.FLASH_STATE = tmp + "/flash.state"
webd.FLASH_PROGRESS = tmp + "/flash.progress"
webd.FLASH_APPLIED = tmp + "/flash.applied"
webd.FLASH_IMAGE_TXT = tmp + "/pipeos-image.txt"
with open(webd.FLASH_IMAGE_TXT, "w") as f:
    f.write("variant=usb\nbuilt=2026-09-01T00:00:00Z\ncommit=abc\n")
# the flashing is pipeos-flash's (check-flash.py has its rows); a stub
# records the argv and reports done
webd.FLASH_BIN = tmp + "/flash-stub"
with open(webd.FLASH_BIN, "w") as f:
    f.write("#!/bin/sh\necho step=done > " + tmp + "/flash.state\n")
os.chmod(webd.FLASH_BIN, 0o755)
webd.LOG_ALLOW = {k: tmp + "/" + k + ".log" for k in webd.LOG_ALLOW}
# Claude, three ways (#192): a stub `claude` on PATH that behaves like the
# real one from the outside — `auth login` prints the OSC-8-wrapped link and
# reads the code from stdin, `auth status` answers from the credentials
# file, `-p` answers ok — and HOME redirected so the credential lands in
# the tempdir. The argv log is how a row sees --console.
os.makedirs(tmp + "/bin"); os.makedirs(tmp + "/home")
with open(tmp + "/bin/claude", "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + tmp + "/claude.argv\n"
            "case \"$1 $2\" in\n"
            "  \"auth login\")\n"
            "    printf 'Opening browser to sign in\\n'\n"
            "    printf 'If the browser did not open, visit: \\033]8;;https://claude.com/cai/oauth/authorize?code=true&state=STUBSTATE\\033\\\\https://claude.com/cai/oauth/authorize?code=true&state=STUBSTATE\\033]8;;\\033\\\\\\n'\n"
            "    printf 'Paste code here if prompted > '\n"
            "    read -r code\n"
            "    if [ \"$code\" = \"good#code\" ]; then mkdir -p \"$HOME/.claude\"; echo '{\"claudeAiOauth\":{\"accessToken\":\"stub\"}}' > \"$HOME/.claude/.credentials.json\"; echo 'Login successful'; exit 0; fi\n"
            "    echo 'Login failed: Request failed with status code 400'; exit 0 ;;\n"
            "  \"auth status\") if [ -f \"$HOME/.claude/.credentials.json\" ]; then echo '{\"loggedIn\":true,\"authMethod\":\"claude.ai\",\"apiProvider\":\"firstParty\"}'; else echo '{\"loggedIn\":false,\"authMethod\":\"none\"}'; fi ;;\n"
            "  \"auth logout\") rm -f \"$HOME/.claude/.credentials.json\" ;;\n"
            "  *) echo ok ;;\n"
            "esac\n")
os.chmod(tmp + "/bin/claude", 0o755)
os.environ["PATH"] = tmp + "/bin:" + os.environ.get("PATH", "")
webd.CLAUDE_HOME = tmp + "/home"
webd.CLAUDE_CREDS = tmp + "/home/.claude/.credentials.json"
with open(webd.CARD, "w") as f:
    f.write("NICK=\nNAME=\nROLE=GENERIC\nOWNER_NICK=\n")
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

# ---- the persistence rule (Sam, 2026-09-10: "whenever settings are changed
# we should persist it"). The root is tmpfs; a handler that changes state and
# does not call save_state() has changed nothing past the next boot. Static,
# so a new handler cannot be added without either saving or being listed here
# with a reason.
import inspect as _inspect
import re
_src = _inspect.getsource(webd)
_table = dict(re.findall(r'"(/api/[^"]+)":\s*self\.(\w+)', _src[_src.index("handlers = {"):]))
_no_save = {
    "/api/logout": "a session, tmpfs by design",
    "/api/claude-login/start": "starts a login; /code saves",
    "/api/file-op": "/work, not the apkovl",
    "/api/backup": "writes an external disk",
    "/api/chat": "a conversation, under /work",
    "/api/reboot": "the shutdown hook saves",
    "/api/reboot-firmware": "the shutdown hook saves",
    "/api/update-now": "pipeos-selfupdate saves itself",
    "/api/update-set": "the pipeos selfupdate verb saves; the handler reads its receipt (#275)",
    "/api/flash": "pipeos-flash writes the media directly",
    "/api/save": "is the save",
    "/api/wake": "a packet on the wire, no state (#241)",
    "/api/secrets/reveal": "a read that re-auths (#244)",
    "/api/secrets/phrase-ack": "forgets a tmpfs copy (#244)",
    "/api/schedule/run": "starts a run; its record lives on /work (#242)",
    "/api/cluster/sync": "pushes the list to the members; nothing here changes (#211)",
}
_missing = []
for _path, _fn in _table.items():
    _m = re.search(r"\n    def %s\(self[^)]*\):(.*?)(?=\n    def |\Z)" % _fn, _src, re.S)
    if _path not in _no_save and (not _m or "save_state" not in _m.group(1)):
        _missing.append(_path)
assert not _missing, "handlers that change state without saving: %s" % _missing
assert not (set(_no_save) - set(_table)), "no-save list names a handler that no longer exists"
ok("every state-changing handler saves (%d handlers, %d exempt with a reason)" % (len(_table), len(_no_save)))


s = req("/api/state")
assert s["claimed"] is False and s["authed"] is False
ok("fresh box reports unclaimed")

# ---- the LAN lobby (#lobby): public, stateless, identical on every Machine
assert s["lan_name"] == "pipeos-7f3a" and s["siblings"] == 0
ok("no cache: the pre-claim name is known and there are no siblings (a lone Machine gets the wizard)")
seed_peers(PEERS)
s = req("/api/state")
assert s["siblings"] == 2
lb = req("/api/lobby")
ms = lb["machines"]
assert lb["discovery_ok"] is True and len(ms) == 3 and sum(1 for m in ms if m["self"]) == 1
assert [m["id"] for m in ms][0] == "9c21"
mine = next(m for m in ms if m["self"])
assert mine["claimed"] is False and mine["host"] == "pipeos-7f3a.local" and mine["name"] == ""
peer = next(m for m in ms if m["id"] == "9c21")
assert peer["name"] == "studio" and peer["verdict"] == "all green" and peer["model"] == "Test Box" and peer["self"] is False
assert b"<title>pipeOS</title>" in req("/lobby")
ok("with siblings: /api/lobby (no session) lists self plus peers, claimed first, every field through; /lobby serves the shell")
seed_peers(PEERS, written=100)
s = req("/api/state"); lb = req("/api/lobby")
assert s["siblings"] == 0 and lb["discovery_ok"] is False and len(lb["machines"]) == 1
ok("a cache the responder stopped writing counts for nothing, and the page says discovery is down")
os.unlink(webd.MDNS_CACHE)
# ---- the roster: a Machine seen before but not answering now is a grey row (#241)
seed_peers(PEERS)
seed_roster(ROSTER)
ms = req("/api/lobby")["machines"]
byid = {m["id"]: m for m in ms}
assert len(ms) == 4 and [m["id"] for m in ms] == ["9c21", "1a2b", "7f3a", "4d4d"], [m["id"] for m in ms]
assert byid["9c21"]["awake"] is True and byid["9c21"]["mac"] == "aa:bb:cc:dd:9c:21"
assert byid["4d4d"]["awake"] is False and byid["4d4d"]["mac"] == "aa:bb:cc:dd:4d:4d" \
    and byid["4d4d"]["name"] == "attic" and byid["4d4d"]["ip"] == "10.0.0.4" and byid["4d4d"]["last_seen"] == 1700000000 \
    and byid["4d4d"]["self"] is False
assert byid["7f3a"]["self"] is True and byid["7f3a"]["awake"] is True
ok("a rostered Machine that is not answering is a grey row after the live ones — its name, last address and MAC through; this box is never its own grey row")
os.unlink(webd.MDNS_CACHE)
os.unlink(webd.MACHINES_ROSTER)
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
_phrase = r["recovery_phrase"]
assert len(_phrase.split("-")) == 8 and os.path.exists(webd.VAULT) and (os.stat(webd.VAULT).st_mode & 0o077) == 0
ok("claim mints the box's vault (0600) and hands the wizard the recovery phrase once")
req("/api/claim", {"password": "another-pass"}, expect=403)
ok("second claim refused")
st = req("/api/status")
assert "verdict: all green" in st["boot_report"]
ok("status returns the boot report")
r = req("/api/services", {"claude": True, "pipe": False})
assert r["services"]["claude"] is True and r["services"]["pipe"] is False
ok("services toggle round-trips")

# ---- Claude, three ways (#192) ------------------------------------------
c = req("/api/claude")
assert c["method"] == "none" and c["logged_in"] is False
ok("fresh box: no Claude credential, and the pill would say so")
r = req("/api/claude-login/start", {"billing": "claude"})
assert r["url"] == "https://claude.com/cai/oauth/authorize?code=true&state=STUBSTATE", r["url"]
assert "auth login --claudeai" in open(tmp + "/claude.argv").read()
ok("sign-in start hands back the one link claude printed, OSC-8 wrapper and duplicate stripped")
r = req("/api/claude-login/code", {"code": "bad#code"}, expect=400)
assert "Login failed" in r["error"] and not os.path.exists(webd.CLAUDE_CREDS)
assert req("/api/claude")["method"] == "none"
ok("a wrong code is refused in claude's own words; nothing is stored")
req("/api/claude-login/code", {"code": "good#code"}, expect=400)
ok("a code with no sign-in waiting is refused")
r = req("/api/claude-login/start", {"billing": "console"})
assert "auth login --console" in open(tmp + "/claude.argv").read()
r = req("/api/claude-login/code", {"code": "good#code"})
assert r["method"] == "login" and r["probe_ok"] is True and os.path.exists(webd.CLAUDE_CREDS)
c = req("/api/claude")
assert c["method"] == "login" and c["logged_in"] is True
ok("Console billing asks claude for --console; the right code signs the box in and the probe answers")
r = req("/api/claude-token", {"token": "sk-ant-api03-" + "k" * 60})
assert r["method"] == "apikey" and open(webd.CLAUDE_AUTH).read().startswith("ANTHROPIC_API_KEY=sk-ant-api03-")
assert req("/api/claude")["method"] == "apikey"
r = req("/api/claude-token", {"token": "sk-ant-oat01-" + "t" * 60})
assert r["method"] == "token" and open(webd.CLAUDE_AUTH).read().startswith("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-")
assert req("/api/claude")["method"] == "token"
req("/api/claude-token", {"token": "short"}, expect=400)
assert not os.path.exists(webd.CLAUDE_AUTH_LEGACY) and webd.vault.get("claude_token") == "sk-ant-oat01-" + "t" * 60
assert "sk-ant" not in open(webd.VAULT).read()
ok("an API key and a setup-token each land as their own variable in the vault's export, never in /etc, and the sealed file does not contain them; the file wins over the sign-in; junk is refused")
req("/api/claude-login/start", {"billing": "claude"})
r = req("/api/claude-login/code", {"code": "good#code"})
assert r["method"] == "login" and not os.path.exists(webd.CLAUDE_AUTH)
ok("a fresh sign-in retires the pasted token — the variable would win over it")
req("/api/claude-logout", {})
assert req("/api/claude")["method"] == "none" and not os.path.exists(webd.CLAUDE_CREDS)
ok("sign-out clears the credential")

# ---- vendor support access (#159): the surface docs/support-relay.md promised
sp = req("/api/support")
assert sp["enabled"] is False and sp["pubkey"] == ""
ok("support off: no key, card hidden")
with open(webd.SUPPORT_CONF, "w") as f:
    f.write("SUPPORT_RELAY=tunnel@relay.example\nSUPPORT_PORT=\n")
r = req("/api/services", {"support": True})
assert r["services"]["support"] is True
sp = req("/api/support")
assert sp["enabled"] and sp["pubkey"].startswith("ssh-ed25519 ") and sp["relay"] == "tunnel@relay.example"
assert sp["configured"] is False and os.path.exists(webd.SUPPORT_KEY)
assert (os.stat(webd.SUPPORT_KEY).st_mode & 0o077) == 0
assert webd.vault.get("support_key").startswith(b"-----BEGIN OPENSSH PRIVATE KEY") and not os.path.exists(tmp + "/support_key")
first_key = sp["pubkey"]
ok("support on: an ed25519 key is made once — the private half in the vault and its export (owner-only), the public half beside the conf; the pubkey and relay are shown, no port = not configured")
with open(webd.SUPPORT_CONF, "w") as f:
    f.write("SUPPORT_RELAY=tunnel@relay.example\nSUPPORT_PORT=42001\n")
req("/api/services", {"support": False})
req("/api/services", {"support": True})
sp = req("/api/support")
assert sp["pubkey"] == first_key and sp["configured"] is True and sp["port"] == "42001"
ok("toggling again keeps the same key; a port makes it configured")
req("/api/services", {"support": False})
with open(webd.SERVICES_CONF) as f:
    assert "SERVICE_CLAUDE=on" in f.read()
ok("service toggle lands in services.conf")
req("/api/name", {"nick": "bad name!"}, expect=400)
ok("hostile nick refused")
seed_peers(PEERS)
_card_set, _save = webd.card_set, webd.save_state
webd.card_set = lambda updates: None
webd.save_state = lambda: (True, "")
for nick, frag in (("studio", "already a Machine"), ("STUDIO", "already a Machine"),
                   ("pipeos-1a2b", "chassis ids"), ("pipeos-ffff", "chassis ids"),
                   ("printer", "already answers"), ("pipeos", "every Machine")):
    r = req("/api/name", {"nick": nick}, expect=409)
    assert frag in r["error"], (nick, r)
_seen = {}
webd.card_set = lambda updates: _seen.update(updates)
# the name is a SAN on the server cert; the rename must re-issue it and hand
# the live :443 context the new file, not leave it for the next boot (#234)
webd.TLS_INIT = tmp + "/tls-init-stub"
webd.SRV_CRT, webd.SRV_KEY = tmp + "/server.crt", tmp + "/server.key"
with open(webd.TLS_INIT, "w") as f:
    f.write("#!/bin/sh\necho ran >> " + tmp + "/tls-init.ran\n")
os.chmod(webd.TLS_INIT, 0o755)


class _Ctx:
    loaded = []

    def load_cert_chain(self, crt, key):
        self.loaded.append((crt, key))


webd.HTTPS["ctx"] = _Ctx()
r = req("/api/name", {"name": "Attic"})
assert r["ok"] and _seen == {"NAME": "attic"}, (r, _seen)
assert r["tls"] is True and os.path.exists(tmp + "/tls-init.ran"), r
assert _Ctx.loaded == [(webd.SRV_CRT, webd.SRV_KEY)], _Ctx.loaded
_seen.clear()
r = req("/api/name", {"owner": "sam"})
assert r["ok"] and _seen == {"OWNER_NICK": "sam"} and r["tls_detail"] == "", r
assert open(tmp + "/tls-init.ran").read().count("ran") == 1, "owner-only change re-issued the cert"
os.chmod(webd.TLS_INIT, 0o644)
with open(webd.TLS_INIT, "w") as f:
    f.write("#!/bin/sh\necho boom >&2; exit 3\n")
os.chmod(webd.TLS_INIT, 0o755)
r = req("/api/name", {"name": "Attic"})
assert r["ok"] and r["tls"] is False and "rc=3" in r["tls_detail"] and r["saved"], r
assert len(_Ctx.loaded) == 1, "a failed re-issue must not reload the old files as if new"
webd.HTTPS["ctx"] = None
webd.card_set, webd.save_state = _card_set, _save
ok("a rename re-issues the server cert and reloads the live TLS context at once — not at next boot; an owner-only change does not; a failed re-issue is reported and still saves")
os.unlink(webd.MDNS_CACHE)
ok("a rename refuses a sibling's name (any case), any pipeos-xxxx chassis id, a name that answers on the LAN, and plain pipeos; a free name lands in NAME= lowercased, never in NICK")
seed_peers(PEERS)
r = req("/api/name-suggest")
assert len(r["names"]) == 5 and "studio" not in r["names"] and all(n in webd.CAR_NAMES for n in r["names"]), r
assert r["names"] == req("/api/name-suggest")["names"]
os.unlink(webd.MDNS_CACHE)
ok("the suggester offers five cars not on the network, the same five each time")
# ---- wake (#241): admin sends the packet; the roster's word on the Machine is the answer
seed_roster(ROSTER)
r = req("/api/wake", {"id": "4d4d"})
assert r["ok"] and r["mac"] == "aa:bb:cc:dd:4d:4d" and "magic packet" in r["detail"], r
assert open(tmp + "/wake.argv").read().split() == ["4d4d"]
req("/api/wake", {"id": "ffff"}, expect=404)
req("/api/wake", {"id": "1a2b"}, expect=409)
req("/api/wake", {"id": "../x"}, expect=400)
req("/api/wake", {}, expect=400)
os.unlink(webd.MACHINES_ROSTER)
ok("wake: the admin's click runs pipeos-wake with the id and reports the MAC; unknown is 404, a Machine with no MAC 409, a hostile id 400")
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
# ---- the Secrets surface (#244): names never values, reserved names refused,
# reveal re-auths, unlock/rephrase/init/ack
sec = req("/api/secrets")
names = {r["name"]: r for r in sec["secrets"]}
assert sec["status"] == "open" and "claude_token" not in names, names  # signed out above: the token is gone from the vault too
assert "support_key" in names and names["support_key"]["kind"] == "bytes" and names["support_key"]["consumer"] == "support" \
    and names["support_key"]["by"] == "system" and names["support_key"]["set_at"] > 0, names
assert "value" not in json.dumps(sec) and "sk-ant" not in json.dumps(sec) and "BEGIN OPENSSH" not in json.dumps(sec)
ok("secrets: the list names what the box holds, who set it and for what — never a value")
req("/api/secrets/set", {"name": "claude_token", "value": "x"}, expect=400)
req("/api/secrets/set", {"name": "stream_key_1", "value": "x"}, expect=400)
req("/api/secrets/set", {"name": "Bad Name", "value": "x"}, expect=400)
req("/api/secrets/set", {"name": "jobs.gh", "value": ""}, expect=400)
req("/api/secrets/set", {"name": "jobs.gh", "value": "x" * 9000}, expect=400)
r = req("/api/secrets/set", {"name": "jobs.gh_token", "value": "ghp_custom"})
assert r["ok"] and open(tmp + "/secrets/jobs.env").read() == "GH_TOKEN='ghp_custom'\n"
ok("secrets: a service's own secret is refused here (its card sets it); hostile names and sizes refused; a custom job secret lands in the vault and its jobs.env export")
req("/api/secrets/reveal", {"name": "jobs.gh_token", "password": "nope"}, expect=403)
r = req("/api/secrets/reveal", {"name": "jobs.gh_token", "password": "hunter22hunter"})
assert r["value"] == "ghp_custom"
req("/api/secrets/reveal", {"name": "support_key", "password": "hunter22hunter"}, expect=400)
req("/api/secrets/reveal", {"name": "nope", "password": "hunter22hunter"}, expect=404)
ok("secrets: reveal needs the admin's own password, shows a text value, refuses a key file")
r = req("/api/secrets/del", {"name": "jobs.gh_token"})
assert r["ok"] and not os.path.exists(tmp + "/secrets/jobs.env")
req("/api/secrets/del", {"name": "jobs.gh_token"}, expect=404)
ok("secrets: delete removes the secret and its export at once")
req("/api/secrets/unlock", {"phrase": "0000-0000-0000-0000-0000-0000-0000-0000"}, expect=403)
req("/api/secrets/unlock", {"phrase": "short"}, expect=400)
r = req("/api/secrets/unlock", {"phrase": _phrase})
assert r["ok"] and req("/api/secrets")["status"] == "open"
r = req("/api/secrets/rephrase", {})
assert len(r["phrase"].split("-")) == 8 and r["phrase"] != _phrase
req("/api/secrets/unlock", {"phrase": _phrase}, expect=403)
req("/api/secrets/unlock", {"phrase": r["phrase"]})
ok("secrets: the wrong phrase is refused, the right one re-seals; rephrase retires the old phrase")
req("/api/secrets/init", {}, expect=409)
with open(webd.VAULT_PHRASE, "w") as f:
    f.write("aaaa-bbbb\n")
assert req("/api/secrets")["phrase_pending"] is True and req("/api/secrets")["phrase"] == "aaaa-bbbb"
req("/api/secrets/phrase-ack", {})
assert req("/api/secrets")["phrase_pending"] is False and not os.path.exists(webd.VAULT_PHRASE)
ok("secrets: init is refused while a vault exists; a parked phrase shows until the owner acknowledges it")
# ---- scheduled runs (#242): the job list, saved; hostile input refused; run-now; logs
_work_ok = os.access("/work", os.W_OK) if os.path.isdir("/work") else False
r = req("/api/schedule/set", {"name": "nightly", "cron": "0 2 * * *", "prompt": "run the tests", "backend": "claude", "notify": True})
assert r["ok"] and r["job"]["cron"] == "0 2 * * *" and r["job"]["session"] == "fresh"
sc = req("/api/schedule")
assert [j["name"] for j in sc["jobs"]] == ["nightly"] and sc["jobs"][0]["human"] == "every day at 02:00" \
    and sc["jobs"][0]["next_run"] and sc["jobs"][0]["enabled"] is True and sc["paused"] == "" and sc["running"] is False
assert json.load(open(webd.SCHEDULE_CONF))["jobs"][0]["prompt"] == "run the tests"
ok("schedule: a job is created, saved, and listed with its human schedule and next run")
r = req("/api/schedule/set", {"name": "nightly", "enabled": False})
assert r["ok"] and req("/api/schedule")["jobs"][0]["enabled"] is False and req("/api/schedule")["jobs"][0]["next_run"] == ""
r = req("/api/schedule/set", {"name": "nightly", "cron": "@hourly", "session": "continue"})
assert r["job"]["cron"] == "0 * * * *" and r["job"]["prompt"] == "run the tests" and r["job"]["session"] == "continue"
ok("schedule: pause is an edit that saves; a partial edit keeps the rest; an alias normalises")
for bad in ("* * * * * ; rm -rf /", "60 * * * *", "*/0 * * * *", "1 2 3 4 5 6", "x" * 3000, "$(id)"):
    req("/api/schedule/set", {"name": "evil", "cron": bad, "prompt": "x"}, expect=400)
req("/api/schedule/set", {"name": "Bad Name", "cron": "* * * * *", "prompt": "x"}, expect=400)
req("/api/schedule/set", {"name": "noprompt", "cron": "* * * * *", "prompt": ""}, expect=400)
req("/api/schedule/set", {"name": "long", "cron": "* * * * *", "prompt": "x" * 9000}, expect=400)
req("/api/schedule/set", {"name": "etc", "cron": "* * * * *", "prompt": "x", "cwd": "/etc"}, expect=400)
req("/api/schedule/set", {"name": "dots", "cron": "* * * * *", "prompt": "x", "cwd": "/work/../etc"}, expect=400)
req("/api/schedule/set", {"name": "skynet", "cron": "* * * * *", "prompt": "x", "backend": "skynet"}, expect=400)
req("/api/schedule/set", {"name": "sess", "cron": "* * * * *", "prompt": "x", "session": "forever"}, expect=400)
assert [j["name"] for j in req("/api/schedule")["jobs"]] == ["nightly"]
ok("schedule: hostile cron strings, names, prompts, a cwd outside /work, an unknown assistant and a bad session mode are all refused and change nothing")
# the handler detaches the runner (start_new_session); the probe wraps Popen
# so the child is reaped before the row reads its record — a detached child
# under a CI sandbox may not get scheduled until someone waits on it
_orig_popen = webd.subprocess.Popen
_spawned = []
def _wait_popen(*a, **k):
    pr = _orig_popen(*a, **k); _spawned.append(list(a[0])); pr.wait(); return pr
webd.subprocess.Popen = _wait_popen
r = req("/api/schedule/run", {"name": "nightly"})
assert r["started"] and _spawned == [[webd.SCHEDULE_RUN_BIN, "nightly"]], _spawned
assert open(tmp + "/sched.argv").read().split() == ["nightly"]
req("/api/schedule/run", {"name": "nope"}, expect=404)
import fcntl as _fcntl
_lk = open(webd.SCHEDULE_LOCK, "a+")
_fcntl.flock(_lk, _fcntl.LOCK_EX)
req("/api/schedule/run", {"name": "nightly"}, expect=409)
assert req("/api/schedule")["running"] is True
_fcntl.flock(_lk, _fcntl.LOCK_UN); _lk.close()
webd.subprocess.Popen = _orig_popen
ok("schedule: run-now starts the runner with the job name; unknown is 404; while the lock is held it is 409 and the list says running")
with open(tmp + "/logs/schedule-nightly.log", "w") as f:
    f.write("=== run job=nightly ===\nthe reply\n")
assert "the reply" in req("/api/logs?name=schedule-nightly")["text"]
req("/api/logs?name=schedule-nope", expect=400)
req("/api/logs?name=schedule-../../etc/passwd", expect=400)
assert "text" in req("/api/logs?name=schedule")
ok("schedule: a job's own log is readable by schedule-<job>, an unknown or hostile name is not; the dispatch log is on the allow-list")
with open(webd.LEDGER_PAUSED, "w") as f:
    f.write("monthly cap USD 40 reached 2026-09-10\n")
assert "monthly cap" in req("/api/schedule")["paused"]
os.unlink(webd.LEDGER_PAUSED)
r = req("/api/schedule/del", {"name": "nightly"})
assert r["ok"] and req("/api/schedule")["jobs"] == []
req("/api/schedule/del", {"name": "nightly"}, expect=404)
ok("schedule: the ledger's pause marker shows in the list; delete removes the job and saves")
# ---- usage (#246): a seeded month file, the cap, hostile caps, the chat's own session
import datetime as _dt
_today = _dt.datetime.now(_dt.timezone.utc)
with open(tmp + "/ledger/" + _today.strftime("%Y-%m") + ".jsonl", "w") as f:
    for i, (kind, usd) in enumerate((("assistant", 1.25), ("dashboard", 0.5), ("job", 2.0))):
        f.write(json.dumps({"ts": _today.strftime("%Y-%m-%dT%H:%M:%SZ"), "sid": "s%d" % i, "id": "m%d" % i, "source": "transcript",
                            "actor": {"kind": kind, "name": "nightly" if kind == "job" else ""}, "backend": "claude", "provider": "anthropic",
                            "model": "claude-opus-5", "in": 100, "out": 10, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0,
                            "cost_usd": usd, "est": True}) + "\n")
u = req("/api/usage")
assert abs(u["today"]["usd"] - 3.75) < 1e-6 and u["today"]["calls"] == 3 and u["estimate"] is True and u["cap"]["usd"] == 0
assert set(u["by_actor"]) == {"assistant", "dashboard", "job:nightly"} and len(u["daily"]) == 30 and abs(u["daily"][29] - 3.75) < 1e-6
assert u["rates_updated"]
ok("usage: the view sums the month file — today, by actor, the daily series — and says it is an estimate")
st = req("/api/status")
assert abs(st["spend_today_usd"] - 3.75) < 1e-6 and st["usage_paused"] is False
ok("usage: status carries today's spend for the overview tile")
for bad in ("40; rm -rf /", -1, 1e9, 4.5, "abc", [], True, None):
    req("/api/usage/cap", {"usd": bad}, expect=400)
assert "MONTHLY_CAP_USD" not in open(webd.CARD).read() or "MONTHLY_CAP_USD=\n" in open(webd.CARD).read()
ok("usage: a hostile cap is refused and the card is untouched")
_card_set, webd.card_set = webd.card_set, (lambda updates: open(webd.LEDGER_CONF, "w").write('NICK=""\nOWNER_NICK=""\nMONTHLY_CAP_USD="%s"\n' % updates["MONTHLY_CAP_USD"]))
r = req("/api/usage/cap", {"usd": 40})
assert r["ok"] and r["cap"] == 40 and req("/api/usage")["cap"]["usd"] == 40 and req("/api/usage")["cap"]["pct"] == 9
assert "MONTHLY_CAP_USD=" in open(webd.CARD).read()   # the key line was added to a card that predates it
r = req("/api/usage/cap", {"usd": 3})
assert r["ok"] and r["state"]["paused"] is True and os.path.exists(webd.LEDGER_PAUSED) and req("/api/status")["usage_paused"] is True
assert "monthly cap" in req("/api/schedule")["paused"]
r = req("/api/usage/cap", {"usd": 0})
assert r["ok"] and not os.path.exists(webd.LEDGER_PAUSED) and req("/api/usage")["cap"]["usd"] == 0
webd.card_set = _card_set
ok("usage: setting the cap writes the card (adding the key to an older card), saves, and enforces at once — under the spend pauses the schedule, 0 lifts it")
# the dashboard chat: its own session id, --session-id first then --resume
req("/api/services", {"claude": True})
os.unlink(tmp + "/claude.argv") if os.path.exists(tmp + "/claude.argv") else None
r = req("/api/chat", {"message": "hello"})
assert r["reply"] == "ok"
r = req("/api/chat", {"message": "again"})
_cargv = [l for l in open(tmp + "/claude.argv").read().splitlines() if l.startswith("-p ")]
_sid = open(webd.WEBCHAT_SID).read().strip()
assert len(_cargv) == 2 and _cargv[0].endswith("--session-id " + _sid) and _cargv[1].endswith("--resume " + _sid) and "--continue" not in "".join(_cargv), _cargv
ok("chat: the dashboard chat has its own Claude session — --session-id on the first turn, --resume after — never --continue into the master session's transcript")
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
assert "STREAM_T1_KEY=''" in conf and "STREAM_T2_KEY=''" in conf and "yt-secret" not in conf and "tw-secret" not in conf
assert webd.vault.get("stream_key_1") == "yt-secret" and webd.vault.get("stream_key_2") == "tw-secret"
assert open(tmp + "/secrets/stream.env").read() == "STREAM_T1_KEY='yt-secret'\nSTREAM_T2_KEY='tw-secret'\n"
assert "STREAM_T1_NAME='YouTube'" in conf and "STREAM_T2_ON='1'" in conf
ok("stream config round-trips multi-provider targets; the keys go to the vault and its export, the conf carries blanks")
# a blank key with keep_key preserves what was saved for that provider slot
req("/api/stream-config", {"mode": "browser", "url": "https://basho.dev",
    "targets": [{"name": "YouTube", "url": "rtmp://a.rtmp.youtube.com/live2", "on": True, "keep_key": True}]})
g2 = req("/api/stream")
assert g2["targets"][0]["key_set"] is True and g2["targets"][1]["key_set"] is False, g2["targets"]
assert webd.vault.get("stream_key_1") == "yt-secret" and "stream_key_2" not in {r["name"] for r in webd.vault.list_()}
ok("blank provider key with keep_key preserves the saved key; a target posted without keep_key drops its key from the vault")
# assistant terminal: guards injection + port, and refuses an open terminal
req("/api/assistant-config", {"password": "x'; rm -rf /"}, expect=400)
req("/api/assistant-config", {"port": "notaport", "password": "p"}, expect=400)
req("/api/assistant-config", {"user": "admin"}, expect=400)
ok("assistant config guards injection/port and refuses a passwordless terminal")
r = req("/api/assistant-config", {"password": "hunter2pass", "port": "7681"})
assert r["ok"]
a = req("/api/assistant")
assert a["pass_set"] is True and a["port"] == "7681" and "password" not in a, a
assert webd.vault.get("assistant_pass") == "hunter2pass" and "hunter2pass" not in open(webd.ASSISTANT_CONF).read()
assert open(tmp + "/secrets/assistant.env").read() == "ASSISTANT_PASS='hunter2pass'\n"
# backend selection: allowlisted, install-checked, and a backend-only flip
# keeps the saved password and port
req("/api/assistant-config", {"backend": "skynet"}, expect=400)
_absent = next((b for b in webd.ASSISTANT_BACKENDS if webd.shutil.which(b) is None), None)
if _absent:
    req("/api/assistant-config", {"backend": _absent}, expect=400)  # not installed here
assert a["backend"] == "claude" and any(b["id"] == "hermes" for b in a["backends"])
if any(b["id"] == "hermes" and b["installed"] for b in a["backends"]):
    req("/api/assistant-config", {"backend": "hermes", "keep_pass": True})
    a2 = req("/api/assistant")
    assert a2["backend"] == "hermes" and a2["pass_set"] is True and a2["port"] == "7681", a2
    req("/api/assistant-config", {"backend": "claude", "keep_pass": True})
ok("assistant backend allowlist + lossless backend-only flips")
with open(webd.ASSISTANT_CONF) as f:
    assert "ASSISTANT_PASS=''" in f.read() and webd.vault.get("assistant_pass") == "hunter2pass"
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
req("/api/backup", {"dest": "ext/sdx1", "scope": "nope"}, expect=400)
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
assert b["step"] == "done", b
# the identity-only scope reaches the binary as its flag
r = req("/api/backup", {"dest": "ext/sdx1", "scope": "identity"})
assert r["started"]
for _ in range(100):
    if not req("/api/backup")["running"]:
        break
    _sel.select([], [], [], 0.1)
assert "--identity-only" in open(tmp + "/backup.argv").read()
webd.os.path.ismount = _real_ismount
ok("backup validates dest and scope, runs pipeos-backup, and reads its step and stamp")
# flash: the GET shape, the server-side typed confirmation, the busy gate
f = req("/api/flash")
assert f["image"]["variant"] == "usb" and f["running"] is False, f
req("/api/flash", {"mode": "stick"}, expect=400)          # not built yet
req("/api/flash", {"mode": "inplace", "confirm": "wrong"}, expect=400)
import socket as _sock
r = req("/api/flash", {"mode": "inplace", "confirm": _sock.gethostname()})
assert r["started"]
for _ in range(100):
    if not req("/api/flash")["running"]:
        break
    _sel.select([], [], [], 0.1)
f = req("/api/flash")
assert f["ok"] is True and f["step"] == "done", f
ok("flash: GET is shaped; confirm is checked server-side; the worker runs and reports done")
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
# a share-only account (pipeOS#270): unix for samba's sake, no hash, no shell;
# nothing that would give it one is accepted, and it cannot sign in. The
# HTTP path shells out to pipeos-user, absent on a dev host, so the
# creation itself is exercised through _user_add's validation only (500).
req("/api/users/add", {"name": "shareonly", "share": True, "sudo": True}, expect=400)
req("/api/users/add", {"name": "shareonly", "share": True, "terminal": True, "term_pass": "x"}, expect=400)
req("/api/users/add", {"name": "shareonly", "share": True, "ssh_key": "ssh-ed25519 AAAA"}, expect=400)
req("/api/users/add", {"name": "shareonly", "share": True, "password": "hunter22hunter"}, expect=400)
_r = req("/api/users/add", {"name": "shareonly", "share": True}, expect=500)  # pipeos-user missing here
assert "unix user" in _r["error"]
_us = webd.read_users()
_us.append({"name": "shareonly", "role": "viewer", "unix": True, "share": True, "created": 0})
webd.write_users(_us)
assert "hash" not in [u for u in webd.read_users() if u["name"] == "shareonly"][0]
req("/api/users/set", {"name": "shareonly", "password": "hunter22hunter"}, expect=400)
req("/api/users/set", {"name": "shareonly", "ssh_key": "ssh-ed25519 AAAA"}, expect=400)
req("/api/users/set", {"name": "shareonly", "role": "admin"}, expect=400)
r = req("/api/users/set", {"name": "shareonly", "disabled": True})
assert r["ok"]
req("/api/users/set", {"name": "shareonly", "disabled": False})
assert any(u["name"] == "shareonly" and u.get("share") for u in req("/api/users")["users"])
_ck = cookie["v"]; cookie["v"] = None
req("/api/login", {"username": "shareonly", "password": "anything-at-all"}, expect=403)
cookie["v"] = _ck
ok("share-only account: no shell/key/sudo/password accepted, no dashboard sign-in, disable allowed")
# network storage: the share jail (paths resolve through _files_path, names
# validated, users must be real unix accounts), the config round-trip, and
# the empty-post disable path. No samba on the test host, so /api/nas still
# answers (installed:false) and nas-password reports the missing tool.
r = req("/api/nas")
assert r["shares"] == [] and isinstance(r["roots"], list) and r["roots"][0]["key"] == "work"
req("/api/nas", {"shares": [{"name": "bad name!", "path": "work", "users": ["x"]}]}, expect=400)
req("/api/nas", {"shares": [{"name": "esc", "path": "work/../../etc", "users": ["x"]}]}, expect=400)
req("/api/nas", {"shares": [{"name": "ghost", "path": "work", "users": ["nobody-here"]}]}, expect=400)
ok("nas refuses bad names, escaping paths, and unknown users")
# a unix-enabled account, seeded through the store: the HTTP path shells out
# to pipeos-user, which does not exist on a dev host
_us = webd.read_users()
_us.append({"name": "smbuser", "role": "viewer", "hash": "x", "unix": True, "created": 0})
webd.write_users(_us)
os.makedirs(webd.FILES_WORK, exist_ok=True)
os.makedirs(os.path.join(webd.FILES_WORK, "music"), exist_ok=True)
r = req("/api/nas", {"shares": [{"name": "music", "path": "work/music", "users": ["smbuser"]}]})
assert r["ok"]
with open(webd.NAS_CONF) as f:
    nas_text = f.read()
assert "NAS_S1_NAME='music'" in nas_text and "NAS_S1_ROOT='work'" in nas_text
assert "NAS_S1_REL='music'" in nas_text and "NAS_S1_USERS='smbuser'" in nas_text
with open(webd.SERVICES_CONF) as f:
    assert "SERVICE_NAS=on" in f.read()  # configure implies enable
r = req("/api/nas")
assert r["shares"] and r["shares"][0]["name"] == "music" and r["shares"][0]["attached"]
assert "smbuser" in r["eligible_users"]
ok("nas config round-trips and enables the service")
r = req("/api/nas", {"shares": []})
assert r["ok"]
with open(webd.SERVICES_CONF) as f:
    assert "SERVICE_NAS=off" in f.read()  # removing the last share disables
req("/api/nas-password", {"name": "smbuser", "password": "short"}, expect=400)
req("/api/nas-password", {"name": "nobody-here", "password": "longenough1"}, expect=400)
ok("nas: empty share list disables; password endpoint validates first")
webd.write_users([u for u in webd.read_users() if u["name"] != "smbuser"])
# the bare Services toggle (pipeOS#266): with no share there is nothing to
# serve, so nas:true is declined — not an error, the wizard posts every
# toggle in one body and the rest must land — reported in problems[], and
# the services map says what took. With a share it is a plain toggle.
r = req("/api/services", {"nas": True, "claude": True})
assert r["ok"] and r["services"]["nas"] is False and r["services"]["claude"] is True
assert any("Network storage" in p for p in r["problems"])
assert webd.read_services()["nas"] is False
_us = webd.read_users()
_us.append({"name": "smbuser", "role": "viewer", "hash": "x", "unix": True, "created": 0})
webd.write_users(_us)
r = req("/api/nas", {"shares": [{"name": "music", "path": "work/music", "users": ["smbuser"]}]})
assert r["ok"] and webd.read_services()["nas"] is True
r = req("/api/services", {"nas": False})
assert r["ok"] and webd.read_services()["nas"] is False
r = req("/api/services", {"nas": True})
assert r["ok"] and r["services"]["nas"] is True  # a share exists: plain toggle
assert webd.read_services()["nas"] is True
req("/api/nas", {"shares": []})
assert webd.read_services()["nas"] is False
webd.write_users([u for u in webd.read_users() if u["name"] != "smbuser"])
# /api/nas-account (pipeOS#270): validation, then the delete path — a unix
# account on a share is stripped from it, an emptied share is dropped, and
# no share left turns storage off. Seeded through the store (pipeos-user
# and smbpasswd are absent here).
req("/api/nas-account", {"name": "Bad Name", "password": "hunter22hunter"}, expect=400)
req("/api/nas-account", {"name": "smbonly", "password": "short"}, expect=400)  # checked before any account is made
assert not any(u["name"] == "smbonly" for u in webd.read_users())
req("/api/nas-account", {"name": "smbonly", "password": "hunter22hunter"}, expect=500)  # account half needs pipeos-user
req("/api/users/add", {"name": "shareadmin", "share": True, "role": "admin"}, expect=400)  # never an admin
req("/api/nas-account", {"name": "admin", "password": "hunter22hunter"}, expect=400)  # exists
_us = webd.read_users()
_us.append({"name": "shareonly2", "role": "viewer", "unix": True, "share": True, "created": 0})
webd.write_users(_us)
r = req("/api/nas", {"shares": [{"name": "music", "path": "work/music", "users": ["shareonly", "shareonly2"]},
                                {"name": "solo", "path": "work", "users": ["shareonly2"]}]})
assert r["ok"] and webd.read_services()["nas"] is True
r = req("/api/services", {"nas": False})   # the owner turns storage off for the weekend, shares kept
assert r["services"]["nas"] is False
r = req("/api/users/del", {"name": "shareonly2"})
assert r["ok"]
with open(webd.NAS_CONF) as f:
    nas_text = f.read()
assert "NAS_S1_NAME='music'" in nas_text and "NAS_S1_USERS='shareonly'" in nas_text
assert "solo" not in nas_text  # its only user is gone, so is the share
assert webd.read_services()["nas"] is False  # a delete never turns storage back on
r = req("/api/services", {"nas": True})
assert r["services"]["nas"] is True
r = req("/api/users/del", {"name": "shareonly"})
assert r["ok"]
with open(webd.NAS_CONF) as f:
    assert "NAS_S1_NAME=''" in f.read()
assert webd.read_services()["nas"] is False  # no share left
assert not any(u["name"].startswith("shareonly") for u in webd.read_users())
ok("nas-account validates; deleting a unix account strips it from shares, drops emptied shares, turns storage off")
# automatic image updates (pipeOS#275): the switch validates; the write is the
# verb's (pipeos-selfupdate, absent on a dev host -> 500 and nothing saved)
req("/api/update-set", {"image_update": "sometimes"}, expect=400)
req("/api/update-set", {}, expect=400)
_r = req("/api/update", expect=200)
assert _r["image_update"] in ("auto", "off") and "image_pending" in _r
ok("update-set validates auto|off; /api/update reports the image-update state")
# the restart helper must only ever RESTART a running smbd: OpenRC's -i is
# --ifexists (the first cut started a stopped service); --ifstarted is -s
assert '["rc-service", "-s", "pipeos-nas", "restart"]' in _src and '"rc-service", "-i"' not in _src
ok("nas restart helper is rc-service --ifstarted (-s), never -i")
# terminals: the same shape — no slot, no toggle-on
r = req("/api/services", {"terminals": True})
assert r["services"]["terminals"] is False and any("terminal" in p for p in r["problems"])
assert webd.read_services()["terminals"] is False
ok("services declines nas/terminals on with nothing configured, applies the rest; on with a share is a plain toggle")

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
req("/api/file-op", {"op": "mkdir", "path": "work", "name": "nope"}, expect=403)
req("/api/nas", {"shares": []}, expect=403)
req("/api/nas-password", {"name": "peek", "password": "whatever12"}, expect=403)
req("/api/nas-account", {"name": "peek2", "password": "whatever12"}, expect=403)
req("/api/update-set", {"image_update": "off"}, expect=403)
req("/api/backup", {"dest": "ext/sdx1"}, expect=403)
req("/api/flash", {"mode": "inplace", "confirm": "x"}, expect=403)
req("/api/wake", {"id": "4d4d"}, expect=403)
req("/api/secrets", expect=403)
req("/api/secrets/set", {"name": "jobs.x", "value": "y"}, expect=403)
req("/api/schedule/set", {"name": "x", "cron": "* * * * *", "prompt": "x"}, expect=403)
assert "today" in req("/api/usage")   # a viewer may read what the box spends
req("/api/usage/cap", {"usd": 5}, expect=403)
r = req("/api/password", {"current": "peekpassword", "new": "peekpassword2"})
assert r["ok"]
assert req("/api/docs")["pages"], "viewer must be able to read the docs"
ok("viewer reads but cannot mutate; can change own password")
# role "user": files move, nothing else does
cookie["v"] = admin_cookie
r = req("/api/users/add", {"name": "mover", "password": "moverpassword", "role": "user"})
assert r["ok"]
saved_admin2 = cookie["v"]
cookie["v"] = None
req("/api/login", {"username": "mover", "password": "moverpassword"})
r = req("/api/file-op", {"op": "mkdir", "path": "work", "name": "dropbox"})
assert r["ok"]
req("/api/services", {"stream": False}, expect=403)
req("/api/nas", {"shares": []}, expect=403)
req("/api/users", expect=403)
req("/api/name", {"hostname": "nope"}, expect=403)
req("/api/backup", {"dest": "ext/sdx1"}, expect=403)
req("/api/flash", {"mode": "inplace", "confirm": "x"}, expect=403)
req("/api/claude-login/start", {"billing": "claude"}, expect=403)
req("/api/claude-token", {"token": "sk-ant-api03-" + "k" * 60}, expect=403)
req("/api/wake", {"id": "4d4d"}, expect=403)
req("/api/secrets", expect=403)
req("/api/secrets/reveal", {"name": "claude_token", "password": "moverpassword"}, expect=403)
ok("role user: file-op allowed; services/nas/users/name/backup/flash/claude/wake/secrets refused")
cookie["v"] = saved_admin2
req("/api/users/del", {"name": "mover"})

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
