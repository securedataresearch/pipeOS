#!/usr/bin/env python3
"""check-overlay-freshness-controls.py — can check-overlay-freshness.py fail?

Every row in that probe exists because of a defect box1 found by running the
code, not by reading it, so the question "would the probe have caught it" has
an exact answer available: put each defect back.

A, B and C are literally the pre-review code. If a control stops failing, the
gate has stopped covering the bug it was written for. D is the one defect
nobody hit — a silent pass where the box has no deploy record — included
because "absence is a finding" is the whole premise of section 5cc and an
assertion nothing tests is not one.

Each break must fail a DIFFERENT set of rows: A and B are the SAME one-line
defect in two files, and if they failed the same rows one of the two files
would not actually be under test. That is the pairing this fleet keeps getting
wrong — a fix applied to one consumer and not the other.

Run and reverted; the working tree is unchanged on exit, including on error.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIPEOS = os.path.join(HERE, "..", "overlay/usr/local/bin/pipeos")
SELFCHECK = os.path.join(HERE, "..", "overlay/usr/local/bin/pipeos-selfcheck")
PROBE = os.path.join(HERE, "check-overlay-freshness.py")

BREAKS = [
    ("A  pipeos: drop the is-ancestor check (the pre-review code)", PIPEOS,
     '    if ! git -C "$REPO" merge-base --is-ancestor "$_c" "$_tip" 2>/dev/null; then',
     '    if false; then'),
    ("B  selfcheck: drop the is-ancestor check (same defect, other file)", SELFCHECK,
     '        if ! git -C "$_ov_repo" merge-base --is-ancestor "$_ov_c" "$_ov_tip" 2>/dev/null; then',
     '        if false; then'),
    ("C  selfcheck: a stale overlay gates known-good promotion again", SELFCHECK,
     '                noten "overlay is stale:',
     '                crit "overlay is stale:'),
    ("D  selfcheck: no deploy record reads as a pass", SELFCHECK,
     '        noten "no overlay deploy record',
     '        : "no overlay deploy record'),
]

originals = {p: open(p).read() for p in {b[1] for b in BREAKS}}
failed_overall = False
seen_rowsets = {}
try:
    for name, path, old, new in BREAKS:
        src = originals[path]
        if src.count(old) != 1:
            sys.exit("control %s: anchor appears %d times in %s — the file "
                     "changed shape, fix the controls before trusting them"
                     % (name[0], src.count(old), os.path.basename(path)))
        open(path, "w").write(src.replace(old, new, 1))
        p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
        fails = [l[5:].split("  [")[0].strip() for l in p.stdout.splitlines()
                 if l.startswith("FAIL")]
        print("%s\n   -> %d row(s) fail" % (name, len(fails)))
        for f in fails:
            print("      %s" % f)
        if not fails:
            print("      *** BREAK NOT DETECTED — the gate does not cover this ***")
            failed_overall = True
        key = tuple(sorted(fails))
        if key and key in seen_rowsets:
            print("      *** same rows as control %s — one of the two is not "
                  "independently covered ***" % seen_rowsets[key])
            failed_overall = True
        elif key:
            seen_rowsets[key] = name[0]
        print()
        open(path, "w").write(src)
finally:
    for p, src in originals.items():
        open(p, "w").write(src)

p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
print("reverted tree: %s" % (p.stdout.strip().splitlines() or ["<no output>"])[-1])
if p.returncode != 0:
    print("*** revert failed — working tree may be dirty, check git status ***")
    failed_overall = True

sys.exit(1 if failed_overall else 0)
