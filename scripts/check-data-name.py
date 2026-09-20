#!/usr/bin/env python3
"""check-data-name (pipeOS#219 named it, #330 moved it): `/data` is the bulk
volume.

Step 1 made the NAME work with the volume still mounted at /work and /data a
symlink to it. This is the flip: the volume mounts at /data and /work is the
symlink, laid at every boot by /etc/local.d/workspace.sh and by a deploy that
installs a new one. Both names still reach the same bytes, so everything
written down under the old one — a job's working dir, a transcript, a doc —
keeps resolving.

BOTH directions are legal, because a live box meets the script twice in the
wrong order: a deploy installs it while the volume is still mounted at /work,
and only the next boot moves it. So whichever name the volume is already
mounted on is the mount, and the other is linked to it.

The script's link logic is driven through its seams (PIPEOS_WORKSPACE_WORK,
_DATA, _NO_MOUNT — a box never sets them); the rest asserts the wiring that
lays the link on a live box and the row that says when it is wrong.
"""
import os
import re
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
    """A fake root: the volume (the mount, /data's stand-in) exists; the old
    name does not yet."""
    d = tempfile.mkdtemp(prefix="dataname-")
    vol = os.path.join(d, "data"); os.makedirs(vol)
    return d, os.path.join(d, "work"), vol


# The volume is $DATA now and $WORK is the name linked to it (the flip).
# 1. nothing there yet: the link is laid, and the skeleton is under the volume
d, work, vol = case()
rc1, out1 = run(work, vol)
check("1 with nothing there the script lays /work as a symlink to the volume at /data, and the skeleton (repos, logs, cache, claude, pipebox, backup, home) is made under the volume itself",
      rc1 == 0 and os.path.islink(work) and os.path.realpath(work) == os.path.realpath(vol)
      and all(os.path.isdir(os.path.join(vol, x)) for x in ("repos", "logs", "cache", "claude", "pipebox", "backup", "home")),
      "rc=%s link=%s out=%r" % (rc1, os.path.islink(work), out1[-200:]))

# 2. both paths reach the same bytes — what keeps every stored /work path alive
open(os.path.join(vol, "logs", "a.log"), "w").write("one")
check("2 the two names reach the same file: what is written under the volume is read under the old name",
      open(os.path.join(work, "logs", "a.log")).read() == "one", "")

# 3. run again (every boot, and every deploy that installs the script): idempotent
rc3, _ = run(work, vol)
check("3 a second run leaves the link alone (it runs at every boot and after a deploy)",
      rc3 == 0 and os.path.islink(work) and os.path.realpath(work) == os.path.realpath(vol), "")

# 4. an EMPTY real directory at the link's name is replaced
d4, work4, vol4 = case(); os.makedirs(work4, exist_ok=True)
rc4, _ = run(work4, vol4)
check("4 an empty real directory at /work — what a mkdir -p before the link leaves — is replaced by the link",
      rc4 == 0 and os.path.islink(work4), "")

# 5. a real directory with something in it is NOT moved: it is left and said
d5, work5, vol5 = case(); os.makedirs(work5, exist_ok=True); open(os.path.join(work5, "someones.file"), "w").write("x")
rc5, out5 = run(work5, vol5)
check("5 a real directory with content in it is left alone and said (moving an unknown directory is not this script's call), and the run still succeeds",
      rc5 == 0 and not os.path.islink(work5) and os.path.isfile(os.path.join(work5, "someones.file")) and "is not a link to" in out5,
      "rc=%s out=%r" % (rc5, out5[-200:]))

# 6. a link that points somewhere else is OURS to repoint — the name belongs to
# the volume, and leaving it would make every boot and deploy a no-op while
# selfcheck asked for one
d6, work6, vol6 = case(); os.symlink(os.path.join(d6, "elsewhere"), work6)
rc6, out6 = run(work6, vol6)
check("6 a symlink at /work that points somewhere else (a dangling one, or somebody's own) is repointed at the volume and said — not left, which would make every boot and every deploy a no-op",
      rc6 == 0 and os.path.islink(work6) and os.readlink(work6) == vol6 and "repointing it at the volume" in out6,
      "rc=%s target=%r out=%r" % (rc6, os.path.realpath(work6), out6[-200:]))

# 6b. the other direction, which a live box is in between the deploy and its
# reboot: the volume is STILL mounted at /work and nothing may move it out
# from under a running daemon, so /work stays the mount and /data is linked to
# it. A probe cannot make a mountpoint unprivileged, so this reads the branch
# rather than running it — the runtime half is rows 1-6 and the reboot drill.
ws = open(WS).read()
check("6b a volume already mounted at /work keeps it (the deploy window): that name stays the mount and /data is linked to it, so a deploy never moves a mount out from under a running daemon",
      re.search(r"if mounted_on \"\$WORK\"", ws) and "MOUNT=$WORK" in ws and "LINK=$DATA" in ws
      and "MOUNT=$DATA" in ws and "LINK=$WORK" in ws
      and re.search(r"mounted_on\(\)[^}]*\[ -L \"\$1\" \] && return 1", ws, re.S),
      "the branch, or mounted_on's symlink guard, is not there")

# 6c. the stored paths are rewritten once, after the flip only
check("6c after the flip — and only then — the script runs pipeos-data-migrate, handing it the old name and the new: stored job cwds, claude's trust and its project dirs key on the physical path and do not follow a symlink",
      '[ "$MOUNT" = "$DATA" ]' in ws and "pipeos-data-migrate" in ws
      and "PIPEOS_MIGRATE_FROM=\"$WORK\"" in ws and "PIPEOS_MIGRATE_TO=\"$DATA\"" in ws, "")

# 7. the wiring: a deploy that installs the script runs it and shows what it said
dep = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-deploy-overlay")).read()
check("7 a deploy that installs a new workspace.sh runs it (idempotent, so no rc-service restart bounces pipe-daemon, the assistant or the owner's terminals) and prints what the script said — the script exits 0 when it REFUSES a /data that is not ours, so swallowing its line would have the deploy claim a link it did not lay",
      'grep -qxF "etc/local.d/workspace.sh" "$installed_rels"' in dep and 'sh "$ROOT/etc/local.d/workspace.sh" 2>&1' in dep
      and "rc-service pipeos-workspace" not in dep and '_ws_out' in dep, "")

# 8. selfcheck reports it — and calls helpers that EXIST. selfcheck has no root
# seam, so a probe cannot run it; a row that greps for message text passes just
# as happily when the line calls `warn` (no such function: the report gets
# nothing, the DM gets nothing) as when it calls `warnn`. So: read the helpers
# the file defines, then check every reporting call in the file against them.
sc = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
defined = set(re.findall(r"^(\w+)\(\)\s*\{", sc, re.M))
REPORTERS = {"ok", "warn", "warnn", "crit", "critstate", "note", "noten", "fixed", "fix", "would", "info", "pass"}
called = set(re.findall(r"^\s*(\w+)\s+\"", sc, re.M)) & REPORTERS
undefined = sorted(called - defined)
sec = re.search(r"^# ---- 1ba\..*?(?=^# ---- )", sc, re.M | re.S)
sec = sec.group(0) if sec else ""
check("8 selfcheck's /data section is its own numbered section (not spliced into 1b's comment) and reports through helpers the file actually defines — no call anywhere in selfcheck goes to a reporting function that does not exist",
      sec and "warnn " in sec and "crit " in sec and "is a real directory" in sec and "does not exist" in sec and not undefined,
      "undefined reporters called: %r; section found: %s" % (undefined, bool(sec)))

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
