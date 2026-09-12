#!/usr/bin/env python3
"""Controls for check-release-guard.py: break each guard in a copy of
verify-image-generic.sh and assert the probe notices (the house rule since #100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "verify-image-generic.sh")
PROBE = os.path.join(HERE, "check-release-guard.py")

BREAKS = [
    ("A  the authorized_keys check is gone",
     '''&& hit="root/.ssh/authorized_keys''', '''&& true # hit="root/.ssh/authorized_keys'''),
    ("B  a named card is not an operator image any more",
     '''[ -n "$nick" ] && hit=''', '''[ -n "$nick" ] && : # hit='''),
    ("C  an unreadable apkovl passes instead of refusing",
     '''|| { echo "verify-image-generic: $OVL is not a readable tar.gz — refusing" >&2; exit 1; }''',
     '''|| LIST=""'''),
    ("D  an operator image exits 0",
     '''    exit 2
fi''', '''    exit 0
fi'''),
]

src = open(GUARD).read()
bad = 0
for label, old, new in BREAKS:
    if old not in src:
        print("FAIL %s: anchor not found in %s — the control is stale" % (label, os.path.basename(GUARD)))
        bad += 1
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, dir=HERE) as f:
        f.write(src.replace(old, new, 1))
        broken = f.name
    os.chmod(broken, 0o755)
    try:
        p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True,
                           env=dict(os.environ, CHECK_RELEASE_GUARD_BIN=broken))
        if p.returncode == 0:
            print("FAIL %s: probe still passes" % label)
            bad += 1
        else:
            print("ok   %s: probe fails" % label)
    finally:
        os.unlink(broken)
sys.exit(1 if bad else 0)
