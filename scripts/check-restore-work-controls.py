#!/usr/bin/env python3
"""Controls for check-restore-work.py: put each guard back to broken, in a
copy of the shipped script (or of a settings file), and assert the probe
notices (the house rule since #100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "overlay/usr/local/bin/pipeos-restore-work")
SETTINGS = os.path.join(REPO, "overlay/etc/pipeos/pipebox-settings.json")
TMPL = os.path.join(REPO, "overlay/usr/local/share/pipeos/card/pipebox-settings.json.tmpl")
PROBE = os.path.join(HERE, "check-restore-work.py")

BREAKS = [
    ("A  --delete rides along",
     "    $RSYNC -a --exclude /lost+found", "    $RSYNC -a --delete --exclude /lost+found"),
    ("B  this box's runtime state is restored over",
     "--exclude /.pipeos ", ""),
    ("C  every /work counts as empty",
     '            *) echo "$_n"; return 1 ;;', '            *) continue ;;'),
    ("D  --force is not parsed",
     "        --force) FORCE=yes ;;", "        --force) : ;;"),
    ("E  the own-work-disk refusal is gone",
     '        [ "$(src_of_mount "$WORK")" != "$SRC" ] ||', '        true ||'),
    ("F  the device source is mounted read-write",
     '        do_mount mount -t ext4 -o ro "$SRC"', '        do_mount mount -t ext4 "$SRC"'),
    ("G  --onto the running /work is allowed",
     '        [ "$(src_of_mount "$WORK")" != "$ONTO" ] ||', '        true ||'),
]

src = open(BIN).read()
failed = False


def run_probe(env):
    p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True,
                       env=dict(os.environ, **env))
    return [l for l in p.stdout.splitlines() if l.startswith("FAIL")]


def report(name, fails):
    global failed
    print("%s\n   -> %d row(s) fail" % (name, len(fails)))
    for l in fails:
        print("      " + l[5:].split("  [")[0])
    if not fails:
        failed = True
        print("   !! the probe did not notice")


for name, old, new in BREAKS:
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them"
                 % (name[0], src.count(old)))
    fd, path = tempfile.mkstemp(prefix="ckrw-ctl-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    report(name, run_probe({"CHECK_RESTORE_BIN": path}))
    os.unlink(path)

# H: the deny entry drops out of the TEMPLATE (the file a card regenerates
# from) — the probe reads both files, so a broken copy of either must show.
tsrc = open(TMPL).read()
anchor = '      "Bash(pipeos-restore-work*)",\n'
if tsrc.count(anchor) != 1:
    sys.exit("control H: anchor appears %d times in the template" % tsrc.count(anchor))
fd, path = tempfile.mkstemp(prefix="ckrw-ctl-", suffix=".tmpl")
with os.fdopen(fd, "w") as f:
    f.write(tsrc.replace(anchor, "", 1))
report("H  the template no longer denies the verb", run_probe({"CHECK_RESTORE_SETTINGS": SETTINGS + ":" + path}))
os.unlink(path)

p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
print("intact tree: " + p.stdout.strip().splitlines()[-1])
sys.exit(1 if failed or p.returncode else 0)
