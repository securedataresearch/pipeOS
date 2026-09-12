#!/usr/bin/env python3
"""Controls for check-tls-public.py (pipeOS#286).

Each control breaks ONE property of the shipped files and must make a
DIFFERENT row fail, for its own reason. The shipped files are restored
after every run.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
W = os.path.join(WEB, "webd.py")
S = os.path.join(REPO, "overlay/usr/local/bin/pipeos-tls-public")
H = os.path.join(REPO, "overlay/usr/local/share/pipeos/acme/dns_pipe.sh")
PROBE = os.path.join(HERE, "check-tls-public.py")
orig = {p: open(p).read() for p in (W, S, H)}

controls = [
    ("A: the public name is never given its certificate (no SNI split — every name gets the box CA's)", W,
     lambda s: s.replace("    ctx.sni_callback = pick\n", "\n"), ["6"]),

    ("B: the redirect has no exemptions (the lobby's JSON and the CA download redirect too)", W,
     lambda s: s.replace('        if any(path == e or path.startswith(e + "/") for e in self.REDIRECT_EXEMPT):\n            return False\n', "\n"), ["7"]),

    ("C: the redirect ignores a resolver that refuses the name (the owner is sent to an address the LAN cannot reach)", W,
     lambda s: s.replace('        if public_status().get("resolves") == "no":\n            return False\n', "\n"), ["7b"]),

    ("D: the hook signs under the user-write domain tag (the relay must refuse a Machine's signature made as a user's)", H,
     lambda s: s.replace('"pipe-machine-v1"', '"pipe-bbs-v1"'), ["1"]),

    ("E: acme.sh is asked without the ACME server from the conf (staging drills would hit production)", S,
     lambda s: s.replace(' --server "$PUBLIC_ACME" ', ' '), ["1"]),

    ("F: the machine key lands with everyone's read bit (the vault's file mode is the fence)", S,
     lambda s: s.replace('    [ -s "$KEY" ] || die "the vault exported nothing at $KEY"\n', '    [ -s "$KEY" ] || die "the vault exported nothing at $KEY"\n    chmod 644 "$KEY"\n'), ["1"]),

    ("G: a renewed certificate on disk is not noticed (the listener keeps the old one until a restart)", W,
     lambda s: s.replace("    for p in (cluster.BUNDLE, PUB_CRT, PUB_KEY, PUBLIC_CONF):", "    for p in (cluster.BUNDLE, PUBLIC_CONF):"), ["8"]),
]

rc = 0
for name, path, f, must in controls:
    mut = f(orig[path])
    if mut == orig[path]:
        print("--- %s: CONTROL DID NOT APPLY" % name)
        rc = 1
        continue
    open(path, "w").write(mut)
    try:
        r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    finally:
        open(path, "w").write(orig[path])
    fails = [l.split()[1] for l in r.stdout.splitlines() if l.startswith("FAIL")]
    missing = [m for m in must if m not in fails]
    print("--- %s: rows %s fail%s" % (name, fails or "(none)", "" if not missing else "  — EXPECTED %s TO FAIL" % missing))
    if missing:
        rc = 1
sys.exit(rc)
