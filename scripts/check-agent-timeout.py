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
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHER = os.path.join(HERE, "..", "overlay", "usr", "local", "bin",
                       "pipebox-cohort-watch")
TMPL = os.path.join(HERE, "..", "overlay", "usr", "local", "share", "pipeos",
                    "card", "pipebox.conf.tmpl")
CARDS = os.path.join(HERE, "..", "docs", "cards")

results = []


def ck(desc, ok, detail=""):
    results.append(ok)
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", desc,
                           ("  [%s]" % detail) if detail and not ok else ""))


SRC = open(WATCHER).read()


def extract():
    """Slice the case block + CLAUDE_TIMEOUT assignment out of the watcher."""
    m = re.findall(r'^case "\$\{AGENT_TIMEOUT_MIN:-\}" in\n.*?^esac\n'
                   r'CLAUDE_TIMEOUT="\$\{CLAUDE_TIMEOUT:-\$_agent_timeout_s\}"\n',
                   SRC, flags=re.M | re.S)
    if len(m) != 1:
        sys.exit("probe: expected exactly 1 AGENT_TIMEOUT_MIN resolution block "
                 "in %s, found %d — it changed shape, fix the probe before "
                 "trusting it" % (WATCHER, len(m)))
    return m[0]


def resolve(block, conf_value=None, env_value=None):
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
        p = subprocess.run(["sh", path], capture_output=True, text=True,
                           env=env)
        return p.stdout.strip(), p.stderr.strip(), p.returncode
    finally:
        os.unlink(path)


# conf value, env CLAUDE_TIMEOUT, expected seconds, description
ROWS = [
    ("30",   None, "1800", "BUILD's 30 minutes -> 1800s"),
    ("15",   None, "900",  "TEST/SHIP's 15 minutes -> 900s"),
    ("5",    None, "300",  "the generator floor -> 300s"),
    ("120",  None, "7200", "the generator ceiling -> 7200s"),
    ("",     None, "900",  "EMPTY (unprovisioned or older card) -> 900s default"),
    (None,   None, "900",  "key absent entirely -> 900s default"),
    ("0",    None, "900",  "0 -> 900s, NOT `timeout 0` killing every run"),
    ("abc",  None, "900",  "non-numeric -> 900s, no arithmetic error"),
    ("-5",   None, "900",  "negative -> 900s (the `-` fails the digits test)"),
    ("1 800", None, "900", "embedded space -> 900s, not a split word"),
    ("30",  "60",  "60",   "explicit CLAUDE_TIMEOUT in env beats the card"),
    ("",    "60",  "60",   "env wins over the fallback too"),
]


def main():
    block = extract()

    print("AGENT_TIMEOUT_MIN resolution")
    for conf, env, want, desc in ROWS:
        got, err, rc = resolve(block, conf, env)
        ck(desc, got == want and rc == 0 and err == "",
           "got %r rc=%s stderr=%r" % (got, rc, err))

    # THE ORDERING PROPERTY — the actual bug. Both the conf source line and the
    # resolution must exist, and the resolution must come SECOND. A correct
    # value assigned above the source line is silently overwritten, which is
    # what shipped, and no amount of arithmetic testing sees it.
    print("\nordering: the conf is sourced BEFORE the timeout is resolved")
    src_line = SRC.find(". /etc/pipeos/pipebox.conf")
    resolve_at = SRC.find('case "${AGENT_TIMEOUT_MIN:-}" in')
    use_at = SRC.find('timeout "$CLAUDE_TIMEOUT"')
    ck("pipebox.conf is sourced", src_line >= 0)
    ck("the timeout is resolved after the conf is sourced",
       resolve_at > src_line >= 0, "source@%d resolve@%d" % (src_line, resolve_at))
    ck("the timeout is used after it is resolved",
       use_at > resolve_at >= 0, "resolve@%d use@%d" % (resolve_at, use_at))
    ck("no bare `CLAUDE_TIMEOUT=` assignment survives anywhere",
       re.search(r'^CLAUDE_TIMEOUT=(?!"\$\{CLAUDE_TIMEOUT:-)', SRC,
                 flags=re.M) is None,
       "an unconditional assignment would overwrite the conf again")

    # A cut-off run must not be logged as a failure. Both spellings, because
    # both were observed: 124 is timeout killing the child, 143 is the child's
    # own SIGTERM status propagated -- and 143 is the one netgaze displayed as
    # FAILED on three boxes.
    print("\na cut-off run is reported as cut off, not failed")
    for rc in ("124", "143"):
        ck("rc=%s is handled distinctly" % rc,
           re.search(r'^\s*124\|143\)', SRC, flags=re.M) is not None)
    m = re.search(r'124\|143\)\n\s*log "([^"]*)"', SRC)
    ck("the cut-off log line says CUT OFF and names the budget",
       m is not None and "CUT OFF" in m.group(1)
       and re.search(r"\$\{?CLAUDE_TIMEOUT\}?", m.group(1)) is not None,
       "log line: %s" % (m.group(1) if m else None))

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
        want = 30 if role and role.group(1) == "BUILD" else 15
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
                       ("-5", "negative")]:
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

    n_fail = results.count(False)
    print("\ncheck-agent-timeout: %s (%d/%d)"
          % ("PASS" if n_fail == 0 else "FAIL", len(results) - n_fail,
             len(results)))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
