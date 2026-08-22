#!/usr/bin/env python3
"""check-setup-nick.py — the box's own NICK is derived at setup (pipeOS#134).

WHY THIS GATE EXISTS. NICK used to be baked into the shipped card at build
time, which meant the "downloadable" image was one named customer's box and
every new business needed a bespoke build. Setup now reads the nick back from
`pipe status` — from the thing that actually knows which identity the sign-in
key produced — and writes it to the card.

The bug this must never allow back is not "NICK is wrong". It is "the card and
the daemon disagree", because that is silent: the box runs, answers, and simply
cannot tell a message about itself from one about a sibling. So the probe
asserts the DERIVATION, not the presence of a prompt.

Same slice-the-shipped-file discipline as check-agent-timeout.py: the block
under test is cut out of the real pipebox-setup and run under /bin/sh against
stubbed `pipe`/`pipebox-card`, and there is a CONTROL run with the block
removed — a probe that passes with the feature deleted has tested nothing.

Usage: python3 scripts/check-setup-nick.py
Exit 0 if every check passes.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SETUP = os.path.join(HERE, "..", "overlay", "usr", "local", "bin", "pipebox-setup")

START = "# 1b. the box's OWN nick"
END = "# 2. owner-approved settings"

failures = []


def slice_block(text):
    """Cut the NICK step out of the shipped script, or die saying so."""
    i = text.find(START)
    j = text.find(END)
    if i < 0 or j < 0 or j <= i:
        sys.exit("check-setup-nick: cannot find the NICK block in pipebox-setup "
                 "— it was renamed or removed; this probe now covers nothing")
    return text[i:j]


HARNESS = r"""
CARD="$PWD/card.conf"
say() { printf '%s\n' "$*"; }
die() { printf 'pipebox-setup: %s\n' "$*" >&2; exit 1; }
PATH="$PWD/stub:$PATH"
"""


def run(block, nick, card_nick, with_block=True):
    """Run the sliced block with `pipe status` reporting `nick`."""
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "stub")
        os.mkdir(stub)
        # `pipe status` prints what a signed-in (or anon) daemon reports.
        with open(os.path.join(stub, "pipe"), "w") as f:
            f.write("#!/bin/sh\n[ \"$1\" = status ] && echo 'nick: %s' && exit 0\nexit 0\n" % nick)
        # pipebox-card records that regeneration was attempted.
        with open(os.path.join(stub, "pipebox-card"), "w") as f:
            f.write("#!/bin/sh\ntouch \"$PWD/generated\"\nexit 0\n")
        for p in ("pipe", "pipebox-card"):
            os.chmod(os.path.join(stub, p), 0o755)
        with open(os.path.join(d, "card.conf"), "w") as f:
            f.write("NICK=%s\nROLE=GENERIC\nOWNER_NICK=\n" % card_nick)
        script = HARNESS + (block if with_block else "")
        proc = subprocess.run(["/bin/sh", "-c", script], cwd=d,
                              capture_output=True, text=True)
        card = open(os.path.join(d, "card.conf")).read()
        got = re.search(r"^NICK=(.*)$", card, re.M).group(1).strip()
        return proc.returncode, got, os.path.exists(os.path.join(d, "generated"))


def check(label, cond, detail=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        failures.append(label)


def main():
    block = slice_block(open(SETUP).read())

    print("derivation:")
    rc, nick, gen = run(block, "acme0", "")
    check("signed in as acme0 -> card NICK=acme0", nick == "acme0" and rc == 0,
          "(rc=%s nick=%r)" % (rc, nick))
    check("...and the card was regenerated", gen)

    rc, nick, gen = run(block, "acme0", "acme0")
    check("already correct -> card unchanged", nick == "acme0" and rc == 0,
          "(nick=%r)" % nick)
    check("...and NO pointless regeneration", not gen)

    rc, nick, gen = run(block, "acme0", "oldname")
    check("stale card corrected to the live nick", nick == "acme0",
          "(nick=%r)" % nick)

    print("refusals:")
    rc, nick, gen = run(block, "anon", "")
    check("anon (not signed in) -> NICK left empty", nick == "" and rc == 0,
          "(rc=%s nick=%r)" % (rc, nick))
    check("...and no regeneration on an unprovisioned box", not gen)

    rc, nick, gen = run(block, "bad;nick", "")
    check("a nick that is not a plain nick is REFUSED", rc != 0,
          "(rc=%s)" % rc)
    check("...and the card is not written", nick == "", "(nick=%r)" % nick)

    # A probe that passes with the feature deleted has tested nothing.
    print("control (block removed — these MUST fail to derive):")
    rc, nick, gen = run(block, "acme0", "", with_block=False)
    check("without the block, NICK stays empty", nick == "",
          "(nick=%r — the probe is not measuring the block)" % nick)

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("check-setup-nick: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
