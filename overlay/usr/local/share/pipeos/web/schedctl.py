#!/usr/bin/env python3
"""schedctl — the scheduled-jobs table from the command line (#242, #259).

    pipeos schedule ls
    pipeos schedule add NAME --cron "M H D M W" --prompt TEXT [--cwd DIR]
                        [--backend claude|hermes] [--notify on|off]
                        [--session fresh|continue]
    pipeos schedule set NAME [the same flags — only the given ones change]
    pipeos schedule rm NAME
    pipeos schedule enable|disable NAME
    pipeos schedule run NAME          (detached, like Run now)
    pipeos schedule log NAME [N]      (the last N lines, default 40)

Same table, same rules, same persistence as the dashboard's Schedule view:
/etc/pipeos/schedule.json, names [a-z0-9-] up to 32, a five-field cron
expression or an @alias, the working dir under /work or blank for
/work/pipebox/jobs/<name>, and every change saved through pipeos-save at
once (the rule from #238). This exists so an operator — human or the
owner's agent on a workstation — can drive a Machine's jobs over ssh
without the dashboard login; the dashboard and this tool never disagree
because the probe (check-schedctl.py) holds them to one shape.

Seams (the probe): PIPEOS_SCHED_CONF, PIPEOS_SCHED_STATE_DIR,
PIPEOS_SCHED_LOGDIR, PIPEOS_SCHED_RUN_BIN, PIPEOS_SAVE_BIN, PIPEOS_SCHED_WORK,
PIPEOS_SCHED_PAUSED.
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cronspec  # noqa: E402

CONF = os.environ.get("PIPEOS_SCHED_CONF", "/etc/pipeos/schedule.json")
STATE_DIR = os.environ.get("PIPEOS_SCHED_STATE_DIR", "/work/.pipeos/schedule")
LOGDIR = os.environ.get("PIPEOS_SCHED_LOGDIR", "/work/logs")
RUN_BIN = os.environ.get("PIPEOS_SCHED_RUN_BIN", "/usr/local/bin/pipeos-schedule-run")
SAVE_BIN = os.environ.get("PIPEOS_SAVE_BIN", "/usr/local/bin/pipeos-save")
PAUSED = os.environ.get("PIPEOS_SCHED_PAUSED", "/work/.pipeos/ledger/paused")
WORK = os.environ.get("PIPEOS_SCHED_WORK", "/work")
MAX_JOBS = 32
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
BACKENDS = ("claude", "hermes")


class Refused(Exception):
    pass


def read_jobs():
    try:
        with open(CONF) as f:
            d = json.load(f)
        jobs = d.get("jobs", []) if isinstance(d, dict) else []
        return [j for j in jobs if isinstance(j, dict) and j.get("name")]
    except (OSError, ValueError):
        return []


def write_jobs(jobs):
    tmp = CONF + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"v": 1, "jobs": jobs}, indent=1) + "\n")
    os.rename(tmp, CONF)


def save():
    """Every mutating verb ends here: the table is /etc, and /etc dies at
    reboot unless pipeos-save commits it."""
    p = subprocess.run([SAVE_BIN], capture_output=True, text=True)
    if p.returncode != 0:
        print("saved: NO — %s" % (p.stdout + p.stderr).strip()[-300:], file=sys.stderr)
        return 1
    print("saved")
    return 0


def state():
    try:
        with open(os.path.join(STATE_DIR, "state.json")) as f:
            return json.load(f).get("jobs", {})
    except (OSError, ValueError):
        return {}


def parse_flags(argv, allowed):
    """--k v pairs; unknown or dangling flags are refused."""
    out = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("--") or a[2:] not in allowed:
            raise Refused("unknown flag %s (flags: %s)" % (a, ", ".join("--" + k for k in allowed)))
        if i + 1 >= len(argv):
            raise Refused("%s needs a value" % a)
        out[a[2:]] = argv[i + 1]
        i += 2
    return out


def apply(job, flags, new):
    if "cron" in flags or new:
        try:
            job["cron"] = cronspec.parse(flags.get("cron", "")).text
        except cronspec.CronError as e:
            raise Refused("schedule: %s" % e)
    if "prompt" in flags or new:
        prompt = flags.get("prompt", "")
        if not isinstance(prompt, str) or not prompt.strip():
            raise Refused("the job needs a prompt")
        if len(prompt) > 16384:
            raise Refused("the prompt is too long (16 KB)")
        job["prompt"] = prompt.strip()
    if "cwd" in flags:
        cwd = flags["cwd"].strip()
        if cwd:
            real = os.path.realpath(cwd)
            if not (real == WORK or real.startswith(WORK + "/")) or any(c in cwd for c in "\n\r\0'\""):
                raise Refused("the working dir must be under %s" % WORK)
            job["cwd"] = real
        else:
            job["cwd"] = ""
    if "backend" in flags:
        b = flags["backend"].strip()
        if b not in BACKENDS:
            raise Refused("assistant must be one of: " + ", ".join(BACKENDS))
        if shutil.which(b) is None:
            raise Refused("%s is not installed on this image" % b)
        job["backend"] = b
    for k in ("notify", "enabled"):
        if k in flags:
            v = flags[k].strip().lower()
            if v not in ("on", "off", "true", "false", "1", "0"):
                raise Refused("--%s is on or off" % k)
            job[k] = v in ("on", "true", "1")
    if "session" in flags:
        if flags["session"] not in ("fresh", "continue"):
            raise Refused("--session is fresh or continue")
        job["session"] = flags["session"]
    return job


def cmd_ls():
    jobs = read_jobs()
    if not jobs:
        print("no scheduled jobs")
        return 0
    st = state()
    for j in jobs:
        try:
            spec = cronspec.parse(j.get("cron", ""))
            human = cronspec.describe(spec)
        except cronspec.CronError as e:
            human = "invalid: %s" % e
        s = st.get(j["name"], {})
        last = s.get("last_status", "never run")
        fails = s.get("consecutive_failures", 0)
        print("%-32s %-18s %-9s %-8s %-8s %s%s" % (
            j["name"], j.get("cron", ""), "enabled" if j.get("enabled", True) else "disabled",
            j.get("backend", "claude"), j.get("session", "fresh"),
            last, " (%d failures in a row)" % fails if fails else ""))
        print("    %s — %s" % (human, (j.get("prompt", "")[:70] + "…") if len(j.get("prompt", "")) > 70 else j.get("prompt", "")))
    return 0


def cmd_add_set(argv, new):
    if not argv:
        raise Refused("usage: pipeos schedule %s NAME [flags]" % ("add" if new else "set"))
    name = argv[0].strip().lower()
    if not NAME_RE.match(name):
        raise Refused("job names: lowercase letters, digits and dashes, up to 32")
    flags = parse_flags(argv[1:], ("cron", "prompt", "cwd", "backend", "notify", "session"))
    jobs = read_jobs()
    cur = next((j for j in jobs if j["name"] == name), None)
    if new and cur is not None:
        raise Refused("a job named %s exists — pipeos schedule set %s …" % (name, name))
    if not new and cur is None:
        raise Refused("no job named %s" % name)
    if new and len(jobs) >= MAX_JOBS:
        raise Refused("at most %d jobs on one Machine" % MAX_JOBS)
    job = dict(cur) if cur else {"name": name, "cwd": "", "backend": "claude", "notify": True,
                                 "enabled": True, "session": "fresh", "prompt": "", "cron": ""}
    job = apply(job, flags, new)
    if cur is None:
        jobs.append(job)
    else:
        jobs[jobs.index(cur)] = job
    write_jobs(jobs)
    print("%s %s: %s — %s" % ("added" if new else "set", name, job["cron"], cronspec.describe(cronspec.parse(job["cron"]))))
    return save()


def cmd_rm(argv):
    name = (argv[0] if argv else "").strip().lower()
    jobs = read_jobs()
    keep = [j for j in jobs if j["name"] != name]
    if len(keep) == len(jobs):
        raise Refused("no job named %s" % name)
    write_jobs(keep)
    for p in (os.path.join(STATE_DIR, "sessions", name),):
        try:
            os.unlink(p)
        except OSError:
            pass
    print("removed " + name)
    return save()


def cmd_toggle(argv, on):
    name = (argv[0] if argv else "").strip().lower()
    jobs = read_jobs()
    cur = next((j for j in jobs if j["name"] == name), None)
    if cur is None:
        raise Refused("no job named %s" % name)
    cur["enabled"] = on
    write_jobs(jobs)
    print(("enabled " if on else "disabled ") + name)
    return save()


def cmd_run(argv):
    name = (argv[0] if argv else "").strip().lower()
    if not any(j["name"] == name for j in read_jobs()):
        raise Refused("no job named %s" % name)
    if os.path.exists(PAUSED):
        # the runner refuses too (rc 75), but "started" would be a lie — the dashboard answers 409 here
        try:
            why = open(PAUSED).read().strip()
        except OSError:
            why = "the monthly cap is reached"
        raise Refused("scheduled runs are paused — %s (pipeos usage cap N|none)" % why)
    subprocess.Popen([RUN_BIN, name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    print("started %s — pipeos schedule log %s" % (name, name))
    return 0


def cmd_log(argv):
    name = (argv[0] if argv else "").strip().lower()
    if not NAME_RE.match(name):
        raise Refused("usage: pipeos schedule log NAME [N]")
    n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 40
    p = os.path.join(LOGDIR, "schedule-%s.log" % name)
    try:
        with open(p) as f:
            lines = f.read().splitlines()
    except OSError:
        print("no log yet for %s (%s)" % (name, p))
        return 1
    print("\n".join(lines[-n:]))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().split("\n\n")[0])
        return 0 if argv else 2
    verb, rest = argv[0], argv[1:]
    try:
        if verb == "ls":
            return cmd_ls()
        if verb == "add":
            return cmd_add_set(rest, True)
        if verb == "set":
            return cmd_add_set(rest, False)
        if verb == "rm":
            return cmd_rm(rest)
        if verb == "enable":
            return cmd_toggle(rest, True)
        if verb == "disable":
            return cmd_toggle(rest, False)
        if verb == "run":
            return cmd_run(rest)
        if verb == "log":
            return cmd_log(rest)
        raise Refused("unknown verb %s" % verb)
    except Refused as e:
        print("schedule: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("schedule: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
