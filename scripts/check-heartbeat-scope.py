#!/usr/bin/env python3
"""check-heartbeat-scope.py — the fleet work-heartbeat stays off customer boxes (#134).

THE DEFECT THIS LOCKS OUT, measured before the fix: work_pending wakes an idle
box so it goes and drains fleet work — open PRs in REPOS, Ready cards on the
kanban. On a GENERIC box all of those are empty, and the `held = 0` arm reads
that emptiness as "idle, therefore go find work". So it returned true on EVERY
heartbeat, and a customer box that had set up a team board woke a full agent
session every WORK_HEARTBEAT seconds (1800 default, ~48/day) to drain a queue
it has no part in — on that customer's Claude plan.

Note the shape: nothing was broken, no error was logged, and the box worked.
The cost was invisible from the box and landed on someone else's bill. That is
why this is a gate and not a comment.

Also asserts the vendor's GitHub org is not hardcoded anywhere in the watcher.
It was reachable on customer machines and was "safe" only because a stranger's
`gh` is unauthenticated — a missing credential is not a fence.

Usage: python3 scripts/check-heartbeat-scope.py
Exit 0 if every check passes.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "..", "overlay", "usr", "local", "bin",
                     "pipebox-cohort-watch")

START = "_hb_applies=yes"
END = 'HEARTBEAT=""'

failures = []


def check(label, cond, detail=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        failures.append(label)


def gate(src, role, repos, gh_owner):
    """Run the sliced gate with a given card shape; return _hb_applies."""
    script = (
        'ROLE=%s\nREPOS=%s\nGH_OWNER=%s\n' % (role, repos, gh_owner)
        + src
        + '\nprintf "%s" "$_hb_applies"\n'
    )
    return subprocess.run(["/bin/sh", "-c", script],
                          capture_output=True, text=True).stdout.strip()


def main():
    text = open(WATCH).read()
    i, j = text.find(START), text.find(END)
    if i < 0 or j < 0 or j <= i:
        sys.exit("check-heartbeat-scope: cannot find the gate in "
                 "pipebox-cohort-watch — renamed or removed; this probe now "
                 "covers nothing")
    src = text[i:j]

    print("the gate:")
    check("GENERIC + nothing to drain -> heartbeat OFF",
          gate(src, "GENERIC", "", "") == "no")
    check("GENERIC even if a repo is somehow set -> OFF",
          gate(src, "GENERIC", "pipe", "someorg") == "no",
          "(the role is a declaration; honour it)")
    check("unprovisioned box (no role, no repos) -> OFF",
          gate(src, "", "", "") == "no")

    print("fleet boxes must be unaffected:")
    check("BUILD with repos + org -> heartbeat ON",
          gate(src, "BUILD", "pipe,pipeOS", "someorg") == "yes")
    check("TEST with an org but no repos -> ON",
          gate(src, "TEST", "", "someorg") == "yes",
          "(kanban alone is still fleet work)")
    check("SHIP with repos but no org -> ON",
          gate(src, "SHIP", "pipe", "") == "yes")

    print("control (gate deleted — GENERIC must then wrongly apply):")
    # With the gate gone, _hb_applies is never set to no; emulate the
    # pre-fix state to prove the assertions above are measuring the gate.
    bare = '_hb_applies=yes\n'
    check("without the gate, GENERIC would run the heartbeat",
          gate(bare, "GENERIC", "", "") == "yes",
          "(probe is not measuring the gate)")

    print("the vendor's org never reaches a customer box:")
    code = [ln for ln in text.splitlines()
            if "securedataresearch" in ln and not ln.lstrip().startswith("#")]
    # The fleet's STANDING ORDERS legitimately name the vendor's repos — that
    # text is transcribed from the deployed watcher and must not be edited.
    # What matters is that a GENERIC box never receives it, so assert the
    # BRANCH, not the absence of the string.
    check("GENERIC gets its own standing orders, before the fleet block",
          re.search(r'elif \[ "\$\{ROLE:-\}" = GENERIC \]; then', text) is not None)
    generic_branch = text.split('= GENERIC ]; then', 1)[-1].split('\nelse\n', 1)[0]
    check("...and that branch names no vendor repo",
          "securedataresearch" not in generic_branch)
    check("...and does not order PR review or issue claiming",
          not re.search(r"gh (pr|issue) list", generic_branch))
    # Locate the transcribed fleet block by position and require every
    # mention of the vendor's org to fall inside it. Filtering by keyword
    # missed continuation lines of the same string, which is how a probe
    # reports clean on text it simply failed to recognise.
    fleet_start = text.index("# STANDING ORDERS \u2014 VERBATIM")
    fleet_end = text.index('\nfi\n', fleet_start)
    outside = [ln for ln in text.splitlines()
               if "securedataresearch" in ln
               and not (fleet_start <= text.index(ln) <= fleet_end)]
    check("every vendor-org mention is inside the fleet standing orders",
          not outside, "(%d outside: %s)" % (len(outside), outside[:1]))
    check("...and the fleet block is unreachable for GENERIC",
          text.index('= GENERIC ]; then') < fleet_start)

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("check-heartbeat-scope: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
