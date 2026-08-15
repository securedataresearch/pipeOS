#!/usr/bin/env python3
"""check-worksweep-controls.py — does check-worksweep.py actually discriminate?

A gate that passes is only evidence if it can fail. This breaks the shipped
pipeos-worksweep four ways, one at a time, runs check-worksweep.py against
each break, and reverts. Each break must fail a DIFFERENT check for its own
reason — a break that fails everything, or nothing, means the gate is not
measuring what it claims.

A break may also declare checks that must STILL PASS under it. That is not
decoration: control D widens the allowlist so the protected list is the only
thing left holding, and "the survivor rows still pass" is the entire claim
being tested. Without the must-still-pass assertion the control would be
satisfied by a script that deleted everything.

Run and reverted; the working tree is unchanged on exit, including on error.

Usage: python3 scripts/check-worksweep-controls.py
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP = os.path.join(HERE, "..", "overlay", "etc", "periodic", "weekly",
                     "pipeos-worksweep")
PROBE = os.path.join(HERE, "check-worksweep.py")

BREAKS = [
    ("A  drop the under-threshold early exit",
     'if [ "$force" = 0 ] && [ "${use:-0}" -lt "$sweep_pct" ]; then\n    exit 0\nfi',
     'if false; then\n    exit 0\nfi'),
    ("B  drop the symlink guard in reclaim",
     '    if [ -L "$p" ]; then\n        log "  skip (symlink) $p"\n        return 0\n    fi',
     '    if false; then :; fi'),
    ("C  sweep out/ wholesale instead of entry by entry",
     '        case "$(basename "$e")" in\n            keys) continue ;;\n        esac',
     '        :'),
    # D is the one box1 asked for on review. Every survivor row except
    # out/keys was VACUOUS: no tier ever nominates /work/claude or
    # /work/backup, so those rows passed for any implementation of
    # protected() — delete its body and they still pass. This widens the
    # allowlist (the last tier iterates /work/* instead of naming one path)
    # so the protected list is the ONLY thing standing between the sweep and
    # agent memory, the credential backups and the signing key. It must
    # refuse each of them by name, which trips `refused nothing` — and the
    # survivor rows must still pass, because that is the defence in depth
    # this script's header claims. /work/repos is skipped so the seam under
    # test is the protected list alone and not the tier order.
    ("D  widen the allowlist: last tier iterates /work/* by name",
     'done_enough || reclaim /work/cargo-target "shared artifact cache"',
     'for e in /work/*; do\n'
     '    case "$e" in /work/repos) continue ;; esac\n'
     '    done_enough && break\n'
     '    [ -e "$e" ] || continue\n'
     '    reclaim "$e" "shared artifact cache"\n'
     'done',
     ["NOTHING non-regenerable was touched",
      "agent memory survives",
      "every protected path intact"]),
]

# Normalise: (name, old, new) or (name, old, new, must_still_pass).
BREAKS = [b if len(b) == 4 else (*b, []) for b in BREAKS]

original = open(SWEEP).read()
failed_overall = False
try:
    for name, old, new, must_pass in BREAKS:
        if old not in original:
            sys.exit("control %s: anchor not found — the script changed shape, "
                     "fix the controls before trusting them" % name[0])
        open(SWEEP, "w").write(original.replace(old, new, 1))
        p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
        fails = [l.strip()[5:].strip() for l in p.stdout.splitlines()
                 if l.strip().startswith("FAIL")]
        print("%s\n   -> %d check(s) fail" % (name, len(fails)))
        for f in fails:
            print("      %s" % f.split("  [")[0])
        if not fails:
            print("      *** BREAK NOT DETECTED — the gate does not cover this ***")
            failed_overall = True
        for want in must_pass:
            # The row has to exist, not merely be absent from the fail list:
            # a check that stopped running would otherwise read as passing.
            ran = any(want in l for l in p.stdout.splitlines())
            broke = any(want in f for f in fails)
            if not ran:
                print("      *** '%s' did not run under this break ***" % want)
                failed_overall = True
            elif broke:
                print("      *** '%s' must STILL PASS under this break ***" % want)
                failed_overall = True
        if must_pass and not failed_overall:
            print("      and %d row(s) still pass, as required" % len(must_pass))
        print()
finally:
    open(SWEEP, "w").write(original)

# Sanity: unbroken tree must still pass, or the revert did not work.
p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
print("reverted tree: %s" % p.stdout.strip().splitlines()[-1])
if p.returncode != 0:
    print("*** revert failed — working tree may be dirty, check git status ***")
    failed_overall = True

sys.exit(1 if failed_overall else 0)
