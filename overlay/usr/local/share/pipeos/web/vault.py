#!/usr/bin/env python3
"""pipeos-vault — one sealed store for the box's secrets (#244).

    pipeos vault status            open | locked <why> | none
    pipeos vault init              a new empty vault; prints the recovery phrase ONCE
    pipeos vault list              names, consumer, when, by whom — never values
    pipeos vault set NAME [CONSUMER]   value on stdin (text; --bytes for a file)
    pipeos vault get NAME          the value, to stdout (root only)
    pipeos vault del NAME
    pipeos vault export            write every consumer's file under /run/pipeos/secrets
    pipeos vault unlock            the recovery phrase on stdin: re-seal to THIS chassis
    pipeos vault rephrase          a new recovery phrase (the old one stops working)
    pipeos vault migrate           move the known plaintext secrets in, shred them

Until #244 every secret on a Machine was a plain file under /etc/pipeos:
the Claude token, the support tunnel key, the stream keys, the assistant
password, samba's passdb. They rode the apkovl in plaintext onto the boot
stick and into every clone. Now they live in ONE file, /etc/pipeos/
vault.sealed, and are materialised at boot into /run/pipeos/secrets (tmpfs,
0700) for the services that read them. Nothing else about those services
changes — they source a file, as before, from /run instead of /etc.

THE KEY MODEL — two slots, LUKS-style. A random data key K seals the
payload. Slot `chassis` wraps K under a key derived from a seed (minted at
claim, kept in this file's header — a KDF input, not a protected secret)
plus what the hardware says it is: the primary MAC, the DMI serial, the
product name. So the stick opens in the chassis it was claimed in and does
not open in another one. Slot `recovery` wraps K under the recovery phrase
shown once at claim and never stored: a rehomed stick opens with the
phrase and `unlock` re-seals the chassis slot to the new hardware.

Honestly stated: the label on the box (docs/cluster.md §2) carries the
MAC; a photo of it plus this file plus the DMI values opens the vault.
The phrase is not the defence against that — label discipline is. What
the vault does close is the case that actually happened: a stick or a
clone on a desk, readable with `tar`.

THE CIPHER. openssl is on every Machine; age is not, and a decryptor that
has to be installed first cannot be the thing every service waits for at
boot. So: AES-256-CBC by `openssl enc -pbkdf2`, keyed by a 32-byte key
derived here (PBKDF2-HMAC-SHA256, `ITER` rounds) and handed to openssl
over a pipe — never argv — then HMAC-SHA256 over the ciphertext with a
second derived key, checked before anything is decrypted (encrypt-then-
MAC). The envelope is versioned JSON; a later move to a different
primitive is a `v: 2`.

Seams (the probe, never production): PIPEOS_VAULT_FILE, PIPEOS_VAULT_RUN,
PIPEOS_VAULT_IDENT (a JSON file standing in for the hardware), PIPEOS_VAULT_ITER,
PIPEOS_VAULT_ETC (where the plaintext files to migrate live).
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lanid  # noqa: E402

VAULT_FILE = os.environ.get("PIPEOS_VAULT_FILE", "/etc/pipeos/vault.sealed")
RUN_DIR = os.environ.get("PIPEOS_VAULT_RUN", "/run/pipeos/secrets")
ETC = os.environ.get("PIPEOS_VAULT_ETC", "/etc/pipeos")
IDENT_FILE = os.environ.get("PIPEOS_VAULT_IDENT", "")
ITER = int(os.environ.get("PIPEOS_VAULT_ITER", "200000"))
NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
STREAM_MAX = 4


class VaultError(Exception):
    pass


class Locked(VaultError):
    pass


# ---- who this chassis is ------------------------------------------------------

def ident():
    """The hardware half of the chassis key. Every field may be empty on
    odd firmware; what matters is that the same box gives the same answer
    every boot, and a different box a different one."""
    if IDENT_FILE:
        try:
            with open(IDENT_FILE) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        return {"mac": d.get("mac", ""), "serial": d.get("serial", ""), "product": d.get("product", "")}
    return {"mac": lanid.mac(), "serial": lanid.serial(), "product": lanid.model()}


def _ident_string(i):
    return "|".join((i.get("mac", ""), i.get("serial", ""), i.get("product", "")))


# ---- the primitives -------------------------------------------------------------

def _kdf(secret, salt):
    """64 bytes: the first 32 key openssl, the last 32 key the HMAC."""
    return hashlib.pbkdf2_hmac("sha256", secret, salt, ITER, dklen=64)


def _openssl(args, key32, data):
    """Run openssl enc with the key material on a pipe (fd 3), never argv."""
    r, w = os.pipe()
    os.write(w, key32.hex().encode())
    os.close(w)
    try:
        p = subprocess.run(["openssl", "enc"] + args + ["-pass", "fd:%d" % r], input=data,
                           capture_output=True, pass_fds=(r,))
    finally:
        os.close(r)
    if p.returncode != 0:
        raise VaultError("openssl: " + p.stderr.decode(errors="replace").strip()[-200:])
    return p.stdout


def _seal(keys64, plaintext):
    ct = _openssl(["-aes-256-cbc", "-pbkdf2", "-iter", "1000", "-salt"], keys64[:32], plaintext)
    tag = hmac.new(keys64[32:], ct, "sha256").digest()
    return ct + tag


def _open(keys64, blob):
    if len(blob) < 48:
        raise VaultError("sealed blob too short")
    ct, tag = blob[:-32], blob[-32:]
    if not hmac.compare_digest(hmac.new(keys64[32:], ct, "sha256").digest(), tag):
        raise Locked("integrity check failed — wrong key or a damaged vault")
    return _openssl(["-d", "-aes-256-cbc", "-pbkdf2", "-iter", "1000"], keys64[:32], ct)


def _b64(b):
    return base64.b64encode(b).decode()


def _unb64(s):
    return base64.b64decode(s)


# ---- the envelope ----------------------------------------------------------------

def _read():
    with open(VAULT_FILE) as f:
        env = json.load(f)
    if env.get("v") != 1:
        raise VaultError("vault format v%s is not one this build reads" % env.get("v"))
    return env


def _write(env):
    d = os.path.dirname(VAULT_FILE)
    os.makedirs(d, exist_ok=True)
    tmp = VAULT_FILE + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(env, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, VAULT_FILE)


def _chassis_keys(env, i=None):
    salt = bytes.fromhex(env["slots"]["chassis"]["salt"])
    return _kdf((env["seed"] + "|" + _ident_string(i or ident())).encode(), salt)


def _phrase_keys(env, phrase):
    salt = bytes.fromhex(env["slots"]["recovery"]["salt"])
    return _kdf(_norm_phrase(phrase).encode(), salt)


def _norm_phrase(p):
    return re.sub(r"[^0-9a-f]", "", p.lower())


def _new_phrase():
    h = secrets.token_hex(16)
    return "-".join(h[i:i + 4] for i in range(0, 32, 4))


def _unwrap(env, phrase=None):
    """K, or raise Locked. The chassis slot first; the phrase when given."""
    if phrase is not None:
        try:
            return _open(_phrase_keys(env, phrase), _unb64(env["slots"]["recovery"]["wrapped"]))
        except Locked:
            raise Locked("that is not this vault's recovery phrase")
    try:
        return _open(_chassis_keys(env), _unb64(env["slots"]["chassis"]["wrapped"]))
    except Locked:
        raise Locked("sealed to another chassis — this stick was claimed in a different machine "
                     "(recovery phrase: pipeos vault unlock)")


def _load(phrase=None):
    env = _read()
    K = _unwrap(env, phrase)
    doc = json.loads(_open(_kdf(K, b"pipeos-vault-data"), _unb64(env["data"])))
    return env, K, doc


def _store(env, K, doc):
    env["data"] = _b64(_seal(_kdf(K, b"pipeos-vault-data"), json.dumps(doc).encode()))
    env["updated"] = int(time.time())
    _write(env)


# ---- operations ----------------------------------------------------------------------

def exists():
    return os.path.exists(VAULT_FILE)


def init(force=False):
    """A fresh, empty vault sealed to this chassis. Returns the recovery
    phrase — the only time it is ever seen."""
    if exists() and not force:
        raise VaultError("a vault already exists at %s" % VAULT_FILE)
    K = secrets.token_bytes(32)
    phrase = _new_phrase()
    env = {"v": 1, "seed": secrets.token_hex(32), "iter": ITER, "created": int(time.time()),
           "ident_hint": (ident().get("mac") or "")[-5:],
           "slots": {"chassis": {"salt": secrets.token_hex(16)}, "recovery": {"salt": secrets.token_hex(16)}}}
    env["slots"]["chassis"]["wrapped"] = _b64(_seal(_chassis_keys(env), K))
    env["slots"]["recovery"]["wrapped"] = _b64(_seal(_phrase_keys(env, phrase), K))
    _store(env, K, {"secrets": {}})
    return phrase


def status():
    """('none'|'open'|'locked', detail)."""
    if not exists():
        return "none", "no vault at %s" % VAULT_FILE
    try:
        _load()
        return "open", ""
    except Locked as e:
        return "locked", str(e)
    except (VaultError, OSError, ValueError) as e:
        return "locked", "unreadable: %s" % e


def list_():
    _env, _K, doc = _load()
    return [{"name": n, "consumer": s.get("consumer", ""), "kind": s.get("kind", "text"),
             "set_at": s.get("set_at", 0), "by": s.get("by", "")}
            for n, s in sorted(doc["secrets"].items())]


def get(name):
    _env, _K, doc = _load()
    s = doc["secrets"].get(name)
    if s is None:
        raise VaultError("no secret named %s" % name)
    return _unb64(s["v"]) if s.get("kind") == "bytes" else s["v"]


def set_(name, value, consumer="", by=""):
    if not NAME_RE.match(name):
        raise VaultError("secret names are lowercase letters, digits, _ and . (%r)" % name)
    env, K, doc = _load()
    if isinstance(value, bytes):
        rec = {"v": _b64(value), "kind": "bytes"}
    else:
        if "\0" in value:
            raise VaultError("a text secret cannot contain NUL")
        rec = {"v": value, "kind": "text"}
    rec.update({"consumer": consumer or CONSUMER_OF.get(name, ""), "set_at": int(time.time()), "by": by})
    doc["secrets"][name] = rec
    _store(env, K, doc)


def delete(name):
    env, K, doc = _load()
    if name in doc["secrets"]:
        del doc["secrets"][name]
        _store(env, K, doc)
        return True
    return False


def unlock(phrase):
    """Open with the phrase and re-seal the chassis slot to THIS hardware."""
    env, K, doc = _load(phrase)
    env["slots"]["chassis"]["salt"] = secrets.token_hex(16)
    env["slots"]["chassis"]["wrapped"] = _b64(_seal(_chassis_keys(env), K))
    env["ident_hint"] = (ident().get("mac") or "")[-5:]
    _store(env, K, doc)


def rephrase():
    env, K, doc = _load()
    phrase = _new_phrase()
    env["slots"]["recovery"]["salt"] = secrets.token_hex(16)
    env["slots"]["recovery"]["wrapped"] = _b64(_seal(_phrase_keys(env, phrase), K))
    _store(env, K, doc)
    return phrase


# ---- export: what each service reads ---------------------------------------------------
#
# name             -> file under RUN_DIR, and how it is written
# claude_token        claude.env      NAME=value   (NAME by the token's prefix)
# assistant_pass      assistant.env   ASSISTANT_PASS='…'
# stream_key_N        stream.env      STREAM_TN_KEY='…'  (N = 1..4)
# support_key         support_key     raw bytes, 0600
# nas_passdb          nas/passdb.tdb  raw bytes, dir 0700
# jobs.*              jobs.env        NAME='…'  (uppercased, the dot stripped; #242)
# anything else       custom.env      NAME='…'

CONSUMER_OF = {"claude_token": "claude", "assistant_pass": "assistant", "support_key": "support",
               "nas_passdb": "nas"}
for _n in range(1, STREAM_MAX + 1):
    CONSUMER_OF["stream_key_%d" % _n] = "stream"


def _sq(v):
    """The single-quote guard every dashboard-written conf uses: the
    consumers `. source` these files."""
    if any(c in v for c in "'\n\r\0"):
        raise VaultError("a shell-sourced secret may not contain quotes or newlines")
    return "'" + v + "'"


def _write_file(path, data, mode=0o600):
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def claude_env_line(token):
    name = "ANTHROPIC_API_KEY" if token.startswith("sk-ant-api") else "CLAUDE_CODE_OAUTH_TOKEN"
    return "%s=%s\n" % (name, token)


def export():
    """Materialise every consumer's file under RUN_DIR. Files for secrets
    that are absent are removed, so a deleted secret is gone at once."""
    _env, _K, doc = _load()
    sec = doc["secrets"]
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    os.chmod(RUN_DIR, 0o700)
    files = {}

    def env_file(fname):
        return files.setdefault(fname, [])

    for name, s in sec.items():
        val = _unb64(s["v"]) if s.get("kind") == "bytes" else s["v"]
        if name == "claude_token":
            files["claude.env"] = [claude_env_line(val)]
        elif name == "assistant_pass":
            env_file("assistant.env").append("ASSISTANT_PASS=%s\n" % _sq(val))
        elif name.startswith("stream_key_"):
            env_file("stream.env").append("STREAM_T%s_KEY=%s\n" % (name[len("stream_key_"):], _sq(val)))
        elif name == "support_key":
            files["support_key"] = val if isinstance(val, bytes) else val.encode()
        elif name == "nas_passdb":
            files["nas/passdb.tdb"] = val if isinstance(val, bytes) else val.encode()
        elif name.startswith("jobs."):
            env_file("jobs.env").append("%s=%s\n" % (re.sub(r"[^A-Z0-9_]", "_", name[5:].upper()), _sq(val)))
        else:
            env_file("custom.env").append("%s=%s\n" % (re.sub(r"[^A-Z0-9_]", "_", name.upper()), _sq(val)))
    written = set()
    for fname, content in files.items():
        path = os.path.join(RUN_DIR, fname)
        d = os.path.dirname(path)
        if d != RUN_DIR:
            os.makedirs(d, mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)
        _write_file(path, content if isinstance(content, bytes) else "".join(content))
        written.add(fname)
    for fname in ("claude.env", "assistant.env", "stream.env", "support_key", "nas/passdb.tdb", "jobs.env", "custom.env"):
        if fname not in written:
            try:
                os.unlink(os.path.join(RUN_DIR, fname))
            except OSError:
                pass
    return sorted(written)


# ---- migration: the plaintext files a claimed box has today ---------------------------

def _shred(path):
    try:
        n = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(b"\0" * n)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _conf_value(path, key):
    try:
        with open(path) as f:
            m = re.search(r"^%s=(.*)$" % re.escape(key), f.read(), re.M)
    except OSError:
        return None
    if not m:
        return None
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


def _conf_blank(path, key):
    """KEY='…' -> KEY='' in place, keeping the rest of the file."""
    try:
        with open(path) as f:
            s = f.read()
    except OSError:
        return
    s2 = re.sub(r"^(%s=).*$" % re.escape(key), r"\1''", s, flags=re.M)
    if s2 != s:
        _write_file(path, s2)


def migrate(by="boot"):
    """Move every known plaintext secret into the vault and shred the
    original. Idempotent: a path that is already gone is skipped. Returns
    the names moved. The vault must exist and be open."""
    moved = []
    env, K, doc = _load()

    def put(name, value, kind="text"):
        rec = {"v": _b64(value) if kind == "bytes" else value, "kind": kind,
               "consumer": CONSUMER_OF.get(name, ""), "set_at": int(time.time()), "by": by}
        doc["secrets"][name] = rec
        moved.append(name)

    p = os.path.join(ETC, "claude-auth.env")
    if os.path.exists(p):
        try:
            with open(p) as f:
                m = re.search(r"^(?:CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY)=(\S+)", f.read(), re.M)
        except OSError:
            m = None
        if m:
            put("claude_token", m.group(1))
        _shred(p)
    p = os.path.join(ETC, "support_key")
    if os.path.exists(p):
        with open(p, "rb") as f:
            put("support_key", f.read(), "bytes")
        _shred(p)
    p = os.path.join(ETC, "assistant.conf")
    v = _conf_value(p, "ASSISTANT_PASS")
    if v:
        put("assistant_pass", v)
        _conf_blank(p, "ASSISTANT_PASS")
    p = os.path.join(ETC, "stream.conf")
    for n in range(1, STREAM_MAX + 1):
        v = _conf_value(p, "STREAM_T%d_KEY" % n)
        if v:
            put("stream_key_%d" % n, v)
            _conf_blank(p, "STREAM_T%d_KEY" % n)
    p = os.path.join(ETC, "nas-private", "passdb.tdb")
    if os.path.exists(p):
        with open(p, "rb") as f:
            put("nas_passdb", f.read(), "bytes")
        _shred(p)
        shutil.rmtree(os.path.join(ETC, "nas-private"), ignore_errors=True)
    if moved:
        _store(env, K, doc)
    return moved


PLAINTEXT_PATHS = ("claude-auth.env", "support_key", "nas-private/passdb.tdb")


def plaintext_left():
    """What `pipeos verify` asks: any known secret still in the clear?"""
    left = []
    for rel in PLAINTEXT_PATHS:
        if os.path.exists(os.path.join(ETC, rel)):
            left.append(rel)
    if _conf_value(os.path.join(ETC, "assistant.conf"), "ASSISTANT_PASS"):
        left.append("assistant.conf:ASSISTANT_PASS")
    for n in range(1, STREAM_MAX + 1):
        if _conf_value(os.path.join(ETC, "stream.conf"), "STREAM_T%d_KEY" % n):
            left.append("stream.conf:STREAM_T%d_KEY" % n)
    return left


# ---- the CLI ------------------------------------------------------------------------------

def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().split("\n\n")[0])
        return 0 if argv else 2
    verb = argv[0]
    try:
        if verb == "status":
            st, why = status()
            print(st + (" — " + why if why else ""))
            return {"open": 0, "none": 1, "locked": 3}[st]
        if verb == "init":
            print(init(force="--force" in argv))
            return 0
        if verb == "list":
            for r in list_():
                print("%-24s %-10s %-6s %s %s" % (r["name"], r["consumer"] or "-", r["kind"],
                                                   time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["set_at"])) if r["set_at"] else "-",
                                                   r["by"]))
            return 0
        if verb == "set":
            if len(argv) < 2:
                print("usage: pipeos vault set NAME [CONSUMER] < value", file=sys.stderr)
                return 2
            data = sys.stdin.buffer.read()
            if "--bytes" in argv:
                value = data
            else:
                value = data.decode().rstrip("\n")
            consumer = next((a for a in argv[2:] if not a.startswith("--")), "")
            set_(argv[1], value, consumer, by="cli")
            print("set " + argv[1])
            return 0
        if verb == "get":
            v = get(argv[1])
            sys.stdout.buffer.write(v if isinstance(v, bytes) else (v + "\n").encode())
            return 0
        if verb == "del":
            print("deleted " + argv[1] if delete(argv[1]) else "no such secret")
            return 0
        if verb == "export":
            print("exported: " + ", ".join(export()))
            return 0
        if verb == "unlock":
            unlock(sys.stdin.readline())
            print("unlocked — sealed to this chassis now")
            return 0
        if verb == "rephrase":
            print(rephrase())
            return 0
        if verb == "migrate":
            moved = migrate(by="migrate")
            print("migrated: " + (", ".join(moved) if moved else "nothing to move"))
            return 0
        if verb == "plaintext-left":
            left = plaintext_left()
            print("\n".join(left) if left else "none")
            return 1 if left else 0
        print("unknown verb: " + verb, file=sys.stderr)
        return 2
    except Locked as e:
        print("vault locked: %s" % e, file=sys.stderr)
        return 3
    except (VaultError, OSError, ValueError, KeyError, IndexError) as e:
        print("vault: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
