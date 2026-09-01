#!/usr/bin/env python3
"""Controls for check-flash.py: break each guard in a copy of the shipped
script and assert the probe notices (the house rule since #100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "overlay/usr/local/bin/pipeos-flash")
PROBE = os.path.join(HERE, "check-flash.py")

BREAKS = [
    ("A  the GPT signature check is gone",
     '    [ "$_sig" = "EFI PART" ] ||', '    false &&'),
    ("B  a bigger image squeezes in anyway",
     '    if [ "$2" -gt "$4" ]; then', '    if false; then'),
    ("C  the start-at-2048 rule is gone",
     '    [ "$1" = 2048 ] ||', '    true ||'),
    ("D  the world union keeps only the box's world",
     '''        cat "$_t/a/etc/apk/world" "$_t/b/etc/apk/world" 2>/dev/null | grep -v '^$' | sort -u \\''',
     '''        cat "$_t/a/etc/apk/world" 2>/dev/null | grep -v '^$' | sort -u \\'''),
    ("E  the merge overwrites the box's NEVER paths with the image's",
     '''    # NEVER paths stay the box's — except the stamp, which is the image's.''',
     '''    cp -a "$_t/b/etc/pipeos/." "$_t/a/etc/pipeos/" 2>/dev/null || true
    cp "$_t/b/etc/pipeos/card.conf" "$_t/a/etc/pipeos/card.conf" 2>/dev/null || true'''),
    ("F  the dd writes the whole file, bounds gone",
     'skip=$((IMG_START * 512)) count=$((IMG_SIZE * 512)) \\', '\\'),
    ("G  the NO_MOUNT fence is gone",
     'if [ "$NO_MOUNT" = 1 ] && [ -z "$DEV_OVERRIDE" ]; then',
     'if false; then'),
]

src = open(BIN).read()
failed = False
for name, old, new in BREAKS:
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them"
                 % (name[0], src.count(old)))
    fd, path = tempfile.mkstemp(prefix="ckfl-ctl-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True,
                       env=dict(os.environ, CHECK_FLASH_BIN=path))
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
