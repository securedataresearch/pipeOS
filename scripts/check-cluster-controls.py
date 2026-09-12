#!/usr/bin/env python3
"""Controls for check-cluster.py (pipeOS#222).

Each control breaks ONE check in the shipped cluster.py or webd.py and must
make a DIFFERENT row fail, for its own reason — a probe over an auth
primitive that cannot see the primitive lose a check is worse than none.
The shipped files are restored after every run.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
C = os.path.join(WEB, "cluster.py")
W = os.path.join(WEB, "webd.py")
PROBE = os.path.join(HERE, "check-cluster.py")
orig = {p: open(p).read() for p in (C, W)}

controls = [
    ("A: the skew check is gone (any timestamp is accepted)", C,
     lambda s: s.replace("    if skew > SKEW_S:\n", "    if False:\n"), ["6"]),

    ("B: the replay set is gone (a signature can be presented forever)", C,
     lambda s: s.replace("    if replay and _replay_seen(sig, ts):\n", "    if False:\n"), ["5"]),

    ("C: the signature is not verified (any base64 passes)", C,
     lambda s: s.replace("    if not verify_sig(m[\"pub\"], canonical(box_id, ts, nonce, method, path, body), sig):\n",
                         "    if False:\n"), ["7", "8"]),

    ("D: the body is not in the canonical string (a tampered body verifies)", C,
     lambda s: s.replace("                      hashlib.sha256(body).hexdigest()]).encode()",
                         "                      \"\"]).encode()"), ["7"]),

    ("E: the member list is not consulted (any key that verifies against ANY member passes as that id)", C,
     lambda s: s.replace("    m = mem.get(box_id)\n    if not m or not m.get(\"pub\"):\n",
                         "    m = mem.get(box_id) or next(iter(mem.values()), None)\n    if not m or not m.get(\"pub\"):\n"), ["3", "10"]),

    ("F: a failed signature falls back to the cookie (the stranger gets 'sign in first', not the reason)", W,
     lambda s: s.replace("                self._peer_reason = why\n                return None\n",
                         "                return valid_session(self.cookie_token())\n"), ["3"]),

    ("G: the answer is not signed (a caller cannot tell a member's reply from anything else on the port)", W,
     lambda s: s.replace("        if getattr(self, \"_peer\", None):\n            try:\n                for k, v in cluster.sign_headers",
                         "        if False:\n            try:\n                for k, v in cluster.sign_headers"), ["4", "9"]),
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
