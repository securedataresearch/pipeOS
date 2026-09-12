#!/usr/bin/env python3
"""pipeos-webd — the box's LAN web surface: first-run wizard + management.

This is first contact for a client box. The box boots signed into nothing,
listening on :80; the first visitor claims it by setting an admin password
(the claim also writes /etc/pipeos/provisioned and runs pipeos-save, so the
claim itself survives a reboot even if the wizard is abandoned right after).
Everything else — naming the box, enabling services, Claude and pipe sign-in,
status, toggles — happens here, authenticated by that password.

stdlib only, single-threaded on purpose: one request at a time means the
claim race (two browsers on an unclaimed box) is serialized for free.

Runs as ROOT, deliberately and permanently: this daemon IS the box's control
plane (rc-service/rc-update, lbu saves, adduser via pipeos-user, mount,
reboot, /etc/pipeos writes). De-rooting efforts go into the services it
manages (svc-stream, svc-mdns), never into this process.

State files (all persisted via lbu.list + lines):
  /etc/pipeos/web-admin.conf   HASH='$6$...'   — absent == unclaimed
  /etc/pipeos/services.conf    SERVICE_PIPE=on/off ...
  /etc/pipeos/claude-auth.env  CLAUDE_CODE_OAUTH_TOKEN=...
  /etc/pipeos/stream.conf      STREAM_SRC/DST/KEY/ARGS (Phase B page)
Sessions live in /run/pipeos/web-sessions (tmpfs: a reboot logs everyone out).
"""

import base64
import collections
import glob
import hmac
import html
import fcntl
import datetime
import json
import os
import random
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import threading
import uuid
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lanid  # noqa: E402  — LAN identity + the mDNS wire, shared with mdnsd.py
import vault  # noqa: E402  — the sealed secret store (#244)
import cronspec  # noqa: E402  — the cron expression a scheduled job carries (#242)
import ledger  # noqa: E402  — every model call, costed (#246)
import cluster  # noqa: E402  — the cross-box auth primitive: a member's signature is a session (#222)

ETC = "/etc/pipeos"
ADMIN_CONF = ETC + "/web-admin.conf"
SERVICES_CONF = ETC + "/services.conf"
# The vault (#244): every service secret lives sealed in VAULT and is
# materialised into SECRETS_DIR at boot (init.d/pipeos-vault) and after
# every dashboard change (vault.export). CLAUDE_AUTH is that export; the
# legacy /etc path is read only until the first boot that migrates it.
VAULT = ETC + "/vault.sealed"
SECRETS_DIR = "/run/pipeos/secrets"
VAULT_PHRASE = "/run/pipeos/vault-phrase"
VAULT_STATUS = "/run/pipeos/vault.status"
CLAUDE_AUTH = SECRETS_DIR + "/claude.env"
CLAUDE_AUTH_LEGACY = ETC + "/claude-auth.env"
# The Claude credential, three ways (#192). A browser sign-in lands where
# `claude` itself keeps it (and refreshes it); an API key or a setup-token
# is one line in CLAUDE_AUTH. The env file wins when both exist — claude
# reads the variable every session — so a successful sign-in removes it.
CLAUDE_HOME = "/root"
CLAUDE_CREDS = CLAUDE_HOME + "/.claude/.credentials.json"
CLAUDE_BIN = "claude"
CARD = ETC + "/card.conf"
# network storage (SMB): nas.conf declares the shares (source of truth, the
# init script renders smb.conf from it at every start); mounts.conf records
# which external drives to re-mount after a reboot, by filesystem UUID.
NAS_CONF = ETC + "/nas.conf"
NAS_RENDERED_CONF = "/run/pipeos/smb.conf"   # the init renders it from nas.conf at every start
NAS_MINI_CONF = "/run/pipeos/smbpasswd.conf"  # just the private dir, for smbpasswd/pdbedit when smbd is off
# what a share-only account (pipeOS#270) can never be given: anything that
# would make it a login. One tuple, checked at creation and on every edit.
SHARE_ONLY_FORBIDS = ("password", "ssh_key", "terminal", "term_pass", "sudo", "role")
SUPPORT_CONF = ETC + "/support.conf"
SUPPORT_KEY = SECRETS_DIR + "/support_key"
SUPPORT_PUB = ETC + "/support_key.pub"
MOUNTS_CONF = ETC + "/mounts.conf"
NAS_MAX_SHARES = 8
NAS_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
PROVISIONED = ETC + "/provisioned"
TLS_DIR = ETC + "/tls"
CA_CRT = TLS_DIR + "/ca.crt"
SRV_CRT = TLS_DIR + "/server.crt"
SRV_KEY = TLS_DIR + "/server.key"
SESS_DIR = "/run/pipeos/web-sessions"
BOOT_REPORT = "/run/pipeos/boot-report"
# The LAN lobby (#lobby): mdnsd's peer cache, and how stale a row may be
# before the lobby stops showing it (3× the responder's 10 s interval).
MACHINES_ROSTER = "/work/pipeos/mdns/machines.json"
WAKE_BIN = "/usr/local/bin/pipeos-wake"
MDNS_CACHE = "/run/pipeos/mdns/peers.json"
MDNS_EXPIRE_S = 30
LAN_QUERY_S = 1.0
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# On-box operator docs (markdown, shipped with the image). The dashboard's
# Docs view lists and renders them; the box may be offline, so they live here.
DOCS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs"))
DOC_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Display order; pages not listed here sort alphabetically after these.
DOCS_ORDER = ("getting-started", "dashboard", "streaming", "nas", "users",
              "secrets", "files-and-backup", "persistence", "fence")

USERS_CONF = ETC + "/users.json"
TERMINALS_CONF = ETC + "/terminals.conf"

SESSION_IDLE_S = 24 * 3600
NICK_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
USER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# names that must never become unix accounts from the dashboard
USER_NAME_DENY = {"root", "nobody", "operator", "shutdown", "halt", "sync", "bin", "daemon", "adm"}
TERM_PORT_BASE = 7701
# The name suggester's pool (docs/cluster.md §1): classic cars, every one a
# valid NICK_RE name and a valid DNS label.
CAR_NAMES = (
    "corvette", "mustang", "giulia", "miura", "deuxchevaux", "delorean", "testarossa",
    "countach", "esprit", "stratos", "dino", "capri", "cortina", "beetle", "spitfire",
    "elan", "europa", "gullwing", "fairlady", "hakosuka", "celica", "supra", "impala",
    "belair", "charger", "challenger", "barracuda", "firebird", "camaro", "thunderbird",
    "cobra", "daytona", "pantera", "mangusta", "dauphine", "montreal", "duetto",
    "fulvia", "flaminia", "aurelia", "interceptor", "healey", "frogeye", "morgan",
    "karmann", "silvia", "skyline", "roadster", "bluebird", "quattro", "manta",
    "kadett", "escort", "anglia", "minor", "midget", "sprite", "javelin", "hornet",
)
SVC_KEYS = ("pipe", "claude", "stream", "support", "assistant", "terminals", "nas")

# How many stream targets (providers) the dashboard manages. Each is a slot in
# stream.conf: STREAM_T{N}_URL / _KEY / _ON / _NAME.
STREAM_MAX_TARGETS = 4

# The server is threaded (ThreadingHTTPServer) so a slow request — a service
# start, an update, a save — never blocks the dashboard from loading: that
# single-threaded stall was the "the box isn't coming up" symptom. Reads run
# concurrently; every state-mutating POST takes this lock, which preserves the
# claim-race serialization the single-threaded server used to give for free
# (two browsers claiming an unclaimed box still resolve to one winner).
MUTATE_LOCK = threading.Lock()

# Apple .mobileconfig that installs the box CA as a trusted root. Filled by
# serve_ca_mobileconfig; no literal braces in the body so str.format is safe.
MOBILECONFIG_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadType</key><string>com.apple.security.root</string>
      <key>PayloadVersion</key><integer>1</integer>
      <key>PayloadIdentifier</key><string>online.pipe.ca.{cuuid}</string>
      <key>PayloadUUID</key><string>{cuuid}</string>
      <key>PayloadDisplayName</key><string>pipeOS {host} CA</string>
      <key>PayloadCertificateFileName</key><string>pipeos-ca.crt</string>
      <key>PayloadContent</key>
      <data>{cert_b64}</data>
    </dict>
  </array>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadVersion</key><integer>1</integer>
  <key>PayloadIdentifier</key><string>online.pipe.profile.{puuid}</string>
  <key>PayloadUUID</key><string>{puuid}</string>
  <key>PayloadDisplayName</key><string>pipeOS {host} — secure access</string>
  <key>PayloadDescription</key><string>Trusts this pipeOS box so its dashboard shows a secure padlock.</string>
</dict>
</plist>
"""


# Linux CA installer, served at /install-ca.sh with @PEM@/@HOST@ filled in
# (str.replace, not format — the shell body is full of braces and dollars).
# One pasted command covers the system trust store AND the browser NSS stores,
# because Chrome and Firefox on Linux ignore the system store entirely.
CA_INSTALLER_TMPL = r"""#!/bin/sh
# pipeOS CA installer (Linux) — trust the box "@HOST@" for HTTPS.
# Usage:  curl -s http://@HOST@.local/install-ca.sh | sudo sh
set -e
if [ "$(id -u)" != 0 ]; then
	echo "needs root — run:  curl -s http://@HOST@.local/install-ca.sh | sudo sh" >&2
	exit 1
fi
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<'PEM'
@PEM@
PEM
chmod 644 "$tmp"

# --- system trust store (curl, package managers, most CLI tools) ---
if [ -d /usr/local/share/ca-certificates ] && command -v update-ca-certificates >/dev/null 2>&1; then
	# Debian / Ubuntu / Alpine
	cp "$tmp" /usr/local/share/ca-certificates/pipeos-ca-@HOST@.crt
	update-ca-certificates >/dev/null
	echo "installed: system trust store (update-ca-certificates)"
elif [ -d /etc/pki/ca-trust/source/anchors ]; then
	# Fedora / RHEL
	cp "$tmp" /etc/pki/ca-trust/source/anchors/pipeos-ca-@HOST@.crt
	update-ca-trust extract
	echo "installed: system trust store (update-ca-trust)"
elif [ -d /etc/ca-certificates/trust-source/anchors ]; then
	# Arch
	cp "$tmp" /etc/ca-certificates/trust-source/anchors/pipeos-ca-@HOST@.crt
	trust extract-compat
	echo "installed: system trust store (trust extract-compat)"
else
	echo "warning: no known system trust store on this distro — browsers may still work below" >&2
fi

# --- browser NSS stores: Chrome/Chromium (~/.pki/nssdb) + every Firefox profile ---
u="${SUDO_USER:-}"
if [ -n "$u" ] && [ "$u" != root ] && command -v certutil >/dev/null 2>&1; then
	home=$(getent passwd "$u" | cut -d: -f6)
	if [ ! -f "$home/.pki/nssdb/cert9.db" ]; then
		su -s /bin/sh "$u" -c "mkdir -p '$home/.pki/nssdb' && certutil -d sql:'$home/.pki/nssdb' -N --empty-password" 2>/dev/null || true
	fi
	for db in "$home/.pki/nssdb" "$home"/.mozilla/firefox/*/ "$home"/snap/firefox/common/.mozilla/firefox/*/; do
		[ -f "$db/cert9.db" ] || continue
		su -s /bin/sh "$u" -c "certutil -A -d sql:'$db' -t C,, -n 'pipeOS @HOST@ CA' -i '$tmp'" 2>/dev/null \
			&& echo "installed: browser store $db"
	done
elif [ -n "$u" ] && [ "$u" != root ]; then
	echo "note: certutil not found (package: libnss3-tools / nss-tools) — Chrome and Firefox keep their own trust store; install it and re-run, or import /ca.crt in the browser's certificate settings." >&2
fi
echo "done — reload https://@HOST@.local/ and look for the padlock (restart the browser if it was open)."
"""


def run(argv, timeout=60, input_text=None, env=None):
    """Run a command (no shell, ever). Returns (rc, stdout+stderr)."""
    try:
        p = subprocess.run(
            argv, input=input_text, capture_output=True, text=True, timeout=timeout, env=env
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out: " + " ".join(argv)
    except FileNotFoundError:
        return 127, "not found: " + argv[0]


# ---- Claude auth (#192) ---------------------------------------------------
# One browser sign-in at a time: `claude auth login` is spawned with its
# browser suppressed, the sign-in URL it prints is handed to the page, the
# owner signs in on any device and pastes the code the browser shows back
# into the page, which feeds it to the waiting process's stdin. Nothing
# here needs a terminal on either side.
CLAUDE_LOGIN_LOCK = threading.Lock()
CLAUDE_LOGIN = {"proc": None, "url": "", "billing": "", "started": 0, "out": []}
CLAUDE_LOGIN_TTL = 600
ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")


def claude_auth_env():
    """(name, value) of the line in CLAUDE_AUTH (the vault's export), or in
    the legacy plaintext file until the first boot migrates it, or (None, None)."""
    for path in (CLAUDE_AUTH, CLAUDE_AUTH_LEGACY):
        try:
            with open(path) as f:
                m = re.search(r"^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY)=(\S+)", f.read(), re.M)
        except OSError:
            continue
        if m:
            return m.group(1), m.group(2)
    return None, None


def vault_put(name, value, by=""):
    """Store one secret and re-export. Raises RuntimeError with the owner's
    sentence when the vault is locked or absent."""
    try:
        vault.set_(name, value, by=by)
        vault.export()
    except vault.Locked as e:
        raise RuntimeError("the vault is locked (%s) — unlock it under Secrets first" % e)
    except (vault.VaultError, OSError, ValueError) as e:
        raise RuntimeError("vault: %s" % e)


def vault_drop(name):
    try:
        vault.delete(name)
        vault.export()
    except (vault.VaultError, OSError, ValueError):
        pass


def vault_names():
    try:
        return {r["name"] for r in vault.list_()}
    except (vault.VaultError, OSError, ValueError):
        return set()


def vault_get(name):
    try:
        return vault.get(name)
    except (vault.VaultError, OSError, ValueError):
        return None


def claude_env(extra=None):
    env = dict(os.environ, HOME=CLAUDE_HOME)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    name, value = claude_auth_env()
    if name:
        env[name] = value
    if extra:
        env.update(extra)
    return env


def claude_status():
    """`claude auth status` as a dict — {} when the CLI is absent or mute."""
    rc, out = run([CLAUDE_BIN, "auth", "status"], timeout=20, env=claude_env())
    try:
        return json.loads(out[out.index("{"):out.rindex("}") + 1])
    except (ValueError, AttributeError):
        return {}


def claude_method():
    """How the box is connected: login | apikey | token | none, plus detail."""
    name, _ = claude_auth_env()
    if name == "ANTHROPIC_API_KEY":
        return "apikey", {}
    if name == "CLAUDE_CODE_OAUTH_TOKEN":
        return "token", {}
    st = claude_status() if os.path.exists(CLAUDE_CREDS) else {}
    if st.get("loggedIn"):
        return "login", st
    return "none", st


def claude_probe(env):
    """Does the credential answer? One real call; the last 200 chars either way."""
    try:
        p = subprocess.run(
            [CLAUDE_BIN, "-p", "reply with exactly: ok"],
            capture_output=True, text=True, timeout=90, env=env, cwd=CLAUDE_HOME,
        )
        return p.returncode == 0, (p.stdout or p.stderr or "").strip()[-200:]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, str(e)[:200]


def claude_login_reap():
    """Drop a finished or abandoned sign-in. Caller holds CLAUDE_LOGIN_LOCK."""
    p = CLAUDE_LOGIN["proc"]
    if p is None:
        return
    if p.poll() is None and time.time() - CLAUDE_LOGIN["started"] < CLAUDE_LOGIN_TTL:
        return
    try:
        p.kill()
    except OSError:
        pass
    CLAUDE_LOGIN.update({"proc": None, "url": "", "billing": "", "started": 0, "out": []})


def claude_login_start(billing):
    """Spawn `claude auth login`, return the sign-in URL it prints."""
    with CLAUDE_LOGIN_LOCK:
        claude_login_reap()
        if CLAUDE_LOGIN["proc"] is not None:
            try:
                CLAUDE_LOGIN["proc"].kill()
            except OSError:
                pass
        argv = [CLAUDE_BIN, "auth", "login", "--console" if billing == "console" else "--claudeai"]
        env = claude_env({"BROWSER": "/bin/false", "DISPLAY": "", "NO_COLOR": "1"})
        try:
            p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, env=env, cwd=CLAUDE_HOME)
        except FileNotFoundError:
            raise RuntimeError("the claude command is not installed on this box")
        out = []
        CLAUDE_LOGIN.update({"proc": p, "url": "", "billing": billing, "started": time.time(), "out": out})

        def pump():
            for line in p.stdout:
                out.append(ANSI_RE.sub("", line))
        threading.Thread(target=pump, daemon=True).start()
        deadline = time.time() + 15
        url = ""
        while time.time() < deadline and not url:
            for line in list(out):
                m = re.search(r"https://\S+", line)
                if m:
                    url = m.group(0)
                    cut = url.find("https://", 8)   # the link's text and target, concatenated
                    if cut > 0:
                        url = url[:cut]
                    break
            if not url and p.poll() is not None:
                break
            if not url:
                time.sleep(0.2)
        if not url:
            p.kill()
            CLAUDE_LOGIN.update({"proc": None, "url": "", "billing": "", "started": 0, "out": []})
            raise RuntimeError("claude did not offer a sign-in link: " + "".join(out).strip()[-200:])
        CLAUDE_LOGIN["url"] = url
        return url


def claude_login_code(code):
    """Feed the pasted code to the waiting sign-in; True when claude is now logged in."""
    with CLAUDE_LOGIN_LOCK:
        claude_login_reap()
        p = CLAUDE_LOGIN["proc"]
        if p is None:
            return False, "no sign-in is waiting — press Sign in with Claude again"
        out = CLAUDE_LOGIN["out"]
        try:
            p.stdin.write(code + "\n")
            p.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
        time.sleep(0.2)
        text = "".join(out)
        CLAUDE_LOGIN.update({"proc": None, "url": "", "billing": "", "started": 0, "out": []})
    if os.path.exists(CLAUDE_CREDS) and claude_status().get("loggedIn"):
        return True, ""
    m = re.search(r"(Login failed[^\n]*|[Ee]rror[^\n]*)", text)
    return False, (m.group(1) if m else text.strip()[-200:] or "sign-in did not complete")


# ---- the LAN lobby ------------------------------------------------------------
# Every Machine serves the same page: itself plus whatever mdnsd has heard.
# Stateless on purpose — a view of the network needs no leader.

def peers():
    """(list of peer dicts, discovery_ok). A cache the responder has not
    touched for a minute is a dead responder's memory, not the LAN."""
    try:
        with open(MDNS_CACHE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return [], False
    now = time.time()
    if now - float(d.get("written", 0)) > 60:
        return [], False
    out = [p for p in d.get("peers", {}).values() if now - float(p.get("last_seen", 0)) <= MDNS_EXPIRE_S]
    return out, True


def roster():
    """Every Machine the responder has ever seen on this LAN (#241), keyed
    by id, with the MAC it advertised. Kept on /work by mdnsd and never
    pruned there — a Machine that is off is one that is here and not in
    peers(). Empty when /work has no roster yet."""
    try:
        with open(MACHINES_ROSTER) as f:
            d = json.load(f)
        return d.get("machines", {}) if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def box_hostname():
    return socket.gethostname().lower()


ID_NAME_RE = re.compile(r"pipeos(-[0-9a-f]{4})?", re.I)


def box_name():
    """The owner's alias (docs/cluster.md §1): NAME= in the card. On a box
    imaged before the chassis-id scheme the hostname itself was the name,
    so it stands in until the box is re-claimed."""
    name = card_get("NAME").strip().lower()
    if name:
        return name
    hn = box_hostname()
    return "" if ID_NAME_RE.fullmatch(hn) else hn


def self_entry():
    m4 = lanid.mac4()
    lan = lanid.lan_name(m4)
    name = box_name()
    img = lanid.image_info(FLASH_IMAGE_TXT)
    return {"id": m4, "name": name,
            "host": (name or lan) + ".local",
            "ip": primary_ip()[0], "claimed": claimed(),
            "verdict": lanid.verdict_line(BOOT_REPORT), "commit": img["commit"][:12],
            "built": img["built"], "model": lanid.model(), "self": True}


def lobby_entries():
    """Self, the live peers, then every rostered Machine that is not live —
    a grey row (awake False) with its last address and MAC, so the Network
    view can offer Wake (#241). Awake first, claimed first, then by name."""
    ps, ok = peers()
    me = self_entry()
    rows = [dict(me, awake=True)] + [dict(p, self=False, awake=True) for p in ps]
    live = {r["id"] for r in rows}
    for pid, r in roster().items():
        if pid in live or pid == me["id"]:
            continue
        rows.append({"id": pid, "name": r.get("name", ""), "host": r.get("host", ""), "ip": r.get("ip", ""),
                     "claimed": bool(r.get("claimed")), "verdict": "", "commit": "", "built": "",
                     "model": r.get("model", ""), "mac": r.get("mac", ""), "last_seen": r.get("last_seen", 0),
                     "self": False, "awake": False})
    rows.sort(key=lambda r: (not r.get("awake"), not r.get("claimed"), (r.get("name") or r.get("host") or "").lower()))
    return rows, ok


def name_taken(nick):
    """The refusal, or "". First what the responder already knows, then
    one real question to the LAN — a printer or a laptop called studio is
    not a Machine but still owns studio.local."""
    n = nick.lower()
    ps, _ok = peers()
    for p in ps:
        if n in ((p.get("name") or "").lower(), (p.get("host") or "").split(".")[0].lower(), "pipeos-" + p.get("id", "")):
            return "%s is already a Machine on this network — pick another name" % nick
    if lanid.query_a(n + ".local", LAN_QUERY_S) - lanid.local_ips():
        return "%s.local already answers on this network — pick another name" % nick
    return ""


def claimed():
    try:
        return os.path.getsize(ADMIN_CONF) > 0
    except OSError:
        return False


def write_private(path, data):
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.rename(tmp, path)


def hash_password(pw):
    rc, out = run(["openssl", "passwd", "-6", "-stdin"], input_text=pw + "\n")
    out = out.strip()
    if rc != 0 or not out.startswith("$6$"):
        raise RuntimeError("openssl passwd failed")
    return out


def check_hash(pw, stored):
    if not stored or not stored.startswith("$6$"):
        return False
    salt = stored.split("$")[2]
    rc, out = run(
        ["openssl", "passwd", "-6", "-salt", salt, "-stdin"], input_text=pw + "\n"
    )
    return rc == 0 and hmac.compare_digest(out.strip(), stored)


def legacy_admin_hash():
    try:
        with open(ADMIN_CONF) as f:
            m = re.search(r"HASH='(\$6\$[^']+)'", f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def check_password(pw):
    # legacy single-admin check — still what api_claim/first-login rest on
    return check_hash(pw, legacy_admin_hash())


# ---- users -----------------------------------------------------------------
# users.json is the dashboard's user store: web login (role+hash) plus flags
# for the unix account, sudo, and the browser terminal. Unix truth itself
# stays in /etc/passwd|shadow|group (lbu-captured); pipeos-user edits those.
# LOCKOUT SAFETY: if users.json is missing or corrupt, read_users() serves the
# legacy web-admin.conf admin instead — the dashboard must always be
# reachable with the original admin password.

def read_users():
    try:
        with open(USERS_CONF) as f:
            j = json.load(f)
        users = j.get("users")
        if isinstance(users, list) and any(
                u.get("role") == "admin" and not u.get("disabled") for u in users):
            return users
    except (OSError, ValueError, AttributeError):
        pass
    h = legacy_admin_hash()
    return [{"name": "admin", "role": "admin", "hash": h}] if h else []


def write_users(users):
    write_private(USERS_CONF, json.dumps({"version": 1, "users": users}, indent=1) + "\n")


def find_user(users, name):
    for u in users:
        if u.get("name") == name:
            return u
    return None


def write_terminals(users):
    """Generate the shell-sourceable conf pipeos-terminals reads. Values pass
    the same single-quote guard as stream.conf (they are shell-quoted)."""
    lines, n = [], 0
    for u in users:
        if not (u.get("terminal") and u.get("unix") and u.get("term_pass")
                and u.get("term_port") and not u.get("disabled")):
            continue
        if any(c in u["term_pass"] for c in "'\n\r\0"):
            continue
        n += 1
        lines.append("TERM_%d_USER='%s'\nTERM_%d_PORT='%s'\nTERM_%d_PASS='%s'\n"
                     % (n, u["name"], n, int(u["term_port"]), n, u["term_pass"]))
    write_private(TERMINALS_CONF, "".join(lines))
    return n


# ---- sessions --------------------------------------------------------------

def new_session(user="admin", role="admin"):
    os.makedirs(SESS_DIR, mode=0o700, exist_ok=True)
    tok = secrets.token_hex(32)
    write_private(os.path.join(SESS_DIR, tok),
                  json.dumps({"user": user, "role": role}))
    return tok


def valid_session(tok):
    """The session record {user, role}, or None. A pre-multi-user session
    file (bare timestamp) reads as the admin — live sessions survive a webd
    upgrade."""
    if not tok or not re.fullmatch(r"[0-9a-f]{64}", tok):
        return None
    path = os.path.join(SESS_DIR, tok)
    try:
        if time.time() - os.path.getmtime(path) > SESSION_IDLE_S:
            os.unlink(path)
            return None
        with open(path) as f:
            body = f.read()
        os.utime(path)
    except OSError:
        return None
    # Pre-multi-user session files hold a bare timestamp — which json.loads
    # parses SUCCESSFULLY (as an int), so "is it a dict" is the real test,
    # not "does it parse". The int case crashed every request that carried an
    # old cookie (found live on basho_box: the owner's browser got a
    # connection reset on /api/state while cookie-less curl looked healthy).
    try:
        j = json.loads(body)
    except ValueError:
        j = None
    if not isinstance(j, dict):
        return {"user": "admin", "role": "admin"}
    return {"user": j.get("user") or "admin", "role": j.get("role") or "admin"}


def drop_user_sessions(name):
    try:
        for tok in os.listdir(SESS_DIR):
            s = valid_session(tok)
            if s and s["user"] == name:
                os.unlink(os.path.join(SESS_DIR, tok))
    except OSError:
        pass


def drop_session(tok):
    if tok and re.fullmatch(r"[0-9a-f]{64}", tok):
        try:
            os.unlink(os.path.join(SESS_DIR, tok))
        except OSError:
            pass


# ---- box state -------------------------------------------------------------

def read_services():
    svcs = {k: False for k in SVC_KEYS}
    try:
        with open(SERVICES_CONF) as f:
            for line in f:
                m = re.match(r"^SERVICE_([A-Z]+)=(on|off)\s*$", line)
                if m and m.group(1).lower() in svcs:
                    svcs[m.group(1).lower()] = m.group(2) == "on"
    except OSError:
        pass
    return svcs


def write_services(svcs):
    body = "".join(
        "SERVICE_%s=%s\n" % (k.upper(), "on" if svcs[k] else "off") for k in SVC_KEYS
    )
    write_private(SERVICES_CONF, body)
    os.chmod(SERVICES_CONF, 0o644)


def daemons_for(svcs):
    """Map the declared service set to OpenRC service names."""
    out = []
    if svcs["pipe"]:
        out.append("pipe-daemon")
        if svcs["claude"]:
            out.append("pipebox-listener")
    if svcs["stream"]:
        out.append("pipeos-stream")
    if svcs["support"]:
        out.append("pipeos-support")
    if svcs.get("assistant"):
        out.append("pipeos-assistant")
    if svcs.get("terminals"):
        out.append("pipeos-terminals")
    if svcs.get("nas"):
        out.append("pipeos-nas")
    return out


def _read_phrase():
    try:
        with open(VAULT_PHRASE) as f:
            return f.read().strip()
    except OSError:
        return ""


def nas_restart_if_running():
    """Bounce smbd so it reopens the passdb (a share change, or a password
    set — sealing writes the db as a new inode, pipeOS#268). Returns the
    problem string, or "" when it restarted or was not running."""
    # -s = --ifstarted. NOT -i: OpenRC reads -i as --ifexists, and the first
    # cut had it — a passdb edit then START-ed storage the owner had switched
    # off (the re-drill on zero, 2026-09-12: "no configured share is usable").
    rc, out = run(["rc-service", "-s", "pipeos-nas", "restart"], timeout=60)
    if rc != 0:
        return "network storage did not restart: " + out.strip()[-200:]
    return ""


def restart_secret_consumers():
    """After an unlock the exports exist for the first time this boot:
    start what the owner has on and what refused to start without them."""
    problems = []
    for svc in daemons_for(read_services()):
        if svc in ("pipebox-listener", "pipeos-support", "pipeos-assistant", "pipeos-stream", "pipeos-nas"):
            rc, out = run(["rc-service", svc, "restart"], timeout=120)
            if rc != 0:
                problems.append("%s: %s" % (svc, out.strip()[-160:]))
    return problems


def support_ensure_key():
    """The box's tunnel identity, made on first enable (ed25519, no
    passphrase — a supervised daemon cannot type one). The private half
    lives in the vault and is exported to SUPPORT_KEY; the public half is
    a plain file under /etc (it is public). Idempotent."""
    if "support_key" in vault_names() and os.path.exists(SUPPORT_PUB):
        if not os.path.exists(SUPPORT_KEY):
            try:
                vault.export()
            except (vault.VaultError, OSError, ValueError):
                return False
        return True
    os.makedirs(os.path.dirname(SUPPORT_KEY), mode=0o700, exist_ok=True)
    tmp = SUPPORT_KEY + ".gen"
    for p in (tmp, tmp + ".pub"):
        try:
            os.unlink(p)
        except OSError:
            pass
    rc, _ = run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                 "pipeos-support-" + (card_get("NICK") or socket.gethostname()),
                 "-f", tmp], timeout=30)
    if rc != 0 or not os.path.exists(tmp + ".pub"):
        return False
    try:
        with open(tmp, "rb") as f:
            priv = f.read()
        with open(tmp + ".pub") as f:
            pub = f.read()
        vault_put("support_key", priv, by="system")
        write_private(SUPPORT_PUB, pub)
        os.chmod(SUPPORT_PUB, 0o644)
    except (OSError, RuntimeError):
        return False
    finally:
        for p in (tmp, tmp + ".pub"):
            try:
                os.unlink(p)
            except OSError:
                pass
    return os.path.exists(SUPPORT_KEY)


def support_info():
    conf = read_conf_values(SUPPORT_CONF, ["SUPPORT_RELAY", "SUPPORT_PORT"])
    try:
        with open(SUPPORT_PUB) as f:
            pub = f.read().strip()
    except OSError:
        pub = ""
    return {"pubkey": pub, "relay": conf["SUPPORT_RELAY"], "port": conf["SUPPORT_PORT"]}


def apply_services(svcs):
    """Mirror the declaration into rc-update + running state. Best-effort per
    daemon; returns a list of human-readable problems (empty == clean)."""
    problems = []
    want = set(daemons_for(svcs))
    managed = ("pipe-daemon", "pipebox-listener", "pipeos-stream",
               "pipeos-support", "pipeos-assistant", "pipeos-terminals",
               "pipeos-nas")
    if "pipeos-support" in want and not support_ensure_key():
        problems.append("could not create the support key (ssh-keygen)")
    for svc in managed:
        if svc in want:
            # STREAM_BOOT=0 = "stream now when asked, but not by itself at
            # boot": start it, keep it out of the runlevel. The boot
            # reconciler (etc/local.d/pipeos-services.start) applies the same
            # rule, so toggle-time and boot-time can never disagree.
            if (svc == "pipeos-stream"
                    and read_conf_values(STREAM_CONF, ["STREAM_BOOT"])["STREAM_BOOT"] == "0"):
                run(["rc-update", "del", svc, "default"])
            else:
                run(["rc-update", "add", svc, "default"])
            rc, out = run(["rc-service", svc, "start"], timeout=150)
            if rc != 0:
                # pipeos-stream / pipeos-support refuse to start unconfigured —
                # that is their documented shape, not a broken box.
                if svc == "pipeos-stream":
                    problems.append("streaming is enabled but not configured yet")
                elif svc == "pipeos-support":
                    problems.append("support access is enabled but no relay is configured yet")
                elif svc == "pipeos-assistant":
                    problems.append("the assistant terminal is enabled but has no password yet")
                elif svc == "pipeos-terminals":
                    problems.append("user terminals are enabled but no user has one configured yet")
                elif svc == "pipeos-nas":
                    problems.append("network storage is enabled but no usable share is configured yet")
                else:
                    problems.append("%s failed to start: %s" % (svc, out.strip()[-200:]))
        else:
            run(["rc-update", "del", svc, "default"])
            run(["rc-service", svc, "stop"], timeout=60)
    return problems


def card_get(key):
    try:
        with open(CARD) as f:
            m = re.search(r"^%s=([^\n]*)$" % re.escape(key), f.read(), re.M)
            return m.group(1) if m else ""
    except OSError:
        return ""


def card_set(updates):
    """sed-equivalent in-place card edit (same pattern pipebox-setup uses),
    then regenerate the derived files. Raises on failure."""
    with open(CARD) as f:
        text = f.read()
    for key, val in updates.items():
        text, n = re.subn(r"^%s=.*$" % re.escape(key), "%s=%s" % (key, val), text, flags=re.M)
        if n != 1:
            raise RuntimeError("card has no %s= line" % key)
    write_private(CARD, text)
    os.chmod(CARD, 0o644)
    rc, out = run(["pipebox-card", "generate", "--card", CARD], timeout=120)
    if rc != 0:
        raise RuntimeError("card regeneration failed: " + out.strip()[-300:])


def save_state():
    rc, out = run(["pipeos-save"], timeout=300)
    return rc == 0, out.strip()[-300:]


def boot_report():
    try:
        with open(BOOT_REPORT) as f:
            return f.read()
    except OSError:
        return ""


def uptime_disk():
    up = ""
    try:
        with open("/proc/uptime") as f:
            up = int(float(f.read().split()[0]))
    except (OSError, ValueError):
        pass
    rc, out = run(["df", "-k", "/work"], timeout=10)
    pct, free_mb = None, None
    if rc == 0 and len(out.splitlines()) >= 2:
        parts = out.splitlines()[1].split()
        try:
            free_mb = int(parts[3]) // 1024
            pct = int(parts[4].rstrip("%"))
        except (IndexError, ValueError):
            pass
    return up, pct, free_mb


def proc_metrics():
    """The subprocess-free half of the metrics: pure /proc + /sys reads,
    cheap enough for a 10s sampler. A missing sensor reports None rather
    than failing the whole call."""
    m = {"load1": None, "load5": None, "load15": None, "ncpu": None,
         "mem_total_mb": None, "mem_avail_mb": None, "temp_c": None,
         "cpu_busy": None, "cpu_total": None}
    # /proc/stat's aggregate cpu line, as jiffies. Utilisation is the delta
    # between two samples (busy/total), computed where two samples exist —
    # the sampler's ring and the /api/metrics tail — never here.
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        if parts and parts[0] == "cpu":
            j = [int(x) for x in parts[1:]]
            idle = j[3] + (j[4] if len(j) > 4 else 0)   # idle + iowait
            m["cpu_total"], m["cpu_busy"] = sum(j), sum(j) - idle
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open("/proc/loadavg") as f:
            l1, l5, l15 = f.read().split()[:3]
        m["load1"], m["load5"], m["load15"] = float(l1), float(l5), float(l15)
    except (OSError, ValueError):
        pass
    try:
        m["ncpu"] = os.cpu_count()
    except OSError:
        pass
    try:
        fields = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                fields[k] = v
        m["mem_total_mb"] = int(fields["MemTotal"].split()[0]) // 1024
        m["mem_avail_mb"] = int(fields["MemAvailable"].split()[0]) // 1024
    except (OSError, KeyError, ValueError, IndexError):
        pass
    best = None
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            with open(zone) as f:
                t = int(f.read().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if 0 < t < 150 and (best is None or t > best):
            best = t  # hottest plausible zone ≈ the CPU package
    if best is not None:
        m["temp_c"] = round(best, 1)
    return m


def root_pct():
    # root is tmpfs — its fill level is RAM the overlay is eating
    rc, out = run(["df", "-k", "/"], timeout=10)
    if rc == 0 and len(out.splitlines()) >= 2:
        try:
            return int(out.splitlines()[1].split()[4].rstrip("%"))
        except (IndexError, ValueError):
            pass
    return None


def system_metrics():
    m = proc_metrics()
    m["root_pct"] = root_pct()
    return m


def net_counters():
    """(rx_bytes, tx_bytes) summed over every interface but lo, plus the
    primary interface name. Raw counters — rates come from deltas."""
    rx = tx = 0
    iface = None
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                name, _, rest = line.partition(":")
                name = name.strip()
                if name == "lo":
                    continue
                parts = rest.split()
                if len(parts) < 10:
                    continue
                r, t = int(parts[0]), int(parts[8])
                rx += r; tx += t
                if iface is None and r > 0:
                    iface = name  # first interface that has actually received
    except (OSError, ValueError, IndexError):
        return None, None, None
    return rx, tx, iface


def primary_ip():
    """(ip, iface) of the first global IPv4 — same source pipeos-tls-init
    uses for the cert SAN."""
    rc, out = run(["ip", "-4", "-o", "addr", "show", "scope", "global"], timeout=10)
    if rc == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                return parts[3].split("/")[0], parts[1]
    return None, None


# The only pipe preferences the dashboard may flip (pipe set's own settable set)
PIPE_PREFS = ("dm_relay", "remember_login", "agent_events")

# Assistant backends the box may ship: claude-code, hermes-agent.
# The id doubles as the binary name; a backend is offered only when installed.
ASSISTANT_BACKENDS = ("claude", "hermes")

# bare (no --boot) is read-only by contract — pipeOS#13's "safe to run any
# time, by anyone, on any box": no DM, no writes, no service starts
SELFCHECK_BIN = "/usr/local/bin/pipeos-selfcheck"

# ---- disks -----------------------------------------------------------------
# Inventory straight from /sys/block + busybox blkid (no lsblk on the image).
# A disk is PROTECTED — untouchable by every disk-op — when any part of it is
# the boot media (LABEL=PIPEOS), the work disk (LABEL=PIPEWORK), or mounted at
# a system path. Externals mount under /media/ext/<dev> and appear as extra
# roots in the file explorer.

FILES_WORK = "/work"
FILES_EXT_BASE = "/media/ext"
PROTECTED_LABELS = ("PIPEOS", "PIPEWORK")
PROTECTED_MOUNTS = ("/", "/work", "/media/usb")
DEV_RE = re.compile(r"^[a-z][a-z0-9]{1,31}$")


def blkid_all():
    """dev -> {label, fstype, uuid} for every device blkid knows."""
    out = {}
    rc, text = run(["blkid"], timeout=15)
    if rc != 0:
        return out
    for line in text.splitlines():
        dev, _, rest = line.partition(":")
        if not dev.startswith("/dev/"):
            continue
        d = {}
        for m in re.finditer(r'(\w+)="([^"]*)"', rest):
            d[m.group(1).lower()] = m.group(2)
        out[dev] = {"label": d.get("label", ""), "fstype": d.get("type", ""),
                    "uuid": d.get("uuid", "")}
    return out


def mounts_by_dev():
    out = {}
    try:
        with open("/proc/mounts") as f:
            for line in f:
                dev, mp = line.split()[:2]
                if dev.startswith("/dev/"):
                    out[os.path.realpath(dev)] = mp.replace("\\040", " ")
    except OSError:
        pass
    return out


def read_mount_uuids():
    """The external drives the owner mounted, by filesystem UUID — the set
    pipeos-mounts.start replays after a reboot (device names move; UUIDs
    are the identity)."""
    return read_conf_values(MOUNTS_CONF, ["MOUNT_UUIDS"])["MOUNT_UUIDS"].split()


def write_mount_uuids(uuids):
    seen = list(dict.fromkeys(u for u in uuids if u))
    write_private(MOUNTS_CONF, "MOUNT_UUIDS='%s'\n" % " ".join(seen))
    os.chmod(MOUNTS_CONF, 0o644)


def _sysread(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def disk_inventory():
    ids = blkid_all()
    mounts = mounts_by_dev()
    disks = []
    try:
        names = sorted(os.listdir("/sys/block"))
    except OSError:
        return disks
    for name in names:
        if name.startswith(("loop", "ram", "zram", "dm-")):
            continue
        base = "/sys/block/" + name
        try:
            size = int(_sysread(base + "/size") or 0) * 512
        except ValueError:
            size = 0
        if size == 0:
            continue
        model = _sysread(base + "/device/model")
        removable = _sysread(base + "/removable") == "1"
        rows = []
        for sub in sorted(os.listdir(base)):
            if sub.startswith(name) and os.path.isdir(base + "/" + sub):
                rows.append(sub)
        if not rows:
            rows = [name]  # partitionless disk: the fs lives on the device
        parts, protected = [], False
        for sub in rows:
            dev = "/dev/" + sub
            info = ids.get(dev, {})
            mp = mounts.get(os.path.realpath(dev), "")
            try:
                psize = int(_sysread("/sys/class/block/%s/size" % sub) or 0) * 512
            except ValueError:
                psize = 0
            if info.get("label") in PROTECTED_LABELS or mp in PROTECTED_MOUNTS:
                protected = True
            parts.append({"dev": sub, "size": psize,
                          "fstype": info.get("fstype", ""),
                          "label": info.get("label", ""), "mount": mp})
        disks.append({"dev": name, "size": size, "model": model,
                      "removable": removable, "protected": protected,
                      "parts": parts})
    return disks


# ---- backup ----------------------------------------------------------------
# One button copies what a restore needs onto a mounted external: the
# identity bundle (a fresh apkovl — keys, tokens, config), the work disk and
# the boot media, into pipeos-backup/<nick>/{identity,work,boot-media}. The
# copying itself is /usr/local/bin/pipeos-backup — one implementation for
# the dashboard and the `pipeos backup` verb (#180) — and this is only the
# thread that runs it and the state the card polls.

BACKUP_BIN = "/usr/local/bin/pipeos-backup"
BACKUP_STATE = "/run/pipeos/backup.state"
BACKUP = {"running": False, "dest": "", "started": 0, "ok": None, "detail": ""}
BACKUP_LOCK = threading.Lock()


def backup_worker(destroot, scope):
    argv = [BACKUP_BIN] + (["--identity-only"] if scope == "identity" else []) + [destroot]
    rc, out = run(argv, timeout=6 * 3600)
    ok = rc == 0
    detail = "" if ok else out.strip()[-300:]
    with BACKUP_LOCK:
        BACKUP.update({"running": False, "ok": ok, "detail": detail})


def backup_step():
    """The step pipeos-backup is on (identity|work|media|done|failed), or ""."""
    try:
        with open(BACKUP_STATE) as f:
            for line in f:
                if line.startswith("step="):
                    return line[5:].strip()
    except OSError:
        pass
    return ""


# ---- flash -----------------------------------------------------------------
# The dashboard face of pipeos-flash (#181): a thread that runs the binary,
# state the Live disk card polls, and the release's image digest. All the
# judgement — geometry, merge, quiesce — is the script's; see check-flash.py.

FLASH_BIN = "/usr/local/bin/pipeos-flash"
FLASH_STATE = "/run/pipeos/flash.state"
FLASH_PROGRESS = "/run/pipeos/flash.progress"
FLASH_APPLIED = "/work/.pipeos/flash.applied"
FLASH_IMAGE_TXT = "/media/usb/pipeos-image.txt"
FLASH = {"running": False, "mode": "", "started": 0, "ok": None, "detail": ""}
FLASH_LOCK = threading.Lock()


def flash_worker(argv):
    rc, out = run(argv, timeout=3 * 3600)
    with FLASH_LOCK:
        FLASH.update({"running": False, "ok": rc == 0,
                      "detail": "" if rc == 0 else out.strip()[-300:]})


def flash_step():
    try:
        with open(FLASH_STATE) as f:
            for line in f:
                if line.startswith("step="):
                    return line[5:].strip()
    except OSError:
        pass
    return ""


def release_sums():
    """The release's SHA256SUMS as {asset: digest}, or {} when unreachable.
    One fetch for both the update pill and the Live disk card."""
    conf = read_conf_values(SELFUPDATE_CONF, ["UPDATE_RELEASE_URL"])
    url = (conf["UPDATE_RELEASE_URL"] or "").rstrip("/")
    if not url:
        return {}
    try:
        with urllib.request.urlopen(url + "/SHA256SUMS", timeout=10) as r:
            out = {}
            for line in r.read().decode().splitlines():
                parts = line.split()
                if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                    out[parts[1]] = parts[0]
            return out
    except OSError:
        return {}


def dev_protected(sub):
    """True unless `sub` (a partition or disk name) sits on a disk the
    inventory calls safe."""
    for d in disk_inventory():
        if d["dev"] == sub or any(p["dev"] == sub for p in d["parts"]):
            return d["protected"]
    return True  # unknown device = protected

# ---- metrics history: one sample every SAMPLE_S, 24h ring -------------------

SAMPLE_S = 10
METRICS_LOCK = threading.Lock()
METRICS_HIST = collections.deque(maxlen=(24 * 3600) // SAMPLE_S)


def metrics_sampler():
    """Daemon thread. The df-based numbers fork a subprocess, so they run
    every 6th tick (once a minute) and ride along stale in between."""
    slow = {"work_pct": None, "root_pct": None}
    tick = 0
    while True:
        try:
            p = proc_metrics()
            rx, tx, _ = net_counters()
            if tick % 6 == 0:
                _, wpct, _ = uptime_disk()
                slow = {"work_pct": wpct, "root_pct": root_pct()}
            mem = None
            if p["mem_total_mb"] and p["mem_avail_mb"] is not None:
                mem = round((p["mem_total_mb"] - p["mem_avail_mb"]) * 100.0
                            / p["mem_total_mb"], 1)
            with METRICS_LOCK:
                METRICS_HIST.append({
                    "t": int(time.time()), "load1": p["load1"], "mem": mem,
                    "cpu_busy": p["cpu_busy"], "cpu_total": p["cpu_total"],
                    "temp": p["temp_c"], "rx": rx, "tx": tx,
                    "work_pct": slow["work_pct"], "root_pct": slow["root_pct"],
                })
        except Exception:
            pass  # a bad sample must never kill the sampler
        tick += 1
        time.sleep(SAMPLE_S)


def cpu_pct_between(a, b):
    """Utilisation over the interval from sample a to sample b, as a whole
    percent; None when either side lacks jiffies or the counters went
    backwards (a reboot's worth of history in the ring)."""
    try:
        db = b["cpu_busy"] - a["cpu_busy"]
        dt = b["cpu_total"] - a["cpu_total"]
    except (KeyError, TypeError):
        return None
    if dt <= 0 or db < 0:
        return None
    return round(min(100.0, db * 100.0 / dt))


def _bucket(vals, how):
    vs = [v for v in vals if v is not None]
    if not vs:
        return None
    if how == "max":
        return max(vs)
    return round(sum(vs) / len(vs), 2)


def metrics_history(span_s):
    """Series for the last span_s seconds, downsampled to <=360 points.
    Rates are per-pair deltas with resets clamped to zero."""
    cut = time.time() - span_s
    with METRICS_LOCK:
        rows = [r for r in METRICS_HIST if r["t"] >= cut]
    # rates first, on the raw samples
    rates, pcts = [], []
    for i, r in enumerate(rows):
        bps = (None, None)
        if i and r["rx"] is not None and rows[i - 1]["rx"] is not None:
            dt = max(1, r["t"] - rows[i - 1]["t"])
            bps = (max(0, r["rx"] - rows[i - 1]["rx"]) * 8 // dt,
                   max(0, r["tx"] - rows[i - 1]["tx"]) * 8 // dt)
        rates.append(bps)
        pcts.append(cpu_pct_between(rows[i - 1], r) if i else None)
    k = max(1, (len(rows) + 359) // 360)
    out = {"interval_s": k * SAMPLE_S, "t0": rows[0]["t"] if rows else None,
           "cpu": [], "cpu_pct": [], "mem_pct": [], "temp": [], "rx_bps": [],
           "tx_bps": [], "work_pct": [], "root_pct": []}
    for i in range(0, len(rows), k):
        b, rb, pb = rows[i:i + k], rates[i:i + k], pcts[i:i + k]
        out["cpu"].append(_bucket([r["load1"] for r in b], "max"))
        out["cpu_pct"].append(_bucket(pb, "avg"))
        out["mem_pct"].append(_bucket([r["mem"] for r in b], "avg"))
        out["temp"].append(_bucket([r["temp"] for r in b], "max"))
        out["rx_bps"].append(_bucket([r[0] for r in rb], "avg"))
        out["tx_bps"].append(_bucket([r[1] for r in rb], "avg"))
        out["work_pct"].append(_bucket([r["work_pct"] for r in b], "avg"))
        out["root_pct"].append(_bucket([r["root_pct"] for r in b], "avg"))
    return out


# ---- HTTP ------------------------------------------------------------------


def user_add(body):
    """Create an account. Returns (200, payload) or (code, message); the
    caller saves. Shared by /api/users/add, /api/nas-account and the
    `pipeos nas account` verb (pipeOS#270)."""
    name = (body.get("name") or "").strip()
    if not USER_NAME_RE.fullmatch(name) or name in USER_NAME_DENY:
        return (400, "user name: a-z, digits, _ -, max 32, lowercase first")
    users = read_users()
    if find_user(users, name):
        return (400, "that user already exists")
    role = body.get("role") or "viewer"
    if role not in ("admin", "user", "viewer"):
        return (400, "role must be admin, user or viewer")
    # A share-only account (pipeOS#270) is a unix login that exists for
    # samba's passdb and nothing else: /sbin/nologin, no key, no terminal,
    # no sudo, and NO dashboard sign-in (no hash — api_login refuses a
    # user without one). Made from the Storage page, next to the share.
    want_share = bool(body.get("share"))
    want_unix = bool(body.get("unix")) or want_share
    want_sudo = bool(body.get("sudo"))
    want_term = bool(body.get("terminal"))
    term_pass = (body.get("term_pass") or "").strip()
    key = (body.get("ssh_key") or "").strip()
    if want_share and any(body.get(k) for k in SHARE_ONLY_FORBIDS):
        return (400, "a share-only account has no shell — no ssh key, terminal, sudo, role or dashboard "
                     "password; its only credential is the SMB password")
    if want_share:
        role = "viewer"     # never an admin the lockout guards would count
    pw = body.get("password") or ""
    if not want_share and len(pw) < 8:
        return (400, "password must be at least 8 characters")
    if want_sudo and not want_unix:
        return (400, "sudo needs an ssh/terminal account (enable unix access)")
    if want_term and not want_unix:
        want_unix = True  # a terminal IS a unix login
    if want_unix and not (key or want_term or want_share):
        return (400, "an ssh account needs a public key (or enable the browser terminal)")
    if want_term and (not term_pass or any(c in term_pass for c in "'\n\r\0")):
        return (400, "the browser terminal needs its own password (no quotes/newlines)")
    if want_unix and name in [l.split(":")[0] for l in
                              open("/etc/passwd").read().splitlines() if l]:
        return (400, "that name is taken by a system account")
    h = None
    if not want_share:
        try:
            h = hash_password(pw)
        except RuntimeError as e:
            return (500, str(e))
    problems = []
    if want_unix:
        rc, out = run(["/usr/local/bin/pipeos-user", "add", name] + (["--nologin"] if want_share else []),
                      timeout=30)
        if rc != 0:
            return (500, "could not create the unix user: " + out.strip()[-200:])
        if key:
            rc, out = run(["/usr/local/bin/pipeos-user", "set-key", name],
                          input_text=key + "\n")
            if rc != 0:
                problems.append("ssh key not installed: " + out.strip()[-200:])
        if want_sudo:
            run(["/usr/local/bin/pipeos-user", "grant-sudo", name])
            run(["/usr/local/bin/pipeos-user", "set-hash", name],
                input_text=h + "\n")
    u = {"name": name, "role": role, "unix": want_unix,
         "sudo": want_sudo, "terminal": want_term,
         "created": int(time.time())}
    if want_share:
        u["share"] = True     # no "hash": this account cannot sign in
    else:
        u["hash"] = h
    if want_term:
        ports = [x.get("term_port") or 0 for x in users]
        u["term_port"] = max([TERM_PORT_BASE - 1] + ports) + 1
        u["term_pass"] = term_pass
    users.append(u)
    write_users(users)
    if want_term:
        problems += apply_terminals(users)   # unchanged conf otherwise: no ttyd bounce
    return (200, {"ok": True, "problems": problems, "term_port": u.get("term_port")})

def apply_terminals(users):
    """Regenerate terminals.conf; keep the service running iff it should."""
    n = write_terminals(users)
    svcs = read_services()
    problems = []
    if n and not svcs.get("terminals"):
        svcs["terminals"] = True
        write_services(svcs)
        problems += apply_services(svcs)
    elif svcs.get("terminals"):
        if n:
            run(["rc-service", "pipeos-terminals", "restart"], timeout=60)
        else:
            svcs["terminals"] = False
            write_services(svcs)
            problems += apply_services(svcs)
    return problems

def nas_read_shares():
    keys = []
    for n in range(1, NAS_MAX_SHARES + 1):
        keys += ["NAS_S%d_%s" % (n, k) for k in ("NAME", "ROOT", "REL", "USERS")]
    vals = read_conf_values(NAS_CONF, keys)
    out = []
    for n in range(1, NAS_MAX_SHARES + 1):
        if vals["NAS_S%d_NAME" % n]:
            out.append({
                "name": vals["NAS_S%d_NAME" % n],
                "root": vals["NAS_S%d_ROOT" % n],
                "rel": vals["NAS_S%d_REL" % n],
                "users": vals["NAS_S%d_USERS" % n].split(),
            })
    return out


def nas_commit(shares, enable):
    """Write nas.conf from SHARES ([{name, root, rel, users}], the shape
    nas_read_shares returns; unused slots blanked) and make the service
    match. ENABLE is the caller's intent: the Storage page configuring a
    share means "turn it on" (configure implies enable, like streaming);
    the delete path only keeps what the owner had — restart if running,
    off when no share is left, never back on behind their back."""
    fields = {}
    for n in range(1, NAS_MAX_SHARES + 1):
        sh = shares[n - 1] if n <= len(shares) else {}
        fields["NAS_S%d_NAME" % n] = sh.get("name", "")
        fields["NAS_S%d_ROOT" % n] = sh.get("root", "")
        fields["NAS_S%d_REL" % n] = sh.get("rel", "")
        fields["NAS_S%d_USERS" % n] = " ".join(sh.get("users", []))
    write_private(NAS_CONF, "".join("%s='%s'\n" % (k, fields[k]) for k in sorted(fields)))
    problems = []
    svcs = read_services()
    if shares and not svcs.get("nas"):
        if enable:
            svcs["nas"] = True
            write_services(svcs)
            problems += apply_services(svcs)
    elif svcs.get("nas"):
        if shares:
            why = nas_restart_if_running()
            if why:
                problems.append(why)
        else:
            svcs["nas"] = False
            write_services(svcs)
            problems += apply_services(svcs)
    return problems


def nas_passdb_edit(args, input_text=None, by=""):
    """Run one smbpasswd verb against the sealed passdb and reseal it.
    The passdb is a vault secret exported to SECRETS_DIR/nas (#244): export
    first so smbpasswd edits the current db; smbpasswd needs a config
    naming that private dir, which may predate the service's first start,
    so render a minimal one; store the result back; and bounce smbd — the
    reseal writes a NEW inode and a running smbd parent keeps the old one
    open for every forked child ("error fetching database", pipeOS#268).
    Returns (200, problems) or (code, message)."""
    if not shutil.which("smbpasswd"):
        return (500, "samba is not installed yet (samba-common-tools)")
    private = os.path.join(SECRETS_DIR, "nas")
    try:
        vault.export()
    except (vault.VaultError, OSError, ValueError) as e:
        return (500, "the vault is not open (%s) — unlock it under Secrets first" % e)
    os.makedirs(private, mode=0o700, exist_ok=True)
    with open(NAS_MINI_CONF, "w") as f:
        f.write("[global]\nprivate dir = %s\npassdb backend = tdbsam:%s/passdb.tdb\n" % (private, private))
    rc, out = run(["smbpasswd", "-c", NAS_MINI_CONF, "-s"] + list(args), input_text=input_text, timeout=30)
    if rc != 0:
        return (500, "smbpasswd failed: " + out.strip()[-200:])
    try:
        with open(os.path.join(private, "passdb.tdb"), "rb") as f:
            vault_put("nas_passdb", f.read(), by=by)
    except (OSError, RuntimeError) as e:
        return (500, "could not seal the SMB password db: %s" % e)
    return (200, [p for p in [nas_restart_if_running()] if p])


def nas_password_check(pw):
    if len(pw) < 8 or any(c in pw for c in "\n\r\0"):
        return "SMB password: at least 8 characters, no newlines"
    return ""


def nas_set_password(name, pw, by=""):
    """Set (or update) a user's SMB password. Samba keeps its own password
    db — the dashboard hash cannot be converted — so it is set explicitly.
    Returns (200, payload) or (code, message); the caller saves."""
    if name not in {u["name"] for u in read_users() if u.get("unix") and not u.get("disabled")}:
        return (400, "that user has no enabled unix account")
    why = nas_password_check(pw)
    if why:
        return (400, why)
    code, out = nas_passdb_edit(["-a", name], input_text=pw + "\n" + pw + "\n", by=by)
    if code != 200:
        return (code, out)
    return (200, {"ok": True, "problems": out})


def nas_account_create(name, pw, by=""):
    """One step (pipeOS#270): a share-only account plus its SMB password.
    The password is checked BEFORE the account is made, so a typo is a 400
    and nothing half-made is left behind; only samba itself refusing (not
    installed, vault shut) leaves the account with its password still to
    set, and problems[] says so. Returns (200, payload) or (code, message);
    the caller saves."""
    why = nas_password_check(pw)
    if why:
        return (400, why)
    code, payload = user_add({"name": name, "share": True})
    if code != 200:
        return (code, payload)
    problems = list(payload.get("problems") or [])
    code, out = nas_passdb_edit(["-a", name], input_text=pw + "\n" + pw + "\n", by=by)
    if code == 200:
        problems += out
    else:
        problems.append("account created, SMB password not set: " + out)
    return (200, {"ok": True, "name": name, "problems": problems})


def nas_user_enabled(name, enabled, by=""):
    """Disable/enable mirrored into samba (smbpasswd -d / -e): for a
    share-only account the SMB password IS its only credential, so a
    dashboard 'disabled' that left samba alone was a lockout that never
    happened (pipeOS#270 review). No samba (a dev host): nothing to do."""
    if not shutil.which("smbpasswd"):
        return []
    code, out = nas_passdb_edit(["-e" if enabled else "-d", name], by=by)
    if code == 200:
        return out
    if "Failed to find" in out or "not found" in out.lower():
        return []          # never had an SMB password
    return ["SMB logon not %s: %s" % ("enabled" if enabled else "disabled", out)]


def nas_forget_user(name, by=""):
    """A deleted unix account leaves samba too: its passdb entry and every
    share's user list; a share with nobody left is dropped, no share left
    turns storage off — and storage is never turned back ON here. Without
    samba (a dev host) there is no passdb to edit and that is not an error."""
    problems = []
    if shutil.which("smbpasswd"):
        code, out = nas_passdb_edit(["-x", name], by=by)
        if code == 200:
            problems += out
        elif "Failed to find" not in out and "not found" not in out.lower():
            problems.append("SMB logon not removed: " + out)
    shares = nas_read_shares()
    kept = [dict(sh, users=[u for u in sh["users"] if u != name]) for sh in shares]
    kept = [sh for sh in kept if sh["users"]]
    if kept == shares:
        return problems        # not on any share: nothing to rewrite
    return problems + nas_commit(kept, enable=False)



class Handler(BaseHTTPRequestHandler):
    server_version = "pipeos-webd"
    protocol_version = "HTTP/1.1"

    # -- plumbing --
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send(self, code, body, ctype="application/json", cookie=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        # A member that signed its request gets a signed answer (#222): the
        # status code stands in the METHOD slot, the request-target is the
        # request's (query included), so the caller knows who answered and
        # that the body is theirs.
        if getattr(self, "_peer", None):
            try:
                for k, v in cluster.sign_headers(str(code), self.path, data).items():
                    self.send_header(k, v)
            except cluster.ClusterError as e:
                sys.stderr.write("cluster: cannot sign the response: %s\n" % e)
        self.end_headers()
        self.wfile.write(data)

    def err(self, code, message):
        self.send(code, {"error": message})

    def unauth(self):
        """401, and when the request was a member's that failed the
        cluster check, the reason — which check fired is the whole of what
        the other box's log needs (skew is a clock to fix; replay is a
        network to look at; not-a-member is a list to compare)."""
        why = getattr(self, "_peer_reason", "")
        return self.err(401, "cluster: " + why if why else "sign in first")

    def cookie_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session":
                return v
        return None

    def authed(self):
        """The session, or a member's signature standing in for one (#222).
        A request that names a box id is a member's or nothing: it is
        checked against cluster.json and never falls back to the cookie, so
        a stranger with the headers and no key learns only which check
        refused it. A member is an admin here — the cluster is the owner's
        decision (docs/cluster.md §3) and every member holds the same list;
        roles (#214) refine that later."""
        if self.headers.get(cluster.H_ID):
            if getattr(self, "_peer", None):
                return self._peer
            # the whole request-target, query included — a reader's ?path=
            # is exactly what a signature must cover
            who, why = cluster.check(self.headers, self.command, self.path, getattr(self, "_raw", b""))
            if not who:
                self._peer_reason = why
                return None
            self._peer = {"user": "cluster:" + who, "role": "admin", "peer": who}
            return self._peer
        return valid_session(self.cookie_token())

    def body_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if n <= 0 or n > 65536:
            return None
        # the raw bytes are kept: a member's signature covers them (#222)
        self._raw = self.rfile.read(n)
        try:
            return json.loads(self._raw.decode())
        except (ValueError, UnicodeDecodeError):
            return None

    def same_origin(self):
        """State-changing requests must come from our own page: if the
        browser names an Origin, its host must match the Host we were
        addressed as. (SameSite=Strict on the cookie covers the rest.)"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        m = re.match(r"https?://([^/:]+)", origin)
        return bool(m) and m.group(1).lower() == host

    def _fresh(self):
        """Per-request state. protocol_version is HTTP/1.1, so ONE Handler
        instance serves every request on a keep-alive connection: anything
        cached on self by one request is seen by the next. The review of
        #283 found the member verdict cached that way — the second request
        on a member's socket would have been admitted with any signature.
        Reset at the top of every request, before anything reads them."""
        self._peer = None
        self._peer_reason = ""
        self._raw = b""

    # -- routes --
    def do_GET(self):
        self._fresh()
        path = self.path.split("?")[0]
        if path == "/" or path in ("/setup", "/login", "/dashboard", "/lobby"):
            return self.serve_static("index.html")
        if path.startswith("/static/"):
            return self.serve_static(path[len("/static/"):])
        if path == "/api/state":
            return self.api_state()
        if path == "/api/lobby":
            return self.api_lobby()
        # The CA root is public by design — the owner installs it to trust this
        # box, so these are unauthenticated (downloading a public cert is safe).
        if path == "/ca.crt":
            return self.serve_ca_cert()
        if path == "/pipeos-ca.mobileconfig":
            return self.serve_ca_mobileconfig()
        if path == "/install-ca.sh":
            return self.serve_ca_installer()
        # A Machine's cluster identity is public like its CA root (#222): the
        # public key and the box id are what another Machine needs to admit
        # it (#211/#213), and a public key is safe to hand out.
        if path == "/api/cluster/identity":
            return self.api_cluster_identity()
        # /api/docs carries a slug segment, so it routes by prefix. Read-only,
        # viewer-readable like the other readers.
        if path == "/api/docs" or path.startswith("/api/docs/"):
            if not self.authed():
                return self.unauth()
            return self.api_docs(path)
        readers = {
            "/api/status": self.api_status,
            "/api/name-suggest": self.api_name_suggest,
            "/api/users": self.api_users,
            "/api/metrics": self.api_metrics,
            "/api/metrics-history": self.api_metrics_history,
            "/api/files": self.api_files,
            "/api/file-dl": self.api_file_dl,
            "/api/file-tar": self.api_file_tar,
            "/api/disks": self.api_disks,
            "/api/nas": self.api_nas_get,
            "/api/backup": self.api_backup_get,
            "/api/health": self.api_health,
            "/api/logs": self.api_logs,
            "/api/stream": self.api_stream_get,
            "/api/stream-log": self.api_stream_log,
            "/api/assistant": self.api_assistant_get,
            "/api/claude": self.api_claude_get,
            "/api/support": self.api_support_get,
            "/api/secrets": self.api_secrets,
            "/api/schedule": self.api_schedule,
            "/api/usage": self.api_usage,
            "/api/pipe": self.api_pipe_get,
            "/api/pipe-contacts": self.api_pipe_contacts,
            "/api/pipe-board": self.api_pipe_board,
            "/api/update": self.api_update_get,
            "/api/flash": self.api_flash_get,
            "/api/cluster": self.api_cluster_get,
        }
        fn = readers.get(path)
        if fn is not None:
            if not self.authed():
                return self.unauth()
            return fn()
        self.err(404, "no such page")

    def do_POST(self):
        self._fresh()
        # Uploads stream a raw body and may run for minutes — they skip both
        # the JSON body cap and MUTATE_LOCK (a big file must not freeze every
        # other mutation). Still same-origin + session gated like the rest.
        if self.path.split("?")[0] == "/api/file-up":
            if not self.same_origin():
                return self.err(403, "cross-origin request refused")
            # A member's signature covers the body, and this body is streamed
            # past the handler unhashed — so a signed upload is refused by
            # name rather than failing 'bad signature' for a reason the
            # sender cannot see. Files between members go over the share or
            # a clone (#243), not this endpoint.
            if self.headers.get(cluster.H_ID):
                return self.err(401, "cluster: signed uploads are not supported on /api/file-up")
            sess = self.authed()
            if not sess:
                return self.unauth()
            if sess.get("role") == "viewer":
                return self.err(403, "your account can view this box, not change it")
            # role "user" may transfer files — that is the role's whole point
            return self.api_file_up()
        # Every mutation serializes on MUTATE_LOCK; GET readers are not held, so
        # the dashboard still loads while a slow POST runs. The lock also keeps
        # the claim race single-winner now that the server is threaded.
        with MUTATE_LOCK:
            self._do_post_locked()

    def _do_post_locked(self):
        if not self.same_origin():
            return self.err(403, "cross-origin request refused")
        path = self.path.split("?")[0]
        body = self.body_json() or {}
        if path == "/api/claim":
            return self.api_claim(body)
        if path == "/api/login":
            return self.api_login(body)
        # everything below requires a session
        sess = self.authed()
        if not sess:
            return self.unauth()
        # Three roles: viewers read; users read AND move files (the explorer's
        # mutations plus their own session/password); admins do everything.
        role = sess.get("role")
        if role == "viewer" and path not in ("/api/logout", "/api/password"):
            return self.err(403, "your account can view this box, not change it")
        if role == "user" and path not in (
                "/api/logout", "/api/password", "/api/file-op"):
            return self.err(403, "your account can browse and move files, not change the box")
        handlers = {
            "/api/logout": self.api_logout,
            "/api/name": self.api_name,
            "/api/services": self.api_services,
            "/api/claude-token": self.api_claude_token,
            "/api/claude-login/start": self.api_claude_login_start,
            "/api/claude-login/code": self.api_claude_login_code,
            "/api/claude-logout": self.api_claude_logout,
            "/api/pipe-key": self.api_pipe_key,
            "/api/pipe-contact": self.api_pipe_contact,
            "/api/file-op": self.api_file_op,
            "/api/disk-op": self.api_disk_op,
            "/api/backup": self.api_backup,
            "/api/nas": self.api_nas_set,
            "/api/nas-password": self.api_nas_password,
            "/api/nas-account": self.api_nas_account,
            "/api/pipe-set": self.api_pipe_set,
            "/api/pipe-logout": self.api_pipe_logout,
            "/api/password": self.api_password,
            "/api/users/add": self.api_users_add,
            "/api/users/set": self.api_users_set,
            "/api/users/del": self.api_users_del,
            "/api/save": self.api_save,
            "/api/chat": self.api_chat,
            "/api/reboot": self.api_reboot,
            "/api/reboot-firmware": self.api_reboot_firmware,
            "/api/repair-access": self.api_repair_access,
            "/api/stream-config": self.api_stream_set,
            "/api/assistant-config": self.api_assistant_set,
            "/api/cohort": self.api_cohort,
            "/api/update-now": self.api_update_now,
            "/api/update-set": self.api_update_set,
            "/api/flash": self.api_flash,
            "/api/wake": self.api_wake,
            "/api/secrets/set": self.api_secrets_set,
            "/api/secrets/del": self.api_secrets_del,
            "/api/secrets/reveal": self.api_secrets_reveal,
            "/api/secrets/unlock": self.api_secrets_unlock,
            "/api/secrets/rephrase": self.api_secrets_rephrase,
            "/api/secrets/init": self.api_secrets_init,
            "/api/secrets/phrase-ack": self.api_secrets_phrase_ack,
            "/api/cluster/init": self.api_cluster_init,
            "/api/schedule/set": self.api_schedule_set,
            "/api/schedule/del": self.api_schedule_del,
            "/api/schedule/run": self.api_schedule_run,
            "/api/usage/cap": self.api_usage_cap,
        }
        fn = handlers.get(path)
        if fn is None:
            return self.err(404, "no such action")
        return fn(body)

    def serve_static(self, name):
        if "/" in name or name.startswith("."):
            return self.err(404, "no")
        path = os.path.join(STATIC, name)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self.err(404, "no such file")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript",
            ".css": "text/css",
            ".svg": "image/svg+xml",
        }.get(os.path.splitext(name)[1], "application/octet-stream")
        self.send(200, data, ctype=ctype)

    def _send_download(self, data, ctype, filename):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_ca_cert(self):
        try:
            with open(CA_CRT, "rb") as f:
                data = f.read()
        except OSError:
            return self.err(404, "no CA yet — HTTPS is not set up on this box")
        self._send_download(data, "application/x-x509-ca-cert", "pipeos-ca.crt")

    def serve_ca_mobileconfig(self):
        # An Apple config profile that installs the CA as a trusted root in ~2
        # taps. (iOS still requires the one-time "enable full trust" toggle in
        # Settings › General › About › Certificate Trust — Apple does not let a
        # profile grant root trust silently.)
        try:
            with open(CA_CRT) as f:
                pem = f.read()
        except OSError:
            return self.err(404, "no CA yet — HTTPS is not set up on this box")
        der_b64 = base64.b64encode(ssl.PEM_cert_to_DER_cert(pem)).decode()
        host = socket.gethostname()
        prof = MOBILECONFIG_TMPL.format(
            cert_b64=der_b64, host=html.escape(host),
            puuid=str(uuid.uuid4()).upper(), cuuid=str(uuid.uuid4()).upper())
        self._send_download(prof.encode(), "application/x-apple-aspen-config",
                            "pipeos-ca.mobileconfig")

    def serve_ca_installer(self):
        # Same public-by-design stance as /ca.crt: the script only contains the
        # public certificate. Served over plain HTTP so there is no -k
        # bootstrap problem before the CA is trusted.
        try:
            with open(CA_CRT) as f:
                pem = f.read().strip()
        except OSError:
            return self.err(404, "no CA yet — HTTPS is not set up on this box")
        body = (CA_INSTALLER_TMPL
                .replace("@PEM@", pem)
                .replace("@HOST@", socket.gethostname()))
        self.send(200, body.encode(), ctype="text/x-shellscript")

    # -- API: unauthenticated surface (deliberately tiny) --
    def api_state(self):
        ps, _ok = peers()
        self.send(200, {
            "claimed": claimed(),
            "authed": bool(self.authed()),
            "hostname": socket.gethostname(),
            "id": lanid.mac4(),
            "name": box_name(),
            "lan_name": lanid.lan_name(),
            "siblings": len(ps),
        })

    def api_lobby(self):
        """Public like /api/state: what mDNS already tells the LAN, plus one
        word of health per Machine. The page a stranger uses to find the
        unclaimed one."""
        rows, ok = lobby_entries()
        self.send(200, {"machines": rows, "discovery_ok": ok, "lan_name": lanid.lan_name()})

    # -- usage (#246) -----------------------------------------------------------

    def api_usage(self):
        ledger_refresh()
        try:
            t = ledger_obj().totals()
        except Exception as e:
            return self.err(500, "ledger: %s" % e)
        t["rates_updated"] = ""
        try:
            with open(LEDGER_RATES) as f:
                t["rates_updated"] = json.load(f).get("updated", "")
        except (OSError, ValueError):
            pass
        self.send(200, t)

    def api_usage_cap(self, body):
        v = body.get("usd")
        if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 100000:
            return self.err(400, "the cap is a whole number of dollars, 0 (none) to 100000")
        try:
            card_ensure_key("MONTHLY_CAP_USD")
            card_set({"MONTHLY_CAP_USD": str(v) if v else ""})
        except RuntimeError as e:
            return self.err(500, str(e))
        saved, detail = save_state()
        # lowering under this month's spend pauses now; raising above it resumes now
        try:
            state = ledger_obj().enforce_cap()
        except Exception as e:
            state = {"error": str(e)}
        self.send(200, {"ok": True, "cap": v, "state": state, "saved": saved, "save_detail": "" if saved else detail})

    # -- scheduled runs (#242) --------------------------------------------------

    def api_schedule(self):
        jobs = read_schedule()
        st = schedule_state()
        now = datetime.datetime.now()
        out = []
        for j in jobs:
            row = {k: j.get(k) for k in ("name", "cron", "prompt", "cwd", "backend", "notify", "enabled", "session")}
            try:
                spec = cronspec.parse(j.get("cron", ""))
                nxt = cronspec.next_run(spec, now) if j.get("enabled", True) else None
                row["human"] = cronspec.describe(spec)
                row["next_run"] = nxt.strftime("%Y-%m-%dT%H:%M") if nxt else ""
            except cronspec.CronError as e:
                row["human"] = "invalid: %s" % e
                row["next_run"] = ""
            row["last"] = {k: v for k, v in st.get(j["name"], {}).items() if k != "running_pid"}
            out.append(row)
        rc, _ = run(["rc-service", "crond", "status"], timeout=10)
        paused = ""
        try:
            with open(LEDGER_PAUSED) as f:
                paused = f.read().strip() or "the monthly cap is reached"
        except OSError:
            pass
        self.send(200, {"jobs": out, "running": schedule_running(), "crond_up": rc == 0,
                        "paused": paused, "now": now.strftime("%Y-%m-%dT%H:%M"),
                        "backends": [{"id": b, "installed": shutil.which(b) is not None} for b in ASSISTANT_BACKENDS]})

    def api_schedule_set(self, body):
        name = (body.get("name") or "").strip().lower()
        if not JOB_NAME_RE.match(name):
            return self.err(400, "job names: lowercase letters, digits and dashes, up to 32")
        jobs = read_schedule()
        cur = next((j for j in jobs if j["name"] == name), None)
        job = dict(cur) if cur else {"name": name, "cwd": "", "backend": "claude", "notify": True,
                                     "enabled": True, "session": "fresh", "prompt": "", "cron": ""}
        if "cron" in body or not cur:
            try:
                spec = cronspec.parse(body.get("cron") or "")
            except cronspec.CronError as e:
                return self.err(400, "schedule: %s" % e)
            job["cron"] = spec.text
        if "prompt" in body or not cur:
            prompt = body.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000 or "\0" in prompt:
                return self.err(400, "a prompt, up to 8000 characters")
            job["prompt"] = prompt.strip()
        if "cwd" in body:
            cwd = (body.get("cwd") or "").strip()
            if cwd:
                real = os.path.realpath(cwd)
                if not (real == "/work" or real.startswith("/work/")) or any(c in cwd for c in "\n\r\0'\""):
                    return self.err(400, "the working dir must be under /work")
                job["cwd"] = real
            else:
                job["cwd"] = ""
        if "backend" in body:
            backend = (body.get("backend") or "claude").strip()
            if backend not in ASSISTANT_BACKENDS:
                return self.err(400, "assistant must be one of: " + ", ".join(ASSISTANT_BACKENDS))
            if shutil.which(backend) is None:
                return self.err(400, "%s is not installed on this image" % backend)
            job["backend"] = backend
        for k in ("notify", "enabled"):
            if k in body:
                job[k] = bool(body[k])
        if "session" in body:
            if body.get("session") not in ("fresh", "continue"):
                return self.err(400, "session is fresh or continue")
            job["session"] = body["session"]
        if cur is None:
            if len(jobs) >= SCHEDULE_MAX_JOBS:
                return self.err(400, "at most %d jobs on one Machine" % SCHEDULE_MAX_JOBS)
            jobs.append(job)
        else:
            jobs[jobs.index(cur)] = job
        write_schedule(jobs)
        saved, detail = save_state()
        self.send(200, {"ok": True, "job": job, "saved": saved, "save_detail": "" if saved else detail})

    def api_schedule_del(self, body):
        name = (body.get("name") or "").strip().lower()
        jobs = read_schedule()
        keep = [j for j in jobs if j["name"] != name]
        if len(keep) == len(jobs):
            return self.err(404, "no job named %s" % name)
        write_schedule(keep)
        # its runtime leftovers on /work: the session, the state row
        for p in (os.path.join(SCHEDULE_STATE_DIR, "sessions", name),):
            try:
                os.unlink(p)
            except OSError:
                pass
        # under the same lock the tick and the runner hold for their
        # read-modify-write, or a stale copy could rename over their commit
        try:
            sp = os.path.join(SCHEDULE_STATE_DIR, "state.json")
            with open(os.path.join(SCHEDULE_STATE_DIR, ".state.lock"), "a+") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                with open(sp) as f:
                    st = json.load(f)
                st.get("jobs", {}).pop(name, None)
                write_private(sp, json.dumps(st))
        except (OSError, ValueError):
            pass
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

    def api_schedule_run(self, body):
        """Run now. Detached, like the tick does it; refused while another
        job runs (one at a time). Writes nothing the apkovl carries."""
        name = (body.get("name") or "").strip().lower()
        if not any(j["name"] == name for j in read_schedule()):
            return self.err(404, "no job named %s" % name)
        if schedule_running():
            return self.err(409, "another job is running on this Machine — one at a time; try again when it finishes")
        if os.path.exists(LEDGER_PAUSED):
            return self.err(409, "scheduled runs are paused — the monthly cap is reached; raise it under Usage")
        try:
            subprocess.Popen([SCHEDULE_RUN_BIN, name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            return self.err(500, "could not start the runner: %s" % e)
        self.send(200, {"ok": True, "started": True})

    # -- secrets (#244) -------------------------------------------------------

    def api_secrets(self):
        """Names, never values. Admin only — the reader gate lets every
        role in, and a viewer has no business with the list of what the
        box holds."""
        if self._user_admin_guard() is None:
            return
        st, why = vault.status()
        rows = []
        if st == "open":
            try:
                rows = vault.list_()
            except (vault.VaultError, OSError, ValueError):
                rows = []
        self.send(200, {"status": st, "detail": why, "secrets": rows,
                        "phrase_pending": os.path.exists(VAULT_PHRASE),
                        "phrase": _read_phrase() if os.path.exists(VAULT_PHRASE) else ""})

    def api_secrets_set(self, body):
        if self._user_admin_guard() is None:
            return
        name = (body.get("name") or "").strip().lower()
        value = body.get("value")
        if not vault.NAME_RE.match(name):
            return self.err(400, "secret names are lowercase letters, digits, _ and . — up to 64")
        if name in vault.CONSUMER_OF or name.startswith("stream_key_"):
            return self.err(400, "%s is set from its own card (Setup, Streaming, Services…), not here" % name)
        if not isinstance(value, str) or not value or len(value) > 8192 or "\0" in value:
            return self.err(400, "a value, up to 8 KB, no NUL")
        try:
            vault_put(name, value, by=(self.authed() or {}).get("user", ""))
        except RuntimeError as e:
            return self.err(500, str(e))
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

    def api_secrets_del(self, body):
        if self._user_admin_guard() is None:
            return
        name = (body.get("name") or "").strip().lower()
        if not vault.NAME_RE.match(name):
            return self.err(400, "which secret?")
        try:
            gone = vault.delete(name)
            vault.export()
        except vault.Locked as e:
            return self.err(409, "the vault is locked (%s)" % e)
        except (vault.VaultError, OSError, ValueError) as e:
            return self.err(500, "vault: %s" % e)
        if not gone:
            return self.err(404, "no secret named %s" % name)
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

    def api_secrets_reveal(self, body):
        """The value, once, to an admin who just re-typed their password.
        No save: a read."""
        sess = self._user_admin_guard()
        if sess is None:
            return
        name = (body.get("name") or "").strip().lower()
        u = find_user(read_users(), sess.get("user", ""))
        if not u or not check_hash(body.get("password") or "", u.get("hash", "")):
            time.sleep(2)
            return self.err(403, "that is not your password")
        v = vault_get(name)
        if v is None:
            return self.err(404, "no secret named %s" % name)
        if isinstance(v, bytes):
            return self.err(400, "%s is a binary secret (a key file) — it has no value to show" % name)
        self.send(200, {"ok": True, "name": name, "value": v})

    def api_secrets_unlock(self, body):
        """The recovery phrase re-seals the vault to THIS chassis: a stick
        that moved to another machine opens again."""
        if self._user_admin_guard() is None:
            return
        phrase = (body.get("phrase") or "").strip()
        if len(re.sub(r"[^0-9a-fA-F]", "", phrase)) != 32:
            return self.err(400, "the recovery phrase is eight groups of four characters")
        try:
            vault.unlock(phrase)
            vault.export()
        except vault.Locked:
            time.sleep(2)
            return self.err(403, "that is not this vault's recovery phrase")
        except (vault.VaultError, OSError, ValueError) as e:
            return self.err(500, "vault: %s" % e)
        try:
            with open(VAULT_STATUS, "w") as f:
                f.write("open unlocked\n")
        except OSError:
            pass
        problems = restart_secret_consumers()
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems, "saved": saved, "save_detail": "" if saved else detail})

    def api_secrets_rephrase(self, body):
        """A new recovery phrase; the old one stops working. Shown once."""
        if self._user_admin_guard() is None:
            return
        try:
            phrase = vault.rephrase()
        except vault.Locked as e:
            return self.err(409, "the vault is locked (%s)" % e)
        except (vault.VaultError, OSError, ValueError) as e:
            return self.err(500, "vault: %s" % e)
        saved, detail = save_state()
        self.send(200, {"ok": True, "phrase": phrase, "saved": saved, "save_detail": "" if saved else detail})

    def api_secrets_init(self, body):
        """A claimed box with no vault (the boot migration did not run, or
        failed): make one now, move the plaintext in, and show the phrase.
        The same thing pipeos-vault does at boot, on demand."""
        if self._user_admin_guard() is None:
            return
        if vault.exists():
            return self.err(409, "this box already has a vault")
        try:
            phrase = vault.init()
            moved = vault.migrate(by=(self.authed() or {}).get("user", "") or "dashboard")
            vault.export()
        except (vault.VaultError, OSError, ValueError) as e:
            return self.err(500, "vault: %s" % e)
        saved, detail = save_state()
        self.send(200, {"ok": True, "phrase": phrase, "moved": moved, "saved": saved,
                        "save_detail": "" if saved else detail})

    def api_secrets_phrase_ack(self, body):
        """The owner wrote the phrase down: forget the tmpfs copy. No save."""
        if self._user_admin_guard() is None:
            return
        try:
            os.unlink(VAULT_PHRASE)
        except OSError:
            pass
        self.send(200, {"ok": True})

    # -- the cluster primitive (#222) --
    def api_cluster_identity(self):
        """Public: who this Machine is to a cluster. No key yet is a fact,
        not an error — a fresh Machine has none until it is joined."""
        s = cluster.status_doc()
        self.send(200, {"id": s["self"], "pub": cluster.self_pub() or None,
                        "fingerprint": s["fingerprint"] or None, "cluster": s["cluster"]})

    def api_cluster_get(self):
        s = cluster.status_doc()
        self.send(200, {"self": s["self"], "key": s["key"], "key_mode_ok": s["key_mode_ok"],
                        "fingerprint": s["fingerprint"], "cluster": s["cluster"],
                        "members_hash": s["members_hash"], "error": s["error"],
                        "members": [{"id": mid, "name": m.get("name", ""), "added": m.get("added"),
                                     "fingerprint": cluster.fingerprint(m.get("pub", "")),
                                     "self": mid == s["self"]}
                                    for mid, m in sorted(s["members"].items())]})

    def api_cluster_init(self, body):
        """A cluster of one: this Machine's key and a member list holding
        only it. What the owner does before marking others in (#211)."""
        try:
            d = cluster.init(name=box_name())
        except cluster.ClusterError as e:
            return self.err(409, str(e))
        saved, out = save_state()
        self.send(200, {"ok": True, "cluster": d["id"], "saved": saved, "save_output": out})

    def api_wake(self, body):
        """A magic packet to a rostered Machine (#241). Admin only — the
        role gate lets every admin POST in, but powering hardware on is an
        owner's act, so it is said here too. Writes nothing: no save."""
        if self._user_admin_guard() is None:
            return
        mid = (body.get("id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{4}", mid):
            return self.err(400, "which Machine? (its id)")
        rc, out = run([WAKE_BIN, mid], timeout=20)
        if rc == 2:
            return self.err(404, out.strip() or "not a Machine this box has seen")
        if rc != 0:
            return self.err(409, out.strip()[-300:] or "could not send the packet")
        r = roster().get(mid, {})
        self.send(200, {"ok": True, "id": mid, "mac": r.get("mac", ""), "detail": out.strip()})

    def api_claim(self, body):
        if claimed():
            return self.err(403, "this box is already claimed")
        pw = body.get("password") or ""
        if len(pw) < 8:
            return self.err(400, "password must be at least 8 characters")
        try:
            h = hash_password(pw)
            write_private(ADMIN_CONF, "HASH='%s'\n" % h)
        except RuntimeError as e:
            return self.err(500, str(e))
        # seed the multi-user store; web-admin.conf stays the claim marker
        # and the lockout-safety fallback
        write_users([{"name": "admin", "role": "admin", "hash": h,
                      "created": int(time.time())}])
        # The box's secrets get their vault now (#244): sealed to this
        # chassis, with a recovery phrase the wizard shows exactly once.
        phrase = ""
        try:
            phrase = vault.init(force=True)
            vault.export()
        except (vault.VaultError, OSError, ValueError) as e:
            sys.stderr.write("pipeos-webd: vault init at claim failed: %s\n" % e)
        # The claim IS the provisioning event: from here on, saves persist.
        # Save NOW — a claim that exists only in RAM is not a claim.
        with open(PROVISIONED, "a"):
            pass
        saved, detail = save_state()
        tok = new_session()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail,
                        "recovery_phrase": phrase},
                  cookie="session=%s; HttpOnly; SameSite=Strict; Path=/" % tok)

    def api_login(self, body):
        if not claimed():
            return self.err(403, "box is not claimed yet")
        name = (body.get("username") or "").strip() or "admin"
        users = read_users()
        u = find_user(users, name)
        # one flat cost and one message for every failure — no user enumeration
        if (u is None or u.get("disabled") or u.get("share")
                or not check_hash(body.get("password") or "", u.get("hash"))):
            time.sleep(2)
            return self.err(403, "wrong username or password")
        if not os.path.exists(USERS_CONF) and name == "admin":
            # pre-multi-user box: lazily seed the store from the legacy hash
            write_users([{"name": "admin", "role": "admin", "hash": u["hash"],
                          "created": int(time.time())}])
        tok = new_session(name, u.get("role") or "admin")
        self.send(200, {"ok": True, "user": name, "role": u.get("role") or "admin"},
                  cookie="session=%s; HttpOnly; SameSite=Strict; Path=/" % tok)

    # -- API: authenticated --
    def api_logout(self, _body):
        drop_session(self.cookie_token())
        self.send(200, {"ok": True},
                  cookie="session=gone; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")

    def api_status(self):
        up, pct, free_mb = uptime_disk()
        svcs = read_services()
        running = {}
        for svc in ["pipeos-web", "pipeos-mdns"] + daemons_for(svcs):
            rc, _ = run(["rc-service", svc, "status"], timeout=15)
            running[svc] = rc == 0
        sess = self.authed() or {}
        try:
            spend = ledger_obj().totals()
        except Exception:
            spend = {}
        self.send(200, {
            "user": sess.get("user"),
            "role": sess.get("role"),
            "hostname": socket.gethostname(),
            "id": lanid.mac4(),
            "name": box_name(),
            "nick": card_get("NICK"),
            "owner": card_get("OWNER_NICK"),
            "uptime_s": up,
            "work_pct": pct,
            "work_free_mb": free_mb,
            "services": svcs,
            "running": running,
            "boot_report": boot_report(),
            "spend_today_usd": spend.get("today", {}).get("usd", 0),
            "spend_month_usd": spend.get("month", {}).get("usd", 0),
            "usage_cap": spend.get("cap", {}),
            "usage_paused": bool(spend.get("cap", {}).get("paused")),
            # saves are fenced: a new image applied (tmpfs marker) or a rollback
            # staged — every change until the reboot answers saved:false
            "save_fence": ("new image applied — reboot to boot it" if os.path.exists("/run/pipeos/flash-pending")
                           else "rollback staged — reboot to apply it" if os.path.exists(ETC + "/rollback-pending")
                           else ""),
        })

    # -- files: an explorer over /work plus any mounted external drive.
    # Everything else on the box is either regenerated tmpfs or the agent's
    # identity — not the owner's to shuffle. Paths are root-prefixed:
    # "work/…" or "ext/<dev>/…"; "" is the virtual root listing the drives.

    def _file_roots(self):
        roots = {"work": FILES_WORK}
        try:
            for name in os.listdir(FILES_EXT_BASE):
                p = os.path.join(FILES_EXT_BASE, name)
                if os.path.ismount(p):
                    roots["ext/" + name] = p
        except OSError:
            pass
        return roots

    def _files_path(self, rel):
        """Resolve a root-prefixed path, or return None for the virtual root.
        Raises ValueError on escape attempts and unknown roots."""
        rel = (rel or "").strip().strip("/")
        if "\0" in rel:
            raise ValueError("bad path")
        if not rel:
            return None
        parts = rel.split("/")
        if parts[0] == "work":
            key, rest = "work", parts[1:]
        elif parts[0] == "ext" and len(parts) >= 2:
            key, rest = "ext/" + parts[1], parts[2:]
        else:
            raise ValueError("unknown drive")
        base = self._file_roots().get(key)
        if base is None:
            raise ValueError("that drive is not mounted")
        p = os.path.realpath(os.path.join(base, *rest))
        rb = os.path.realpath(base)
        if p != rb and not p.startswith(rb + "/"):
            raise ValueError("path escapes the drive")
        return p

    def _files_rel(self, p):
        """Absolute path back to its root-prefixed form."""
        for key, base in self._file_roots().items():
            rb = os.path.realpath(base)
            if p == rb:
                return key
            if p.startswith(rb + "/"):
                return key + "/" + os.path.relpath(p, rb)
        return ""

    def api_files(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        raw = (q.get("path") or [""])[0]
        try:
            p = self._files_path(raw)
        except ValueError as e:
            return self.err(400, str(e))
        if p is None:
            # virtual root: one folder per drive
            dirs = [{"name": key, "mtime": 0} for key in sorted(self._file_roots())]
            return self.send(200, {"path": "", "dirs": dirs, "files": [],
                                   "truncated": False, "roots": True})
        if not os.path.isdir(p):
            return self.err(404, "no such folder")
        dirs, files = [], []
        try:
            with os.scandir(p) as it:
                for de in it:
                    try:
                        st = de.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    row = {"name": de.name, "mtime": int(st.st_mtime)}
                    if de.is_dir(follow_symlinks=False):
                        dirs.append(row)
                    else:
                        row["size"] = st.st_size
                        files.append(row)
        except OSError as e:
            return self.err(500, "cannot read folder: %s" % e)
        dirs.sort(key=lambda d: d["name"])
        files.sort(key=lambda f: f["name"])
        self.send(200, {"path": self._files_rel(p),
                        "dirs": dirs[:2000], "files": files[:2000],
                        "truncated": len(dirs) > 2000 or len(files) > 2000})

    def api_file_op(self, body):
        op = body.get("op")
        try:
            p = self._files_path(body.get("path"))
        except ValueError as e:
            return self.err(400, str(e))
        if p is None:
            return self.err(400, "pick a drive first")
        root_paths = {os.path.realpath(b) for b in self._file_roots().values()}
        if os.path.realpath(p) in root_paths and op != "mkdir":
            return self.err(400, "not on a drive's top level itself")
        try:
            if op == "mkdir":
                name = (body.get("name") or "").strip()
                if not name or "/" in name or name.startswith("."):
                    return self.err(400, "folder name: no slashes, no leading dot")
                os.makedirs(os.path.join(p, name), exist_ok=False)
            elif op in ("move", "rename"):
                dest = self._files_path(body.get("dest"))
                if dest is None:
                    return self.err(400, "pick a destination drive first")
                if os.path.isdir(dest):
                    dest = os.path.join(dest, os.path.basename(p))
                if os.path.exists(dest):
                    return self.err(400, "destination already exists")
                try:
                    os.rename(p, dest)
                except OSError as e:
                    if e.errno != 18:  # EXDEV: across drives — copy+delete
                        raise
                    shutil.move(p, dest)
            elif op == "delete":
                if os.path.isdir(p):
                    if os.listdir(p) and not body.get("recursive"):
                        return self.err(400, "folder is not empty")
                    shutil.rmtree(p)
                else:
                    os.unlink(p)
            else:
                return self.err(400, "op must be mkdir, move, rename or delete")
        except FileExistsError:
            return self.err(400, "already exists")
        except FileNotFoundError:
            return self.err(404, "no such file")
        except OSError as e:
            return self.err(500, str(e))
        self.send(200, {"ok": True})

    def api_file_dl(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            p = self._files_path((q.get("path") or [""])[0])
        except ValueError as e:
            return self.err(400, str(e))
        if not os.path.isfile(p):
            return self.err(404, "no such file")
        try:
            size = os.path.getsize(p)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(p).replace('"', "_"))
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(1 << 16)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except OSError:
            pass  # client went away or file vanished mid-stream

    def api_file_tar(self):
        """A folder as one download: streamed tar.gz, no temp file. Length is
        unknowable up front, so the connection closes to end the stream."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            p = self._files_path((q.get("path") or [""])[0])
        except ValueError as e:
            return self.err(400, str(e))
        if p is None or not os.path.isdir(p):
            return self.err(404, "no such folder")
        name = (os.path.basename(p) or "drive") + ".tar.gz"
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name.replace('"', "_"))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with tarfile.open(fileobj=self.wfile, mode="w|gz") as tf:
                tf.add(p, arcname=os.path.basename(p) or "drive")
        except (OSError, tarfile.TarError):
            pass  # client went away or a file vanished mid-walk

    UPLOAD_MAX = 4 << 30  # 4 GiB — media files are the use case

    def api_file_up(self):
        # Any refusal leaves the raw body unread on the socket — close rather
        # than let keep-alive read it as the next request.
        self.close_connection = True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (q.get("name") or [""])[0]
        if not name or "/" in name or name.startswith(".") or "\0" in name:
            return self.err(400, "file name: no slashes, no leading dot")
        try:
            d = self._files_path((q.get("path") or [""])[0])
        except ValueError as e:
            return self.err(400, str(e))
        if not os.path.isdir(d):
            return self.err(404, "no such folder")
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self.err(400, "bad length")
        if n <= 0 or n > self.UPLOAD_MAX:
            return self.err(400, "upload must be 1 byte to 4 GiB")
        tmp = os.path.join(d, ".upload-%s.part" % secrets.token_hex(6))
        try:
            left = n
            with open(tmp, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 16, left))
                    if not chunk:
                        raise OSError("connection dropped mid-upload")
                    f.write(chunk)
                    left -= len(chunk)
            os.rename(tmp, os.path.join(d, name))
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return self.err(500, str(e))
        self.send(200, {"ok": True, "name": name, "size": n})

    def api_disks(self):
        self.send(200, {"disks": disk_inventory(), "ext_base": FILES_EXT_BASE})

    def api_health(self):
        """A fresh health verdict, NOW — the boot report is a snapshot of boot,
        and healed findings linger there until the next reboot. Runs the bare
        (read-only) selfcheck."""
        rc, out = run([SELFCHECK_BIN], timeout=180)
        m = re.search(r"^verdict: (.*)$", out, re.M)
        self.send(200, {"ok": rc == 0, "text": out[-8000:],
                        "verdict": m.group(1) if m else ""})

    def api_backup_get(self):
        nick = card_get("NICK") or socket.gethostname()
        exts = []
        for key, base in self._file_roots().items():
            if not key.startswith("ext/"):
                continue
            last, identity_last = None, None
            try:
                with open(os.path.join(base, "pipeos-backup", nick, ".last")) as f:
                    last = int(f.read().strip())
            except (OSError, ValueError):
                pass
            try:
                identity_last = int(os.path.getmtime(os.path.join(
                    base, "pipeos-backup", nick, "identity", "MANIFEST")))
            except OSError:
                pass
            exts.append({"root": key, "last": last, "identity_last": identity_last})
        with BACKUP_LOCK:
            state = dict(BACKUP)
        state["exts"] = exts
        state["step"] = backup_step()
        self.send(200, state)

    def api_backup(self, body):
        dest = (body.get("dest") or "").strip()
        if not dest.startswith("ext/"):
            return self.err(400, "pick a mounted external drive")
        base = self._file_roots().get(dest)
        if base is None:
            return self.err(400, "that drive is not mounted")
        scope = body.get("scope") or "full"
        if scope not in ("full", "identity"):
            return self.err(400, "scope is full or identity")
        with BACKUP_LOCK:
            if BACKUP["running"]:
                return self.err(400, "a backup is already running")
            BACKUP.update({"running": True, "dest": dest,
                           "started": int(time.time()), "ok": None, "detail": ""})
        threading.Thread(target=backup_worker, args=(base, scope), daemon=True).start()
        self.send(200, {"ok": True, "started": True})

    def api_disk_op(self, body):
        op = body.get("op")
        dev = (body.get("dev") or "").strip()
        if not DEV_RE.fullmatch(dev) or not os.path.exists("/sys/class/block/" + dev):
            return self.err(400, "no such device")
        if dev_protected(dev):
            return self.err(400, "that device belongs to the system (boot media or work disk) — refusing")
        node = "/dev/" + dev
        mp = os.path.join(FILES_EXT_BASE, dev)
        if op == "mount":
            if mounts_by_dev().get(os.path.realpath(node)):
                return self.err(400, "already mounted")
            try:
                os.makedirs(mp, exist_ok=True)
            except OSError as e:
                return self.err(500, str(e))
            rc, out = run(["mount", "-o", "noexec,nosuid,nodev", node, mp], timeout=30)
            if rc != 0:
                try:
                    os.rmdir(mp)
                except OSError:
                    pass
                return self.err(500, "mount failed: " + out.strip()[-200:])
            # Record the drive so pipeos-mounts.start re-mounts it after a
            # reboot; the save carries mounts.conf onto the media.
            uuid = blkid_all().get(node, {}).get("uuid", "")
            if uuid:
                write_mount_uuids(read_mount_uuids() + [uuid])
                save_state()
            self.send(200, {"ok": True, "mount": mp, "root": "ext/" + dev})
        elif op == "unmount":
            if not os.path.ismount(mp):
                return self.err(400, "not mounted here")
            uuid = blkid_all().get(node, {}).get("uuid", "")
            rc, out = run(["umount", mp], timeout=30)
            if rc != 0:
                return self.err(500, "unmount failed (files in use?): " + out.strip()[-200:])
            try:
                os.rmdir(mp)
            except OSError:
                pass
            # An explicit unmount is the owner saying "stop replaying this
            # drive at boot" — drop it from the record.
            if uuid:
                write_mount_uuids([u for u in read_mount_uuids() if u != uuid])
                save_state()
            self.send(200, {"ok": True})
        elif op == "format":
            label = (body.get("label") or "").strip()
            if label and not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", label):
                return self.err(400, "label: letters, digits, _ -, max 16")
            if mounts_by_dev().get(os.path.realpath(node)) or os.path.ismount(mp):
                return self.err(400, "unmount it first")
            args = ["mkfs.ext4", "-F"]
            if label:
                args += ["-L", label]
            rc, out = run(args + [node], timeout=600)
            if rc != 0:
                return self.err(500, "format failed: " + out.strip()[-200:])
            self.send(200, {"ok": True})
        else:
            self.err(400, "op must be mount, unmount or format")

    # -- network storage: the owner's chosen folders, served over SMB.
    # nas.conf (written here, quote-guarded) is the source of truth; the
    # pipeos-nas init script renders smb.conf from it at every start, so a
    # drive that changed device names re-resolves by UUID and nothing stale
    # persists. Roots are stored as 'work' or the drive's filesystem UUID.

    def _nas_uuid_roots(self):
        """explorer root key ('work' / 'ext/<dev>') maps both ways to the
        stored identity ('work' / UUID)."""
        by_key, by_uuid = {"work": "work"}, {"work": "work"}
        ids = blkid_all()
        for key, path in self._file_roots().items():
            if key == "work":
                continue
            dev = "/dev/" + key.split("/", 1)[1]
            uuid = ids.get(dev, {}).get("uuid", "")
            if uuid:
                by_key[key] = uuid
                by_uuid[uuid] = key
        return by_key, by_uuid

    def api_nas_get(self):
        _, by_uuid = self._nas_uuid_roots()
        shares = []
        for sh in self._nas_read_shares():
            key = by_uuid.get(sh["root"])
            shares.append({
                "name": sh["name"], "users": sh["users"], "rel": sh["rel"],
                "path": (key + ("/" + sh["rel"] if sh["rel"] else "")) if key else "",
                "attached": key is not None,
            })
        users = [u["name"] for u in read_users()
                 if u.get("unix") and not u.get("disabled")]
        rc, _out = run(["rc-service", "pipeos-nas", "status"], timeout=15)
        smb_users = []
        # pdbedit must be pointed at OUR private dir; -s /dev/null read
        # samba's compiled-in one and this list was always empty (#268)
        conf = NAS_RENDERED_CONF if os.path.exists(NAS_RENDERED_CONF) else NAS_MINI_CONF
        if os.path.exists(conf):
            rc2, out2 = run(["pdbedit", "-L", "-s", conf], timeout=15)
            if rc2 == 0:
                smb_users = [l.split(":")[0] for l in out2.splitlines() if ":" in l]
        ids = blkid_all()
        roots = [{"key": "work", "label": "work (the box's data drive)"}]
        for key, path in sorted(self._file_roots().items()):
            if key == "work":
                continue
            dev = "/dev/" + key.split("/", 1)[1]
            label = ids.get(dev, {}).get("label", "")
            roots.append({"key": key, "label": key + (" — " + label if label else "")})
        self.send(200, {
            "shares": shares, "eligible_users": users, "smb_users": smb_users,
            "roots": roots,
            "enabled": read_services().get("nas", False), "running": rc == 0,
            "installed": bool(shutil.which("smbd")),
            "host": socket.gethostname(),
        })

    def api_nas_set(self, body):
        def guard(v, label):
            v = (v or "").strip()
            if any(c in v for c in "'\n\r\0"):
                raise ValueError("%s may not contain quotes or newlines" % label)
            if len(v) > 300:
                raise ValueError("%s is too long" % label)
            return v
        shares = body.get("shares")
        if not isinstance(shares, list) or len(shares) > NAS_MAX_SHARES:
            return self.err(400, "shares must be a list (max %d)" % NAS_MAX_SHARES)
        by_key, _ = self._nas_uuid_roots()
        users_known = {u["name"] for u in read_users()
                       if u.get("unix") and not u.get("disabled")}
        out, names = [], set()
        try:
            for i, t in enumerate(shares):
                n = i + 1
                if not isinstance(t, dict):
                    raise ValueError("share %d is not an object" % n)
                name = guard(t.get("name"), "share %d name" % n)
                if not NAS_NAME_RE.fullmatch(name):
                    raise ValueError("share %d name: letters, digits, _ -, max 32" % n)
                if name.lower() in names:
                    raise ValueError("two shares named '%s'" % name)
                names.add(name.lower())
                path = guard(t.get("path"), "share %d path" % n)
                resolved = self._files_path(path)
                if resolved is None or not os.path.isdir(resolved):
                    raise ValueError("share %d: that folder does not exist" % n)
                root_key = path.split("/")[0] if path.split("/")[0] == "work" \
                    else "/".join(path.split("/")[:2])
                root_id = by_key.get(root_key)
                if not root_id:
                    raise ValueError("share %d: its drive has no filesystem UUID" % n)
                rel = "/".join(path.split("/")[1 if root_key == "work" else 2:])
                users = t.get("users") or []
                if not isinstance(users, list) or not users:
                    raise ValueError("share %d needs at least one user" % n)
                for u in users:
                    if u not in users_known:
                        raise ValueError(
                            "share %d: '%s' is not an enabled account with unix access" % (n, u))
                out.append({"name": name, "root": root_id,
                            "rel": guard(rel, "share %d path" % n), "users": users})
        except ValueError as e:
            return self.err(400, str(e))
        problems = nas_commit(out, enable=True)   # configure implies enable
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_nas_password(self, body):
        code, payload = nas_set_password((body.get("name") or "").strip(), body.get("password") or "",
                                         by=(self.authed() or {}).get("user", ""))
        self._send_saved(code, payload)  # save_state inside

    def api_nas_account(self, body):
        """Storage page, one step: a share-only account + its SMB password
        (pipeOS#270). Same code as `pipeos nas account NAME`."""
        code, payload = nas_account_create((body.get("name") or "").strip(), body.get("password") or "",
                                           by=(self.authed() or {}).get("user", ""))
        self._send_saved(code, payload)  # save_state inside

    def api_metrics(self):
        up, pct, free_mb = uptime_disk()
        m = system_metrics()
        m.update({"uptime_s": up, "work_pct": pct, "work_free_mb": free_mb})
        ip, iface = primary_ip()
        rx, tx, niface = net_counters()
        m.update({"ip": ip, "iface": iface or niface,
                  "rx_total": rx, "tx_total": tx,
                  "rx_bps": None, "tx_bps": None})
        with METRICS_LOCK:
            tail = list(METRICS_HIST)[-2:]
        m["cpu_pct"] = cpu_pct_between(tail[0], tail[1]) if len(tail) == 2 else None
        if len(tail) == 2 and tail[1]["rx"] is not None and tail[0]["rx"] is not None:
            dt = max(1, tail[1]["t"] - tail[0]["t"])
            m["rx_bps"] = max(0, tail[1]["rx"] - tail[0]["rx"]) * 8 // dt
            m["tx_bps"] = max(0, tail[1]["tx"] - tail[0]["tx"]) * 8 // dt
        self.send(200, m)

    def api_metrics_history(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        span = (q.get("span") or ["1h"])[0]
        span_s = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}.get(span, 3600)
        self.send(200, metrics_history(span_s))

    def api_name(self, body):
        # The name is the owner's alias (docs/cluster.md §1); the hostname
        # stays the chassis id. `nick` is the field the old wizard posted.
        name = (body.get("name") or body.get("nick") or "").strip().lower()
        owner = (body.get("owner") or "").strip()
        if name and not NICK_RE.fullmatch(name):
            return self.err(400, "box name: letters, digits, . _ - only")
        if owner and not NICK_RE.fullmatch(owner):
            return self.err(400, "owner name: letters, digits, . _ - only")
        if name and ID_NAME_RE.fullmatch(name):
            if name == "pipeos":
                return self.err(409, "pipeos is every Machine's address — pick a name of its own")
            return self.err(409, "pipeos-xxxx names are chassis ids — pick a name of its own")
        if name and name != box_name():
            taken = name_taken(name)
            if taken:
                return self.err(409, taken)
        updates = {}
        if name:
            updates["NAME"] = name
        if owner:
            updates["OWNER_NICK"] = owner
        if not updates:
            return self.err(400, "nothing to set")
        try:
            card_set(updates)
        except RuntimeError as e:
            return self.err(500, str(e))
        # the name rides the server cert as a SAN (pipeos-tls-init); until
        # #234 it only got there at the next boot, so https://<name>.local/
        # warned for the rest of the day the box was named
        tls_ok, tls_detail = (True, "") if "NAME" not in updates else tls_reissue()
        saved, detail = save_state()
        self.send(200, {"ok": True, "hostname": socket.gethostname(), "name": box_name(),
                        "tls": tls_ok, "tls_detail": tls_detail,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_name_suggest(self):
        """Five classic cars not already on this network (docs/cluster.md
        §1). Seeded by the chassis id so a box keeps its first suggestion
        across reloads; the owner may type anything else."""
        rows, _ok = lobby_entries()
        used = {(r.get("name") or "").lower() for r in rows}
        used |= {(r.get("host") or "").lower().removesuffix(".local") for r in rows}
        pool = [c for c in CAR_NAMES if c not in used]
        rnd = random.Random(lanid.mac4())
        rnd.shuffle(pool)
        self.send(200, {"names": pool[:5]})

    def _unconfigured(self, key):
        """Why turning KEY on would be an empty gesture, or "" if it is
        configured. Two services mean nothing without their configuration:
        network storage without a share, terminals without a slot. Their
        inits refuse to start in that state, so a bare toggle-on wrote
        SERVICE_X=on that failed at every boot and the box sat DEGRADED
        (zero, 2026-09-11, pipeOS#266). Adding the configuration turns each
        on by itself (api_nas_set, _apply_terminals)."""
        if key == "nas" and not self._nas_read_shares():
            return ("network storage stays off until there is something to share — "
                    "add a share under Files → Network storage; adding one turns it on")
        if key == "terminals" and not any(
                u.get("terminal") and u.get("unix") and u.get("term_pass")
                and u.get("term_port") and not u.get("disabled") for u in read_users()):
            return ("user terminals stay off until a user has one — give an account a "
                    "browser terminal under Users; that turns them on")
        return ""

    def api_services(self, body):
        svcs = read_services()
        problems = []
        for k in SVC_KEYS:
            if k in body:
                want = bool(body[k])
                if want and not svcs.get(k):
                    why = self._unconfigured(k)
                    if why:
                        # not an error: the wizard posts every toggle in one
                        # body and the rest must still land. The response's
                        # services map says what actually took.
                        problems.append(why)
                        continue
                svcs[k] = want
        write_services(svcs)
        problems += apply_services(svcs)
        saved, detail = save_state()
        self.send(200, {"ok": True, "services": svcs, "problems": problems,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_claude_token(self, body):
        token = (body.get("token") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:\-]{20,512}", token):
            return self.err(400, "that does not look like an API key or a setup-token")
        # an Anthropic Console key bills the Console account; a setup-token
        # rides the owner's Claude subscription. Same file, one line, the
        # variable claude expects for each.
        name = "ANTHROPIC_API_KEY" if token.startswith("sk-ant-api") else "CLAUDE_CODE_OAUTH_TOKEN"
        try:
            vault_put("claude_token", token, by=(self.authed() or {}).get("user", ""))
        except RuntimeError as e:
            return self.err(500, str(e))
        run(["pipebox-claude-trust"], timeout=60)
        probe_ok, probe_out = claude_probe(claude_env({name: token}))
        saved, detail = save_state()
        self.send(200, {"ok": True, "method": "apikey" if name == "ANTHROPIC_API_KEY" else "token",
                        "probe_ok": probe_ok, "probe": probe_out,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_claude_get(self):
        method, st = claude_method()
        self.send(200, {"method": method, "logged_in": method != "none",
                        "auth_method": st.get("authMethod", ""),
                        "billing": st.get("apiProvider", ""),
                        "pending": CLAUDE_LOGIN["proc"] is not None and CLAUDE_LOGIN["proc"].poll() is None})

    def api_claude_login_start(self, body):
        billing = "console" if body.get("billing") == "console" else "claude"
        try:
            url = claude_login_start(billing)
        except RuntimeError as e:
            return self.err(500, str(e))
        self.send(200, {"ok": True, "url": url, "billing": billing})

    def api_claude_login_code(self, body):
        code = (body.get("code") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_#.:\-]{6,512}", code):
            return self.err(400, "that does not look like the code the sign-in page shows")
        ok, why = claude_login_code(code)
        if not ok:
            return self.err(400, why)
        # the sign-in is the credential now; a leftover token would win over it
        vault_drop("claude_token")
        for p in (CLAUDE_AUTH, CLAUDE_AUTH_LEGACY):
            try:
                os.unlink(p)
            except OSError:
                pass
        run(["pipebox-claude-trust"], timeout=60)
        probe_ok, probe_out = claude_probe(claude_env())
        saved, detail = save_state()
        self.send(200, {"ok": True, "method": "login", "probe_ok": probe_ok, "probe": probe_out,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_claude_logout(self, body):
        run([CLAUDE_BIN, "auth", "logout"], timeout=20, env=claude_env())
        vault_drop("claude_token")
        for p in (CLAUDE_AUTH, CLAUDE_AUTH_LEGACY):
            try:
                os.unlink(p)
            except OSError:
                pass
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

    def api_pipe_key(self, body):
        key = (body.get("key") or "").strip()
        if not key or len(key) > 256 or any(c.isspace() for c in key):
            return self.err(400, "paste the one-time key exactly as issued")
        svcs = read_services()
        if not svcs["pipe"]:
            return self.err(400, "enable pipe in services first")
        run(["rc-service", "pipe-daemon", "start"], timeout=150)
        rc, out = run(["pipe", key], timeout=60)
        if rc != 0:
            return self.err(400, "pipe rejected the key (expired? mint a fresh one at pipe.online): "
                            + out.strip()[-200:])
        run(["pipe", "set", "remember_login", "on"], timeout=30)
        run(["pipe", "set", "agent_events", "on"], timeout=30)
        # The box's nick is whatever the key signed in as — read it back from
        # the daemon (never ask a human to retype it; pipeOS#134).
        rc, out = run(["pipe", "status"], timeout=30)
        m = re.search(r"^nick: (\S+)", out, re.M)
        nick = m.group(1) if m else ""
        if nick and nick != "anon" and NICK_RE.fullmatch(nick) and nick != card_get("NICK"):
            try:
                card_set({"NICK": nick})
            except RuntimeError as e:
                return self.err(500, str(e))
        owner = card_get("OWNER_NICK")
        if owner:
            run(["pipe", "contacts", "add", owner], timeout=30)
        saved, detail = save_state()
        self.send(200, {"ok": True, "nick": nick,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_password(self, body):
        # Changes the SESSION's own password (any role). Admins reset other
        # users through /api/users/set instead.
        sess = self.authed()
        users = read_users()
        u = find_user(users, sess["user"])
        if u is None:
            return self.err(403, "your account no longer exists")
        if not check_hash(body.get("current") or "", u.get("hash")):
            time.sleep(2)
            return self.err(403, "current password is wrong")
        new = body.get("new") or ""
        if len(new) < 8:
            return self.err(400, "new password must be at least 8 characters")
        try:
            h = hash_password(new)
        except RuntimeError as e:
            return self.err(500, str(e))
        u["hash"] = h
        write_users(users)
        if u["name"] == "admin":
            # keep the legacy fallback credential in step — it is the
            # users.json-corruption escape hatch and must never go stale
            write_private(ADMIN_CONF, "HASH='%s'\n" % h)
        if u.get("unix") and u.get("sudo"):
            run(["/usr/local/bin/pipeos-user", "set-hash", u["name"]],
                input_text=h + "\n")
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

    # -- users ----------------------------------------------------------------

    def _user_admin_guard(self):
        sess = self.authed()
        if not sess or sess.get("role") != "admin":
            self.err(403, "admin only")
            return None
        return sess

    def api_users(self):
        sess = self._user_admin_guard()
        if sess is None:
            return
        out = [{k: u.get(k) for k in
                ("name", "role", "unix", "share", "sudo", "terminal", "term_port", "disabled")}
               for u in read_users()]
        for u in out:
            u["self"] = u["name"] == sess["user"]
        self.send(200, {"users": out,
                        "terminals_on": read_services().get("terminals", False)})

    def _last_admin_guard(self, users, name):
        """True if removing/disabling `name` would leave no enabled admin."""
        return not any(u["name"] != name and u.get("role") == "admin"
                       and not u.get("disabled") for u in users)

    def api_users_add(self, body):
        if self._user_admin_guard() is None:
            return
        code, payload = user_add(body)
        self._send_saved(code, payload)  # save_state inside

    def _send_saved(self, code, payload):
        """The tail every (code, payload) helper shares: an error passes
        through; a success is saved and says so (the persist rule)."""
        if code != 200:
            return self.err(code, payload)
        saved, detail = save_state()
        payload.update({"saved": saved, "save_detail": "" if saved else detail})
        self.send(200, payload)

    def _apply_terminals(self, users):
        return apply_terminals(users)

    def _nas_read_shares(self):
        return nas_read_shares()

    def api_users_set(self, body):
        sess = self._user_admin_guard()
        if sess is None:
            return
        users = read_users()
        u = find_user(users, (body.get("name") or "").strip())
        if u is None:
            return self.err(404, "no such user")
        if "role" in body and body["role"] not in ("admin", "user", "viewer"):
            return self.err(400, "role must be admin, user or viewer")
        if u.get("share") and any(k in body for k in SHARE_ONLY_FORBIDS):
            return self.err(400, "a share-only account has no shell or dashboard sign-in — "
                                 "set its SMB password under Files → Network storage, or disable it")
        demote = (body.get("role") in ("user", "viewer") or body.get("disabled") is True)
        if demote and u.get("role") == "admin" and self._last_admin_guard(users, u["name"]):
            return self.err(400, "that would leave the box with no admin")
        if "role" in body:
            u["role"] = body["role"]
        problems = []
        if "disabled" in body and bool(body["disabled"]) != bool(u.get("disabled")):
            u["disabled"] = bool(body["disabled"])
            if u["disabled"]:
                drop_user_sessions(u["name"])
            if u.get("unix"):
                # a unix account's SMB logon follows the switch (pipeOS#270)
                problems += nas_user_enabled(u["name"], not u["disabled"], by=sess.get("user", ""))
        if body.get("password"):
            if len(body["password"]) < 8:
                return self.err(400, "password must be at least 8 characters")
            try:
                u["hash"] = hash_password(body["password"])
            except RuntimeError as e:
                return self.err(500, str(e))
            if u["name"] == "admin":
                write_private(ADMIN_CONF, "HASH='%s'\n" % u["hash"])
            if u.get("unix") and u.get("sudo"):
                run(["/usr/local/bin/pipeos-user", "set-hash", u["name"]],
                    input_text=u["hash"] + "\n")
        if body.get("ssh_key") and u.get("unix"):
            rc, out = run(["/usr/local/bin/pipeos-user", "set-key", u["name"]],
                          input_text=body["ssh_key"].strip() + "\n")
            if rc != 0:
                return self.err(400, "ssh key rejected: " + out.strip()[-200:])
        if "term_pass" in body and u.get("terminal"):
            tp = (body.get("term_pass") or "").strip()
            if not tp or any(c in tp for c in "'\n\r\0"):
                return self.err(400, "terminal password: no quotes/newlines")
            u["term_pass"] = tp
        write_users(users)
        problems += apply_terminals(users)
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_users_del(self, body):
        sess = self._user_admin_guard()
        if sess is None:
            return
        users = read_users()
        name = (body.get("name") or "").strip()
        u = find_user(users, name)
        if u is None:
            return self.err(404, "no such user")
        if name == sess["user"]:
            return self.err(400, "sign in as another admin to remove this account")
        if u.get("role") == "admin" and self._last_admin_guard(users, name):
            return self.err(400, "that would leave the box with no admin")
        problems = []
        if u.get("unix"):
            # out of samba first — while the name still resolves: the passdb
            # entry, and every share's user list (a share left with nobody
            # goes too; no share left turns storage off) — otherwise a
            # deleted name kept its SMB logon (pipeOS#270)
            problems += nas_forget_user(name, by=sess.get("user", ""))
            args = ["/usr/local/bin/pipeos-user", "del", name]
            if body.get("purge_home"):
                args.append("--purge-home")
            rc, out = run(args, timeout=30)
            if rc != 0:
                problems.append("unix account not fully removed: " + out.strip()[-200:])
        users.remove(u)
        write_users(users)
        drop_user_sessions(name)
        problems += apply_terminals(users)
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_save(self, _body):
        saved, detail = save_state()
        self.send(200, {"ok": saved, "detail": "" if saved else detail})

    def api_chat(self, body):
        """Talk to the box's Claude from the dashboard — the assistant surface
        for a pipe-less box. Same fence as the pipe listener (the shipped
        pipebox settings); one conversation per box, continued across turns."""
        msg = (body.get("message") or "").strip()
        if not msg or len(msg) > 8000:
            return self.err(400, "say something (under 8000 characters)")
        svcs = read_services()
        if not svcs["claude"]:
            return self.err(400, "the assistant service is switched off")
        backend = read_conf_values(ASSISTANT_CONF, ["ASSISTANT_BACKEND"])["ASSISTANT_BACKEND"] or "claude"
        env = dict(os.environ, HOME="/root")
        os.makedirs(WEBCHAT_DIR, exist_ok=True)
        stdin = None
        if backend == "hermes":
            # -z = one-shot; a named --continue session keeps one conversation
            argv = ["hermes", "-z", msg, "--continue", "webchat"]
        else:
            backend = "claude"
            name, value = claude_auth_env()
            if name:
                env[name] = value
            # The chat's OWN session (#246; Sam, 2026-09-10): --continue picked
            # up whatever transcript was newest in this dir — the master
            # session's — so the chat silently extended the assistant's
            # conversation and the ledger could not tell the two apart.
            try:
                with open(WEBCHAT_SID) as f:
                    sid = f.read().strip()
            except OSError:
                sid = ""
            if not re.fullmatch(r"[0-9a-f-]{36}", sid):
                sid = str(uuid.uuid4())
                write_private(WEBCHAT_SID, sid + "\n")
            argv = ["claude", "-p", "--settings", "/etc/pipeos/pipebox-settings.json"]
            argv += ["--resume", sid] if os.path.exists(os.path.join(WEBCHAT_DIR, ".started")) else ["--session-id", sid]
            stdin = msg
        try:
            p = subprocess.run(
                argv, input=stdin, capture_output=True, text=True,
                timeout=180, env=env, cwd=WEBCHAT_DIR,
            )
        except subprocess.TimeoutExpired:
            return self.err(504, "the assistant took longer than 3 minutes — try again")
        except FileNotFoundError:
            return self.err(500, "%s is not installed on this image" % backend)
        if p.returncode != 0:
            return self.err(502, "%s errored: %s" % (backend, (p.stderr or p.stdout or "")[-300:].strip()))
        with open(os.path.join(WEBCHAT_DIR, ".started"), "a"):
            pass
        self.send(200, {"reply": (p.stdout or "").strip()})

    def api_reboot(self, _body):
        """The recovery lever basho0's ssh lockout proved missing (pipeOS#148):
        on a diskless box a clean reboot IS a restore to last-saved state, and
        before this the only path to one on a shell-less box was the power
        button. Answer first, then reboot — the browser deserves its 200."""
        subprocess.Popen(
            ["sh", "-c", "sleep 2; reboot"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.send(200, {"ok": True, "note": "rebooting — the box is back in about a minute"})

    def api_reboot_firmware(self, _body):
        """Reboot into the UEFI setup (BIOS). On a headless box nobody can hit
        the POST key, so the wizard sets the firmware-setup indication instead.
        Check support first and only reboot on success, so a firmware that does
        not support it reports back cleanly rather than doing a plain reboot."""
        rc, out = run(["/usr/local/bin/pipeos-reboot-firmware"], timeout=15)
        if rc != 0:
            return self.err(400, out.strip()[-200:] or "could not enter firmware setup")
        # Armed the indication; answer first, then reboot (as api_reboot does).
        subprocess.Popen(
            ["sh", "-c", "sleep 2; reboot"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.send(200, {"ok": True, "note": "rebooting into firmware setup — connect a display to the box"})

    def api_repair_access(self, _body):
        """Cheaper than a reboot when only remote access is wedged: put the
        key back if it vanished (the #148 failure), bounce sshd (clears any
        in-process auth state), and say what was done."""
        actions = []
        try:
            if (not os.path.getsize("/root/.ssh/authorized_keys")
                    if os.path.exists("/root/.ssh/authorized_keys") else True):
                raise OSError
        except OSError:
            if os.path.exists("/work/.authorized_keys.backup"):
                os.makedirs("/root/.ssh", mode=0o700, exist_ok=True)
                rc, _ = run(["cp", "/work/.authorized_keys.backup",
                             "/root/.ssh/authorized_keys"])
                if rc == 0:
                    os.chmod("/root/.ssh/authorized_keys", 0o600)
                    actions.append("restored authorized_keys from the /work backup")
                else:
                    actions.append("authorized_keys is missing and the backup would not restore")
            else:
                actions.append("authorized_keys is missing and no backup exists")
        rc, out = run(["rc-service", "sshd", "restart"], timeout=60)
        actions.append("restarted sshd" if rc == 0
                       else "sshd restart FAILED: " + out.strip()[-150:])
        # a restored key that is not saved is a key that vanishes again at
        # the next boot — the very failure this repairs
        saved, detail = save_state()
        actions.append("saved" if saved else "save FAILED: " + detail)
        self.send(200, {"ok": True, "actions": actions, "saved": saved})


# ---- Phase B surfaces: logs, streaming, pipe, updates ----------------------

LOG_ALLOW = {
    "selfcheck": "/work/logs/selfcheck.log",
    "schedule": "/work/logs/schedule.log",
    "pipe-daemon": "/work/logs/pipe-daemon.log",
    "pipebox-listener": "/work/logs/pipebox-listener.log",
    "pipeos-web": "/work/logs/pipeos-web.log",
    "pipeos-mdns": "/work/logs/pipeos-mdns.log",
    "pipeos-stream": "/work/logs/pipeos-stream.log",
    "pipeos-assistant": "/work/logs/pipeos-assistant.log",
    "selfupdate": "/work/logs/selfupdate.log",
    "worksweep": "/work/logs/worksweep.log",
}
STREAM_CONF = ETC + "/stream.conf"
ASSISTANT_CONF = ETC + "/assistant.conf"
# Scheduled runs (#242): the job list is owner intent and rides the apkovl;
# the runtime state (last rc, failures, sessions, runs.log) is on /work.
SCHEDULE_CONF = ETC + "/schedule.json"
SCHEDULE_STATE_DIR = "/work/.pipeos/schedule"
SCHEDULE_RUN_BIN = "/usr/local/bin/pipeos-schedule-run"
SCHEDULE_LOCK = "/run/pipeos/schedule.lock"
SCHEDULE_LOGDIR = "/work/logs"
SCHEDULE_MAX_JOBS = 32
JOB_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
# The usage ledger (#246): rows on /work, the shipped rate table, the card's
# conf for the cap and the owner, the schedule's runs.log and the listener's
# sessions for attribution, the dashboard chat's own session id.
LEDGER_DIR = "/work/.pipeos/ledger"
LEDGER_PAUSED = LEDGER_DIR + "/paused"
LEDGER_TRANSCRIPTS = "/work/claude/projects"
LEDGER_RATES = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rates.json"))
LEDGER_CONF = ETC + "/pipebox.conf"
LEDGER_SESSIONS = "/work/pipebox/sessions"
WEBCHAT_DIR = "/work/pipebox/webchat"
WEBCHAT_SID = WEBCHAT_DIR + "/.dashboard-sid"
LEDGER_INGEST_S = 60
LEDGER_LOCK = threading.Lock()
LEDGER_STATE = {"obj": None, "last": 0}
SELFUPDATE_CONF = ETC + "/selfupdate.conf"
UPDATE_STAMP = "/work/.pipeos/selfupdate.applied"


def tail_file(path, lines):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    return "\n".join(data.splitlines()[-lines:])


def ledger_obj():
    """One Ledger over the module's constants — built lazily so a probe
    that repoints the constants gets its own."""
    with LEDGER_LOCK:
        L = LEDGER_STATE["obj"]
        if L is None or L.dir != LEDGER_DIR or L.transcripts != LEDGER_TRANSCRIPTS:
            L = ledger.Ledger(dir=LEDGER_DIR, transcripts=LEDGER_TRANSCRIPTS, rates=LEDGER_RATES, conf=LEDGER_CONF,
                              runs_log=os.path.join(SCHEDULE_STATE_DIR, "runs.log"), sessions_dir=LEDGER_SESSIONS,
                              webchat_sid=WEBCHAT_SID)
            LEDGER_STATE["obj"] = L
        return L


def ledger_refresh(force=False):
    """Ingest + enforce, at most once per LEDGER_INGEST_S unless forced.
    Never raises: the ledger is a view of the box, not the box."""
    now = time.time()
    if not force and now - LEDGER_STATE["last"] < LEDGER_INGEST_S:
        return
    LEDGER_STATE["last"] = now
    try:
        L = ledger_obj()
        L.ingest()
        L.enforce_cap()
    except Exception as e:  # a bad transcript line must never kill the dashboard
        sys.stderr.write("pipeos-webd: ledger: %s\n" % e)


def ledger_worker():
    while True:
        ledger_refresh()
        time.sleep(LEDGER_INGEST_S)


def card_ensure_key(key):
    """A box claimed before a card key existed has no KEY= line for
    card_set to rewrite; add an empty one (the generator's default)."""
    try:
        with open(CARD) as f:
            text = f.read()
    except OSError:
        return
    if not re.search(r"^%s=" % re.escape(key), text, re.M):
        write_private(CARD, text.rstrip("\n") + "\n%s=\n" % key)
        os.chmod(CARD, 0o644)


def read_schedule():
    try:
        with open(SCHEDULE_CONF) as f:
            d = json.load(f)
        jobs = d.get("jobs", []) if isinstance(d, dict) else []
        return [j for j in jobs if isinstance(j, dict) and j.get("name")]
    except (OSError, ValueError):
        return []


def write_schedule(jobs):
    write_private(SCHEDULE_CONF, json.dumps({"v": 1, "jobs": jobs}, indent=1) + "\n")


def schedule_state():
    try:
        with open(os.path.join(SCHEDULE_STATE_DIR, "state.json")) as f:
            return json.load(f).get("jobs", {})
    except (OSError, ValueError):
        return {}


def schedule_running():
    """Is a job running right now? The runner holds SCHEDULE_LOCK for the
    duration; a non-blocking try tells."""
    try:
        with open(SCHEDULE_LOCK, "a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def read_conf_values(path, keys):
    out = {k: "" for k in keys}
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return out
    for k in keys:
        m = re.search(rf"^{k}='?\"?([^'\"\n]*)", text, re.M)
        if m:
            out[k] = m.group(1)
    return out


class PhaseB:
    """Mixin-style handlers kept in one place; bound onto Handler below."""

    def api_logs(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (q.get("name") or ["selfcheck"])[0]
        path = LOG_ALLOW.get(name)
        # a scheduled job's own log (#242): schedule-<job>, for a job that exists
        if path is None and name.startswith("schedule-") and JOB_NAME_RE.match(name[9:]) \
                and any(j["name"] == name[9:] for j in read_schedule()):
            path = os.path.join(SCHEDULE_LOGDIR, name + ".log")
        if path is None:
            return self.err(400, "unknown log (choose: %s)" % ", ".join(sorted(LOG_ALLOW)))
        try:
            lines = min(500, max(10, int((q.get("lines") or ["100"])[0])))
        except ValueError:
            lines = 100
        text = tail_file(path, lines)
        self.send(200, {"name": name, "text": text if text is not None
                        else "(no log yet — the service may not have run)"})

    def api_docs(self, path):
        """GET /api/docs — the operator docs index; /api/docs/<slug> — one
        page as raw markdown. Slugs match DOC_SLUG_RE, so nothing here can
        name a path (no dots, no slashes)."""
        slug = path[len("/api/docs"):].strip("/")
        if not slug:
            pages = []
            try:
                names = sorted(os.listdir(DOCS_DIR))
            except OSError:
                names = []
            for fn in names:
                if not fn.endswith(".md"):
                    continue
                s = fn[:-3]
                if not DOC_SLUG_RE.match(s):
                    continue
                title = s
                try:
                    with open(os.path.join(DOCS_DIR, fn), encoding="utf-8") as f:
                        for line in f:
                            if line.startswith("# "):
                                title = line[2:].strip()
                                break
                except OSError:
                    continue
                pages.append({"slug": s, "title": title})
            order = {s: i for i, s in enumerate(DOCS_ORDER)}
            pages.sort(key=lambda p: (order.get(p["slug"], len(order)), p["slug"]))
            return self.send(200, {"pages": pages})
        if not DOC_SLUG_RE.match(slug):
            return self.err(400, "bad page name")
        try:
            with open(os.path.join(DOCS_DIR, slug + ".md"), "rb") as f:
                data = f.read()
        except OSError:
            return self.err(404, "no such page")
        self.send(200, data, ctype="text/markdown; charset=utf-8")

    def api_stream_get(self):
        base = ["STREAM_MODE", "STREAM_SRC", "STREAM_URL", "STREAM_RES",
                "STREAM_FPS", "STREAM_VAAPI", "STREAM_BITRATE", "STREAM_ARGS",
                "STREAM_BOOT"]
        tk = []
        for n in range(1, STREAM_MAX_TARGETS + 1):
            tk += ["STREAM_T%d_URL" % n, "STREAM_T%d_KEY" % n,
                   "STREAM_T%d_ON" % n, "STREAM_T%d_NAME" % n,
                   "STREAM_T%d_BR" % n]
        vals = read_conf_values(STREAM_CONF, base + tk)
        have = vault_names()
        targets = [{
            "name": vals["STREAM_T%d_NAME" % n],
            "url": vals["STREAM_T%d_URL" % n],
            "on": vals["STREAM_T%d_ON" % n] == "1",
            "br": vals["STREAM_T%d_BR" % n],
            "key_set": ("stream_key_%d" % n) in have or bool(vals["STREAM_T%d_KEY" % n]),
        } for n in range(1, STREAM_MAX_TARGETS + 1)]
        rc, _ = run(["rc-service", "pipeos-stream", "status"], timeout=15)
        self.send(200, {
            "mode": vals["STREAM_MODE"] or "media",
            "src": vals["STREAM_SRC"], "url": vals["STREAM_URL"],
            "res": vals["STREAM_RES"] or "1920x1080", "fps": vals["STREAM_FPS"] or "30",
            "vaapi": vals["STREAM_VAAPI"] == "1", "bitrate": vals["STREAM_BITRATE"] or "3500k",
            "args": vals["STREAM_ARGS"], "boot": vals["STREAM_BOOT"] != "0",
            "targets": targets, "running": rc == 0})

    def api_stream_set(self, body):
        # Everything written here is shell-sourced by the wrapper, so every
        # free-text value runs the single-quote injection guard.
        def guard(v, label):
            v = (v or "").strip()
            if any(c in v for c in "'\n\r\0"):
                raise ValueError("%s may not contain quotes or newlines" % label)
            if len(v) > 500:
                raise ValueError("%s is too long" % label)
            return v
        fields = {}
        try:
            for k, name in (("mode", "STREAM_MODE"), ("src", "STREAM_SRC"),
                            ("url", "STREAM_URL"), ("res", "STREAM_RES"),
                            ("fps", "STREAM_FPS"), ("bitrate", "STREAM_BITRATE"),
                            ("args", "STREAM_ARGS")):
                fields[name] = guard(body.get(k), k)
            targets = body.get("targets") or []
            if not isinstance(targets, list):
                return self.err(400, "targets must be a list")
            have = vault_names()
            keys = {}
            for i in range(STREAM_MAX_TARGETS):
                n = i + 1
                t = targets[i] if i < len(targets) and isinstance(targets[i], dict) else {}
                key = guard(t.get("key"), "target %d key" % n)
                if not key and t.get("keep_key") and ("stream_key_%d" % n) in have:
                    key = None   # keep what the vault has
                keys[n] = key
                br = guard(t.get("br"), "target %d bitrate" % n)
                if br and not re.match(r"^\d{2,6}k?$", br):
                    return self.err(400, "target %d bitrate must look like 6000k" % n)
                fields["STREAM_T%d_URL" % n] = guard(t.get("url"), "target %d url" % n)
                fields["STREAM_T%d_NAME" % n] = guard(t.get("name"), "target %d name" % n)
                # the key itself is in the vault (#244); the conf carries a blank
                fields["STREAM_T%d_KEY" % n] = ""
                fields["STREAM_T%d_ON" % n] = "1" if t.get("on") else "0"
                fields["STREAM_T%d_BR" % n] = br
        except ValueError as e:
            return self.err(400, str(e))
        if fields["STREAM_MODE"] not in ("media", "browser"):
            fields["STREAM_MODE"] = "media"
        if fields["STREAM_RES"] and not re.match(r"^\d{2,5}x\d{2,5}$", fields["STREAM_RES"]):
            return self.err(400, "resolution must look like 1920x1080")
        if fields["STREAM_FPS"] and not re.match(r"^\d{1,3}$", fields["STREAM_FPS"]):
            return self.err(400, "fps must be a number")
        if fields["STREAM_BITRATE"] and not re.match(r"^\d{2,6}k?$", fields["STREAM_BITRATE"]):
            return self.err(400, "bitrate must look like 3500k")
        fields["STREAM_VAAPI"] = "1" if body.get("vaapi") else "0"
        fields["STREAM_BOOT"] = "1" if body.get("boot", True) else "0"
        # keys: a new value replaces, blank+keep_key keeps, blank alone deletes (#244)
        try:
            by = (self.authed() or {}).get("user", "")
            for n, key in keys.items():
                if key is None:
                    continue
                if key:
                    vault_put("stream_key_%d" % n, key, by=by)
                else:
                    vault_drop("stream_key_%d" % n)
        except RuntimeError as e:
            return self.err(500, str(e))
        write_private(STREAM_CONF, "".join(
            "%s='%s'\n" % (k, v) for k, v in fields.items()))
        problems = []
        svcs = read_services()
        has_target = any(fields["STREAM_T%d_ON" % n] == "1" and fields["STREAM_T%d_URL" % n]
                         for n in range(1, STREAM_MAX_TARGETS + 1))
        if not svcs["stream"] and has_target:
            # Configure implies enable: "I filled in the form" means "it
            # streams — now, and after a reboot". The Services toggle and
            # STREAM_BOOT=0 remain the two opt-outs.
            svcs["stream"] = True
            write_services(svcs)
            problems += apply_services(svcs)
        elif svcs["stream"]:
            rc, out = run(["rc-service", "pipeos-stream", "restart"], timeout=60)
            if rc != 0:
                problems.append("stream service did not start: " + out.strip()[-200:])
            # keep the runlevel in step with a changed STREAM_BOOT
            if fields["STREAM_BOOT"] == "0":
                run(["rc-update", "del", "pipeos-stream", "default"])
            else:
                run(["rc-update", "add", "pipeos-stream", "default"])
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_stream_log(self):
        self.send(200, {"text": tail_file(LOG_ALLOW["pipeos-stream"], 100)
                        or "(no stream log yet)"})

    def api_assistant_get(self):
        vals = read_conf_values(ASSISTANT_CONF, ["ASSISTANT_USER", "ASSISTANT_PORT",
                                                "ASSISTANT_PASS", "ASSISTANT_BACKEND"])
        rc, _ = run(["rc-service", "pipeos-assistant", "status"], timeout=15)
        self.send(200, {"user": vals["ASSISTANT_USER"] or "admin",
                        "port": vals["ASSISTANT_PORT"] or "7681",
                        "pass_set": "assistant_pass" in vault_names() or bool(vals["ASSISTANT_PASS"]),
                        "backend": vals["ASSISTANT_BACKEND"] or "claude",
                        "backends": [{"id": b, "installed": shutil.which(b) is not None}
                                     for b in ASSISTANT_BACKENDS],
                        "running": rc == 0})

    def api_assistant_set(self, body):
        fields = {}
        backend = (body.get("backend") or "").strip()
        if backend:
            if backend not in ASSISTANT_BACKENDS:
                return self.err(400, "backend must be one of: " + ", ".join(ASSISTANT_BACKENDS))
            if shutil.which(backend) is None:
                return self.err(400, "%s is not installed on this image" % backend)
            fields["ASSISTANT_BACKEND"] = backend
        else:
            fields["ASSISTANT_BACKEND"] = read_conf_values(
                ASSISTANT_CONF, ["ASSISTANT_BACKEND"])["ASSISTANT_BACKEND"] or "claude"
        for k, name in (("user", "ASSISTANT_USER"), ("port", "ASSISTANT_PORT"),
                        ("password", "ASSISTANT_PASS")):
            v = (body.get(k) or "").strip()
            # sourced by the init/wrapper: same single-quote guard as stream.conf
            if any(c in v for c in "'\n\r\0"):
                return self.err(400, "%s may not contain quotes or newlines" % k)
            if len(v) > 200:
                return self.err(400, "%s is too long" % k)
            fields[name] = v
        # a partial update (e.g. a backend flip) must not clobber saved values
        prev = read_conf_values(ASSISTANT_CONF, ["ASSISTANT_USER", "ASSISTANT_PORT"])
        if not fields["ASSISTANT_USER"]:
            fields["ASSISTANT_USER"] = prev["ASSISTANT_USER"] or "admin"
        if fields["ASSISTANT_PORT"] and not re.match(r"^\d{2,5}$", fields["ASSISTANT_PORT"]):
            return self.err(400, "port must be a number")
        if not fields["ASSISTANT_PORT"]:
            fields["ASSISTANT_PORT"] = prev["ASSISTANT_PORT"] or "7681"
        keep = not fields["ASSISTANT_PASS"] and body.get("keep_pass") and (
            "assistant_pass" in vault_names()
            or read_conf_values(ASSISTANT_CONF, ["ASSISTANT_PASS"])["ASSISTANT_PASS"])
        if not fields["ASSISTANT_PASS"] and not keep and not (backend and body.get("keep_pass")):
            # backend-only flips on a not-yet-configured terminal are fine;
            # anything that would SERVE a terminal still demands a password
            return self.err(400, "set a password — the terminal is shell access and must not be served open")
        # the password lives in the vault (#244); the conf carries a blank
        if fields["ASSISTANT_PASS"]:
            try:
                vault_put("assistant_pass", fields["ASSISTANT_PASS"], by=(self.authed() or {}).get("user", ""))
            except RuntimeError as e:
                return self.err(500, str(e))
        fields["ASSISTANT_PASS"] = ""
        write_private(ASSISTANT_CONF, "".join("%s='%s'\n" % (k, v) for k, v in fields.items()))
        problems = []
        if read_services().get("assistant"):
            rc, out = run(["rc-service", "pipeos-assistant", "restart"], timeout=60)
            if rc != 0:
                problems.append("assistant terminal did not start: " + out.strip()[-200:])
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems, "saved": saved,
                        "save_detail": "" if saved else detail})

    def api_support_get(self):
        """The support surface docs/support-relay.md promised: the box's
        public key (the owner sends it to the vendor), the relay and port
        it will dial, and whether the tunnel is up right now."""
        info = support_info()
        svcs = read_services()
        info["enabled"] = bool(svcs.get("support"))
        rc, _ = run(["rc-service", "pipeos-support", "status"], timeout=15)
        info["connected"] = rc == 0
        info["configured"] = bool(info["relay"] and info["port"])
        self.send(200, info)

    def api_pipe_get(self):
        rc, out = run(["pipe", "status", "-o", "json"], timeout=20)
        nick, authed_flag = "", False
        if rc == 0:
            try:
                j = json.loads(out[out.index("{"):])
                nick = j.get("nick") or j.get("status", {}).get("nick") or ""
                authed_flag = bool(j.get("status", {}).get("authenticated"))
            except (ValueError, AttributeError):
                pass
        if not nick:
            rc2, out2 = run(["pipe", "status"], timeout=20)
            m = re.search(r"^nick: (\S+)", out2, re.M) if rc2 == 0 else None
            nick = m.group(1) if m else ""
        prefs = {}
        rc3, out3 = run(["pipe", "get"], timeout=20)
        if rc3 == 0:
            for m in re.finditer(r"^\s*(dm_relay|remember_login|agent_events)\b\D*?\b(on|off|true|false)\b",
                                 out3, re.M | re.I):
                prefs[m.group(1)] = m.group(2).lower() in ("on", "true")
        # memberships are discovered, never typed — pipe knows them already
        cohorts = []
        rc4, out4 = run(["pipe", "cohorts", "-o", "json"], timeout=20)
        if rc4 == 0:
            try:
                cohorts = json.loads(out4[out4.index("{"):]).get("cohorts") or []
            except (ValueError, AttributeError):
                pass
        self.send(200, {"enabled": read_services()["pipe"], "nick": nick,
                        "authed": authed_flag,
                        "owner": card_get("OWNER_NICK"),
                        "cohort": card_get("COHORT_ID"),
                        "cohorts": cohorts,
                        "prefs": prefs})

    def api_pipe_contacts(self):
        rc, out = run(["pipe", "contacts", "-o", "json"], timeout=20)
        contacts = None
        if rc == 0:
            try:
                contacts = json.loads(out[out.index("["):])
            except ValueError:
                try:
                    contacts = json.loads(out[out.index("{"):])
                except ValueError:
                    pass
        self.send(200, {"contacts": contacts,
                        "text": "" if contacts is not None else out.strip()[-2000:]})

    def api_pipe_contact(self, body):
        nick = (body.get("nick") or "").strip()
        if not NICK_RE.fullmatch(nick):
            return self.err(400, "nick: letters, digits, . _ - only")
        verb = "remove" if body.get("remove") else "add"
        rc, out = run(["pipe", verb, nick], timeout=20)
        if rc != 0:
            return self.err(500, ("could not %s %s: " % (verb, nick)) + out.strip()[-200:])
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

    def api_pipe_set(self, body):
        pref = body.get("pref") or ""
        if pref not in PIPE_PREFS:
            return self.err(400, "pref must be one of: " + ", ".join(PIPE_PREFS))
        val = "on" if body.get("value") else "off"
        rc, out = run(["pipe", "set", pref, val], timeout=20)
        if rc != 0:
            return self.err(500, "pipe set failed: " + out.strip()[-200:])
        saved, detail = save_state()
        self.send(200, {"ok": True, "pref": pref, "value": val == "on",
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_pipe_logout(self, body):
        rc, out = run(["pipe", "logout"], timeout=20)
        if rc != 0:
            return self.err(500, "logout failed: " + out.strip()[-200:])
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

    def api_pipe_board(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        cid = (q.get("id") or [""])[0] or card_get("COHORT_ID")
        if cid and not re.fullmatch(r"[0-9]{1,12}", cid):
            return self.err(400, "cohort id is digits only")
        if not cid:
            return self.send(200, {"cohort": "", "text": ""})
        rc, out = run(["pipe", "cohorts", "board", cid], timeout=20)
        self.send(200, {"cohort": cid,
                        "text": out.strip()[-4000:] if rc == 0
                        else "(board unavailable: %s)" % out.strip()[-200:]})

    def api_cohort(self, body):
        cid = (body.get("id") or "").strip()
        if cid and not re.fullmatch(r"[0-9]{1,12}", cid):
            return self.err(400, "cohort id is digits only")
        try:
            card_set({"COHORT_ID": cid})
        except RuntimeError as e:
            return self.err(500, str(e))
        saved, detail = save_state()
        self.send(200, {"ok": True, "cohort": cid,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_update_get(self):
        conf = read_conf_values(SELFUPDATE_CONF, ["UPDATE_RELEASE_URL", "UPDATE_URL", "IMAGE_UPDATE"])
        origin = conf["UPDATE_RELEASE_URL"] or conf["UPDATE_URL"]
        image_update = "off" if conf["IMAGE_UPDATE"] == "off" else "auto"   # absent = auto (#275)
        image_last = ""
        try:
            with open("/work/.pipeos/image-updated") as f:
                image_last = f.read().strip()
        except OSError:
            pass
        applied = ""
        try:
            with open(UPDATE_STAMP) as f:
                applied = f.read().strip()
        except OSError:
            pass
        remote, state = "", "unknown"
        if conf["UPDATE_RELEASE_URL"]:
            try:
                with urllib.request.urlopen(
                        conf["UPDATE_RELEASE_URL"].rstrip("/") + "/SHA256SUMS",
                        timeout=10) as r:
                    m = re.search(r"^([0-9a-f]{64})\s+pipeos-repo\.tar\.gz",
                                  r.read().decode(), re.M)
                    remote = m.group(1) if m else ""
            except OSError:
                state = "origin unreachable"
        if remote:
            state = "current" if remote == applied else "update available"
        elif not origin:
            state = "self-update disabled"
        self.send(200, {"origin": origin, "applied": applied[:12],
                        "remote": remote[:12], "state": state,
                        "image_update": image_update, "image_last": image_last,
                        "image_pending": os.path.exists("/run/pipeos/flash-pending"),
                        "last": tail_file(LOG_ALLOW["selfupdate"], 3) or ""})

    def api_update_set(self, body):
        """The System page's switch: automatic image updates on/off — the
        `pipeos selfupdate image` verb does the write and the save (#275)."""
        want = body.get("image_update")
        if want not in ("auto", "off"):
            return self.err(400, "image_update must be auto or off")
        rc, out = run(["pipeos-selfupdate", "image", "on" if want == "auto" else "off"], timeout=300)
        if rc != 0:
            return self.err(500, "could not set image updates: " + out.strip()[-200:])
        # the verb wrote the conf AND saved (or reported why it could not); do
        # not save again here (that was two full lbu cycles per flip). "saved"
        # in its output is the receipt (#275 review).
        saved = "\nsaved" in ("\n" + out)
        self.send(200, {"ok": True, "image_update": want, "saved": saved,
                        "save_detail": "" if saved else out.strip()[-200:]})

    def api_flash_get(self):
        image = lanid.image_info(FLASH_IMAGE_TXT)
        applied = ""
        try:
            with open(FLASH_APPLIED) as f:
                applied = f.read().strip()
        except OSError:
            pass
        remote = release_sums().get("pipeos-usb.img.xz", "")
        progress = ""
        try:
            with open(FLASH_PROGRESS) as f:
                progress = f.read()[-4000:].replace("\r", "\n").strip().split("\n")[-1]
        except OSError:
            pass
        with FLASH_LOCK:
            state = dict(FLASH)
        state.update({"image": image, "applied": applied[:12], "remote": remote[:12],
                      "step": flash_step(), "progress": progress})
        self.send(200, state)

    def api_flash(self, body):
        mode = body.get("mode")
        if mode != "inplace":
            return self.err(400, "mode must be inplace (a second stick is `pipeos flash apply --to /dev/sdX` from a shell for now)")
        # the typed confirmation is checked HERE, not only in the browser:
        # a dashboard that trusts the client on a media rewrite trusts too much
        want = card_get("NICK") or socket.gethostname()
        if (body.get("confirm") or "").strip() != want:
            return self.err(400, "type the box's name (%s) to confirm" % want)
        with BACKUP_LOCK:
            if BACKUP["running"]:
                return self.err(400, "a backup is running — let it finish first")
        with FLASH_LOCK:
            if FLASH["running"]:
                return self.err(400, "a flash is already running")
            FLASH.update({"running": True, "mode": mode,
                          "started": int(time.time()), "ok": None, "detail": ""})
        # fetch (no-op when staged and current), then apply
        threading.Thread(target=flash_worker, args=(
            ["sh", "-c", "%s fetch && %s apply --yes" % (FLASH_BIN, FLASH_BIN)],),
            daemon=True).start()
        self.send(200, {"ok": True, "started": True})

    def api_update_now(self, _body):
        # packages only: the manual button must never rewrite p1 and reboot the
        # box under the owner — that is the automatic path and pipeos flash
        # apply, each with its own confirmation (#275 review).
        rc, out = run(["pipeos-selfupdate", "--packages"], timeout=900)
        self.send(200, {"ok": rc == 0, "detail": out.strip()[-500:]})


for _n in dir(PhaseB):
    if _n.startswith("api_"):
        setattr(Handler, _n, getattr(PhaseB, _n))


TLS_INIT = "/usr/local/bin/pipeos-tls-init"
# the one SSLContext behind :443. load_cert_chain on it again swaps the cert
# for every connection accepted from then on — no listener restart, no
# dropped sessions — which is how a rename gets its SAN at once (#234).
HTTPS = {"ctx": None}


def tls_reissue():
    """Re-run pipeos-tls-init (it re-issues server.crt only when a SAN it wants
    — the name, the IP — is missing) and hand the live listener the result.
    Returns (ok, detail). Best-effort, like start_https: HTTP on :80 never
    depends on it."""
    rc, out = run([TLS_INIT], timeout=30)
    if rc != 0:
        return False, "pipeos-tls-init rc=%d: %s" % (rc, out.strip()[-300:])
    ctx = HTTPS["ctx"]
    if ctx is None:
        # no :443 at boot (first boot before the CA existed, say) — bring it
        # up now that a cert exists rather than wait for the next reboot
        start_https(init=False)
        return HTTPS["ctx"] is not None, "https listener started"
    try:
        ctx.load_cert_chain(SRV_CRT, SRV_KEY)
    except Exception as e:
        return False, "reload cert: %s" % e
    return True, "cert reloaded"


def start_https(init=True):
    """Serve HTTPS on :443 in a background thread if the box CA + server cert
    exist. HTTP on :80 keeps working regardless, so a TLS problem can never lock
    the owner out of the wizard — HTTPS is strictly additive until they install
    the CA and choose to use it. Best-effort: any failure just means no :443."""
    if init:
        try:
            subprocess.run([TLS_INIT], timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    if not (os.path.exists(SRV_CRT) and os.path.exists(SRV_KEY)):
        return
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(SRV_CRT, SRV_KEY)
        httpsd = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
        httpsd.daemon_threads = True
        httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True)
    except Exception as e:
        sys.stderr.write("pipeos-webd: HTTPS not started: %s\n" % e)
        return
    HTTPS["ctx"] = ctx
    threading.Thread(target=httpsd.serve_forever, daemon=True).start()
    sys.stderr.write("pipeos-webd listening on :443 (TLS)\n")


def main():
    port = int(os.environ.get("PIPEOS_WEB_PORT", "80"))
    os.makedirs(SESS_DIR, mode=0o700, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    threading.Thread(target=metrics_sampler, daemon=True).start()
    threading.Thread(target=ledger_worker, daemon=True).start()
    start_https()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    sys.stderr.write("pipeos-webd listening on :%d (claimed=%s)\n" % (port, claimed()))
    srv.serve_forever()


if __name__ == "__main__":
    main()
