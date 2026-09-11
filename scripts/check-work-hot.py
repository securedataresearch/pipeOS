#!/usr/bin/env python3
"""/work's hot set in RAM (#264): pipeos-work stages hot.list from a tmpfs,
flushes RAM -> disk, parks /work read-only; the listener idles on a policy
denial instead of respawning; the mount carries commit=120. Rows run the
shipped script through its no-mount seam over a throwaway tree (mounts are
the box's; the probe pins the logic around them)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OVL = os.path.join(REPO, "overlay")
SCRIPT = os.path.join(OVL, "usr/local/bin/pipeos-work")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="workhot-")
WORK, HOT, DISK, RUN = (os.path.join(D, x) for x in ("work", "hot", "disk", "run"))
os.makedirs(WORK)
LIST = os.path.join(D, "hot.list")
open(LIST, "w").write("# comment\nlogs\n\npipeos/mdns\n.pipeos/ledger\n")
ENV = dict(os.environ, PIPEOS_WORK=WORK, PIPEOS_HOT=HOT, PIPEOS_WORK_DISK=DISK, PIPEOS_HOT_LIST=LIST,
           PIPEOS_WORK_NO_MOUNT="1", PIPEOS_WORK_RUN=RUN)
# under the seam the "disk" view is a separate dir, so seed it as the box's /work would be
os.makedirs(os.path.join(DISK, "logs"))
open(os.path.join(DISK, "logs", "old.log"), "w").write("from disk\n")
open(os.path.join(DISK, "logs", "stale.log"), "w").write("gone after flush\n")


def w(*args, env=None, script=SCRIPT):
    p = subprocess.run(["sh", script] + list(args), capture_output=True, text=True, env=env or ENV)
    return p.returncode, p.stdout + p.stderr


rc, out = w("hot-up")
check("1 hot-up creates every hot.list path in RAM, seeded from the disk copy (comments and blanks in the list ignored), and records the status",
      rc == 0 and open(os.path.join(HOT, "logs", "old.log")).read() == "from disk\n" and os.path.isdir(os.path.join(HOT, "pipeos", "mdns"))
      and os.path.isdir(os.path.join(HOT, ".pipeos", "ledger")) and open(os.path.join(RUN, "work.status")).read().startswith("hot "),
      "rc=%s out=%s" % (rc, out))
open(os.path.join(HOT, "logs", "new.log"), "w").write("written in RAM\n")
os.unlink(os.path.join(HOT, "logs", "stale.log"))
open(os.path.join(HOT, "pipeos", "mdns", "machines.json"), "w").write("{}")
rc, out = w("flush")
st = open(os.path.join(RUN, "work.status")).read()
check("2 flush carries RAM to disk with --delete: a new file lands, a file removed in RAM is removed on disk, the seed survives; the status records the flush",
      rc == 0 and open(os.path.join(DISK, "logs", "new.log")).read() == "written in RAM\n" and not os.path.exists(os.path.join(DISK, "logs", "stale.log"))
      and open(os.path.join(DISK, "logs", "old.log")).read() == "from disk\n" and os.path.exists(os.path.join(DISK, "pipeos", "mdns", "machines.json"))
      and "flushed" in st and "flushed 1 path" not in out and "3 path" in out, "rc=%s out=%s st=%s" % (rc, out, st))
rc_s, out_s = w("status")
check("3 status names the hot set, its paths, and the last flush", rc_s == 0 and "hot set:" in out_s and "logs" in out_s and "flushed" in out_s, out_s)
bad = open(os.path.join(D, "bad.list"), "w"); bad.write("../etc\n"); bad.close()
rc_b, out_b = w("flush", env=dict(ENV, PIPEOS_HOT_LIST=os.path.join(D, "bad.list")))
open(os.path.join(D, "abs.list"), "w").write("/etc\n")
rc_a, out_a = w("flush", env=dict(ENV, PIPEOS_HOT_LIST=os.path.join(D, "abs.list")))
check("4 a hot.list entry with .. or an absolute path is refused before anything is copied", rc_b != 0 and "refusing" in out_b and rc_a != 0 and "refusing" in out_a, "%s %s" % (out_b, out_a))
rc_p, out_p = w("park")
rc_u, out_u = w("unpark")
check("5 park flushes first, then (behind the no-mount seam) would remount read-only; unpark the reverse — both rc 0",
      rc_p == 0 and "flushed" in out_p and "read-only" in out_p and rc_u == 0, "%s %s" % (out_p, out_u))
rc_d, out_d = w("hot-down")
check("6 hot-down flushes and releases: the status file is gone, the disk holds the RAM copy", rc_d == 0 and not os.path.exists(os.path.join(RUN, "work.status"))
      and os.path.exists(os.path.join(DISK, "logs", "new.log")), out_d)
rc_f, out_f = w("flush")
check("7 flush with nothing staged is a no-op, not an error", rc_f == 0 and "nothing to flush" in out_f, out_f)

# ── the wiring ────────────────────────────────────────────────────────────
init = open(os.path.join(OVL, "etc/init.d/pipeos-hot")).read()
hotlist = [l for l in open(os.path.join(OVL, "usr/local/share/pipeos/hot.list")).read().split("\n") if l and not l.startswith("#")]
ws = open(os.path.join(OVL, "etc/local.d/workspace.sh")).read()
lbu = open(os.path.join(OVL, "etc/apk/protected_paths.d/lbu.list")).read()
build = open(os.path.join(REPO, "scripts/40-build-apkovl.sh")).read()
save = open(os.path.join(OVL, "usr/local/bin/pipeos-save")).read()
runner = open(os.path.join(OVL, "usr/local/bin/pipeos-schedule-run")).read()
listener = open(os.path.join(OVL, "usr/local/bin/pipebox-listener")).read()
selfcheck = open(os.path.join(OVL, "usr/local/bin/pipeos-selfcheck")).read()
hourly = os.path.join(OVL, "etc/periodic/hourly/pipeos-work-flush")
check("8 wiring: hot.list names logs, the roster, the ledger and the schedule state; pipeos-hot runs after the mount and before every writer incl. crond; it is in lbu.list and the build's default runlevel right after pipeos-workspace; the hourly flush exists and is executable",
      hotlist == ["logs", "pipeos/mdns", ".pipeos/ledger", ".pipeos/schedule"] and "after pipeos-workspace" in init
      and all(s in init for s in ("pipeos-web", "pipeos-mdns", "pipebox-listener", "crond", "pipeos-vault"))
      and "+etc/init.d/pipeos-hot" in lbu and "pipeos-workspace pipeos-hot " in build and os.access(hourly, os.X_OK) and "pipeos-work flush" in open(hourly).read(),
      repr(hotlist))
check("9 /work mounts with commit=120,lazytime,noatime; pipeos save flushes the hot set first; the schedule runner unparks a parked /work for the run and re-parks after (also on the one-at-a-time refusal)",
      "noatime,lazytime,commit=120" in ws and "pipeos-work flush" in save and save.index("pipeos-work flush") < save.index("tar ")
      and "pipeos-work unpark" in runner and runner.count("repark") >= 3 and "mount -o remount" not in runner, "")
check("10 the listener idles an hour on a policy denial (rc 5) and writes /run/pipeos/listener.status, instead of exiting into supervise-daemon's respawn loop (#258); selfcheck WARNs on that status and on a hot path that is not a mountpoint, and notes a parked /work",
      "sleep 3600; continue" in listener and "policy-denied" in listener and 'exit 1 ;;' not in listener.split('5) # terminal')[1].split('0) ;;')[0]
      and "listener.status" in selfcheck and "hot set not staged" in selfcheck and "parked" in selfcheck, "")

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
