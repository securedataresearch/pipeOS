#!/usr/bin/env python3
"""Probe for the secrets vault (#244): vault.py end to end through its
seams — a tempdir stands in for /etc/pipeos and /run/pipeos/secrets, a
JSON file for the hardware identity, a small PBKDF2 count for speed. No
root, no real secret touched. Also the wiring: the fence, the front door,
persistence (lbu.list gained the vault and LOST the plaintext paths), the
runlevel, and the consumers reading from /run.

Exit 0 if every row passes. Controls: check-vault-controls.py.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
VAULT = os.environ.get("CHECK_VAULT_BIN", os.path.join(WEB, "vault.py"))
INIT = os.environ.get("CHECK_VAULT_INIT", os.path.join(REPO, "overlay/etc/init.d/pipeos-vault"))
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="ckvault-")
shutil.copy(os.path.join(WEB, "lanid.py"), os.path.join(D, "lanid.py"))
shutil.copy(VAULT, os.path.join(D, "vault.py"))
ETC = os.path.join(D, "etc")
RUN = os.path.join(D, "run", "secrets")
os.makedirs(ETC)
IDENT_A = os.path.join(D, "idA.json")
IDENT_B = os.path.join(D, "idB.json")
json.dump({"mac": "aa:bb:cc:dd:7f:3a", "serial": "PC1AB2C3", "product": "LENOVO 10RR"}, open(IDENT_A, "w"))
json.dump({"mac": "aa:bb:cc:dd:9c:21", "serial": "PC9Z8Y7X", "product": "LENOVO 10RR"}, open(IDENT_B, "w"))
ARGV_LOG = os.path.join(D, "openssl.argv")
# an openssl shim that logs every argv, then runs the real one — the row
# "no key material on argv" reads this log
BIN = os.path.join(D, "bin")
os.makedirs(BIN)
with open(os.path.join(BIN, "openssl"), "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + ARGV_LOG + "\nexec " + shutil.which("openssl") + " \"$@\"\n")
os.chmod(os.path.join(BIN, "openssl"), 0o755)


def vault(*args, ident=IDENT_A, stdin=None):
    env = dict(os.environ, PIPEOS_VAULT_FILE=os.path.join(ETC, "vault.sealed"), PIPEOS_VAULT_RUN=RUN,
               PIPEOS_VAULT_ETC=ETC, PIPEOS_VAULT_IDENT=ident, PIPEOS_VAULT_ITER="1500",
               PATH=BIN + ":" + os.environ.get("PATH", ""))
    p = subprocess.run([sys.executable, os.path.join(D, "vault.py")] + list(args),
                       capture_output=True, env=env, input=stdin)
    return p.returncode, p.stdout.decode(errors="replace") + p.stderr.decode(errors="replace")


# ── 1. init ──────────────────────────────────────────────────────────────
rc, out = vault("init")
phrase = out.strip()
st = os.stat(os.path.join(ETC, "vault.sealed"))
rc_st, out_st = vault("status")
check("1 init makes a 0600 vault sealed to this chassis and prints an eight-group recovery phrase once; status is open",
      rc == 0 and len(phrase.split("-")) == 8 and all(len(g) == 4 for g in phrase.split("-"))
      and stat.S_IMODE(st.st_mode) == 0o600 and rc_st == 0 and out_st.strip() == "open"
      and vault("init")[0] != 0,
      "rc=%s phrase=%r st=%s" % (rc, phrase, out_st))

# ── 2. set / list / get / del, never a value in the list ─────────────────
vault("set", "claude_token", stdin=b"sk-ant-api03-SECRET-TOKEN\n")
vault("set", "jobs.github_token", stdin=b"ghp_SECRET\n")
rc_b, _ = vault("set", "support_key", "--bytes", stdin=b"-----BEGIN OPENSSH PRIVATE KEY-----\n\x00\x01binary\n")
rc_l, lst = vault("list")
rc_g, got = vault("get", "claude_token")
rc_gb, gotb = vault("get", "support_key")
rc_d, _ = vault("del", "jobs.github_token")
rc_l2, lst2 = vault("list")
check("2 set/list/get/del round-trip; the listing carries name, consumer, kind and who — never a value; a bytes secret comes back byte for byte",
      rc_l == 0 and "claude_token" in lst and "claude" in lst and "SECRET" not in lst and "ghp_" not in lst
      and got.strip() == "sk-ant-api03-SECRET-TOKEN" and rc_gb == 0 and "binary" in gotb
      and rc_d == 0 and "jobs.github_token" not in lst2 and "claude_token" in lst2,
      "lst=%r got=%r" % (lst, got))

# ── 3. export ────────────────────────────────────────────────────────────
vault("set", "assistant_pass", stdin=b"hunter2\n")
vault("set", "stream_key_2", stdin=b"tw-secret\n")
rc, out = vault("export")
ce = open(os.path.join(RUN, "claude.env")).read()
ae = open(os.path.join(RUN, "assistant.env")).read()
se = open(os.path.join(RUN, "stream.env")).read()
modes = {f: stat.S_IMODE(os.stat(os.path.join(RUN, f)).st_mode) for f in ("claude.env", "assistant.env", "stream.env", "support_key")}
check("3 export writes each consumer's file under the run dir: the Claude line by the token's prefix, the sourced envs single-quoted, the key file raw; dir 0700, files 0600",
      rc == 0 and ce == "ANTHROPIC_API_KEY=sk-ant-api03-SECRET-TOKEN\n" and ae == "ASSISTANT_PASS='hunter2'\n"
      and se == "STREAM_T2_KEY='tw-secret'\n" and open(os.path.join(RUN, "support_key"), "rb").read().startswith(b"-----BEGIN")
      and stat.S_IMODE(os.stat(RUN).st_mode) == 0o700 and all(m == 0o600 for m in modes.values()),
      "rc=%s ce=%r ae=%r se=%r modes=%r out=%s" % (rc, ce, ae, se, modes, out))
vault("del", "stream_key_2")
vault("export")
check("3b a deleted secret's export file is gone after the next export", not os.path.exists(os.path.join(RUN, "stream.env")), "")
rc_q, out_q = vault("set", "assistant_pass", stdin=b"it's'; rm -rf /\n")
rc_e, out_e = vault("export")
check("3c a shell-sourced secret with a quote is refused at export, not written", rc_e != 0 and "quotes" in out_e and "rm -rf" not in open(os.path.join(RUN, "assistant.env")).read(), out_e)
vault("set", "assistant_pass", stdin=b"hunter2\n")

# ── 4-6. the chassis binding and the phrase ─────────────────────────────
rc, out = vault("status", ident=IDENT_B)
rc_g, _ = vault("get", "claude_token", ident=IDENT_B)
check("4 in another chassis the vault is locked (rc 3) and says so; nothing can be read", rc == 3 and "another chassis" in out and rc_g == 3, "rc=%s %s" % (rc, out))
rc, out = vault("unlock", ident=IDENT_B, stdin=("0000-" * 8).encode())
check("5 a wrong phrase is refused", rc == 3 and "not this vault" in out, out)
rc, out = vault("unlock", ident=IDENT_B, stdin=(phrase + "\n").encode())
rc_b, out_b = vault("status", ident=IDENT_B)
rc_a, out_a = vault("status", ident=IDENT_A)
rc_g, got = vault("get", "claude_token", ident=IDENT_B)
check("6 the recovery phrase opens it in the new chassis and re-seals it there: B is open with the secrets intact, A is now locked; the phrase still works afterwards",
      rc == 0 and rc_b == 0 and out_b.strip() == "open" and rc_a == 3 and got.strip() == "sk-ant-api03-SECRET-TOKEN"
      and vault("unlock", ident=IDENT_A, stdin=(phrase + "\n").encode())[0] == 0,
      "rc=%s B=%s A=%s got=%r" % (rc, out_b, out_a, got))

# ── 7. rephrase ──────────────────────────────────────────────────────────
rc, out = vault("rephrase")
new_phrase = out.strip()
rc_old, _ = vault("unlock", ident=IDENT_B, stdin=(phrase + "\n").encode())
rc_new, _ = vault("unlock", ident=IDENT_B, stdin=(new_phrase + "\n").encode())
vault("unlock", ident=IDENT_A, stdin=(new_phrase + "\n").encode())
check("7 rephrase mints a new phrase; the old one stops working, the new one works", rc == 0 and new_phrase != phrase and rc_old == 3 and rc_new == 0, "rc=%s old=%s new=%s" % (rc, rc_old, rc_new))
phrase = new_phrase

# ── 8. integrity: a flipped byte is refused before anything is decrypted ─
env = json.load(open(os.path.join(ETC, "vault.sealed")))
good = env["data"]
import base64
raw = bytearray(base64.b64decode(good))
raw[len(raw) // 2] ^= 0x01
env["data"] = base64.b64encode(bytes(raw)).decode()
json.dump(env, open(os.path.join(ETC, "vault.sealed"), "w"))
rc, out = vault("get", "claude_token")
env["data"] = good
json.dump(env, open(os.path.join(ETC, "vault.sealed"), "w"))
check("8 a flipped byte in the sealed data fails the integrity check (rc 3, 'integrity'), and the intact file opens again", rc == 3 and "integrity" in out and vault("status")[0] == 0, out)

# ── 9. migrate ───────────────────────────────────────────────────────────
open(os.path.join(ETC, "claude-auth.env"), "w").write("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-MIGRATED\n")
open(os.path.join(ETC, "support_key"), "wb").write(b"PRIVATE-KEY-BYTES")
open(os.path.join(ETC, "assistant.conf"), "w").write("ASSISTANT_USER='admin'\nASSISTANT_PASS='migrated-pw'\nASSISTANT_PORT='7681'\n")
open(os.path.join(ETC, "stream.conf"), "w").write("STREAM_MODE='browser'\nSTREAM_T1_KEY='yt-key'\nSTREAM_T1_URL='rtmp://x'\nSTREAM_T2_KEY=''\n")
os.makedirs(os.path.join(ETC, "nas-private"))
open(os.path.join(ETC, "nas-private", "passdb.tdb"), "wb").write(b"TDB-BYTES")
open(os.path.join(ETC, "web-admin.conf"), "w").write("HASH='$6$abc'\n")
open(os.path.join(ETC, "users.json"), "w").write("[]")
os.makedirs(os.path.join(ETC, "tls"))
open(os.path.join(ETC, "tls", "server.key"), "w").write("TLSKEY")
rc, out = vault("migrate")
rc2, out2 = vault("migrate")
rc_l, lst = vault("list")
aconf = open(os.path.join(ETC, "assistant.conf")).read()
sconf = open(os.path.join(ETC, "stream.conf")).read()
rc_p, left = vault("plaintext-left")
check("9 migrate moves the five plaintext secrets in, shreds the files, blanks the conf fields (keeping the rest), leaves web-admin/users/tls alone, is idempotent, and plaintext-left says none",
      rc == 0 and all(n in out for n in ("claude_token", "support_key", "assistant_pass", "stream_key_1", "nas_passdb"))
      and not os.path.exists(os.path.join(ETC, "claude-auth.env")) and not os.path.exists(os.path.join(ETC, "support_key"))
      and not os.path.exists(os.path.join(ETC, "nas-private"))
      and "ASSISTANT_PASS=''" in aconf and "ASSISTANT_USER='admin'" in aconf and "ASSISTANT_PORT='7681'" in aconf
      and "STREAM_T1_KEY=''" in sconf and "STREAM_T1_URL='rtmp://x'" in sconf
      and os.path.exists(os.path.join(ETC, "web-admin.conf")) and os.path.exists(os.path.join(ETC, "users.json")) and os.path.exists(os.path.join(ETC, "tls", "server.key"))
      and rc2 == 0 and "nothing to move" in out2
      and vault("get", "claude_token")[1].strip() == "sk-ant-oat01-MIGRATED" and "stream_key_1" in lst
      and rc_p == 0 and left.strip() == "none",
      "rc=%s out=%s aconf=%r sconf=%r left=%r" % (rc, out, aconf, sconf, left))
open(os.path.join(ETC, "claude-auth.env"), "w").write("ANTHROPIC_API_KEY=x\n")
rc_p, left = vault("plaintext-left")
check("9b plaintext-left names a secret that is back in the clear (rc 1)", rc_p == 1 and "claude-auth.env" in left, left)
os.unlink(os.path.join(ETC, "claude-auth.env"))

# ── 10. no key material on any openssl argv ─────────────────────────────
argv = open(ARGV_LOG).read()
check("10 openssl never sees key material on argv: every call uses -pass fd:N, never -K or -pass pass:",
      "-pass fd:" in argv and "-K " not in argv and "pass:" not in argv and "SECRET" not in argv and phrase.replace("-", "")[:8] not in argv,
      argv[-300:])

# ── 11. the wiring ───────────────────────────────────────────────────────
front = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
settings = [open(os.path.join(REPO, p)).read() for p in
            ("overlay/etc/pipeos/pipebox-settings.json", "overlay/usr/local/share/pipeos/card/pipebox-settings.json.tmpl")]
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read()
lbu_lines = [l.strip() for l in lbu.splitlines() if not l.startswith("#")]
build = open(os.path.join(REPO, "scripts/40-build-apkovl.sh")).read()
default_line = next((l for l in build.splitlines() if l.startswith("mk_runlevel default")), "")
consumers = {p: open(os.path.join(REPO, "overlay", p)).read() for p in
             ("usr/local/bin/pipebox-listener", "usr/local/bin/pipebox-cohort-watch", "usr/local/bin/pipeos-assistant-run",
              "usr/local/bin/pipeos-assistant-claude", "etc/init.d/pipeos-support", "etc/init.d/pipeos-nas",
              "etc/init.d/pipeos-stream", "etc/init.d/pipeos-assistant")}
check("11 the fence denies the verb and the sealed file and the run dir; `pipeos` dispatches it; lbu.list carries the vault + its service and no longer the three plaintext paths; the service is in the default runlevel before the web",
      all(all(m in s for m in ('"Bash(pipeos vault*)"', '"Bash(pipeos-vault*)"', '"Read(/etc/pipeos/vault.sealed)"', '"Read(/run/pipeos/secrets/**)"')) for s in settings)
      and "vault)       shift; exec /usr/local/bin/pipeos-vault" in front and "pipeos vault status" in front
      and "+etc/init.d/pipeos-vault" in lbu_lines and "+etc/pipeos/vault.sealed" in lbu_lines
      and not any(l in lbu_lines for l in ("+etc/pipeos/claude-auth.env", "+etc/pipeos/support_key", "+etc/pipeos/nas-private"))
      and "pipeos-vault pipeos-web" in default_line,
      "default=%r" % default_line)
check("12 every consumer reads its secret from /run/pipeos/secrets (the vault's export), none from a plaintext /etc path first",
      all("/run/pipeos/secrets" in s for s in consumers.values())
      and "PRIVATE=/run/pipeos/secrets/nas" in consumers["etc/init.d/pipeos-nas"]
      and "/run/pipeos/secrets/stream.env" in consumers["etc/init.d/pipeos-stream"]
      and "/run/pipeos/secrets/assistant.env" in consumers["etc/init.d/pipeos-assistant"]
      and "/run/pipeos/secrets/support_key" in consumers["etc/init.d/pipeos-support"]
      and "AUTH=/run/pipeos/secrets/claude.env" in consumers["usr/local/bin/pipebox-listener"],
      "")
init = open(INIT).read()
_i = [init.find(k) for k in ("pipeos-vault init", "pipeos-vault migrate", "pipeos-vault export", "/usr/local/bin/pipeos-save")]
check("13 the boot service migrates a claimed box (init, migrate, export, then SAVE — the shred is not real until the media no longer carries the plaintext), parks the phrase, and orders itself before every consumer",
      all(i >= 0 for i in _i) and _i == sorted(_i)
      and "vault-phrase" in init and "before pipeos-web pipe-daemon pipebox-listener pipeos-nas pipeos-stream pipeos-assistant pipeos-support" in init
      and "web-admin.conf" in init,
      "")

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
