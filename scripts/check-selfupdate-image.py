#!/usr/bin/env python3
"""Probe for the automatic IMAGE update in pipeos-selfupdate (pipeOS#275) —
the step the review found could never fire (flash apply refused the lock the
self-update itself held) and the two ways it wrongly fired or held forever.

Runs the shipped pipeos-selfupdate with every side channel replaced by a
seam: a fake pipeos-flash that records fetch/apply, a fake reboot that
records, a fake pipeos-save, a chosen release commit (PIPEOS_RELEASE_COMMIT,
so no GitHub call), and PIPEOS_SELFUPDATE_IMAGE_ONLY to stop before the
package step. Asserts:
  - a different release commit -> fetch, apply (reached, not refused by the
    lock), reboot, image-updated stamp written;
  - the running commit already at the release -> hold, no apply;
  - an unknown release commit -> hold, never reflash on a guess;
  - a live session / a held schedule lock -> hold, no apply;
  - the flash caller seam is what lets apply run under the self-update lock.
Exit 0 if every row passes. Controls: none (the seams ARE the test; the
apply-reached row is itself the control for the lock bug).
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfupdate")
FLASH = os.path.join(REPO, "overlay/usr/local/bin/pipeos-flash")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def run(d, release_commit, running_commit="0000old", *, sched_locked=False,
        who="", tmux_client=False, flash_apply_rc=0, real_flash=False):
    """One pipeos-selfupdate run, image step only. Returns (rc, output, marks)
    where marks is the dict of recorder files the fakes wrote."""
    bindir = os.path.join(d, "bin")
    os.makedirs(bindir, exist_ok=True)
    marks = os.path.join(d, "marks")
    os.makedirs(marks, exist_ok=True)
    # fake pipeos-flash: record argv (so we see fetch/apply and the caller seam)
    flash_path = FLASH if real_flash else os.path.join(bindir, "pipeos-flash")
    if not real_flash:
        with open(flash_path, "w") as f:
            f.write("#!/bin/sh\n"
                    'echo "$PIPEOS_FLASH_CALLER $*" >> %s/flash.log\n'
                    'case "$1" in apply) exit %d ;; esac\nexit 0\n' % (marks, flash_apply_rc))
        os.chmod(flash_path, 0o755)
    for name, body in (("reboot", 'echo rebooted >> %s/reboot.log\n' % marks),
                       ("pipeos-save", 'echo saved >> %s/save.log\n' % marks),
                       ("who", 'printf "%s"\n' % who),
                       ("tmux", ("echo client\n" if tmux_client else "exit 1\n"))):
        with open(os.path.join(bindir, name), "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(os.path.join(bindir, name), 0o755)
    conf = os.path.join(d, "selfupdate.conf")
    open(conf, "w").write("UPDATE_RELEASE_URL=https://example.invalid/releases/latest/download\nUPDATE_URL=\nIMAGE_UPDATE=auto\n")
    imgtxt = os.path.join(d, "pipeos-image.txt")
    open(imgtxt, "w").write("variant=usb\nkind=generic\ncommit=%s\n" % running_commit)
    # a socket dir for the tmux glob; a live client is simulated by the tmux stub
    sockdir = os.path.join(d, "run/pipeos/assistant")
    os.makedirs(sockdir, exist_ok=True)
    if tmux_client:
        import socket as _s
        s = _s.socket(_s.AF_UNIX)
        try:
            s.bind(os.path.join(sockdir, "tmux.sock"))
        except OSError:
            pass
    sched_lock = os.path.join(d, "schedule.lock")
    open(sched_lock, "w").close()
    env = dict(os.environ,
               PATH=bindir + ":" + os.environ["PATH"],
               PIPEOS_SELFUPDATE_CONF=conf,
               PIPEOS_FLASH_BIN=flash_path,
               PIPEOS_REBOOT_BIN=os.path.join(bindir, "reboot"),
               PIPEOS_SAVE_BIN=os.path.join(bindir, "pipeos-save"),
               PIPEOS_IMAGE_TXT=imgtxt,
               PIPEOS_IMAGE_UPDATED=os.path.join(d, "image-updated"),
               PIPEOS_FLASH_APPLIED=os.path.join(d, "flash.applied"),
               PIPEOS_SCHED_LOCK=sched_lock,
               PIPEOS_PTS_DIR=os.path.join(d, "empty-pts"),
               PIPEOS_RELEASE_COMMIT=release_commit,
               PIPEOS_SELFUPDATE_LOG=os.path.join(d, "selfupdate.log"),
               PIPEOS_SELFUPDATE_LOCK=os.path.join(d, "su.lock"),
               PIPEOS_SELFUPDATE_IMAGE_ONLY="1")
    os.makedirs(env["PIPEOS_PTS_DIR"], exist_ok=True)
    # point the tmux socket glob at our dir by faking /run/pipeos via the
    # assistant socket path the script hard-codes; the script globs
    # /run/pipeos/... which we cannot relocate, so the tmux stub carries the
    # "attached client" signal instead and we only exercise who + stub here.
    if sched_locked:
        # hold the lock from a helper that outlives the run
        holder = subprocess.Popen(["sh", "-c", "exec 8>>%s; flock 8; sleep 5" % sched_lock])
        import time
        time.sleep(0.3)
    p = subprocess.run(["sh", BIN], capture_output=True, text=True, env=env)
    if sched_locked:
        holder.kill()
    def mark(name):
        f = os.path.join(marks, name)
        return open(f).read() if os.path.exists(f) else ""
    return p.returncode, p.stdout + p.stderr, {"flash": mark("flash.log"), "reboot": mark("reboot.log"),
                                               "updated": os.path.exists(env["PIPEOS_IMAGE_UPDATED"])}


with tempfile.TemporaryDirectory() as base:
    def d():
        return tempfile.mkdtemp(dir=base)

    rc, out, m = run(d(), release_commit="abc123new", running_commit="0000old")
    check("a newer release commit: fetch AND apply run (apply is REACHED, not refused by the self-update lock — the bug the review caught), then reboot, and the image-updated stamp is written",
          "apply" in m["flash"] and "fetch" in m["flash"] and m["reboot"] and m["updated"],
          "flash=%r reboot=%r updated=%s out=%s" % (m["flash"], m["reboot"], m["updated"], out[-200:]))
    check("apply is invoked with PIPEOS_FLASH_CALLER=selfupdate (the seam that lets it run under our lock)",
          "selfupdate apply" in m["flash"], m["flash"])

    rc, out, m = run(d(), release_commit="0000old", running_commit="0000old123")
    check("the running commit already at the release -> HOLD, no apply, no reboot",
          "apply" not in m["flash"] and not m["reboot"] and not m["updated"], "flash=%r" % m["flash"])

    rc, out, m = run(d(), release_commit="", running_commit="0000old")
    check("an unknown release commit -> HOLD, never reflash on a guess (a non-GitHub origin / API down)",
          "apply" not in m["flash"] and not m["reboot"], "flash=%r out=%s" % (m["flash"], out[-160:]))

    rc, out, m = run(d(), release_commit="abc123new", running_commit="", )
    check("an unknown running commit -> HOLD", "apply" not in m["flash"], m["flash"])

    rc, out, m = run(d(), release_commit="abc123new", running_commit="0000old", who="root pts/0 2026")
    check("a live login (who) -> HOLD, no apply", "apply" not in m["flash"] and not m["reboot"], "flash=%r" % m["flash"])

    rc, out, m = run(d(), release_commit="abc123new", running_commit="0000old", sched_locked=True)
    check("a held schedule lock (a job running) -> HOLD, no apply", "apply" not in m["flash"] and not m["reboot"], "flash=%r" % m["flash"])

    # the seam is load-bearing: pipeos-flash refuses the /run/pipeos-selfupdate.lock
    # UNLESS the caller says selfupdate, and pipeos-selfupdate sets it on apply.
    flash_src = open(FLASH).read()
    su_src = open(BIN).read()
    check("pipeos-flash skips the self-update-lock refusal only for PIPEOS_FLASH_CALLER=selfupdate, and pipeos-selfupdate sets it on the apply — without this the auto-apply is always refused (#275 review)",
          '"${PIPEOS_FLASH_CALLER:-}" != selfupdate' in flash_src
          and "/run/pipeos-selfupdate.lock" in flash_src.split('!= selfupdate')[1][:200]
          and "PIPEOS_FLASH_CALLER=selfupdate $FLASH_BIN apply" in su_src)

print("check-selfupdate-image: %d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
