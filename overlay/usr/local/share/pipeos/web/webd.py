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
import json
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import uuid
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

ETC = "/etc/pipeos"
ADMIN_CONF = ETC + "/web-admin.conf"
SERVICES_CONF = ETC + "/services.conf"
CLAUDE_AUTH = ETC + "/claude-auth.env"
CARD = ETC + "/card.conf"
PROVISIONED = ETC + "/provisioned"
TLS_DIR = ETC + "/tls"
CA_CRT = TLS_DIR + "/ca.crt"
SRV_CRT = TLS_DIR + "/server.crt"
SRV_KEY = TLS_DIR + "/server.key"
SESS_DIR = "/run/pipeos/web-sessions"
BOOT_REPORT = "/run/pipeos/boot-report"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

USERS_CONF = ETC + "/users.json"
TERMINALS_CONF = ETC + "/terminals.conf"

SESSION_IDLE_S = 24 * 3600
NICK_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
USER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# names that must never become unix accounts from the dashboard
USER_NAME_DENY = {"root", "nobody", "operator", "shutdown", "halt", "sync", "bin", "daemon", "adm"}
TERM_PORT_BASE = 7701
SVC_KEYS = ("pipe", "claude", "stream", "agy", "support", "assistant", "terminals")

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


def run(argv, timeout=60, input_text=None):
    """Run a command (no shell, ever). Returns (rc, stdout+stderr)."""
    try:
        p = subprocess.run(
            argv, input=input_text, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out: " + " ".join(argv)
    except FileNotFoundError:
        return 127, "not found: " + argv[0]


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
    return out


def apply_services(svcs):
    """Mirror the declaration into rc-update + running state. Best-effort per
    daemon; returns a list of human-readable problems (empty == clean)."""
    problems = []
    want = set(daemons_for(svcs))
    managed = ("pipe-daemon", "pipebox-listener", "pipeos-stream",
               "pipeos-support", "pipeos-assistant", "pipeos-terminals")
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
         "mem_total_mb": None, "mem_avail_mb": None, "temp_c": None}
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
                    "temp": p["temp_c"], "rx": rx, "tx": tx,
                    "work_pct": slow["work_pct"], "root_pct": slow["root_pct"],
                })
        except Exception:
            pass  # a bad sample must never kill the sampler
        tick += 1
        time.sleep(SAMPLE_S)


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
    rates = []
    for i, r in enumerate(rows):
        bps = (None, None)
        if i and r["rx"] is not None and rows[i - 1]["rx"] is not None:
            dt = max(1, r["t"] - rows[i - 1]["t"])
            bps = (max(0, r["rx"] - rows[i - 1]["rx"]) * 8 // dt,
                   max(0, r["tx"] - rows[i - 1]["tx"]) * 8 // dt)
        rates.append(bps)
    k = max(1, (len(rows) + 359) // 360)
    out = {"interval_s": k * SAMPLE_S, "t0": rows[0]["t"] if rows else None,
           "cpu": [], "mem_pct": [], "temp": [], "rx_bps": [], "tx_bps": [],
           "work_pct": [], "root_pct": []}
    for i in range(0, len(rows), k):
        b, rb = rows[i:i + k], rates[i:i + k]
        out["cpu"].append(_bucket([r["load1"] for r in b], "max"))
        out["mem_pct"].append(_bucket([r["mem"] for r in b], "avg"))
        out["temp"].append(_bucket([r["temp"] for r in b], "max"))
        out["rx_bps"].append(_bucket([r[0] for r in rb], "avg"))
        out["tx_bps"].append(_bucket([r[1] for r in rb], "avg"))
        out["work_pct"].append(_bucket([r["work_pct"] for r in b], "avg"))
        out["root_pct"].append(_bucket([r["root_pct"] for r in b], "avg"))
    return out


# ---- HTTP ------------------------------------------------------------------

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
        self.end_headers()
        self.wfile.write(data)

    def err(self, code, message):
        self.send(code, {"error": message})

    def cookie_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session":
                return v
        return None

    def authed(self):
        return valid_session(self.cookie_token())

    def body_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if n <= 0 or n > 65536:
            return None
        try:
            return json.loads(self.rfile.read(n).decode())
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

    # -- routes --
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path in ("/setup", "/login", "/dashboard"):
            return self.serve_static("index.html")
        if path.startswith("/static/"):
            return self.serve_static(path[len("/static/"):])
        if path == "/api/state":
            return self.api_state()
        # The CA root is public by design — the owner installs it to trust this
        # box, so these are unauthenticated (downloading a public cert is safe).
        if path == "/ca.crt":
            return self.serve_ca_cert()
        if path == "/pipeos-ca.mobileconfig":
            return self.serve_ca_mobileconfig()
        if path == "/install-ca.sh":
            return self.serve_ca_installer()
        readers = {
            "/api/status": self.api_status,
            "/api/users": self.api_users,
            "/api/metrics": self.api_metrics,
            "/api/metrics-history": self.api_metrics_history,
            "/api/files": self.api_files,
            "/api/file-dl": self.api_file_dl,
            "/api/disks": self.api_disks,
            "/api/logs": self.api_logs,
            "/api/stream": self.api_stream_get,
            "/api/stream-log": self.api_stream_log,
            "/api/assistant": self.api_assistant_get,
            "/api/pipe": self.api_pipe_get,
            "/api/pipe-contacts": self.api_pipe_contacts,
            "/api/pipe-board": self.api_pipe_board,
            "/api/update": self.api_update_get,
        }
        fn = readers.get(path)
        if fn is not None:
            if not self.authed():
                return self.err(401, "sign in first")
            return fn()
        self.err(404, "no such page")

    def do_POST(self):
        # Uploads stream a raw body and may run for minutes — they skip both
        # the JSON body cap and MUTATE_LOCK (a big file must not freeze every
        # other mutation). Still same-origin + session gated like the rest.
        if self.path.split("?")[0] == "/api/file-up":
            if not self.same_origin():
                return self.err(403, "cross-origin request refused")
            sess = self.authed()
            if not sess:
                return self.err(401, "sign in first")
            if sess.get("role") == "viewer":
                return self.err(403, "your account can view this box, not change it")
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
            return self.err(401, "sign in first")
        # viewers read; only admins mutate (their two POSTs are self-scoped)
        if sess.get("role") == "viewer" and path not in ("/api/logout", "/api/password"):
            return self.err(403, "your account can view this box, not change it")
        handlers = {
            "/api/logout": self.api_logout,
            "/api/name": self.api_name,
            "/api/services": self.api_services,
            "/api/claude-token": self.api_claude_token,
            "/api/pipe-key": self.api_pipe_key,
            "/api/pipe-contact": self.api_pipe_contact,
            "/api/file-op": self.api_file_op,
            "/api/disk-op": self.api_disk_op,
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
        self.send(200, {
            "claimed": claimed(),
            "authed": bool(self.authed()),
            "hostname": socket.gethostname(),
        })

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
        # The claim IS the provisioning event: from here on, saves persist.
        # Save NOW — a claim that exists only in RAM is not a claim.
        with open(PROVISIONED, "a"):
            pass
        saved, detail = save_state()
        tok = new_session()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail},
                  cookie="session=%s; HttpOnly; SameSite=Strict; Path=/" % tok)

    def api_login(self, body):
        if not claimed():
            return self.err(403, "box is not claimed yet")
        name = (body.get("username") or "").strip() or "admin"
        users = read_users()
        u = find_user(users, name)
        # one flat cost and one message for every failure — no user enumeration
        if (u is None or u.get("disabled")
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
        self.send(200, {
            "user": sess.get("user"),
            "role": sess.get("role"),
            "hostname": socket.gethostname(),
            "nick": card_get("NICK"),
            "owner": card_get("OWNER_NICK"),
            "uptime_s": up,
            "work_pct": pct,
            "work_free_mb": free_mb,
            "services": svcs,
            "running": running,
            "boot_report": boot_report(),
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
            self.send(200, {"ok": True, "mount": mp, "root": "ext/" + dev})
        elif op == "unmount":
            if not os.path.ismount(mp):
                return self.err(400, "not mounted here")
            rc, out = run(["umount", mp], timeout=30)
            if rc != 0:
                return self.err(500, "unmount failed (files in use?): " + out.strip()[-200:])
            try:
                os.rmdir(mp)
            except OSError:
                pass
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
        nick = (body.get("nick") or "").strip()
        owner = (body.get("owner") or "").strip()
        if nick and not NICK_RE.fullmatch(nick):
            return self.err(400, "box name: letters, digits, . _ - only")
        if owner and not NICK_RE.fullmatch(owner):
            return self.err(400, "owner name: letters, digits, . _ - only")
        updates = {}
        if nick:
            updates["NICK"] = nick
        if owner:
            updates["OWNER_NICK"] = owner
        if not updates:
            return self.err(400, "nothing to set")
        try:
            card_set(updates)
        except RuntimeError as e:
            return self.err(500, str(e))
        saved, detail = save_state()
        self.send(200, {"ok": True, "hostname": socket.gethostname(),
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_services(self, body):
        svcs = read_services()
        for k in SVC_KEYS:
            if k in body:
                svcs[k] = bool(body[k])
        write_services(svcs)
        problems = apply_services(svcs)
        saved, detail = save_state()
        self.send(200, {"ok": True, "services": svcs, "problems": problems,
                        "saved": saved, "save_detail": "" if saved else detail})

    def api_claude_token(self, body):
        token = (body.get("token") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:\-]{20,512}", token):
            return self.err(400, "that does not look like a claude setup-token")
        write_private(CLAUDE_AUTH, "CLAUDE_CODE_OAUTH_TOKEN=%s\n" % token)
        run(["pipebox-claude-trust"], timeout=60)
        # smoke-probe: does the credential actually answer?
        env = dict(os.environ, HOME="/root", CLAUDE_CODE_OAUTH_TOKEN=token)
        try:
            p = subprocess.run(
                ["claude", "-p", "reply with exactly: ok"],
                capture_output=True, text=True, timeout=90, env=env, cwd="/work/pipebox",
            )
            probe_ok = p.returncode == 0
            probe_out = (p.stdout or p.stderr or "").strip()[-200:]
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            probe_ok, probe_out = False, str(e)[:200]
        saved, detail = save_state()
        self.send(200, {"ok": True, "probe_ok": probe_ok, "probe": probe_out,
                        "saved": saved, "save_detail": "" if saved else detail})

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
                ("name", "role", "unix", "sudo", "terminal", "term_port", "disabled")}
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
        name = (body.get("name") or "").strip()
        if not USER_NAME_RE.fullmatch(name) or name in USER_NAME_DENY:
            return self.err(400, "user name: a-z, digits, _ -, max 32, lowercase first")
        users = read_users()
        if find_user(users, name):
            return self.err(400, "that user already exists")
        role = body.get("role") or "viewer"
        if role not in ("admin", "viewer"):
            return self.err(400, "role must be admin or viewer")
        pw = body.get("password") or ""
        if len(pw) < 8:
            return self.err(400, "password must be at least 8 characters")
        want_unix = bool(body.get("unix"))
        want_sudo = bool(body.get("sudo"))
        want_term = bool(body.get("terminal"))
        term_pass = (body.get("term_pass") or "").strip()
        key = (body.get("ssh_key") or "").strip()
        if want_sudo and not want_unix:
            return self.err(400, "sudo needs an ssh/terminal account (enable unix access)")
        if want_term and not want_unix:
            want_unix = True  # a terminal IS a unix login
        if want_unix and not (key or want_term):
            return self.err(400, "an ssh account needs a public key (or enable the browser terminal)")
        if want_term and (not term_pass or any(c in term_pass for c in "'\n\r\0")):
            return self.err(400, "the browser terminal needs its own password (no quotes/newlines)")
        if want_unix and name in [l.split(":")[0] for l in
                                  open("/etc/passwd").read().splitlines() if l]:
            return self.err(400, "that name is taken by a system account")
        try:
            h = hash_password(pw)
        except RuntimeError as e:
            return self.err(500, str(e))
        problems = []
        if want_unix:
            rc, out = run(["/usr/local/bin/pipeos-user", "add", name], timeout=30)
            if rc != 0:
                return self.err(500, "could not create the unix user: " + out.strip()[-200:])
            if key:
                rc, out = run(["/usr/local/bin/pipeos-user", "set-key", name],
                              input_text=key + "\n")
                if rc != 0:
                    problems.append("ssh key not installed: " + out.strip()[-200:])
            if want_sudo:
                run(["/usr/local/bin/pipeos-user", "grant-sudo", name])
                run(["/usr/local/bin/pipeos-user", "set-hash", name],
                    input_text=h + "\n")
        u = {"name": name, "role": role, "hash": h, "unix": want_unix,
             "sudo": want_sudo, "terminal": want_term,
             "created": int(time.time())}
        if want_term:
            ports = [x.get("term_port") or 0 for x in users]
            u["term_port"] = max([TERM_PORT_BASE - 1] + ports) + 1
            u["term_pass"] = term_pass
        users.append(u)
        write_users(users)
        problems += self._apply_terminals(users)
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems, "term_port": u.get("term_port"),
                        "saved": saved, "save_detail": "" if saved else detail})

    def _apply_terminals(self, users):
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

    def api_users_set(self, body):
        sess = self._user_admin_guard()
        if sess is None:
            return
        users = read_users()
        u = find_user(users, (body.get("name") or "").strip())
        if u is None:
            return self.err(404, "no such user")
        if "role" in body and body["role"] not in ("admin", "viewer"):
            return self.err(400, "role must be admin or viewer")
        demote = (body.get("role") == "viewer" or body.get("disabled") is True)
        if demote and u.get("role") == "admin" and self._last_admin_guard(users, u["name"]):
            return self.err(400, "that would leave the box with no admin")
        if "role" in body:
            u["role"] = body["role"]
        if "disabled" in body:
            u["disabled"] = bool(body["disabled"])
            if u["disabled"]:
                drop_user_sessions(u["name"])
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
        problems = self._apply_terminals(users)
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
            args = ["/usr/local/bin/pipeos-user", "del", name]
            if body.get("purge_home"):
                args.append("--purge-home")
            rc, out = run(args, timeout=30)
            if rc != 0:
                problems.append("unix account not fully removed: " + out.strip()[-200:])
        users.remove(u)
        write_users(users)
        drop_user_sessions(name)
        problems += self._apply_terminals(users)
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
            return self.err(400, "the Claude service is switched off")
        env = dict(os.environ, HOME="/root")
        try:
            with open(CLAUDE_AUTH) as f:
                m = re.search(r"CLAUDE_CODE_OAUTH_TOKEN=(\S+)", f.read())
            if m:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = m.group(1)
        except OSError:
            pass
        os.makedirs("/work/pipebox/webchat", exist_ok=True)
        argv = ["claude", "-p", "--settings", "/etc/pipeos/pipebox-settings.json"]
        if os.path.exists("/work/pipebox/webchat/.started"):
            argv.append("--continue")
        try:
            p = subprocess.run(
                argv, input=msg, capture_output=True, text=True,
                timeout=180, env=env, cwd="/work/pipebox/webchat",
            )
        except subprocess.TimeoutExpired:
            return self.err(504, "Claude took longer than 3 minutes — try again")
        except FileNotFoundError:
            return self.err(500, "claude is not installed on this image")
        if p.returncode != 0:
            return self.err(502, "Claude errored: " + (p.stderr or p.stdout or "")[-300:].strip())
        with open("/work/pipebox/webchat/.started", "a"):
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
        self.send(200, {"ok": True, "actions": actions})


# ---- Phase B surfaces: logs, streaming, pipe, updates ----------------------

LOG_ALLOW = {
    "selfcheck": "/work/logs/selfcheck.log",
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
        if path is None:
            return self.err(400, "unknown log (choose: %s)" % ", ".join(sorted(LOG_ALLOW)))
        try:
            lines = min(500, max(10, int((q.get("lines") or ["100"])[0])))
        except ValueError:
            lines = 100
        text = tail_file(path, lines)
        self.send(200, {"name": name, "text": text if text is not None
                        else "(no log yet — the service may not have run)"})

    def api_stream_get(self):
        base = ["STREAM_MODE", "STREAM_SRC", "STREAM_URL", "STREAM_RES",
                "STREAM_FPS", "STREAM_VAAPI", "STREAM_BITRATE", "STREAM_ARGS",
                "STREAM_BOOT"]
        tk = []
        for n in range(1, STREAM_MAX_TARGETS + 1):
            tk += ["STREAM_T%d_URL" % n, "STREAM_T%d_KEY" % n,
                   "STREAM_T%d_ON" % n, "STREAM_T%d_NAME" % n]
        vals = read_conf_values(STREAM_CONF, base + tk)
        targets = [{
            "name": vals["STREAM_T%d_NAME" % n],
            "url": vals["STREAM_T%d_URL" % n],
            "on": vals["STREAM_T%d_ON" % n] == "1",
            "key_set": bool(vals["STREAM_T%d_KEY" % n]),
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
            existing = read_conf_values(STREAM_CONF,
                ["STREAM_T%d_KEY" % n for n in range(1, STREAM_MAX_TARGETS + 1)])
            for i in range(STREAM_MAX_TARGETS):
                n = i + 1
                t = targets[i] if i < len(targets) and isinstance(targets[i], dict) else {}
                key = guard(t.get("key"), "target %d key" % n)
                if not key and t.get("keep_key"):
                    key = existing["STREAM_T%d_KEY" % n]
                fields["STREAM_T%d_URL" % n] = guard(t.get("url"), "target %d url" % n)
                fields["STREAM_T%d_NAME" % n] = guard(t.get("name"), "target %d name" % n)
                fields["STREAM_T%d_KEY" % n] = key
                fields["STREAM_T%d_ON" % n] = "1" if t.get("on") else "0"
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
        vals = read_conf_values(ASSISTANT_CONF, ["ASSISTANT_USER", "ASSISTANT_PORT", "ASSISTANT_PASS"])
        rc, _ = run(["rc-service", "pipeos-assistant", "status"], timeout=15)
        self.send(200, {"user": vals["ASSISTANT_USER"] or "admin",
                        "port": vals["ASSISTANT_PORT"] or "7681",
                        "pass_set": bool(vals["ASSISTANT_PASS"]),
                        "running": rc == 0})

    def api_assistant_set(self, body):
        fields = {}
        for k, name in (("user", "ASSISTANT_USER"), ("port", "ASSISTANT_PORT"),
                        ("password", "ASSISTANT_PASS")):
            v = (body.get(k) or "").strip()
            # sourced by the init/wrapper: same single-quote guard as stream.conf
            if any(c in v for c in "'\n\r\0"):
                return self.err(400, "%s may not contain quotes or newlines" % k)
            if len(v) > 200:
                return self.err(400, "%s is too long" % k)
            fields[name] = v
        if not fields["ASSISTANT_USER"]:
            fields["ASSISTANT_USER"] = "admin"
        if fields["ASSISTANT_PORT"] and not re.match(r"^\d{2,5}$", fields["ASSISTANT_PORT"]):
            return self.err(400, "port must be a number")
        if not fields["ASSISTANT_PORT"]:
            fields["ASSISTANT_PORT"] = "7681"
        if not fields["ASSISTANT_PASS"] and body.get("keep_pass"):
            fields["ASSISTANT_PASS"] = read_conf_values(ASSISTANT_CONF, ["ASSISTANT_PASS"])["ASSISTANT_PASS"]
        if not fields["ASSISTANT_PASS"]:
            return self.err(400, "set a password — the terminal is shell access and must not be served open")
        write_private(ASSISTANT_CONF, "".join("%s='%s'\n" % (k, v) for k, v in fields.items()))
        problems = []
        if read_services().get("assistant"):
            rc, out = run(["rc-service", "pipeos-assistant", "restart"], timeout=60)
            if rc != 0:
                problems.append("assistant terminal did not start: " + out.strip()[-200:])
        saved, detail = save_state()
        self.send(200, {"ok": True, "problems": problems, "saved": saved,
                        "save_detail": "" if saved else detail})

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
        self.send(200, {"ok": True})

    def api_pipe_set(self, body):
        pref = body.get("pref") or ""
        if pref not in PIPE_PREFS:
            return self.err(400, "pref must be one of: " + ", ".join(PIPE_PREFS))
        val = "on" if body.get("value") else "off"
        rc, out = run(["pipe", "set", pref, val], timeout=20)
        if rc != 0:
            return self.err(500, "pipe set failed: " + out.strip()[-200:])
        self.send(200, {"ok": True, "pref": pref, "value": val == "on"})

    def api_pipe_logout(self, body):
        rc, out = run(["pipe", "logout"], timeout=20)
        if rc != 0:
            return self.err(500, "logout failed: " + out.strip()[-200:])
        self.send(200, {"ok": True})

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
        conf = read_conf_values(SELFUPDATE_CONF, ["UPDATE_RELEASE_URL", "UPDATE_URL"])
        origin = conf["UPDATE_RELEASE_URL"] or conf["UPDATE_URL"]
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
                        "last": tail_file(LOG_ALLOW["selfupdate"], 3) or ""})

    def api_update_now(self, _body):
        rc, out = run(["pipeos-selfupdate"], timeout=900)
        self.send(200, {"ok": rc == 0, "detail": out.strip()[-500:]})


for _n in dir(PhaseB):
    if _n.startswith("api_"):
        setattr(Handler, _n, getattr(PhaseB, _n))


def start_https():
    """Serve HTTPS on :443 in a background thread if the box CA + server cert
    exist. HTTP on :80 keeps working regardless, so a TLS problem can never lock
    the owner out of the wizard — HTTPS is strictly additive until they install
    the CA and choose to use it. Best-effort: any failure just means no :443."""
    try:
        subprocess.run(["/usr/local/bin/pipeos-tls-init"], timeout=30,
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
    threading.Thread(target=httpsd.serve_forever, daemon=True).start()
    sys.stderr.write("pipeos-webd listening on :443 (TLS)\n")


def main():
    port = int(os.environ.get("PIPEOS_WEB_PORT", "80"))
    os.makedirs(SESS_DIR, mode=0o700, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    threading.Thread(target=metrics_sampler, daemon=True).start()
    start_https()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    sys.stderr.write("pipeos-webd listening on :%d (claimed=%s)\n" % (port, claimed()))
    srv.serve_forever()


if __name__ == "__main__":
    main()
