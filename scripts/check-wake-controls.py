#!/usr/bin/env python3
"""Controls for check-wake.py: put each rule back to broken in a copy of
wake.py or pipeos-wol and assert the probe notices (the house rule since
#100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WAKE = os.path.join(REPO, "overlay/usr/local/share/pipeos/web/wake.py")
WOL = os.path.join(REPO, "overlay/etc/init.d/pipeos-wol")
PROBE = os.path.join(HERE, "check-wake.py")

BREAKS = [
    ("A  the packet header is five 0xff, not six", WAKE,
     '    return b"\\xff" * 6 + raw * 16', '    return b"\\xff" * 5 + raw * 16'),
    ("B  an unknown name wakes nothing but exits 0", WAKE,
     '        print("%s: not a Machine this box has ever seen (pipeos wake --list)" % argv[0], file=sys.stderr)\n        return 2',
     '        return 0'),
    ("C  this box wakes itself", WAKE,
     '    if pid == me:\n        print("%s is this Machine — it is awake" % argv[0], file=sys.stderr)\n        return 2\n    return 0 if wake_one(pid, r) else 1',
     '    return 0 if wake_one(pid, r) else 1'),
    ("D  --all includes this box", WAKE,
     '            if pid == me:\n                continue\n            n += 1', '            n += 1'),
    ("E  a MAC-less Machine gets a packet of zeros", WAKE,
     '    if not MAC_RE.match(mac):', '    if False:'),
    ("F  WOL=off still arms", WOL,
     '		"$_ethtool" -s "$_if" wol d >/dev/null 2>&1', '		"$_ethtool" -s "$_if" wol g >/dev/null 2>&1'),
    ("G  the down interface is preferred", WOL,
     '		_up=1; [ "$(cat "$_d/operstate" 2>/dev/null)" = up ] && _up=0', '		_up=0; [ "$(cat "$_d/operstate" 2>/dev/null)" = up ] && _up=1'),
]

failed = False
for name, path, old, new in BREAKS:
    src = open(path).read()
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, tmp = tempfile.mkstemp(prefix="ckwake-ctl-")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    env = dict(os.environ, **({"CHECK_WAKE_BIN": tmp} if path == WAKE else {"CHECK_WOL_BIN": tmp}))
    p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True, env=env)
    os.unlink(tmp)
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
