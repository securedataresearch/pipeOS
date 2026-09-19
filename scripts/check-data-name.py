#!/usr/bin/env python3
"""check-data-name (pipeOS#219, step 1): `/data` is the bulk volume's name.

This release makes the NAME work without moving anything: the volume still
mounts at /work and /data is a symlink to it, laid at every boot by
/etc/local.d/workspace.sh and by a deploy that installs a new one. Both paths
then reach the same bytes, so a job's working dir may be /data/... on a box
that has not rebooted since. A later release flips which of the two is the
mount.

The script's link logic is driven through its seams (PIPEOS_WORKSPACE_WORK,
_DATA, _NO_MOUNT — a box never sets them); the rest asserts the wiring that
lays the link on a live box and the row that says when it is wrong.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(REPO, "overlay/etc/local.d/workspace.sh")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", desc, "" if ok else "  <- " + detail))


def run(work, data):
    env = dict(os.environ, PIPEOS_WORKSPACE_WORK=work, PIPEOS_WORKSPACE_DATA=data, PIPEOS_WORKSPACE_NO_MOUNT="1")
    p = subprocess.run(["sh", WS], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def case():
    d = tempfile.mkdtemp(prefix="dataname-")
    w = os.path.join(d, "work"); os.makedirs(w)
    return d, w, os.path.join(d, "data")


# 1. nothing there yet: the link is laid, and the skeleton is under the volume
d, w, data = case()
rc1, out1 = run(w, data)
check("1 with no /data the script lays it as a symlink to the volume, and the skeleton (repos, logs, cache, claude, pipebox, backup, home) is made under the volume itself",
      rc1 == 0 and os.path.islink(data) and os.path.realpath(data) == os.path.realpath(w)
      and all(os.path.isdir(os.path.join(w, x)) for x in ("repos", "logs", "cache", "claude", "pipebox", "backup", "home")),
      "rc=%s link=%s out=%r" % (rc1, os.path.islink(data), out1[-200:]))

# 2. both paths reach the same bytes — that is the whole point of this release
open(os.path.join(w, "logs", "a.log"), "w").write("one")
check("2 the two names reach the same file: what is written under the volume is read under /data",
      open(os.path.join(data, "logs", "a.log")).read() == "one", "")

# 3. run again (every boot, and every deploy that installs the script): idempotent
rc3, _ = run(w, data)
check("3 a second run leaves the link alone (it runs at every boot and after a deploy)",
      rc3 == 0 and os.path.islink(data) and os.path.realpath(data) == os.path.realpath(w), "")

# 4. an EMPTY real directory at /data (something mkdir -p'd it before the link existed) is replaced
d4, w4, data4 = case(); os.makedirs(data4)
rc4, _ = run(w4, data4)
check("4 an empty real directory at /data — what a mkdir -p before the link leaves — is replaced by the link",
      rc4 == 0 and os.path.islink(data4), "")

# 5. a real directory with something in it is NOT moved: it is left and said
d5, w5, data5 = case(); os.makedirs(data5); open(os.path.join(data5, "someones.file"), "w").write("x")
rc5, out5 = run(w5, data5)
check("5 a real directory with content in it is left alone and said (moving an unknown directory is not this script's call), and the run still succeeds",
      rc5 == 0 and not os.path.islink(data5) and os.path.isfile(os.path.join(data5, "someones.file")) and "is not a link to" in out5,
      "rc=%s out=%r" % (rc5, out5[-200:]))

# 6. the wiring: a deploy that installs the script runs it (no service bounce), selfcheck says when the name is wrong
dep = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-deploy-overlay")).read()
sc = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
check("6 a deploy that installs a new workspace.sh runs it (idempotent, so no rc-service restart bounces pipe-daemon, the assistant or the owner's terminals); selfcheck has a row for the name — ok when it resolves to the volume, a warning when it is missing, CRITICAL when it is a real directory (writes there are in RAM)",
      'grep -qxF "etc/local.d/workspace.sh" "$installed_rels"' in dep and 'sh "$ROOT/etc/local.d/workspace.sh"' in dep
      and "rc-service pipeos-workspace" not in dep
      and '/data resolves to the volume' in sc and "/data does not exist yet" in sc and "is not a link to /work" in sc, "")

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
