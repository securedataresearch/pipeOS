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
PROBE = os.path.join(HERE, "check-schedule.py")

BREAKS = [
    ("A  the weekday field is ignored", CRONSPEC,
     "    dow_ok = wd in spec.weekday\n", "    dow_ok = True\n"),
    ("B  a shell character passes the charset", CRONSPEC,
     '_CHARS = re.compile(r"^[0-9a-z*,/\\- @]+$")', '_CHARS = re.compile(r"^.+$")'),
    ("C  the tick fires the same minute twice", TICK,
     '            if js.get("last_fired_minute") == key:\n                continue\n', ''),
    ("D  the tick ignores the pause marker", TICK,
     "    paused = os.path.exists(PAUSED)", "    paused = False"),
    ("E  the one-at-a-time lock is gone", RUNNER,
     'if ! flock -n 9; then', 'if false; then'),
    ("F  a cut-off run counts as a failure", RUNNER,
     '    124|143)  log "claude was CUT OFF', '    999)  log "claude was CUT OFF'),
    ("G  session continue never resumes", RUNNER,
     '[ "$session" = continue ] && [ -f "$STATE_DIR/sessions/.started-$sid" ] && resume_args="--resume $sid"', ''),
    ("H  a cwd outside /work is accepted", RUNNER,
     'case "$cwd" in "$WORKROOT"/*) ;; *) log "job $job: cwd $cwd is not under $WORKROOT — refusing"; exit 2 ;; esac', ''),
]

ENV_FOR = {CRONSPEC: "CHECK_CRONSPEC", TICK: "CHECK_TICK", RUNNER: "CHECK_RUNNER"}
failed = False
for name, path, old, new in BREAKS:
    src = open(path).read()
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, tmp = tempfile.mkstemp(prefix="cksched-ctl-", suffix=".py" if path != RUNNER else "")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    env = dict(os.environ, **{ENV_FOR[path]: tmp})
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
