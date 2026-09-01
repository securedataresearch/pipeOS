#!/usr/bin/env python3
"""Probe for pipeos-install-backend (#193): the shipped script against its
seams. The dangerous halves (selfupdate, apk, save, verify) have their own
gates; these rows pin the wrapper's order and refusals."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "overlay/usr/local/bin/pipeos-install-backend")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def run(backend, repo_has_pkg, selfupdate_provides=False, apk_rc=0, verify_rc=0, save_rc=0):
    d = tempfile.mkdtemp(prefix="ckib-")
    repo = d + "/media/usb/apks/pipeos/x86_64"
    os.makedirs(repo, exist_ok=True)
    if repo_has_pkg:
        open(repo + "/antigravity-cli-1.0-r0.apk", "w").write("x")
    log = d + "/calls"
    def stub(name, rc, extra=""):
        p = d + "/" + name
        with open(p, "w") as f:
            f.write("#!/bin/sh\necho %s $* >> %s\n%s\nexit %d\n" % (name, log, extra, rc))
        os.chmod(p, 0o755)
        return p
    su_extra = ""
    if selfupdate_provides:
        su_extra = "touch %s/antigravity-cli-1.0-r0.apk" % repo
    env = dict(os.environ,
               PIPEOS_INSTALL_ROOT=d,
               PIPEOS_INSTALL_APK=stub("apk", apk_rc),
               PIPEOS_INSTALL_SELFUPDATE=stub("selfupdate", 0, su_extra),
               PIPEOS_INSTALL_SAVE=stub("save", save_rc),
               PIPEOS_INSTALL_VERIFY=stub("verify", verify_rc),
               PATH=d + "/bin:" + os.environ["PATH"])
    os.makedirs(d + "/bin", exist_ok=True)
    p = subprocess.run(["sh", BIN, backend], capture_output=True, text=True, env=env)
    calls = open(log).read() if os.path.exists(log) else ""
    state = ""
    try:
        state = open(d + "/run/pipeos/backend-install.state").read()
    except OSError:
        pass
    return p.returncode, p.stdout + p.stderr, calls, state


rc, out, calls, state = run("nope", True)
check("1 an unknown backend is refused", rc != 0 and "unknown backend" in out, out)

rc, out, calls, state = run("agy", True)
check("2 with the package on the media, install is apk add -> verify -> save, no selfupdate",
      rc == 0 and "selfupdate" not in calls
      and calls.splitlines()[0].startswith("apk add antigravity-cli")
      and [l.split()[0] for l in calls.splitlines()] == ["apk", "verify", "save"]
      and "step=done" in state, calls + state)

rc, out, calls, state = run("agy", False, selfupdate_provides=True)
check("3 a missing package syncs the release repo first, then installs",
      rc == 0 and calls.splitlines()[0].startswith("selfupdate"), calls)

rc, out, calls, state = run("agy", False, selfupdate_provides=False)
check("4 a release repo without the package is a named failure, nothing installed",
      rc != 0 and "does not carry" in out and "apk" not in [l.split()[0] for l in calls.splitlines()],
      out + calls)

rc, out, calls, state = run("agy", True, verify_rc=1)
check("5 a failed verify fails the install before any save",
      rc != 0 and "verify failed" in out and "save" not in [l.split()[0] for l in calls.splitlines()]
      and "step=failed" in state, out + calls)

rc, out, calls, state = run("agy", True, save_rc=1)
check("6 a failed save is a failure — an install that vanishes at reboot is not an install",
      rc != 0 and "survive a reboot" in out, out)

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
