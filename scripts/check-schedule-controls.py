#!/usr/bin/env python3
"""Controls for check-schedule.py: put each rule back to broken in a copy
of cronspec.py, schedtick.py or pipeos-schedule-run and assert the probe
notices (the house rule since #100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
CRONSPEC = os.path.join(WEB, "cronspec.py")
TICK = os.path.join(WEB, "schedtick.py")
RUNNER = os.path.join(REPO, "overlay/usr/local/bin/pipeos-schedule-run")
sys.path.insert(0, HERE)
import controls_lib  # noqa: E402

PROBE = os.path.join(HERE, "check-schedule.py")

BREAKS = [
    ("A  the weekday field is ignored", CRONSPEC,
     "    dow_ok = wd in spec.weekday\n", "    dow_ok = True\n"),
    ("B  a shell character passes the charset", CRONSPEC,
     '_CHARS = re.compile(r"^[0-9a-z*,/\\- @]+$")', '_CHARS = re.compile(r"^.+$")'),
    ("C  the tick fires the same minute twice", TICK,
     '            if js.get("last_fired_minute") == key:\n                continue\n', ''),
    ("D  the tick ignores the pause marker", TICK,
     '        why = ledger.why_paused(name, PAUSED, PAUSED_JSON)', '        why = ""'),
    ("E  the one-at-a-time lock is gone", RUNNER,
     'if ! flock -n 9; then', 'if false; then'),
    ("F  a cut-off run counts as a failure", RUNNER,
     '    124|143)  log "claude was CUT OFF', '    999)  log "claude was CUT OFF'),
    ("G  session continue never resumes", RUNNER,
     '[ "$session" = continue ] && [ -f "$STATE_DIR/sessions/.started-$sid" ] && resume_args="--resume $sid"', ''),
    ("H  a cwd outside /work is accepted", RUNNER,
     'case "$cwd" in "$WORKROOT"|"$WORKROOT"/*) ;; *) refuse "cwd $cwd is not under $WORKROOT" ;; esac', ''),
    ("I  a blank cwd is kept as \"\" instead of the default", RUNNER,
     'if $1 == null or $1 == \\"\\" then', 'if $1 == null then'),
    ("J  the runner ignores the pause marker", RUNNER,
     'if [ -f "$PAUSED" ]; then', 'if false; then'),
    ("K  a failed first run still gets the resume marker", RUNNER,
     '    ok|"cut off") touch "$STATE_DIR/sessions/.started-$sid" ;;\n    *) [ "$session" = continue ] && [ ! -f "$STATE_DIR/sessions/.started-$sid" ] && rm -f "$sid_file" ;;',
     '    *) touch "$STATE_DIR/sessions/.started-$sid" ;;'),
    ("L  a refusal is not recorded", RUNNER,
     '    dm "job $job: refused — $1"\n    exit 2', '    exit 2'),
    ("M  */2 in the day field is read as a plain *", CRONSPEC,
     '    if spec.day_star or spec.weekday_star:\n        return dom_ok and dow_ok',
     '    if spec.day_star and spec.weekday_star:\n        return True\n    if spec.day_star:\n        return dow_ok\n    if spec.weekday_star:\n        return dom_ok'),
    ("O  the tick reads only the plain marker (an agent's own pause entry is ignored, #302)", TICK,
     '        why = ledger.why_paused(name, PAUSED, PAUSED_JSON)', '        why = ledger.why_paused("", PAUSED, os.devnull)'),
    ("P  the runner ignores paused.json (an agent over its own cap runs anyway, #302)", RUNNER,
     '    why=$(jq -r --arg j "$job" \'.agents[$j].text // ""\' "$PAUSED_JSON" 2>/dev/null)\n', '    why=\n'),
    ("Q  a manual job matches every minute (the tick fires what should only run when started)", CRONSPEC,
     "    if spec.manual:\n        return False\n", "    if spec.manual:\n        return True\n"),
    ("N  same-minute jobs race instead of running in order", TICK,
     '    script = "; ".join("%s %s" % (RUN_BIN, name) for name in fire)',
     '    script = " & ".join("%s %s" % (RUN_BIN, name) for name in fire) + " & wait"'),
]

ENV_FOR = {CRONSPEC: "CHECK_CRONSPEC", TICK: "CHECK_TICK", RUNNER: "CHECK_RUNNER"}


def one(brk):
    name, path, old, new = brk
    src = open(path).read()
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, tmp = tempfile.mkstemp(prefix="cksched-ctl-", suffix=".py" if path != RUNNER else "")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    env = dict(os.environ, **{ENV_FOR[path]: tmp})
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
