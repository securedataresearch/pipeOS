#!/usr/bin/env python3
"""check-data-migrate (pipeOS#330): the stored paths that do not follow a
symlink.

When the bulk volume moves from /work to /data, the symlink makes every path
RESOLVE either way — it does not make a stored string right. Three stores key
on the physical path, and each fails quietly and expensively:

  - a job's `cwd` in schedule.json: the runner refuses "cwd ... is not under
    /data" on every tick, with a DM each time;
  - claude's workdir trust in /root/.claude.json: a headless run stalls on a
    dialog nobody is there to answer;
  - claude's project directories (-work-pipebox): --resume finds nothing, so
    every per-peer conversation silently starts over.

pipeos-data-migrate rewrites them once, after the flip. It must be idempotent
(it runs at every boot), must not touch paths that merely start with the same
letters (/workshop), and must never clobber an entry already under the new
name — that one is the live one.

Seams: PIPEOS_MIGRATE_FROM, _TO, _SCHED, _CLAUDE_JSON, _CLAUDE_PROJECTS.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "overlay/usr/local/bin/pipeos-data-migrate")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", desc, "" if ok else "  <- " + detail))


def fixture():
    d = tempfile.mkdtemp(prefix="datamig-")
    sched = os.path.join(d, "schedule.json")
    json.dump({"jobs": [
        {"name": "nightly", "cwd": "/work/pipebox/jobs/nightly"},
        {"name": "root", "cwd": "/work"},
        {"name": "already", "cwd": "/data/already"},
        {"name": "nocwd", "prompt": "no cwd at all"},
        {"name": "lookalike", "cwd": "/workshop/elsewhere"},
    ]}, open(sched, "w"))
    cj = os.path.join(d, "claude.json")
    json.dump({"projects": {"/work/pipebox": {"hasTrustDialogAccepted": True},
                            "/work/other": {"x": 1},
                            "/data/pipebox": {"live": True},
                            "/root": {"y": 2}},
               "hasCompletedOnboarding": True}, open(cj, "w"))
    pr = os.path.join(d, "projects")
    for n in ("-work-pipebox", "-work-repos-x", "-data-pipebox", "-other"):
        os.makedirs(os.path.join(pr, n))
    open(os.path.join(pr, "-work-pipebox", "s1.jsonl"), "w").write("old\n")
    open(os.path.join(pr, "-data-pipebox", "live.jsonl"), "w").write("new\n")
    return d, sched, cj, pr


def run(sched, cj, pr, frm="/work", to="/data"):
    env = dict(os.environ, PIPEOS_MIGRATE_FROM=frm, PIPEOS_MIGRATE_TO=to,
               PIPEOS_MIGRATE_SCHED=sched, PIPEOS_MIGRATE_CLAUDE_JSON=cj,
               PIPEOS_MIGRATE_CLAUDE_PROJECTS=pr)
    p = subprocess.run(["sh", BIN], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


d, sched, cj, pr = fixture()
rc, out = run(sched, cj, pr)
jobs = {j["name"]: j.get("cwd") for j in json.load(open(sched))["jobs"]}
check("1 every stored job working directory moves to the new name — the runner refuses one under the old name on every tick, with a DM each time",
      rc == 0 and jobs["nightly"] == "/data/pipebox/jobs/nightly" and jobs["root"] == "/data",
      "rc=%s jobs=%r out=%r" % (rc, jobs, out[-200:]))
check("2 a job already under the new name, one with no working dir at all, and one whose path merely starts the same way (/workshop) are left exactly as they were",
      jobs["already"] == "/data/already" and jobs["nocwd"] is None and jobs["lookalike"] == "/workshop/elsewhere",
      repr(jobs))

proj = json.load(open(cj))["projects"]
check("3 claude's workdir trust moves with it — an untrusted dir stalls a headless run on a dialog nobody can answer",
      "/data/pipebox" in proj and "/data/other" in proj and not [k for k in proj if k.startswith("/work")],
      repr(sorted(proj)))
check("4 an entry ALREADY under the new name wins: it is the live one, and a stale copy must not overwrite the trust the box is using",
      proj["/data/pipebox"] == {"live": True} and proj["/root"] == {"y": 2}
      and json.load(open(cj))["hasCompletedOnboarding"] is True, repr(proj))

names = sorted(os.listdir(pr))
check("5 claude's project directories are renamed, so --resume still finds the sessions from before the flip instead of silently starting the conversation over",
      "-data-repos-x" in names and "-work-repos-x" not in names
      and os.path.isdir(os.path.join(pr, "-data-repos-x"))
      and open(os.path.join(pr, "-data-pipebox", "live.jsonl")).read() == "new\n",
      repr(names))
check("6 a directory whose new name already holds sessions is left alone and said, not merged blind",
      "-work-pipebox" in names and "-data-pipebox" in names and "already exists" in out
      and open(os.path.join(pr, "-work-pipebox", "s1.jsonl")).read() == "old\n", repr(names) + out[-200:])
check("7 a name that is nobody's business (-other) is untouched", "-other" in names, repr(names))

before = (open(sched).read(), open(cj).read(), sorted(os.listdir(pr)))
rc2, out2 = run(sched, cj, pr)
after = (open(sched).read(), open(cj).read(), sorted(os.listdir(pr)))
check("8 a second run changes nothing (it runs at every boot after the flip)",
      rc2 == 0 and before == after, "rc=%s out=%r" % (rc2, out2[-200:]))

# a box that was never on the old name
d9, s9, c9, p9 = fixture()
json.dump({"jobs": [{"name": "fresh", "cwd": "/data/pipebox/jobs/fresh"}]}, open(s9, "w"))
json.dump({"projects": {"/data/pipebox": {"hasTrustDialogAccepted": True}}}, open(c9, "w"))
rc3, out3 = run(s9, c9, p9)
check("9 a box born after the flip has nothing to migrate and says nothing about it",
      rc3 == 0 and "rewrote job" not in out3 and "workdir trust" not in out3, "rc=%s out=%r" % (rc3, out3))

# the transcripts symlink, when it still points into the old name
d10 = tempfile.mkdtemp(prefix="datamig-link-")
link = os.path.join(d10, "projects")
os.symlink("/work/claude/projects", link)
rc4, out4 = run(s9, c9, link)
check("10 the transcripts symlink is repointed when it still names the old volume",
      rc4 == 0 and os.readlink(link) == "/data/claude/projects", "rc=%s target=%r" % (rc4, os.readlink(link)))

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
