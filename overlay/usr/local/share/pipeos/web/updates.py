#!/usr/bin/env python3
"""updates.py — what has changed on THIS Machine, newest first (pipeOS#335).

Sam: "we badly need an update log, just a way to scroll through and read the
titles and descriptions of the updates."

The Machine already writes down every update it takes. Two kinds:

  * an OVERLAY deploy — `pipeos deploy-overlay` logs
    `OK deployed <ref>=<sha>` with the time it finished;
  * an IMAGE/repo self-update — `pipeos-selfupdate` logs `image: applied
    <tag>` and `applied <digest>` when it takes a release.

The interesting part of a deploy is not the one commit it names: it is
everything that landed BETWEEN the commit this Machine was on and the one it
moved to. So each deploy is read as a range, and every commit in that range
becomes an entry with its own title and description — which is exactly what
`git log` already holds, in the clone the deploy itself reads.

No network, and no invention: a Machine with no clone (a customer's, updated
from releases rather than a checkout) still gets the deploys and the release
tags with their dates — it simply cannot fill in the descriptions, and says
so once rather than pretending.

Seams (the probe, never production): PIPEOS_UPDATES_DEPLOY_LOG,
_SELFUPDATE_LOG, _REPO, _CACHE, _GIT.
"""
import calendar
import fcntl
import json
import os
import re
import subprocess
import sys
import time

DEPLOY_LOG = os.environ.get("PIPEOS_UPDATES_DEPLOY_LOG", "/data/logs/deploy-overlay.log")
SELFUPDATE_LOG = os.environ.get("PIPEOS_UPDATES_SELFUPDATE_LOG", "/data/logs/selfupdate.log")
REPO = os.environ.get("PIPEOS_UPDATES_REPO", "/data/repos/pipeOS")
CACHE = os.environ.get("PIPEOS_UPDATES_CACHE", "/run/pipeos/updates.json")
GIT = os.environ.get("PIPEOS_UPDATES_GIT", "git")
MAX_ENTRIES = 400
MAX_BODY = 4000

# The lines the box really writes, and only those. pipeos-deploy-overlay:697
# `OK deployed $REF=$commit (N new, M changed)` and :702 `OK $did at
# $REF=$commit (nothing installed)`, where $did is "re-stamped", "regenerated
# the card outputs", or both. pipeos-selfupdate:227 `OK image: applied <tag>;
# rebooting` — NOT :180's `image: applied and not yet booted`, which says the
# opposite: an apply is staged and this boot is still the old one.
_DEPLOYED = re.compile(r"^(\S+) OK (?:deployed|.*? at) \S+=([0-9a-f]{7,40})")
_IMAGE = re.compile(r"^(\S+) OK image: applied ([^;\s]+)")


def _ts(stamp):
    """The log's own UTC stamp as epoch seconds; 0 when it will not parse.
    timegm, not mktime minus timezone: strptime leaves tm_isdst at -1, so
    mktime picks altzone for a summer date and lands an hour early — enough
    to move an entry across midnight in a list grouped by day."""
    try:
        return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return 0


def _git(*args):
    try:
        p = subprocess.run([GIT, "-C", REPO] + list(args), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _commits(lo, hi):
    """The commits a deploy brought: (lo, hi] when this Machine was on lo,
    else just hi. None when the clone cannot answer — a missing clone and an
    unknown commit are the same thing to a reader, and both are said once.

    A deploy can also go BACKWARDS (`deploy-overlay --from <older ref>`), and
    then lo..hi is empty although the Machine certainly changed. An empty
    forward range with a non-empty reverse one is exactly that, and it is
    reported as the rollback it is rather than dropped."""
    if lo:
        out = _git("log", "--no-merges", "--date-order", "--format=%H%x1f%ct%x1f%s%x1f%b%x1e", "%s..%s" % (lo, hi))
        if out is not None and not out.strip() and (_git("log", "--format=%H", "%s..%s" % (hi, lo)) or "").strip():
            back = _git("log", "-1", "--format=%H%x1f%ct%x1f%s", hi) or ""
            f = back.strip().split("\x1f")
            return [{"sha": (f[0] if f else hi)[:12], "at": int(f[1]) if len(f) > 1 else 0,
                     "title": "rolled back to %s" % (f[2] if len(f) > 2 else hi[:12]),
                     "body": "This Machine was moved back to an earlier commit — `deploy-overlay --from` names the ref it was sent to."}]
    else:
        out = _git("log", "--no-merges", "--date-order", "--format=%H%x1f%ct%x1f%s%x1f%b%x1e", "-1", hi)
    if out is None:
        return None
    rows = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split("\x1f")
        if len(parts) < 4:
            continue
        sha, at, title, body = parts[0], parts[1], parts[2], parts[3]
        # A squash-merge body opens by repeating its own subject as a bullet
        # — and the merge subject carries a " (#NN)" the bullet does not, so
        # the two are compared with that stripped. The reader already has the
        # title; showing it twice is noise.
        body = body.strip()
        if body.startswith("* "):
            first, _, rest = body.partition("\n")
            opener = first[2:].strip()
            # the merge subject carries a " (#NN)" for the pull request that
            # the body's bullet does not, so the bullet matches the title
            # either as it stands or with one such suffix taken off
            bare = re.sub(r"\s*\(#\d+\)\s*$", "", title).strip()
            if opener in (title.strip(), bare):
                body = rest.lstrip("\n")
        rows.append({"sha": sha[:12], "at": int(at or 0), "title": title, "body": body[:MAX_BODY]})
    return rows


def _lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except OSError:
        return []


def entries():
    """Every update this Machine has taken, newest first. Each carries when
    the Machine took it, so the list reads as this box's history and not as
    the project's."""
    out = []
    prev = None
    have_clone = _git("rev-parse", "--git-dir") is not None
    deploys = []
    for line in _lines(DEPLOY_LOG):
        m = _DEPLOYED.match(line)
        if m:
            deploys.append((_ts(m.group(1)), m.group(2)))
    for landed, sha in deploys:
        if prev == sha:
            continue                      # a re-stamp or a card heal moved nothing
        rows = _commits(prev, sha) if have_clone else None
        prev = sha
        if rows is None:
            out.append({"kind": "overlay", "landed": landed, "sha": sha[:12], "title": "overlay %s" % sha[:12],
                        "body": "", "unknown": True})
            continue
        for r in rows:
            out.append({"kind": "overlay", "landed": landed, "sha": r["sha"], "title": r["title"],
                        "body": r["body"], "at": r["at"]})
    for line in _lines(SELFUPDATE_LOG):
        m = _IMAGE.match(line)
        if m:
            out.append({"kind": "image", "landed": _ts(m.group(1)), "sha": m.group(2),
                        "title": "system image %s" % m.group(2),
                        "body": "The Machine fetched, verified and applied a released image, then rebooted into it."})
    out.sort(key=lambda e: (e.get("landed", 0), e.get("at", 0)), reverse=True)
    return {"at": int(time.time()), "clone": have_clone, "updates": out[:MAX_ENTRIES]}


def page(refresh=False):
    """The cached list. The deploy log only grows when a deploy happens, so
    the cache is valid while its size and mtime are unchanged — a reload of
    the page costs nothing, and a fresh deploy shows up at once."""
    key = []
    for path in (DEPLOY_LOG, SELFUPDATE_LOG):      # either log growing is new history
        try:
            st = os.stat(path)
            key.append("%d:%d" % (st.st_size, int(st.st_mtime)))
        except OSError:
            key.append("none")
    key = "|".join(key)
    if not refresh:
        try:
            with open(CACHE) as f:
                d = json.load(f)
            if d.get("key") == key:
                return d
        except (OSError, ValueError):
            pass
    # One rebuild at a time. A rebuild is one `git log` per recorded deploy —
    # 42 of them on a box that has been running a while — and /api/updates is
    # reachable by any signed-in reader, refresh included; without this, N
    # readers would each run the whole thing (the #335 review).
    lock = None
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        lock = open(CACHE + ".lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:                                        # someone may have just built it
            with open(CACHE) as f:
                d = json.load(f)
            if d.get("key") == key:
                return d
        except (OSError, ValueError):
            pass
    except OSError:
        pass
    try:
        d = entries()
        d["key"] = key
        try:
            tmp = CACHE + ".%d" % os.getpid()
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, CACHE)
        except OSError:
            pass
    finally:
        if lock is not None:
            lock.close()
    return d


def main(argv):
    d = page(refresh="--refresh" in argv)
    if "--json" in argv:
        print(json.dumps(d, indent=1))
        return 0
    if not d["updates"]:
        print("no updates recorded on this Machine yet")
        return 0
    if not d["clone"]:
        print("(no repo clone here, so only what arrived is listed — not what each change said)\n")
    for e in d["updates"]:
        when = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(e.get("landed", 0)))
        print("%s  %s  %s" % (when, e["sha"], e["title"]))
        for ln in (e.get("body") or "").splitlines():
            print("    " + ln)
        if e.get("body"):
            print("")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
