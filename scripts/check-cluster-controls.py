#!/usr/bin/env python3
"""Controls for check-cluster.py (pipeOS#222, #211).

Each control breaks ONE property of the shipped cluster.py or webd.py and
must make a DIFFERENT row fail, for its own reason. The shipped files are
restored after every run.
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
    ("A: the listener never asks for a client certificate (no member is ever admitted)", C,
     lambda s: s.replace("        ctx.verify_mode = ssl.CERT_OPTIONAL\n", "        ctx.verify_mode = ssl.CERT_NONE\n"), ["3"]),

    ("B: the listener is not restarted when the list changes (a removed member's certificate keeps working)", W,
     lambda s: s.replace("cluster.ON_CHANGE.append(lambda: HTTPS.get(\"server\") is not None and start_https(init=False))\n", "\n")
                .replace("        if HTTPS.get(\"server\") is not None and cur != HTTPS.get(\"bundle\"):\n", "        if False:\n"), ["4"]),

    ("C: the member is read off the certificate's position in the list, not off which CA signed it (any member's cert is attributed to the first member)", C,
     lambda s: s.replace("                if p.returncode == 0:\n                    who = mid\n                    break\n",
                         "                who = mid\n                break\n"), ["3"]),

    ("D: the join does not check the password", W,
     lambda s: s.replace("        if u is None or not check_hash(body.get(\"password\") or \"\", u.get(\"hash\")):\n            time.sleep(2)\n            return self.err(403, \"wrong password for this Machine\")\n", "\n"), ["3"]),

    ("E: the caller does not verify the answering certificate (any server on that port is 'a member')", C,
     lambda s: s.replace("        ctx.load_verify_locations(cafile=cafile)\n        ctx.verify_mode = ssl.CERT_REQUIRED\n",
                         "        ctx.verify_mode = ssl.CERT_NONE\n"), ["2"]),

    ("F: the reader does not drop a member seen in another cluster", C,
     lambda s: s.replace("        if pid in d[\"members\"] and pid != self_id() and p.get(\"cl\") and p[\"cl\"] != d[\"id\"]:\n",
                         "        if False:\n"), ["9"]),
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
