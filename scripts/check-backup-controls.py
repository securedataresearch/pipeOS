#!/usr/bin/env python3
"""Controls for check-backup.py: put each guard back to broken, in a copy of
the shipped script, and assert the probe notices. A probe that passes with
the guard gone is testing nothing (the house rule since #100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "overlay/usr/local/bin/pipeos-backup")
PROBE = os.path.join(HERE, "check-backup.py")

BREAKS = [
    ("A  the unclaimed-box guard is gone",
     '    [ -f "$ETC/provisioned" ] || die', '    false || die x; true ||'),
    ("B  the candidate integrity gate is gone",
     '    if ! gzip -t "$1" 2>/dev/null || ! tar -tzf "$1" >/dev/null 2>&1; then',
     '    if false; then'),
    ("C  FAT destinations get the ext4 flags",
     '        vfat|msdos|exfat|fat*) echo "-rtL --no-perms --no-owner --no-group --modify-window=2 --delete" ;;',
     '        never) echo "" ;;'),
    ("D  .last is stamped before the mirrors run",
     'date +%s > "$ROOT/.last"\n', ': \n'),
    ("E  the destination-is-a-mountpoint check is gone",
     '[ -n "$FS" ] || die', 'true || die'),
]

src = open(BIN).read()
failed = False
for name, old, new in BREAKS:
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them"
                 % (name[0], src.count(old)))
    broken = src.replace(old, new, 1)
    if name.startswith("D"):
        # move the stamp to before the mirrors: insert it right after the lock
        broken = broken.replace('take_save_lock\n', 'take_save_lock\nmkdir -p "$ROOT"; date +%s > "$ROOT/.last"\n', 1)
    fd, path = tempfile.mkstemp(prefix="ckbk-ctl-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write(broken)
    p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True,
                       env=dict(os.environ, CHECK_BACKUP_BIN=path))
    os.unlink(path)
    fails = [l for l in p.stdout.splitlines() if l.startswith("FAIL")]
    print("%s\n   -> %d row(s) fail" % (name, len(fails)))
    for l in fails:
        print("      " + l[5:].split("  [")[0])
    if not fails:
        failed = True
        print("   !! the probe did not notice")
p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
print("intact tree: " + p.stdout.strip().splitlines()[-1])
sys.exit(1 if failed or p.returncode else 0)
