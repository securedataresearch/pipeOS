#!/usr/bin/env python3
"""Controls for check-ledger.py: put each rule back to broken in a copy of
ledger.py and assert the probe notices (the house rule since #100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LEDGER = os.path.join(REPO, "overlay/usr/local/share/pipeos/web/ledger.py")
PROBE = os.path.join(HERE, "check-ledger.py")

BREAKS = [
    ("A  the message-id dedupe is gone (every content block counts)", LEDGER,
     "                if not u or not mid or mid in seen:\n                    continue\n", "                if not u or not mid:\n                    continue\n"),
    ("B  cache tokens are free", LEDGER,
     '               + row["cache_w5m"] * r.get("cache_w5m", 0) + row["cache_w1h"] * r.get("cache_w1h", 0)) / 1e6',
     '               ) / 1e6'),
    ("C  the cursor never advances (everything re-ingested each time)", LEDGER,
     '            cursor[path] = {"ino": st.st_ino, "off": off + consumed, "seen": seen[-SEEN_RING:]}',
     '            cursor[path] = {"ino": st.st_ino, "off": 0, "seen": []}'),
    ("D  the 80% DM is sent every time", LEDGER,
     "        if pct >= WARN_PCT and not os.path.exists(warned):", "        if pct >= WARN_PCT:"),
    ("E  the pause is never lifted", LEDGER,
     "            if os.path.exists(self.paused_path()):\n                os.unlink(self.paused_path())\n                state[\"changed\"] = True\n        if pct >= WARN_PCT",
     "            pass\n        if pct >= WARN_PCT"),
    ("F  the torn tail is swallowed as a complete line", LEDGER,
     '            lines = data.split(b"\\n")[:-1]\n', '            lines = data.split(b"\\n")\n'),
    ("G  a job's session is not looked up (everything is other:)", LEDGER,
     '        if sid and sid in jobs:\n            return {"kind": "job", "name": jobs[sid]}\n', ''),
]

failed = False
for name, path, old, new in BREAKS:
    src = open(path).read()
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, tmp = tempfile.mkstemp(prefix="ckledger-ctl-", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    env = dict(os.environ, CHECK_LEDGER_BIN=tmp)
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
