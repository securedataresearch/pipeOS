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

import hmac
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ETC = "/etc/pipeos"
ADMIN_CONF = ETC + "/web-admin.conf"
SERVICES_CONF = ETC + "/services.conf"
CLAUDE_AUTH = ETC + "/claude-auth.env"
CARD = ETC + "/card.conf"
PROVISIONED = ETC + "/provisioned"
SESS_DIR = "/run/pipeos/web-sessions"
BOOT_REPORT = "/run/pipeos/boot-report"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

SESSION_IDLE_S = 24 * 3600
NICK_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
SVC_KEYS = ("pipe", "claude", "stream", "agy", "support", "assistant")


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


def check_password(pw):
    try:
        with open(ADMIN_CONF) as f:
            m = re.search(r"HASH='(\$6\$[^']+)'", f.read())
    except OSError:
        return False
    if not m:
        return False
    stored = m.group(1)
    salt = stored.split("$")[2]
    rc, out = run(
        ["openssl", "passwd", "-6", "-salt", salt, "-stdin"], input_text=pw + "\n"
    )
    return rc == 0 and hmac.compare_digest(out.strip(), stored)


# ---- sessions --------------------------------------------------------------

def new_session():
    os.makedirs(SESS_DIR, mode=0o700, exist_ok=True)
    tok = secrets.token_hex(32)
    write_private(os.path.join(SESS_DIR, tok), str(int(time.time())))
    return tok


def valid_session(tok):
    if not tok or not re.fullmatch(r"[0-9a-f]{64}", tok):
        return False
    path = os.path.join(SESS_DIR, tok)
    try:
        if time.time() - os.path.getmtime(path) > SESSION_IDLE_S:
            os.unlink(path)
            return False
        os.utime(path)
        return True
    except OSError:
        return False


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
    return out


def apply_services(svcs):
    """Mirror the declaration into rc-update + running state. Best-effort per
    daemon; returns a list of human-readable problems (empty == clean)."""
    problems = []
    want = set(daemons_for(svcs))
    managed = ("pipe-daemon", "pipebox-listener", "pipeos-stream", "pipeos-support", "pipeos-assistant")
    for svc in managed:
        if svc in want:
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
        readers = {
            "/api/status": self.api_status,
            "/api/logs": self.api_logs,
            "/api/stream": self.api_stream_get,
            "/api/stream-log": self.api_stream_log,
            "/api/assistant": self.api_assistant_get,
            "/api/pipe": self.api_pipe_get,
            "/api/update": self.api_update_get,
        }
        fn = readers.get(path)
        if fn is not None:
            if not self.authed():
                return self.err(401, "sign in first")
            return fn()
        self.err(404, "no such page")

    def do_POST(self):
        if not self.same_origin():
            return self.err(403, "cross-origin request refused")
        path = self.path.split("?")[0]
        body = self.body_json() or {}
        if path == "/api/claim":
            return self.api_claim(body)
        if path == "/api/login":
            return self.api_login(body)
        # everything below requires a session
        if not self.authed():
            return self.err(401, "sign in first")
        handlers = {
            "/api/logout": self.api_logout,
            "/api/name": self.api_name,
            "/api/services": self.api_services,
            "/api/claude-token": self.api_claude_token,
            "/api/pipe-key": self.api_pipe_key,
            "/api/password": self.api_password,
            "/api/save": self.api_save,
            "/api/chat": self.api_chat,
            "/api/reboot": self.api_reboot,
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

    # -- API: unauthenticated surface (deliberately tiny) --
    def api_state(self):
        self.send(200, {
            "claimed": claimed(),
            "authed": self.authed(),
            "hostname": socket.gethostname(),
        })

    def api_claim(self, body):
        if claimed():
            return self.err(403, "this box is already claimed")
        pw = body.get("password") or ""
        if len(pw) < 8:
            return self.err(400, "password must be at least 8 characters")
        try:
            write_private(ADMIN_CONF, "HASH='%s'\n" % hash_password(pw))
        except RuntimeError as e:
            return self.err(500, str(e))
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
        if not check_password(body.get("password") or ""):
            time.sleep(2)  # flat cost per wrong guess
            return self.err(403, "wrong password")
        tok = new_session()
        self.send(200, {"ok": True},
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
        self.send(200, {
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
        if not check_password(body.get("current") or ""):
            time.sleep(2)
            return self.err(403, "current password is wrong")
        new = body.get("new") or ""
        if len(new) < 8:
            return self.err(400, "new password must be at least 8 characters")
        try:
            write_private(ADMIN_CONF, "HASH='%s'\n" % hash_password(new))
        except RuntimeError as e:
            return self.err(500, str(e))
        saved, detail = save_state()
        self.send(200, {"ok": True, "saved": saved, "save_detail": "" if saved else detail})

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
        vals = read_conf_values(STREAM_CONF, [
            "STREAM_MODE", "STREAM_SRC", "STREAM_URL", "STREAM_DST", "STREAM_KEY",
            "STREAM_DST2", "STREAM_KEY2", "STREAM_RES", "STREAM_FPS", "STREAM_VAAPI", "STREAM_ARGS"])
        rc, _ = run(["rc-service", "pipeos-stream", "status"], timeout=15)
        self.send(200, {
            "mode": vals["STREAM_MODE"] or "media",
            "src": vals["STREAM_SRC"], "url": vals["STREAM_URL"],
            "dst": vals["STREAM_DST"], "key_set": bool(vals["STREAM_KEY"]),
            "dst2": vals["STREAM_DST2"], "key_set2": bool(vals["STREAM_KEY2"]),
            "res": vals["STREAM_RES"] or "1920x1080", "fps": vals["STREAM_FPS"] or "30",
            "vaapi": vals["STREAM_VAAPI"] == "1", "args": vals["STREAM_ARGS"],
            "running": rc == 0})

    def api_stream_set(self, body):
        fields = {}
        # (form field, conf var). STREAM_VAAPI (a checkbox) and STREAM_MODE are
        # normalised below; every free-text field runs the injection guard.
        for k, name in (("mode", "STREAM_MODE"), ("src", "STREAM_SRC"), ("url", "STREAM_URL"),
                        ("dst", "STREAM_DST"), ("key", "STREAM_KEY"),
                        ("dst2", "STREAM_DST2"), ("key2", "STREAM_KEY2"),
                        ("res", "STREAM_RES"), ("fps", "STREAM_FPS"), ("args", "STREAM_ARGS")):
            v = (body.get(k) or "").strip()
            # the conf is shell-sourced by the init script: refuse anything
            # that could escape a single-quoted value rather than escaping it
            if any(c in v for c in "'\n\r\0"):
                return self.err(400, "%s may not contain quotes or newlines" % k)
            if len(v) > 500:
                return self.err(400, "%s is too long" % k)
            fields[name] = v
        if fields["STREAM_MODE"] not in ("media", "browser"):
            fields["STREAM_MODE"] = "media"
        if fields["STREAM_RES"] and not re.match(r"^\d{2,5}x\d{2,5}$", fields["STREAM_RES"]):
            return self.err(400, "resolution must look like 1920x1080")
        if fields["STREAM_FPS"] and not re.match(r"^\d{1,3}$", fields["STREAM_FPS"]):
            return self.err(400, "fps must be a number")
        fields["STREAM_VAAPI"] = "1" if body.get("vaapi") else "0"
        # preserve an existing key when its box is left blank
        for name, keep in (("STREAM_KEY", "keep_key"), ("STREAM_KEY2", "keep_key2")):
            if not fields[name] and body.get(keep):
                fields[name] = read_conf_values(STREAM_CONF, [name])[name]
        write_private(STREAM_CONF, "".join(
            "%s='%s'\n" % (k, v) for k, v in fields.items()))
        problems = []
        if read_services()["stream"]:
            rc, out = run(["rc-service", "pipeos-stream", "restart"], timeout=60)
            if rc != 0:
                problems.append("stream service did not start: " + out.strip()[-200:])
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
        self.send(200, {"enabled": read_services()["pipe"], "nick": nick,
                        "authed": authed_flag,
                        "owner": card_get("OWNER_NICK"),
                        "cohort": card_get("COHORT_ID")})

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


def main():
    port = int(os.environ.get("PIPEOS_WEB_PORT", "80"))
    os.makedirs(SESS_DIR, mode=0o700, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    srv = HTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write("pipeos-webd listening on :%d (claimed=%s)\n" % (port, claimed()))
    srv.serve_forever()


if __name__ == "__main__":
    main()
