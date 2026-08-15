#!/usr/bin/env python3
"""Controls for the pipeOS#88 probe.

A probe that only ever passes has two explanations. Each control below breaks
ONE property of the shipped script and must make a DIFFERENT row fail, for its
own reason — that is what says the rows are wired to the code rather than to
each other. The file is restored after each run.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
P = os.path.join(REPO, "overlay/usr/local/bin/pipebox-gh-notify")
PROBE = os.path.join(HERE, "check-gh-notify.py")
orig = open(P).read()

SLICE_OLD = (
    "        if length <= $n then .\n"
    "        else (.[$n - 1].updatedAt) as $t | [ .[] | select(.updatedAt <= $t) ]\n"
    "        end"
)
CURSOR_OLD = "        printf '%s\\n' \"$last_ts\" > \"$CURSOR\""
CURSOR_NEW = "        printf '%s\\n' \"$now\" > \"$CURSOR\""

controls = [
    ("A: drop the --sort/--order flags",
     lambda s: s.replace("    --sort updated --order asc \\\n", "", 1)),
    ("B: plain [0:n] slice, no tie extension",
     lambda s: s.replace(SLICE_OLD, "        .[0:$n]")),
    ("C: cursor always advances to now",
     lambda s: s.replace(CURSOR_OLD, CURSOR_NEW)),
]

rc = 0
for name, f in controls:
    mut = f(orig)
    if mut == orig:
        print(f"--- {name}: CONTROL DID NOT APPLY (probe is testing nothing here)")
        rc = 1
        continue
    open(P, "w").write(mut)
    try:
        r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    finally:
        open(P, "w").write(orig)
    fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
    print(f"--- {name}: {len(fails)} row(s) fail")
    for l in fails:
        print("   ", l.rstrip())
    if not fails:
        print("    CONTROL PASSED THE PROBE — that row proves nothing")
        rc = 1

sys.exit(rc)
