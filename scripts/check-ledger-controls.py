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
     "        if pct >= WARN_PCT and not os.path.exists(warned):\n", "        if pct >= WARN_PCT:\n"),
    ("E  the pause is never lifted", LEDGER,
     "        elif os.path.exists(self.paused_path()):\n            os.unlink(self.paused_path())\n            changed = True\n",
     "        elif False:\n            pass\n"),
    ("H  an agent's own cap never pauses it (#302)", LEDGER,
     "            if aspent * 100 / acap >= 100:\n", "            if False:\n"),
    ("I  the members' spend is ignored (the cluster cap is only this box's month, #302)", LEDGER,
     "        return round(sum(r[\"month_usd\"] for r in rows), 4), rows, sorted(set(silent))\n", "        return 0.0, rows, sorted(set(silent))\n"),
    ("L  a note for a box that is no longer a member still counts against the cluster cap (#302)", LEDGER,
     "            if ids is not None and mid not in ids:\n                continue\n", "            if False:\n                continue\n"),
    ("J  a member's note from another month is counted (August's spend against September, #302)", LEDGER,
     '            if d.get("month") != month:\n                continue\n', '            if False:\n                continue\n'),
    ("K  the box pause writes no plain marker (every older reader — runner, tick, selfcheck — keeps running, #302)", LEDGER,
     "        if global_entry:\n            if self.paused_text() != global_entry[\"text\"]:\n",
     "        if False:\n            if self.paused_text() != global_entry[\"text\"]:\n"),
]

sys.path.insert(0, HERE)
import controls_lib  # noqa: E402


def one(brk):
    name, path, old, new = brk
    src = open(path).read()
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, tmp = tempfile.mkstemp(prefix="ckledger-ctl-", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    env = dict(os.environ, CHECK_LEDGER_BIN=tmp)
    p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True, env=env)
    os.unlink(tmp)
    return name, [l for l in p.stdout.splitlines() if l.startswith("FAIL")]


failed = False
for name, fails in controls_lib.pmap(one, BREAKS):
    print("%s\n   -> %d row(s) fail" % (name, len(fails)))
    for l in fails:
        print("      " + l[5:].split("  [")[0])
    if not fails:
        failed = True
        print("   !! the probe did not notice")
p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
print("intact tree: " + p.stdout.strip().splitlines()[-1])
sys.exit(1 if failed or p.returncode else 0)
