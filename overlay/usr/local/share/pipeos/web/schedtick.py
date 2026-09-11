#!/usr/bin/env python3
"""pipeos-schedule-tick — crond's minute hand for the box's own jobs (#242).

Runs every minute from /etc/crontabs/root. Reads the owner's job list
(/etc/pipeos/schedule.json — dashboard-written, rides the apkovl) and,
for every enabled job whose cron expression matches this minute and
that has not already fired this minute, starts `pipeos-schedule-run
<job>` detached. The runner does the work; this only decides.

No catch-up: a minute the box was off for is a minute that did not
happen. One tick may be double-invoked (crond and a hand run) and must
not double-fire — the state file remembers the last minute each job
fired. Under the monthly cap (#246: /work/.pipeos/ledger/paused exists)
nothing starts and the reason is logged where the job's log is.

Seams (the probe, never production): PIPEOS_SCHED_CONF, _STATE_DIR,
_RUN_BIN, _LOG, _LOGDIR, _PAUSED, PIPEOS_SCHED_NOW (an ISO minute standing in
for the clock).
"""

import datetime
import fcntl
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cronspec  # noqa: E402

CONF = os.environ.get("PIPEOS_SCHED_CONF", "/etc/pipeos/schedule.json")
STATE_DIR = os.environ.get("PIPEOS_SCHED_STATE_DIR", "/work/.pipeos/schedule")
RUN_BIN = os.environ.get("PIPEOS_SCHED_RUN_BIN", "/usr/local/bin/pipeos-schedule-run")
LOG = os.environ.get("PIPEOS_SCHED_LOG", "/work/logs/schedule.log")
LOGDIR = os.environ.get("PIPEOS_SCHED_LOGDIR", "/work/logs")
PAUSED = os.environ.get("PIPEOS_SCHED_PAUSED", "/work/.pipeos/ledger/paused")
STATE = os.path.join(STATE_DIR, "state.json")
STATE_LOCK = os.path.join(STATE_DIR, ".state.lock")


def log(msg, job=None):
    line = "%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    for path in ([LOG] + ([os.path.join(LOGDIR, "schedule-%s.log" % job)] if job else [])):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as f:
                f.write(line)
        except OSError:
            pass


def read_jobs():
    try:
        with open(CONF) as f:
            d = json.load(f)
        return [j for j in d.get("jobs", []) if isinstance(j, dict) and j.get("name")]
    except (OSError, ValueError):
        return []


def now_minute():
    v = os.environ.get("PIPEOS_SCHED_NOW")
    if v:
        return datetime.datetime.strptime(v, "%Y-%m-%dT%H:%M")
    return datetime.datetime.now().replace(second=0, microsecond=0)


def main():
    jobs = read_jobs()
    if not jobs:
        return 0
    now = now_minute()
    key = now.strftime("%Y-%m-%dT%H:%M")
    os.makedirs(STATE_DIR, exist_ok=True)
    paused = os.path.exists(PAUSED)
    with open(STATE_LOCK, "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(STATE) as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        state.setdefault("jobs", {})
        fire = []
        for j in jobs:
            name = j["name"]
            if not j.get("enabled", True):
                continue
            try:
                spec = cronspec.parse(j.get("cron", ""))
            except cronspec.CronError as e:
                log("job %s: bad schedule (%s) — skipped" % (name, e))
                continue
            if not cronspec.matches(spec, now):
                continue
            js = state["jobs"].setdefault(name, {})
            if js.get("last_fired_minute") == key:
                continue
            js["last_fired_minute"] = key
            fire.append(name)
        tmp = STATE + ".new"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE)
    for name in fire:
        if paused:
            log("skipped %s: scheduled runs are paused — the monthly cap is reached (raise it under Usage)" % name, job=name)
            continue
        try:
            subprocess.Popen([RUN_BIN, name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            log("fired %s (%s)" % (name, key))
        except OSError as e:
            log("could not start %s: %s" % (name, e), job=name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
