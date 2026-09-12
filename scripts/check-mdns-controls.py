#!/usr/bin/env python3
"""Controls for check-mdns.py: put each rule back to broken in a copy of
mdnsd.py (or lanid.py) and assert the probe notices (the house rule since
#100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
MDNSD = os.path.join(WEB, "mdnsd.py")
LANID = os.path.join(WEB, "lanid.py")
PROBE = os.path.join(HERE, "check-mdns.py")

BREAKS = [
    ("A  a Machine no longer excludes itself", MDNSD,
     '        if pid == state["ident"]["id"]:\n            continue\n', ''),
    ("B  expiry is gone", MDNSD,
     '    gone = [p for p, e in state["peers"].items() if now - e["last_seen"] > EXPIRE]', '    gone = []'),
    ("C  the owner's name is never answered as an alias", MDNSD,
     '    if ident["name"]:\n        names.add(ident["name"] + ".local")', '    if False:\n        names.add(ident["name"] + ".local")'),
    ("D  the TXT claimed flag is inverted", MDNSD,
     '"c": "1" if ident["claimed"] else "0"', '"c": "0" if ident["claimed"] else "1"'),
    ("E  goodbye announces TTL 120", MDNSD,
     '    send(sock, lanid.build_response(our_records(ident, ip, ttl=0)))', '    send(sock, lanid.build_response(our_records(ident, ip)))'),
    ("F  compression pointers are not followed", LANID,
     '        if n & 0xC0 == 0xC0:', '        if False:'),
    ("G  the roster is never written (#241)", MDNSD,
     '                roster_upsert(state)', '                pass'),
    ("H  the TXT carries no MAC (#241)", MDNSD,
     '            "mac": ident.get("mac", ""),', '            "mac": "",'),
]

failed = False
for name, path, old, new in BREAKS:
    src = open(path).read()
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, tmp = tempfile.mkstemp(prefix="ckmd-ctl-", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    env = dict(os.environ, **({"CHECK_MDNS_BIN": tmp} if path == MDNSD else {"CHECK_LANID": tmp}))
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
