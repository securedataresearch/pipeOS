#!/usr/bin/env python3
"""Probe for section 4 of 10-mk-chroot.sh — the abuild signing key. (pipeOS#90)

Runs the SHIPPED section, extracted verbatim from the real script, against a
throwaway tree. Two things are shimmed and nothing else:

  sudo    -> runs the command unprivileged
  chroot  -> simulates the only two effects the key logic depends on:
             /home/builder/.abuild exists, and abuild-keygen mints a key into
             it. adduser/addgroup/sudoers are not what this probe tests.

Every fake key carries its own content, so each row asserts WHICH key
survived, not merely how many did. A count is the one thing that stays right
when the wrong key wins: the failure this section exists to prevent is signing
with a stray key, and a stray key counts as one just like the real one.

Exit 0 if every row passes. Controls live in check-signing-key-controls.py.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "scripts", "10-mk-chroot.sh")

FLEET = "fleet@pipeos-1000000000.rsa"  # the key already trusted by flashed sticks
STRAY = "stray@pipeos-2000000000.rsa"  # what a buggy run minted on top of it

SUDO_SHIM = """#!/bin/sh
exec "$@"
"""

# The chroot shim mints into the SAME path the real abuild-keygen would, so
# the section's own copy logic is what moves the key around, not the shim.
CHROOT_SHIM = """#!/bin/sh
root=$1; shift
mkdir -p "$root/home/builder/.abuild"
case "$*" in
  *abuild-keygen*)
    k="$root/home/builder/.abuild/$PROBE_NEW_KEY"
    echo "PRIVATE-$PROBE_NEW_KEY" > "$k"
    echo "PUBLIC-$PROBE_NEW_KEY" > "$k.pub"
    ;;
esac
exit 0
"""


def extract_section():
    """Section 4, from its banner to the line before the closing echo."""
    src = open(SCRIPT).read()
    m = re.search(r"^# 4\. builder user.*?(?=^echo \"chroot ready)",
                  src, re.S | re.M)
    if not m or "SIGNING_KEY_DIR" not in m.group(0):
        sys.exit(f"FAIL could not extract section 4 from {SCRIPT} (markers moved?)")
    return m.group(0)


SECTION = extract_section()


def putkey(d, name):
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, name), "w").write(f"PRIVATE-{name}")
    open(os.path.join(d, name + ".pub"), "w").write(f"PUBLIC-{name}")


class Sandbox:
    def __init__(self, root):
        self.root = root
        self.out = os.path.join(root, "out")
        self.chroot = os.path.join(self.out, "chroot")
        self.abuild = os.path.join(self.chroot, "home/builder/.abuild")
        self.durable = os.path.join(root, "keys/pipeos")
        self.keys = os.path.join(self.out, "keys")
        os.makedirs(os.path.join(self.chroot, "etc/apk/keys"), exist_ok=True)
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir, exist_ok=True)
        for name, body in (("sudo", SUDO_SHIM), ("chroot", CHROOT_SHIM)):
            p = os.path.join(bindir, name)
            open(p, "w").write(body)
            os.chmod(p, 0o755)
        self.bindir = bindir

    def run(self, new_key=STRAY):
        env = dict(os.environ)
        env.update(
            PATH=self.bindir + os.pathsep + env["PATH"],
            CHROOT=self.chroot, OUT=self.out,
            SIGNING_KEY_DIR=self.durable,
            USER=env.get("USER") or "builder",
            PROBE_NEW_KEY=new_key,
        )
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + SECTION],
                           capture_output=True, text=True, env=env)
        self.rc, self.log = r.returncode, r.stdout + r.stderr
        return self

    def is_key(self, d, name):
        """The file is that key, not a same-named husk some copy left behind."""
        try:
            return open(os.path.join(d, name)).read().strip() == f"PRIVATE-{name}"
        except OSError:
            return False

    def has(self, d, name):
        return os.path.exists(os.path.join(d, name))

    def names(self, d):
        try:
            return sorted(f for f in os.listdir(d) if not f.endswith(".pub"))
        except OSError:
            return []


RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else f"  [{detail}]"))


SANDBOXES = []


def case(setup, new_key=STRAY):
    # The tree outlives the call on purpose: the assertions are what read it.
    # Cleaned up at exit, all at once.
    root = tempfile.mkdtemp(prefix="signkey-")
    SANDBOXES.append(root)
    s = Sandbox(root)
    setup(s)
    return s.run(new_key)


def cleanup():
    for root in SANDBOXES:
        for d, _, _ in os.walk(root):  # case 9 leaves a mode-500 directory
            os.chmod(d, 0o755)
        shutil.rmtree(root, ignore_errors=True)


# ── 1. cold start: nothing anywhere ──────────────────────────────────────
s = case(lambda s: None, new_key=FLEET)
check("1 cold start mints one key and lands it in both stores",
      s.rc == 0 and "generating a new one" in s.log
      and s.is_key(s.durable, FLEET) and s.is_key(s.keys, FLEET),
      f"rc={s.rc} durable={s.names(s.durable)} out={s.names(s.keys)}")

# ── 2. key only in the durable home, chroot wiped ────────────────────────
s = case(lambda s: putkey(s.durable, FLEET))
check("2 restores from the durable home instead of minting",
      s.rc == 0 and "restoring abuild signing key" in s.log
      and s.is_key(s.abuild, FLEET) and not s.has(s.abuild, STRAY),
      f"rc={s.rc} chroot={s.names(s.abuild)}")

# ── 3. the original bug: the key survives only in out/keys ───────────────
s = case(lambda s: putkey(s.keys, FLEET))
check("3 out/keys-only survivor is restored, not replaced",
      s.rc == 0 and s.is_key(s.abuild, FLEET) and not s.has(s.abuild, STRAY)
      and s.is_key(s.durable, FLEET),
      f"rc={s.rc} chroot={s.names(s.abuild)} durable={s.names(s.durable)}")

# ── 4. abuild.conf must name the key that was actually restored ──────────
s = case(lambda s: putkey(s.durable, FLEET))
conf = os.path.join(s.abuild, "abuild.conf")
got = open(conf).read().strip() if os.path.exists(conf) else "<missing>"
check("4 abuild.conf points at the restored key",
      got == f'PACKAGER_PRIVKEY="/home/builder/.abuild/{FLEET}"', got)

# ── 5. box3's case: stray in the chroot, fleet key in out/keys ───────────
# The restore never runs — the chroot already has *a* key — so only a guard on
# the WRITE path can catch this. Unguarded, the stray is seeded into the empty
# durable home and every later run restores it as if it were the fleet key.
def stray_vs_fleet(s):
    putkey(s.abuild, STRAY)
    putkey(s.keys, FLEET)


s = case(stray_vs_fleet)
check("5 stray-in-chroot vs fleet-in-out/keys stops the build, seeds nothing",
      s.rc != 0 and not s.has(s.durable, STRAY),
      f"rc={s.rc} durable={s.names(s.durable)}")

# ── 6. two keys in one store ─────────────────────────────────────────────
def two_in_durable(s):
    putkey(s.durable, FLEET)
    putkey(s.durable, STRAY)


s = case(two_in_durable)
check("6 two keys in one store refuses to guess",
      s.rc != 0 and "refusing to guess" in s.log, f"rc={s.rc}")

# ── 7. the stores disagree with each other ───────────────────────────────
def disagree(s):
    putkey(s.durable, FLEET)
    putkey(s.keys, STRAY)


s = case(disagree)
check("7 stores that disagree stop the build",
      s.rc != 0, f"rc={s.rc} — reconciled silently")

# ── 8. steady state: one key, everywhere, agreeing ───────────────────────
def steady(s):
    for d in (s.abuild, s.durable, s.keys):
        putkey(d, FLEET)


s = case(steady)
check("8 steady state is a no-op",
      s.rc == 0 and "generating a new one" not in s.log
      and s.names(s.durable) == [FLEET] and s.is_key(s.abuild, FLEET),
      f"rc={s.rc} durable={s.names(s.durable)}")

# ── 9. the durable home cannot be created ────────────────────────────────
# SIGNING_KEY_DIR defaults outside the repo, so a build host that is not this
# appliance is the normal case, not an exotic one. It must fail with the fix
# in the message rather than a bare `mkdir: permission denied` under set -e.
#
# The obstacle is a plain FILE where the parent directory should be, not a
# mode-500 directory: this suite runs as root on the appliance, and mode bits
# do not bind root. A control that only bites for unprivileged users is a
# control that never runs here.
def unwritable(s):
    blocked = os.path.join(s.root, "nope")
    open(blocked, "w").write("not a directory")
    s.durable = os.path.join(blocked, "keys")


s = case(unwritable, new_key=FLEET)
check("9 uncreatable durable home fails with the fix, not a stack trace",
      s.rc != 0 and "set SIGNING_KEY_DIR" in s.log, f"rc={s.rc} log={s.log[-200:]!r}")

# ── 10. the public key reaches the chroot's apk trust store ──────────────
s = case(lambda s: putkey(s.durable, FLEET))
check("10 restored public key is trusted inside the chroot",
      os.path.exists(os.path.join(s.chroot, "etc/apk/keys", FLEET + ".pub")),
      str(s.names(os.path.join(s.chroot, "etc/apk/keys"))))

cleanup()
sys.exit(0 if all(RESULTS) else 1)
