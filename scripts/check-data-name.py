#!/usr/bin/env python3
"""check-data-name (pipeOS#219 named it, #330 moved it): `/data` is the bulk
volume, and nothing answers to the old name.

The volume mounts at `/data`, and that is the only name for it: no symlink,
no second root, and the name it replaced is not referenced anywhere in this
tree. A compatibility name kept "for a release or two" is a name kept for
ever, and every reader then has to handle both — which is how the ledger's
cursor, a job's stored working dir and claude's session directories each
acquired two truths.

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

# 4. the source itself carries no second name and no migration shim
ws = open(WS).read()
check("4 the script names one volume: no symlink laid, no second root, no 'whichever is mounted' branch",
      "ln -s" not in ws.split("# Agent memory")[0] and "mounted_on" not in ws and "PIPEOS_WORKSPACE_WORK" not in ws,
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
check("7 every reporting call in selfcheck goes to a helper the file defines — a row that greps for message text passes just as happily when the line calls a function that does not exist, and then the report and the owner's DM both get nothing",
      not undefined, "undefined reporters called: %r" % (undefined,))

# 8. and it stays gone. The volume's old name is another thing's name now, so
# a path built from it would not merely be stale — it would point somewhere
# real and wrong. This walks the shipped tree and the probes for the path,
# allowing only names that merely start the same way (workspace.sh,
# worksweep, .github/workflows).
OLD = "/" + "work"          # not written whole, so this row does not trip itself
hits = []
for rel in ("overlay", "scripts", "docs", "fleet", ".claude"):
    base = os.path.join(REPO, rel)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [x for x in dirnames if x not in (".git", "__pycache__")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                body = open(full, errors="replace").read()
            except OSError:
                continue
            for m in re.finditer(re.escape(OLD) + r"($|[^a-zA-Z0-9])", body):
                hits.append("%s:%d" % (os.path.relpath(full, REPO), body.count("\n", 0, m.start()) + 1))
for fn in ("config.sh", "Makefile", "CLAUDE.md", "README.md"):
    full = os.path.join(REPO, fn)
    if os.path.exists(full):
        body = open(full, errors="replace").read()
        for m in re.finditer(re.escape(OLD) + r"($|[^a-zA-Z0-9])", body):
            hits.append("%s:%d" % (fn, body.count("\n", 0, m.start()) + 1))
check("8 the volume's former name appears nowhere in the shipped tree, the probes or the docs — it names something else now, so a path built from it would point somewhere real and wrong, and a single reference is how a second truth gets back in",
      not hits, "found at: %s" % ", ".join(hits[:12]))

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
