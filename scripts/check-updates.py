#!/usr/bin/env python3
"""check-updates (pipeOS#335): the Machine's own update log.

Sam: "we badly need an update log, just a way to scroll through and read the
titles and descriptions of the updates."

A Machine already writes down every update it takes — `OK deployed <ref>=<sha>`
from deploy-overlay, `image: applied <tag>` from selfupdate. The interesting
part of a deploy is everything that landed BETWEEN the commit the Machine was
on and the one it moved to, so each deploy is read as a range and every commit
in it becomes an entry with its own title and description, out of the clone
the deploy itself reads.

Driven through the module's seams against a throwaway git repo, so the
expected titles are ones this probe wrote.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
UPD = os.path.join(REPO, "overlay/usr/local/share/pipeos/web/updates.py")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", desc, "" if ok else "  <- " + detail))


D = tempfile.mkdtemp(prefix="updates-")
GR = os.path.join(D, "repo"); os.makedirs(GR)


def git(*a, **kw):
    return subprocess.run(["git", "-C", GR] + list(a), capture_output=True, text=True, **kw)


git("init", "-q", "-b", "main")
git("config", "user.email", "p@x"); git("config", "user.name", "probe")
shas = []
for subject, body in (("the first thing", "why the first thing was done\nover two lines"),
                      ("the second thing (#12)", "* the second thing (#12)\n\nwhy the second thing was done"),
                      ("the third thing", ""),
                      ("the fourth thing", "why the fourth")):
    open(os.path.join(GR, "f"), "a").write(subject + "\n")
    git("add", "-A")
    git("commit", "-q", "-m", subject, "-m", body) if body else git("commit", "-q", "-m", subject)
    shas.append(git("rev-parse", "HEAD").stdout.strip())

DL = os.path.join(D, "deploy.log"); SL = os.path.join(D, "selfupdate.log")
# The lines the box REALLY writes — pipeos-deploy-overlay:697/:702 and
# pipeos-selfupdate:180/:227, quoted from those files. A probe that invents
# the format agrees with the code and not with the Machine, which is how the
# first cut of this shipped a regex matching nothing (the #335 review).
open(DL, "w").write(
    "2026-09-18T10:00:00Z deploying origin/main=%s (1 new, 0 changed)\n" % shas[0] +
    "2026-09-18T10:00:05Z OK deployed origin/main=%s (1 new, 0 changed)\n" % shas[0] +
    "2026-09-19T11:00:05Z OK deployed origin/main=%s (0 new, 2 changed)\n" % shas[2] +
    "2026-09-19T11:30:05Z OK re-stamped at origin/main=%s (nothing installed)\n" % shas[2] +
    "2026-09-19T11:40:05Z OK regenerated the card outputs at origin/main=%s (nothing installed)\n" % shas[2] +
    "2026-09-19T12:00:05Z OK deployed origin/main=%s (0 new, 1 changed)\n" % shas[3])
open(SL, "w").write("2026-09-19T09:00:01Z image: up to date (repo-old)\n"
                    "2026-09-19T09:10:01Z image: applied and not yet booted\n"
                    "2026-09-19T09:30:01Z OK image: applied repo-2026.09.19-abc1234; rebooting\n")


def run(*args, repo=GR, cache=None):
    env = dict(os.environ, PIPEOS_UPDATES_DEPLOY_LOG=DL, PIPEOS_UPDATES_SELFUPDATE_LOG=SL,
               PIPEOS_UPDATES_REPO=repo, PIPEOS_UPDATES_CACHE=cache or os.path.join(D, "cache.json"))
    p = subprocess.run([sys.executable, UPD] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


rc, out = run("--json")
d = json.loads(out) if rc == 0 else {}
titles = [u["title"] for u in d.get("updates", [])]
check("1 a deploy is read as the RANGE it moved through, not the one commit it names: the jump that skipped two commits lists both, newest first, and every entry carries its own title; the image entry is the one the Machine really applied, not the 'applied and not yet booted' line that says the opposite",
      rc == 0 and titles == ["the fourth thing", "the third thing", "the second thing (#12)",
                             "system image repo-2026.09.19-abc1234", "the first thing"],
      "titles=%r" % titles)
by = {u["title"]: u for u in d.get("updates", [])}
check("2 the description is the commit's own body, and a squash-merge body that opens by repeating its subject as a bullet does not say it twice (the row already shows the title)",
      by["the first thing"]["body"] == "why the first thing was done\nover two lines"
      and by["the second thing (#12)"]["body"] == "why the second thing was done"
      and by["the third thing"]["body"] == "",
      repr({k: v["body"][:40] for k, v in by.items()}))
check("3 each entry is stamped with when THIS Machine took it, not when the change was written — the list is the box's history; a re-stamp and a card heal, which the deployer writes as 'OK <did> at <ref>=<sha>', move nothing and add nothing; an image release is an entry of its own",
      by["the third thing"]["landed"] == by["the second thing (#12)"]["landed"]
      and by["the fourth thing"]["landed"] > by["the third thing"]["landed"]
      and len([u for u in d["updates"] if u["kind"] == "image"]) == 1
      and [u["title"] for u in d["updates"]].count("the third thing") == 1,
      repr([(u["title"], u["landed"]) for u in d["updates"]]))
rc4, out4 = run(repo=os.path.join(D, "no-such-repo"), cache=os.path.join(D, "c4.json"))
check("4 a Machine with no clone (a customer's, updated from releases) still gets what arrived and when, says once that it cannot say what each change was for, and does not invent it",
      rc4 == 0 and "no repo clone here" in out4 and "the first thing" not in out4 and out4.count("overlay ") >= 2,
      out4[:200])
# 4b. a deploy that went BACKWARDS (deploy-overlay --from an older ref) is a
# real change to the Machine and must not vanish into an empty forward range
DLB = os.path.join(D, "deploy-back.log")
open(DLB, "w").write("2026-09-18T10:00:05Z OK deployed origin/main=%s (1 new, 0 changed)\n" % shas[3] +
                     "2026-09-18T12:00:05Z OK deployed feat=%s (0 new, 3 changed)\n" % shas[1])
envb = dict(os.environ, PIPEOS_UPDATES_DEPLOY_LOG=DLB, PIPEOS_UPDATES_SELFUPDATE_LOG=SL,
            PIPEOS_UPDATES_REPO=GR, PIPEOS_UPDATES_CACHE=os.path.join(D, "cb.json"))
pb = subprocess.run([sys.executable, UPD, "--json"], capture_output=True, text=True, env=envb)
db = json.loads(pb.stdout) if pb.returncode == 0 else {}
check("4b a deploy that moved the Machine BACKWARDS says so — `git log lo..hi` is empty for a rollback, and dropping it would leave the box's own history claiming nothing happened",
      pb.returncode == 0 and any(u["title"].startswith("rolled back to") for u in db.get("updates", [])),
      repr([u["title"] for u in db.get("updates", [])]))

c5 = os.path.join(D, "c5.json")
rc5a, _ = run("--json", cache=c5)
mt = os.stat(c5).st_mtime
rc5b, _ = run("--json", cache=c5)
same = os.stat(c5).st_mtime == mt
open(DL, "a").write("2026-09-19T13:00:05Z OK deployed origin/main=%s (0 new, 1 changed)\n" % shas[0])
rc5c, out5c = run("--json", cache=c5)
mt2 = os.stat(c5).st_mtime
open(SL, "a").write("2026-09-19T14:00:01Z OK image: applied repo-2026.09.19-def5678; rebooting\n")
rc5d, _ = run("--json", cache=c5)
check("5 the cache is keyed to BOTH logs' size and mtime: reading the page again costs nothing, and a fresh deploy — or a fresh image release, which the deploy log knows nothing about — shows up at once with no refresh",
      rc5a == 0 and rc5b == 0 and same and rc5c == 0 and os.stat(c5).st_mtime != mt
      and rc5d == 0 and os.stat(c5).st_mtime != mt2,
      "unchanged=%s" % same)
web = open(os.path.join(REPO, "overlay/usr/local/share/pipeos/web/webd.py")).read()
app = open(os.path.join(REPO, "overlay/usr/local/share/pipeos/web/static/app.js")).read()
css = open(os.path.join(REPO, "overlay/usr/local/share/pipeos/web/static/style.css")).read()
front = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
check("6 the dashboard has it: GET /api/updates for a signed-in reader (not in PEER_GETS — a member's certificate may not read another Machine's history), an Updates view in the nav that loads when it is opened (LAZY, not a poller that would no-op forever) with a look-again button, styles for the rows, `pipeos updates` for the terminal, and the verb in check-operator-verbs",
      '"/api/updates": self.api_updates' in web and '"/api/updates"' not in web.split("PEER_GETS = ")[1].split("\n")[0]
      and 'navItem("updates", "Updates")' in app and 'id="uplist"' in app
      and ".upbody" in css and "updates)     shift; exec python3" in front and "pipeos updates" in front
      and "updates: loadUpdates" in app and 'id="uprefresh"' in app
      and '"pipeos updates"' in open(os.path.join(REPO, "scripts/check-operator-verbs.py")).read(), "")

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
