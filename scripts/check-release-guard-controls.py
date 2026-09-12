#!/usr/bin/env python3
"""Controls for check-release-guard.py: break each guard in a copy of
verify-image-generic.sh and assert the probe notices (the house rule since
#100). The copy lives in the system tmpdir and finds config.sh through the
guard's PIPEOS_CONFIG seam."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GUARD = os.path.join(HERE, "verify-image-generic.sh")
PROBE = os.path.join(HERE, "check-release-guard.py")

BREAKS = [
    ("A  the authorized_keys check is gone",
     '''if has '\\.?/?root/\\.ssh/authorized_keys'; then''', '''if false; then'''),
    ("B  a root password is not an operator image any more",
     '''        *) hit="etc/shadow: root has a password hash (ROOT_LOGIN=password build)" ;;''',
     '''        *) : ;;'''),
    ("C  a named card is not an operator image any more",
     '''    [ -n "$nick" ] && hit=''', '''    [ -n "$nick" ] && : # hit='''),
    ("D  an unreadable apkovl passes instead of refusing",
     '''LIST=$(tar -tzf "$OVL" 2>/dev/null) || cannot "$OVL is not a readable tar.gz"''',
     '''LIST=$(tar -tzf "$OVL" 2>/dev/null) || LIST=x'''),
    ("E  an operator image exits 0",
     '''    exit 2
fi''', '''    exit 0
fi'''),
    ("F  the listing goes back through a pipe (fail-open once it outgrows the buffer)",
     '''    grep -qxE "$1" <<<"$LIST" || rc=$?''',
     '''    echo "$LIST" | grep -qxE "$1" || rc=$?'''),
]

src = open(GUARD).read()
failed = False
for name, old, new in BREAKS:
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, path = tempfile.mkstemp(prefix="ckrg-ctl-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    os.chmod(path, 0o755)
    p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True,
                       env=dict(os.environ, CHECK_RELEASE_GUARD_BIN=path, PIPEOS_CONFIG=os.path.join(REPO, "config.sh")))
    os.unlink(path)
    fails = [l for l in p.stdout.splitlines() if l.startswith("FAIL")]
    print("%s\n   -> %d row(s) fail" % (name, len(fails)))
    for l in fails:
        print("      " + l[5:].split("  [")[0])
    if not fails:
        failed = True
        print("   !! the probe did not notice")
p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
print("intact tree: " + (p.stdout.strip().splitlines() or ["?"])[-1])
sys.exit(1 if failed or p.returncode else 0)
