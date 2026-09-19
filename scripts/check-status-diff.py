#!/usr/bin/env python3
"""check-status-diff (pipeOS#307): `pipeos status` and `pipeos diff` run lbu
against OUR apkovl. lbu's status/diff compare the last committed apkovl with
a fresh package — but they look for <hostname>.apkovl.tar.gz, and pipeOS's
canonical is pipeos.apkovl.tar.gz, so on every box lbu listed every file as
"A" forever ("260 uncommitted change(s)" beside a passing verify on zero).
The fix hands lbu a tmpfs dir holding a hostname-named symlink to the
canonical through LBU_BACKUPDIR. A stub lbu asserts exactly that wiring and
answers from a fixture; the apkovl is a real tarball so the integrity gate
is exercised. No root, nothing outside a tempdir.
"""
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PIPEOS = os.environ.get("CHECK_PIPEOS_BIN", os.path.join(REPO, "overlay/usr/local/bin/pipeos"))
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="ckdiff-")
MEDIA = os.path.join(D, "media"); BIN = os.path.join(D, "bin"); os.makedirs(MEDIA); os.makedirs(BIN)
OVL = os.path.join(MEDIA, "pipeos.apkovl.tar.gz")
with tarfile.open(OVL, "w:gz") as t:
    p = os.path.join(D, "hostname"); open(p, "w").write("pipeos-a4e0\n"); t.add(p, arcname="etc/hostname")
CALLS = os.path.join(D, "lbu.calls"); ANSWER = os.path.join(D, "lbu.answer")
# the stub: records how it was called, checks the door pipeos must open, answers from the fixture
with open(os.path.join(BIN, "lbu"), "w") as f:
    f.write("#!/bin/sh\n"
            "echo \"$* BACKUPDIR=${LBU_BACKUPDIR:-unset} MEDIA=${LBU_MEDIA:-unset}\" >> %s\n"
            "[ -n \"$LBU_BACKUPDIR\" ] || { echo 'stub: LBU_BACKUPDIR unset (lbu would mount the media and look for the hostname file there)' >&2; exit 9; }\n"
            "f=\"$LBU_BACKUPDIR/$(hostname).apkovl.tar.gz\"\n"
            "[ -L \"$f\" ] || { echo \"stub: no hostname-named link in $LBU_BACKUPDIR\" >&2; exit 9; }\n"
            "[ \"$(readlink -f \"$f\")\" = \"$(readlink -f %s)\" ] || { echo 'stub: the link does not point at the canonical apkovl' >&2; exit 9; }\n"
            "case \"$1\" in status) cat %s 2>/dev/null; exit 0 ;; diff) echo \"--- content diff ---\"; cat %s 2>/dev/null; exit 0 ;; *) exit 9 ;; esac\n"
            % (CALLS, OVL, ANSWER, ANSWER))
os.chmod(os.path.join(BIN, "lbu"), 0o755)
ENV = dict(os.environ, PIPEOS_MEDIA=MEDIA, PIPEOS_LBU=os.path.join(BIN, "lbu"))


def run(*args):
    p = subprocess.run(["sh", PIPEOS] + list(args), capture_output=True, text=True, env=ENV)
    return p.returncode, p.stdout + p.stderr


def calls():
    try:
        return open(CALLS).read().splitlines()
    except OSError:
        return []


rc0, out0 = run("diff")
check("1 `pipeos diff` runs lbu status with LBU_BACKUPDIR set to a directory holding <hostname>.apkovl.tar.gz -> the canonical apkovl (so lbu compares against OUR file and mounts nothing); an empty answer is 'no uncommitted changes'",
      rc0 == 0 and "no uncommitted changes" in out0 and len(calls()) == 1 and calls()[0].startswith("status -v BACKUPDIR=/") and "MEDIA=" in calls()[0],
      repr((rc0, out0, calls())))
open(ANSWER, "w").write("A etc/pipeos/schedule.json\nU etc/pipeos/card.conf\nD etc/motd\n")
rc1, out1 = run("diff")
check("2 lbu's own A/U/D rows come through untouched (one dialect, lbu's)",
      rc1 == 0 and "A etc/pipeos/schedule.json" in out1 and "U etc/pipeos/card.conf" in out1 and "D etc/motd" in out1, repr((rc1, out1)))
rc2, out2 = run("diff", "--content")
check("3 `pipeos diff --content` is lbu's full diff against the same canonical",
      rc2 == 0 and "content diff" in out2 and calls()[-1].startswith("diff BACKUPDIR="), repr((rc2, out2, calls()[-1:])))
d = os.path.join(D, "tmpdirs-after"); left = [x for x in os.listdir(tempfile.gettempdir()) if x.startswith("tmp.") and os.path.isdir(os.path.join(tempfile.gettempdir(), x)) and os.path.islink(os.path.join(tempfile.gettempdir(), x, socket.gethostname() + ".apkovl.tar.gz"))]
check("4 the tmpfs link dir is removed after every call (no hostname-named apkovl link lingers anywhere)", not left, repr(left))
src = open(PIPEOS).read()
i = src.index("cmd_status() {"); j = src.index("cmd_verify() {")
check("5 the status line counts the same call (lbu_canonical status), never a bare `lbu status`, and a comparison that cannot be made is a WARN carrying lbu_canonical's reason",
      "lbu_canonical status" in src[i:j] and "lbu status" not in src[i:j] and "cannot tell what a save would change" in src[i:j], "")
with open(OVL, "r+b") as f:
    f.truncate(40)
rc3, out3 = run("diff")
n_before = len(calls())
check("6 a truncated canonical apkovl (the power-loss case) is refused before lbu runs: rc 2, 'no readable canonical apkovl', not a listing of the whole tree",
      rc3 == 2 and "no readable canonical apkovl" in out3 and len(calls()) == n_before and "A etc" not in out3, repr((rc3, out3)))
os.unlink(OVL)
rc4, out4 = run("diff")
check("7 no canonical apkovl at all: the same refusal", rc4 == 2 and "no readable canonical apkovl" in out4, repr((rc4, out4)))
docs = {f: open(os.path.join(REPO, f)).read() for f in ("overlay/root/.claude/CLAUDE.md", "overlay/usr/local/bin/extra-add", "docs/fleet-update-runbook.md")}
bare = [f for f, t in docs.items() if "lbu status" in t and "pipeos diff" not in t]
check("8 nothing sends the resident agent or an operator to bare `lbu status` any more (the on-box rulebook, extra-add, the update runbook say pipeos diff)", not bare, repr(bare))

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
