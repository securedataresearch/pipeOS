#!/usr/bin/env python3
"""check-data-name (pipeOS#219 named it, #330 moved it): `/data` is the bulk
volume, and nothing answers to the old name.

The volume mounts at `/data`. There is no `/work`: no symlink, no second
name, nothing a path can still resolve through. A compatibility name kept
"for a release or two" is a name kept for ever, and every reader then has to
handle both — which is how the ledger's cursor, a job's stored working dir
and claude's session directories each acquired two truths.

A `/work` left behind from before the move is a place writes land in RAM and
vanish at the next boot, so the script removes an empty one and selfcheck
reports anything it cannot remove.

The script's logic is driven through its seams (PIPEOS_WORKSPACE_DATA,
_NO_MOUNT — a box never sets them); the rest asserts the wiring that runs it
on a live box and the row that says when the old name is still there.
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


def run(data, root=None):
    env = dict(os.environ, PIPEOS_WORKSPACE_DATA=data, PIPEOS_WORKSPACE_NO_MOUNT="1")
    p = subprocess.run(["sh", WS], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def case():
    d = tempfile.mkdtemp(prefix="dataname-")
    return d, os.path.join(d, "data")


# 1. the skeleton is made under the volume, at the one name
d, vol = case()
rc1, out1 = run(vol)
check("1 the volume's skeleton (repos, logs, cache, claude, pipebox, backup, home) is made under /data",
      rc1 == 0 and all(os.path.isdir(os.path.join(vol, x)) for x in
                       ("repos", "logs", "cache", "claude", "pipebox", "backup", "home")),
      "rc=%s out=%r" % (rc1, out1[-200:]))

# 2. idempotent: it runs at every boot and after every deploy
open(os.path.join(vol, "logs", "a.log"), "w").write("one")
rc2, _ = run(vol)
check("2 a second run changes nothing (it runs at every boot and after a deploy)",
      rc2 == 0 and open(os.path.join(vol, "logs", "a.log")).read() == "one", "")

# 3. no second name is laid anywhere
names = sorted(os.listdir(d))
check("3 no other name is created beside it: the volume is /data and only /data — a compatibility name kept 'for a release or two' is a name kept for ever",
      names == ["data"], repr(names))

# 4-5. the source itself carries no second name and no migration shim
ws = open(WS).read()
check("4 the script names one volume: no symlink laid, no second root, no 'whichever is mounted' branch",
      "ln -s" not in ws.split("# Agent memory")[0] and "mounted_on" not in ws and "PIPEOS_WORKSPACE_WORK" not in ws,
      "")
check("5 a leftover /work is removed when it is a link or an empty directory, and said when it is neither — writes under it would land in RAM and be gone at the next boot",
      re.search(r"if \[ -L /work \]", ws) and "rmdir /work" in ws and "logger" in ws.split("rmdir /work")[1][:400],
      "")

# 6. the wiring: a deploy that installs the script runs it
dep = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-deploy-overlay")).read()
check("6 a deploy that installs a new workspace.sh runs it (idempotent, so no rc-service restart bounces pipe-daemon, the assistant or the owner's terminals) and prints what the script said",
      'grep -qxF "etc/local.d/workspace.sh" "$installed_rels"' in dep and 'sh "$ROOT/etc/local.d/workspace.sh" 2>&1' in dep
      and "rc-service pipeos-workspace" not in dep and '_ws_out' in dep, "")

# 7. selfcheck reports it — and calls helpers that EXIST. selfcheck has no root
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
check("7 selfcheck's /data section is its own numbered section and reports through helpers the file actually defines — a real /work directory is CRITICAL (writes to RAM), a leftover symlink a warning",
      sec and "crit " in sec and "warnn " in sec and "/work" in sec and not undefined,
      "undefined reporters called: %r; section found: %s" % (undefined, bool(sec)))

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
