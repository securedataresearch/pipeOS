#!/usr/bin/env python3
"""Probe for scheduled runs (#242): cronspec (what matches, what is
refused, when next), schedtick (fires once per minute, never when paused
or disabled), and the shipped runner pipeos-schedule-run under its seams
with stub claude/hermes/pipe binaries — the session convention, the rc
classification, the one-at-a-time lock, the DM, the log, runs.log, the
prompt reaching stdin verbatim. No root, nothing outside a tempdir.

Exit 0 if every row passes. Controls: check-schedule-controls.py.
"""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
CRONSPEC = os.environ.get("CHECK_CRONSPEC", os.path.join(WEB, "cronspec.py"))
TICK = os.environ.get("CHECK_TICK", os.path.join(WEB, "schedtick.py"))
RUNNER = os.environ.get("CHECK_RUNNER", os.path.join(REPO, "overlay/usr/local/bin/pipeos-schedule-run"))
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="cksched-")
shutil.copy(CRONSPEC, os.path.join(D, "cronspec.py"))
shutil.copy(TICK, os.path.join(D, "schedtick.py"))
spec = importlib.util.spec_from_file_location("cronspec", os.path.join(D, "cronspec.py"))
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

# ── 1-3. cronspec ────────────────────────────────────────────────────────
T = datetime.datetime
table = [
    ("*/15 * * * *", T(2026, 9, 10, 17, 30), True), ("*/15 * * * *", T(2026, 9, 10, 17, 31), False),
    ("0 2 * * *", T(2026, 9, 11, 2, 0), True), ("0 2 * * *", T(2026, 9, 11, 3, 0), False),
    ("30 3 * * 6", T(2026, 9, 12, 3, 30), True), ("30 3 * * sat", T(2026, 9, 12, 3, 30), True), ("30 3 * * 6", T(2026, 9, 13, 3, 30), False),
    ("0 8 * * mon-fri", T(2026, 9, 11, 8, 0), True), ("0 8 * * mon-fri", T(2026, 9, 12, 8, 0), False),
    ("0 0 * * 0", T(2026, 9, 13, 0, 0), True), ("0 0 * * 7", T(2026, 9, 13, 0, 0), True),
    ("@daily", T(2026, 1, 1, 0, 0), True), ("@hourly", T(2026, 1, 1, 5, 0), True), ("@hourly", T(2026, 1, 1, 5, 1), False),
    # Vixie: both day fields restricted -> either matches
    ("0 12 1 * mon", T(2026, 9, 14, 12, 0), True), ("0 12 1 * mon", T(2026, 10, 1, 12, 0), True), ("0 12 1 * mon", T(2026, 9, 15, 12, 0), False),
    ("0 0 31 2 *", T(2026, 2, 28, 0, 0), False), ("15 4 1 jan *", T(2026, 1, 1, 4, 15), True),
    ("1-5,10 * * * *", T(2026, 1, 1, 0, 10), True), ("1-5,10 * * * *", T(2026, 1, 1, 0, 6), False),
    ("0-30/10 * * * *", T(2026, 1, 1, 0, 20), True), ("0-30/10 * * * *", T(2026, 1, 1, 0, 40), False),
]
bad = []
for e, dt, want in table:
    if cs.matches(cs.parse(e), dt) != want:
        bad.append((e, dt, want))
check("1 the cron table: steps, ranges, lists, names, 0 and 7 as Sunday, the aliases, Vixie's either-day rule, Feb 30 never", not bad, repr(bad))
refused = []
# the fullwidth digit is the one only the charset catches: int() accepts it
for e in ("* * * * * ; rm -rf /", "60 * * * *", "*/0 * * * *", "* * * * * *", "* * * *", "x" * 70, "$(id)", "`id`", "5-1 * * * *", "", "0 25 * * *", "0 0 32 * *", "0 0 * 13 *", "0 0 * * 8", "a * * * *", "\uff10 2 * * *", 12, None):
    try:
        cs.parse(e)
        refused.append(e)
    except cs.CronError:
        pass
check("2 every hostile or out-of-range expression is refused (shell characters, six fields, four fields, zero step, too long, backwards, non-strings)", not refused, repr(refused))
now = T(2026, 9, 10, 17, 30)
nexts = {e: cs.next_run(cs.parse(e), now) for e in ("*/15 * * * *", "0 2 * * *", "0 0 1 1 *", "0 0 31 2 *", "0 8 * * mon-fri", "30 17 * * *")}
check("3 next_run crosses the hour, the day, the month and the year, is strictly after now, and is None for a date that never comes",
      nexts["*/15 * * * *"] == T(2026, 9, 10, 17, 45) and nexts["0 2 * * *"] == T(2026, 9, 11, 2, 0) and nexts["0 0 1 1 *"] == T(2027, 1, 1, 0, 0)
      and nexts["0 0 31 2 *"] is None and nexts["0 8 * * mon-fri"] == T(2026, 9, 11, 8, 0) and nexts["30 17 * * *"] == T(2026, 9, 11, 17, 30),
      repr(nexts))

# ── 4-6. the tick ────────────────────────────────────────────────────────
CONF = os.path.join(D, "schedule.json")
STATE_DIR = os.path.join(D, "state")
LOGDIR = os.path.join(D, "logs")
RUN_STUB = os.path.join(D, "run-stub")
PAUSED = os.path.join(D, "paused")
with open(RUN_STUB, "w") as f:
    f.write("#!/bin/sh\necho \"$1\" >> " + D + "/fired\n")
os.chmod(RUN_STUB, 0o755)


def jobs(*js):
    json.dump({"v": 1, "jobs": list(js)}, open(CONF, "w"))


def tick(now, run_bin=RUN_STUB):
    env = dict(os.environ, PIPEOS_SCHED_CONF=CONF, PIPEOS_SCHED_STATE_DIR=STATE_DIR, PIPEOS_SCHED_RUN_BIN=run_bin,
               PIPEOS_SCHED_LOG=os.path.join(LOGDIR, "schedule.log"), PIPEOS_SCHED_LOGDIR=LOGDIR, PIPEOS_SCHED_PAUSED=PAUSED,
               PIPEOS_SCHED_NOW=now)
    p = subprocess.run([sys.executable, os.path.join(D, "schedtick.py")], capture_output=True, text=True, env=env)
    time.sleep(0.2)
    return p.returncode


def fired():
    try:
        return open(D + "/fired").read().split()
    except OSError:
        return []


jobs({"name": "every-15", "cron": "*/15 * * * *", "prompt": "hi", "enabled": True},
     {"name": "paused-one", "cron": "*/15 * * * *", "prompt": "hi", "enabled": False},
     {"name": "bad-cron", "cron": "99 * * * *", "prompt": "hi", "enabled": True})
tick("2026-09-10T17:45"); tick("2026-09-10T17:45")
a = fired()
tick("2026-09-10T17:46")
b = fired()
tick("2026-09-10T18:00")
c = fired()
check("4 the tick fires a matching job exactly once for a minute (a double tick does not double-fire), a disabled job never, a bad expression is logged and skipped, and a later matching minute fires again",
      a == ["every-15"] and b == ["every-15"] and c == ["every-15", "every-15"]
      and "bad-cron" in open(os.path.join(LOGDIR, "schedule.log")).read() and "paused-one" not in open(os.path.join(LOGDIR, "schedule.log")).read(),
      "a=%r b=%r c=%r" % (a, b, c))
open(PAUSED, "w").write("monthly cap USD 40 reached 2026-09-10")
tick("2026-09-10T18:15")
d = fired()
jl = os.path.join(LOGDIR, "schedule-every-15.log")
check("5 under the ledger's pause marker nothing fires and both the dispatch log and the job's own log say why",
      d == c and os.path.exists(jl) and "monthly cap" in open(jl).read() and "skipped every-15" in open(os.path.join(LOGDIR, "schedule.log")).read(),
      "d=%r" % d)
os.unlink(PAUSED)
st = json.load(open(os.path.join(STATE_DIR, "state.json")))
check("6 the state file remembers the last minute each job fired, atomically", st["jobs"]["every-15"]["last_fired_minute"] == "2026-09-10T18:15"
      and not os.path.exists(os.path.join(STATE_DIR, "state.json.new")), repr(st))

# ── 7-13. the runner ─────────────────────────────────────────────────────
BIN = os.path.join(D, "bin")
os.makedirs(BIN)
with open(os.path.join(BIN, "claude"), "w") as f:
    f.write("#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> " + D + "/claude.argv\n"
            "cat > " + D + "/claude.stdin\n"
            "[ -n \"${STUB_SLEEP:-}\" ] && sleep \"$STUB_SLEEP\"\n"
            "echo \"the reply\"\n"
            "exit \"${STUB_RC:-0}\"\n")
with open(os.path.join(BIN, "hermes"), "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + D + "/hermes.argv\necho hermes-reply\n")
with open(os.path.join(BIN, "pipe"), "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + D + "/pipe.argv\n")
with open(os.path.join(BIN, "timeout"), "w") as f:
    # busybox/coreutils timeout both exist on CI; a shim makes rc 124 testable
    f.write("#!/bin/sh\nt=$1; shift\nif [ -n \"${STUB_TIMEOUT_RC:-}\" ]; then exit \"$STUB_TIMEOUT_RC\"; fi\nexec \"$@\"\n")
for b in ("claude", "hermes", "pipe", "timeout"):
    os.chmod(os.path.join(BIN, b), 0o755)
PBCONF = os.path.join(D, "pipebox.conf")
open(PBCONF, "w").write('NICK="box"\nOWNER_NICK="sam"\nAGENT_TIMEOUT_MIN="7"\n')
SETTINGS = os.path.join(D, "settings.json")
open(SETTINGS, "w").write("{}")
SECRETS = os.path.join(D, "secrets")
os.makedirs(SECRETS)
open(os.path.join(SECRETS, "claude.env"), "w").write("ANTHROPIC_API_KEY=sk-ant-api03-stub\n")
open(os.path.join(SECRETS, "jobs.env"), "w").write("GH_TOKEN='ghp_fromvault'\n")
WORK = os.path.join(D, "work")
RSTATE = os.path.join(D, "rstate")
RLOGS = os.path.join(D, "rlogs")
LOCK = os.path.join(D, "run.lock")
BIG_PROMPT = "line one with 'quotes' and \"doubles\" and $dollar and `ticks`\n" + ("x" * 100 + "\n") * 80


def runner(job, extra_env=None, wait=True):
    env = dict(os.environ, PATH=BIN + ":" + os.environ.get("PATH", ""), PIPEOS_SCHED_CONF=CONF, PIPEOS_SCHED_STATE_DIR=RSTATE,
               PIPEOS_SCHED_LOCK=LOCK, PIPEOS_SCHED_LOGDIR=RLOGS, PIPEOS_SCHED_PIPEBOX_CONF=PBCONF, PIPEOS_SCHED_SETTINGS=SETTINGS,
               PIPEOS_SCHED_SECRETS=SECRETS, PIPEOS_SCHED_WORK=WORK, PIPEOS_SCHED_PAUSED=os.path.join(D, "rpaused"))
    env.pop("CLAUDE_TIMEOUT", None)
    if extra_env:
        env.update(extra_env)
    if not wait:
        return subprocess.Popen(["sh", RUNNER, job], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p = subprocess.run(["sh", RUNNER, job], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def argv(name):
    try:
        return open(os.path.join(D, name + ".argv")).read().splitlines()
    except OSError:
        return []


def clear(name):
    try:
        os.unlink(os.path.join(D, name + ".argv"))
    except OSError:
        pass


jobs({"name": "nightly", "cron": "0 2 * * *", "prompt": BIG_PROMPT, "backend": "claude", "notify": True, "session": "fresh"},
     {"name": "keeper", "cron": "0 3 * * *", "prompt": "keep going", "backend": "claude", "notify": False, "session": "continue"},
     {"name": "herm", "cron": "0 4 * * *", "prompt": "hermes job", "backend": "hermes", "notify": False},
     {"name": "elsewhere", "cron": "0 5 * * *", "prompt": "x", "cwd": "/etc"})
os.makedirs(WORK, exist_ok=True)
rc, out = runner("nightly")
a1 = argv("claude")
log = open(os.path.join(RLOGS, "schedule-nightly.log")).read()
st = json.load(open(os.path.join(RSTATE, "state.json")))["jobs"]["nightly"]
runs = open(os.path.join(RSTATE, "runs.log")).read().split()
check("7 a run: the prompt reaches claude on stdin verbatim (quotes, newlines, 8 KB; a trailing newline is the one thing $(…) drops); argv is `-p --settings <fence> --session-id <uuid>`; the log carries a run header and the reply; state says ok; runs.log has `<ts> <sid> nightly ok`",
      rc == 0 and open(D + "/claude.stdin").read() == BIG_PROMPT.rstrip("\n") and len(a1) == 1
      and a1[0].startswith("-p --settings %s --session-id " % SETTINGS) and "=== run " in log and "job=nightly" in log
      and "the reply" in log and "status=ok" in log and st["last_status"] == "ok" and st["last_rc"] == 0 and st["consecutive_failures"] == 0
      and len(runs) == 4 and runs[2] == "nightly" and runs[3] == "ok" and runs[1] in a1[0],
      "rc=%s out=%s argv=%r st=%r runs=%r" % (rc, out[-200:], a1, st, runs))
pa = argv("pipe")
check("8 notify: the owner gets a started DM and a done DM naming the log; the timeout came from the card (7 min = 420 s)",
      len(pa) == 2 and pa[0].startswith("dm sam job nightly: started") and "job nightly: done" in pa[1] and "schedule-nightly.log" in pa[1]
      and "timeout=420s" in log, repr(pa))
clear("claude"); clear("pipe")
runner("nightly")
a2 = argv("claude")
check("9 session fresh: the second run gets a NEW --session-id, never --resume", len(a2) == 1 and "--session-id" in a2[0] and a2[0] != a1[0] and "--resume" not in a2[0], repr((a1, a2)))
clear("claude"); clear("pipe")
runner("keeper"); k1 = argv("claude"); clear("claude")
runner("keeper"); k2 = argv("claude")
sid1 = k1[0].split("--session-id ")[1].strip()
check("10 session continue: the first run starts a session, the second resumes the same id; notify off sends no DM",
      "--session-id" in k1[0] and k2[0].endswith("--resume " + sid1) and not argv("pipe"), repr((k1, k2, argv("pipe"))))
clear("claude")
rc_c, _ = runner("nightly", {"STUB_TIMEOUT_RC": "124"})
st_c = json.load(open(os.path.join(RSTATE, "state.json")))["jobs"]["nightly"]
log_c = open(os.path.join(RLOGS, "schedule-nightly.log")).read()
rc_f, _ = runner("nightly", {"STUB_RC": "3"})
st_f = json.load(open(os.path.join(RSTATE, "state.json")))["jobs"]["nightly"]
rc_f2, _ = runner("nightly", {"STUB_RC": "3"})
st_f2 = json.load(open(os.path.join(RSTATE, "state.json")))["jobs"]["nightly"]
rc_n, _ = runner("nightly", {"STUB_TIMEOUT_RC": "127"})
st_n = json.load(open(os.path.join(RSTATE, "state.json")))["jobs"]["nightly"]
rc_ok, _ = runner("nightly")
st_ok = json.load(open(os.path.join(RSTATE, "state.json")))["jobs"]["nightly"]
check("11 rc classification: 124 is `cut off` (rc 0, failures stay 0, the DM names the budget); 3 is failed(3) and counts; a second failure counts 2; 127 (never started) counts; an ok run resets the count",
      rc_c == 0 and st_c["last_status"] == "cut off" and st_c["consecutive_failures"] == 0 and "cut off after 420s" in open(D + "/pipe.argv").read()
      and rc_f == 1 and st_f["last_status"] == "failed(3)" and st_f["consecutive_failures"] == 1
      and st_f2["consecutive_failures"] == 2 and rc_n == 1 and st_n["consecutive_failures"] == 3 and "failed(127)" in st_n["last_status"]
      and rc_ok == 0 and st_ok["consecutive_failures"] == 0,
      "c=%r f=%r f2=%r n=%r ok=%r" % (st_c, st_f, st_f2, st_n, st_ok))
clear("claude")
p = runner("nightly", {"STUB_SLEEP": "3"}, wait=False)
time.sleep(1.2)
rc_o, out_o = runner("keeper")
p.wait(timeout=20)
log_k = open(os.path.join(RLOGS, "schedule-keeper.log")).read()
check("12 one at a time: a second job while the first runs exits 75 and logs the refusal, and does not run claude",
      rc_o == 75 and "another scheduled job is running" in log_k and len(argv("claude")) == 1, "rc=%s out=%s" % (rc_o, out_o[-200:]))
clear("hermes"); clear("claude")
rc_h, _ = runner("herm")
ha = argv("hermes")
check("13 a hermes job runs `hermes -z <prompt> --continue job-<name>` and no claude", rc_h == 0 and ha == ["-z hermes job --continue job-herm"] and not argv("claude"), repr(ha))
rc_e, _ = runner("elsewhere")
rc_u, _ = runner("no-such-job")
rc_bad, _ = runner("../etc")
st_e = json.load(open(os.path.join(RSTATE, "state.json")))["jobs"].get("elsewhere", {})
check("14 a cwd outside the /work root is refused (rc 2) AND recorded — state failed(2) with the reason, a runs.log line, a DM; an unknown job is rc 2, a hostile name is rc 2",
      rc_e == 2 and rc_u == 2 and rc_bad == 2 and st_e.get("last_status") == "failed(2)" and st_e.get("consecutive_failures") == 1
      and "not under" in st_e.get("last_error", "") and "elsewhere failed(2)" in open(os.path.join(RSTATE, "runs.log")).read()
      and "job elsewhere: refused" in open(D + "/pipe.argv").read(),
      "e=%s u=%s bad=%s st=%r" % (rc_e, rc_u, rc_bad, st_e))
check("15 jobs.env from the vault reaches the run's environment (GH_TOKEN exported)",
      "ghp_fromvault" in subprocess.run(["sh", "-c", ". %s; set -a; . %s; set +a; env" % (PBCONF, os.path.join(SECRETS, "jobs.env"))], capture_output=True, text=True).stdout
      and "set -a" in open(RUNNER).read() and "jobs.env" in open(RUNNER).read(), "")

# ── 17-20. the review's findings (2026-09-11) ─────────────────────────────
clear("claude"); clear("pipe")
jobs({"name": "blank", "cron": "0 2 * * *", "prompt": "p", "cwd": ""},
     {"name": "root", "cron": "0 2 * * *", "prompt": "p", "cwd": WORK},
     {"name": "flaky", "cron": "0 3 * * *", "prompt": "p", "notify": False, "session": "continue"})
rc_b, out_b = runner("blank")
rc_r, out_r = runner("root")
check("17 a blank working dir (what the dashboard stores for the documented default) runs under /work/pipebox/jobs/<name>, and /work itself is accepted",
      rc_b == 0 and rc_r == 0 and os.path.isdir(os.path.join(WORK, "pipebox", "jobs", "blank")) and len(argv("claude")) == 2,
      "b=%s %s r=%s %s" % (rc_b, out_b[-120:], rc_r, out_r[-120:]))
clear("claude")
open(os.path.join(D, "rpaused"), "w").write("monthly cap USD 40 reached")
rc_p, _ = runner("blank")
check("18 the runner itself honours the cap's pause marker (Run now and an agent's pipeos-schedule-run are not a way around the tick): rc 75, claude not started, the log says why",
      rc_p == 75 and not argv("claude") and "monthly cap" in open(os.path.join(RLOGS, "schedule-blank.log")).read(), "rc=%s" % rc_p)
os.unlink(os.path.join(D, "rpaused"))
clear("claude")
runner("flaky", {"STUB_RC": "1"}); f1 = argv("claude"); clear("claude")
runner("flaky"); f2 = argv("claude"); clear("claude")
runner("flaky"); f3 = argv("claude")
check("19 session continue: a run that failed before a conversation existed is not resumed — the next run starts a new session id; only a run that reached the conversation is resumed",
      len(f1) == 1 and "--session-id" in f1[0] and len(f2) == 1 and "--session-id" in f2[0] and f2[0] != f1[0] and "--resume" not in f2[0]
      and len(f3) == 1 and f3[0].endswith("--resume " + f2[0].split("--session-id ")[1].strip()), repr((f1, f2, f3)))
try:
    os.unlink(D + "/fired")
except OSError:
    pass
jobs({"name": "two-a", "cron": "0 2 * * *", "prompt": "p"}, {"name": "two-b", "cron": "0 2 * * *", "prompt": "p"})
SLOW_STUB = os.path.join(D, "run-slow")   # a run that takes time: start/end order tells sequential from racing
with open(SLOW_STUB, "w") as f:
    f.write("#!/bin/sh\necho \"start-$1\" >> " + D + "/fired\nsleep 0.4\necho \"end-$1\" >> " + D + "/fired\n")
os.chmod(SLOW_STUB, 0o755)
tick("2026-09-12T02:00", SLOW_STUB)
time.sleep(1.2)
sm = [(e, cs.matches(cs.parse("0 0 */2 * mon"), T(2026, 9, d))) for e, d in (("mon-even", 14), ("tue-odd", 15), ("mon-odd", 21))]
check("20 two jobs sharing a minute both fire, in order (one detached shell runs them one after another — the runner's lock is not a race the loser silently loses); Vixie's either/both rule reads `*/2` as a star: `0 0 */2 * mon` is odd-day Mondays only",
      fired() == ["start-two-a", "end-two-a", "start-two-b", "end-two-b"] and sm == [("mon-even", False), ("tue-odd", False), ("mon-odd", True)],
      "fired=%r sm=%r" % (fired(), sm))

# ── 21. the wiring ───────────────────────────────────────────────────────
cron = open(os.path.join(REPO, "overlay/etc/crontabs/root")).read()
lbu = [l.strip() for l in open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")) if not l.startswith("#")]
settings = [open(os.path.join(REPO, p)).read() for p in ("overlay/etc/pipeos/pipebox-settings.json", "overlay/usr/local/share/pipeos/card/pipebox-settings.json.tmpl")]
check("21 the per-minute tick line is in the shipped crontab, schedule.json persists via lbu.list, the runner is fenced from the agent",
      "* * * * * /usr/local/bin/pipeos-schedule-tick" in cron and "+etc/pipeos/schedule.json" in lbu
      and all('"Bash(pipeos-schedule-*)"' in s for s in settings), "")

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
