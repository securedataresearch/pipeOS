#!/usr/bin/env python3
"""check-agent-timeout.py — gate for the agent run budget (pipeOS#101).

Two things, and the second is the one that bit:

  1. AGENT_TIMEOUT_MIN resolves to the right number of seconds, including for
     every malformed value a hand-repaired pipebox.conf can carry.
  2. THE RESOLUTION HAPPENS BELOW THE LINE THAT SOURCES pipebox.conf. The
     original defect was not a wrong value, it was a correct value assigned in
     the wrong place: `CLAUDE_TIMEOUT=900` sat below `. /etc/pipeos/pipebox.conf`
     and silently overwrote whatever the conf said. A probe that only checked
     arithmetic would have passed against that bug, because the arithmetic was
     never what was wrong.

Same slice-the-shipped-file discipline as check-cohort-watch.py: the block
under test is cut out of the real script and run under /bin/sh, and the probe
dies loudly rather than quietly covering nothing if it cannot find it.

Usage: python3 scripts/check-agent-timeout.py
Exit 0 if every check passes.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "overlay", "usr", "local", "bin")
TMPL = os.path.join(HERE, "..", "overlay", "usr", "local", "share", "pipeos",
                    "card", "pipebox.conf.tmpl")
CARDS = os.path.join(HERE, "..", "docs", "cards")

# BOTH agent paths, not just the one anyone had been reading. The board path
# (pipebox-cohort-watch) is where #101 was filed; box1 found the identical
# defect unfixed in the DM path (pipebox-listener) while reviewing #106 —
# same bare assignment, same position below the conf source, and with
# AGENT_TIMEOUT_MIN=120 sourced the two resolved 7200 and 1800. One key spans
# both, so the probe must too: "anywhere" was one file wide.
#
# The fallbacks differ on purpose (the DM path's default was always 1800), so
# each path carries its own expected default rather than sharing a constant.
PATHS = [
    ("pipebox-cohort-watch", "900"),
    ("pipebox-listener", "1800"),
]

results = []


def ck(desc, ok, detail=""):
    results.append(ok)
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", desc,
                           ("  [%s]" % detail) if detail and not ok else ""))


SRCS = {name: open(os.path.join(BIN, name)).read() for name, _ in PATHS}


def extract(name):
    """Slice the case block + CLAUDE_TIMEOUT assignment out of a shipped file."""
    m = re.findall(r'^case "\$\{AGENT_TIMEOUT_MIN:-\}" in\n.*?^esac\n'
                   r'CLAUDE_TIMEOUT="\$\{CLAUDE_TIMEOUT:-\$_agent_timeout_s\}"\n',
                   SRCS[name], flags=re.M | re.S)
    if len(m) != 1:
        sys.exit("probe: expected exactly 1 AGENT_TIMEOUT_MIN resolution block "
                 "in %s, found %d — it changed shape, fix the probe before "
                 "trusting it" % (name, len(m)))
    return m[0]


def shells():
    """Every shell present that could run these scripts.

    CI is ubuntu, so `sh` there is dash and busybox is absent; the appliance
    boots busybox. The octal rule is POSIX and both agreed when box0 and box2
    measured it by hand, so this is not expected to diverge — it is here because
    "the gate only ever exercised the CI shell" is exactly the shape of hole
    #100 was filed over, and the cost of closing it is one subprocess.
    """
    found = [("sh", ["sh"])]
    bb = shutil.which("busybox")
    if bb:
        found.append(("busybox sh", [bb, "sh"]))
    return found


def resolve(block, conf_value=None, env_value=None, argv=("sh",)):
    """Run the block the way the watcher does: conf sourced first, then this."""
    conf = ""
    if conf_value is not None:
        conf = 'AGENT_TIMEOUT_MIN="%s"\n' % conf_value
    script = conf + block + 'echo "$CLAUDE_TIMEOUT"\n'
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        env = dict(os.environ)
        env.pop("CLAUDE_TIMEOUT", None)
        if env_value is not None:
            env["CLAUDE_TIMEOUT"] = env_value
        p = subprocess.run(list(argv) + [path], capture_output=True, text=True,
                           env=env)
        return p.stdout.strip(), p.stderr.strip(), p.returncode
    finally:
        os.unlink(path)


# conf value, env CLAUDE_TIMEOUT, expected seconds, description.
# DEF means "this path's own fallback" — 900 on the board path, 1800 on the DM
# path — so the same row table runs against both files.
DEF = object()
ROWS = [
    ("30",   None, "1800", "30 minutes -> 1800s"),
    ("15",   None, "900",  "15 minutes -> 900s"),
    ("5",    None, "300",  "the generator floor -> 300s"),
    ("120",  None, "7200", "the generator ceiling -> 7200s"),
    ("",     None, DEF,    "EMPTY (unprovisioned or older card) -> default"),
    (None,   None, DEF,    "key absent entirely -> default"),
    ("0",    None, DEF,    "0 -> default, NOT `timeout 0` killing every run"),
    ("abc",  None, DEF,    "non-numeric -> default, no arithmetic error"),
    ("-5",   None, DEF,    "negative -> default (the `-` fails the digits test)"),
    ("1 800", None, DEF,   "embedded space -> default, not a split word"),
    # THE OCTAL ROWS (#106). Every one of these is digits-only, so the lexical
    # arm passes it straight to `$(( ))`, which reads a leading zero as octal.
    # `030` is the quiet one and the row that matters most: before the fix it
    # resolved to 1440 — 24 minutes — while the conf on disk said 030 and no log
    # line anywhere said the operator's number had been reinterpreted.
    ("030",  None, DEF,    "030 -> default, NOT 1440s (octal 030 is 24, not 30)"),
    ("007",  None, DEF,    "007 -> default, NOT 420s"),
    ("05",   None, DEF,    "05 -> default (right answer, wrong road: never $(( ))"),
    # `08`/`09` are not valid octal AT ALL. Pre-fix these were an arithmetic
    # syntax error, and with no `set -e` the variable stayed unset, CLAUDE_TIMEOUT
    # resolved EMPTY, and `timeout "" claude` returned 125/1 — outside the
    # 124|143 arm, so the generic branch advanced the cursor and the agent never
    # ran, every tick, forever. rc and stderr are asserted clean for exactly that
    # reason: an empty result with a nonzero rc is the failure, not a near-miss.
    ("08",   None, DEF,    "08 -> default, not an arithmetic syntax error"),
    ("09",   None, DEF,    "09 -> default, not an arithmetic syntax error"),
    # The runtime half of the range. The generator refuses these, but a conf is
    # a file a box can be hand-repaired into, and 100000 minutes is one wake
    # holding the flock for ten weeks.
    ("100000", None, DEF,  "far above the ceiling -> default, not a 10-week run"),
    ("1",    None, DEF,    "below the 5-minute floor -> default, not 60s"),
    ("30",  "60",  "60",   "explicit CLAUDE_TIMEOUT in env beats the card"),
    ("",    "60",  "60",   "env wins over the fallback too"),
]


def main():
    for name, default in PATHS:
        block = extract(name)
        src = SRCS[name]

        for shname, argv in shells():
            print("AGENT_TIMEOUT_MIN resolution — %s under %s" % (name, shname))
            for conf, env, want, desc in ROWS:
                want = default if want is DEF else want
                got, err, rc = resolve(block, conf, env, argv)
                ck(desc, got == want and rc == 0 and err == "",
                   "got %r rc=%s stderr=%r" % (got, rc, err))

        # THE ORDERING PROPERTY — the actual bug. Both the conf source line and
        # the resolution must exist, and the resolution must come SECOND. A
        # correct value assigned above the source line is silently overwritten,
        # which is what shipped, and no amount of arithmetic testing sees it.
        print("\nordering: the conf is sourced BEFORE the timeout is resolved"
              " — %s" % name)
        src_line = src.find(". /etc/pipeos/pipebox.conf")
        resolve_at = src.find('case "${AGENT_TIMEOUT_MIN:-}" in')
        use_at = src.find('timeout "$CLAUDE_TIMEOUT"')
        ck("pipebox.conf is sourced", src_line >= 0)
        ck("the timeout is resolved after the conf is sourced",
           resolve_at > src_line >= 0,
           "source@%d resolve@%d" % (src_line, resolve_at))
        ck("the timeout is used after it is resolved",
           use_at > resolve_at >= 0, "resolve@%d use@%d" % (resolve_at, use_at))
        ck("no bare `CLAUDE_TIMEOUT=` assignment survives",
           re.search(r'^CLAUDE_TIMEOUT=(?!"\$\{CLAUDE_TIMEOUT:-)', src,
                     flags=re.M) is None,
           "an unconditional assignment would overwrite the conf again")

        # A cut-off run must not be logged as a failure. Both spellings, because
        # both were observed: 124 is timeout killing the child, 143 is the
        # child's own SIGTERM status propagated -- and 143 is the one netgaze
        # displayed as FAILED on three boxes.
        print("\na cut-off run is reported as cut off, not failed — %s" % name)
        ck("rc=124|143 is handled distinctly",
           re.search(r'^\s*124\|143\)', src, flags=re.M) is not None)
        m = re.search(r'124\|143\)\s*\n?\s*log "([^"]*)"', src)
        ck("the cut-off log line says CUT OFF and names the budget",
           m is not None and "CUT OFF" in m.group(1)
           and re.search(r"\$\{?CLAUDE_TIMEOUT\}?", m.group(1)) is not None,
           "log line: %s" % (m.group(1) if m else None))
        print("")

    # The card is the declared home for the value, so the template must carry
    # the placeholder and every fleet card must declare it -- a card that does
    # not is a box silently back on the default.
    print("\nthe card renders it, and every fleet card declares it")
    ck("pipebox.conf.tmpl carries the placeholder",
       "@@AGENT_TIMEOUT_MIN@@" in open(TMPL).read())
    for name in sorted(os.listdir(CARDS)):
        if not name.endswith(".card"):
            continue
        text = open(os.path.join(CARDS, name)).read()
        m = re.search(r"^AGENT_TIMEOUT_MIN=(\d+)$", text, flags=re.M)
        role = re.search(r"^ROLE=(\w+)$", text, flags=re.M)
        want = 30 if role and role.group(1) in ("BUILD", "GENERIC") else 15
        ck("%s (%s) declares AGENT_TIMEOUT_MIN=%d"
           % (name, role.group(1) if role else "?", want),
           m is not None and int(m.group(1)) == want,
           "got %s" % (m.group(1) if m else None))

    # The generator is the other half of the fence. The watcher's `case` makes a
    # bad value harmless at RUN time (fall back to 900); this makes it loud at
    # GENERATE time, so an operator who typed 3 when they meant 30 is told,
    # rather than quietly running on the default they were trying to change.
    print("\nthe generator refuses a card outside 5-120")
    cardtool = os.path.join(HERE, "..", "overlay", "usr", "local", "bin",
                            "pipebox-card")
    tmpldir = os.path.join(HERE, "..", "overlay", "usr", "local", "share",
                           "pipeos", "card")
    base = open(os.path.join(CARDS, "box3.card")).read()
    for value, why in [("3", "below the 5-minute floor"),
                       ("121", "above the 120-minute ceiling"),
                       ("0", "zero"),
                       ("abc", "not a number"),
                       ("-5", "negative"),
                       # The generator is the fence that MUST refuse these
                       # rather than fall back, because a card is data that
                       # arrives with a machine. Pre-#106 all three rendered:
                       # `[ -ge 5 ]` is `test`, which parses decimal, so `030`
                       # passed the 5-120 range check as thirty and wrote
                       # AGENT_TIMEOUT_MIN=030 into the conf through the
                       # sanctioned path.
                       ("030", "a leading zero — octal to $(( ))"),
                       ("05", "a leading zero, even where the value is in range"),
                       ("08", "a leading zero that is not even valid octal")]:
        with tempfile.TemporaryDirectory() as d:
            card = os.path.join(d, "t.card")
            open(card, "w").write(
                re.sub(r"^AGENT_TIMEOUT_MIN=30$", "AGENT_TIMEOUT_MIN=" + value,
                       base, flags=re.M))
            p = subprocess.run(
                ["sh", cardtool, "generate", "--card", card,
                 "--root", os.path.join(d, "root"), "--templates", tmpldir],
                capture_output=True, text=True)
        ck("AGENT_TIMEOUT_MIN=%s is refused (%s)" % (value, why),
           p.returncode == 2 and "AGENT_TIMEOUT_MIN" in p.stderr,
           "rc=%s stderr=%r" % (p.returncode, p.stderr.strip()))

    # ...and accepts the values the fleet actually declares, so the check above
    # is not passing by refusing everything.
    for value in ("5", "15", "30", "120"):
        with tempfile.TemporaryDirectory() as d:
            card = os.path.join(d, "t.card")
            open(card, "w").write(
                re.sub(r"^AGENT_TIMEOUT_MIN=30$", "AGENT_TIMEOUT_MIN=" + value,
                       base, flags=re.M))
            p = subprocess.run(
                ["sh", cardtool, "generate", "--card", card,
                 "--root", os.path.join(d, "root"), "--templates", tmpldir],
                capture_output=True, text=True)
            conf = os.path.join(d, "root", "etc", "pipeos", "pipebox.conf")
            rendered = open(conf).read() if os.path.exists(conf) else ""
        ck("AGENT_TIMEOUT_MIN=%s generates and renders into the conf" % value,
           p.returncode == 0
           and ('AGENT_TIMEOUT_MIN="%s"' % value) in rendered,
           "rc=%s stderr=%r" % (p.returncode, p.stderr.strip()))

    # The leading-zero rule lives in the SHARED numeric arm, not in
    # AGENT_TIMEOUT_MIN's branch, so the sibling card numbers get it from the
    # same line. None of them reaches a `$(( ))` today — box0 checked, they all
    # land in `test` comparisons — and "today" is precisely the qualifier that
    # let this bug exist, so the fence is placed once rather than per-key.
    print("\nthe same rule covers every numeric card key")
    for key, cur in [("DISK_WARN_PCT", "65"), ("DISK_CRIT_PCT", "80"),
                     ("GC_INTERVAL_HOURS", "12")]:
        with tempfile.TemporaryDirectory() as d:
            card = os.path.join(d, "t.card")
            open(card, "w").write(
                re.sub(r"^%s=%s$" % (key, cur), "%s=0%s" % (key, cur),
                       base, flags=re.M))
            p = subprocess.run(
                ["sh", cardtool, "generate", "--card", card,
                 "--root", os.path.join(d, "root"), "--templates", tmpldir],
                capture_output=True, text=True)
        ck("%s=0%s is refused for the leading zero" % (key, cur),
           p.returncode == 2 and key in p.stderr,
           "rc=%s stderr=%r" % (p.returncode, p.stderr.strip()))

    n_fail = results.count(False)
    print("\ncheck-agent-timeout: %s (%d/%d)"
          % ("PASS" if n_fail == 0 else "FAIL", len(results) - n_fail,
             len(results)))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
