#!/usr/bin/env python3
"""check-save (pipeOS#318): pipeos-save packages ONCE and the sha256 of the
candidate against the canonical is the only "nothing changed" decision.

The gate that stood at the top — a bare `lbu status` — compared against
<hostname>.apkovl.tar.gz, which pipeOS never writes, so it never fired; and
lbu's status is itself a full package plus an unpack, so even a working gate
would have packaged the tree to decide whether to package it. Its verdict is
mtime-gated too (a chmod reads as "nothing"), where the sha256 sees the bytes.

What a save must do on every tick: flush the RAM hot set (the owner's "flush
is the save"). What it must do only when it persists: the worksweep (GC
before every persist, not before every no-op). A stub lbu records its calls
and writes a candidate equal to the canonical (a clean tick) or different (a
change); stub pipeos-work and worksweep record theirs.
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SAVE = os.path.join(REPO, "overlay/usr/local/bin/pipeos-save")
PIPEOS = os.path.join(REPO, "overlay/usr/local/bin/pipeos")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", desc, "" if ok else "  <- " + detail))


D = tempfile.mkdtemp(prefix="save-")
MEDIA = os.path.join(D, "media"); os.makedirs(MEDIA)
BIN = os.path.join(D, "bin"); os.makedirs(BIN)
OVL = os.path.join(MEDIA, "pipeos.apkovl.tar.gz")


def tarball(text):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        ti = tarfile.TarInfo("etc/hostname"); data = text.encode(); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


open(OVL, "wb").write(tarball("box\n"))
PROV = os.path.join(D, "provisioned"); open(PROV, "w").close()
CALLS = os.path.join(D, "calls"); CAND = os.path.join(D, "candidate.tar.gz")
open(CAND, "wb").write(open(OVL, "rb").read())
for name, body in (("lbu", "echo \"lbu $1\" >> %s\ncase \"$1\" in package) cp %s \"$2\"; exit 0 ;; esac\necho 'stub: pipeos-save may only package' >&2; exit 9\n" % (CALLS, CAND)),
                   ("pipeos-work", "echo \"work $1\" >> %s\nexit 0\n" % CALLS),
                   ("worksweep", "echo sweep >> %s\nexit 0\n" % CALLS),
                   ("mount", "echo \"mount $2\" >> %s\nexit 0\n" % CALLS)):
    with open(os.path.join(BIN, name), "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(os.path.join(BIN, name), 0o755)
ENV = dict(os.environ, PIPEOS_MEDIA=MEDIA, PIPEOS_LBU=os.path.join(BIN, "lbu"), PIPEOS_PROVISIONED=PROV,
           PIPEOS_SAVE_LOCK=os.path.join(D, "lock"), PIPEOS_SAVE_WORK_BIN=os.path.join(BIN, "pipeos-work"),
           PIPEOS_SAVE_WORKSWEEP=os.path.join(BIN, "worksweep"), PIPEOS_SAVE_MOUNT=os.path.join(BIN, "mount"))


def run(env=None):
    try:
        os.unlink(CALLS)
    except OSError:
        pass
    p = subprocess.run(["sh", SAVE], capture_output=True, text=True, env=env or ENV)
    return p.returncode, p.stdout + p.stderr


def calls():
    try:
        return open(CALLS).read().splitlines()
    except OSError:
        return []


save = open(SAVE).read()
check("1 no `lbu status` gate anywhere in pipeos-save (it compared against a file pipeOS never writes, never fired, and was a full package in itself); the package + sha256 comparison is the one decision",
      not any("lbu status" in l for l in save.splitlines() if l.strip() and not l.strip().startswith("#")) and "lbu_canonical" not in save and '"$LBU" package' in save, "")
rc1, out1 = run(); c1 = calls()
check("2 a clean tick (the candidate equals the canonical): the hot set is flushed, lbu packages exactly once, the sha256 ends the save with rc 0 — and the worksweep does NOT run (GC before every persist, not before every no-op)",
      rc1 == 0 and c1 == ["work flush", "lbu package"], "rc=%s calls=%r out=%r" % (rc1, c1, out1[-200:]))
# the bytes the stub will hand over, kept — NOT a freshly built tarball to
# compare against later: gzip stamps its own mtime into the header, so two
# tarballs of the same content built in different seconds differ, and the
# comparison passes or fails on when the clock ticks (caught by CI, 2026-09-20)
changed_bytes = tarball("changed\n")
open(CAND, "wb").write(changed_bytes)
rc2, out2 = run(); c2 = calls()
canon_now = open(OVL, "rb").read()
check("3 a changed tick: flush, one package, the sha256 differs, the worksweep runs before the write, and the canonical on the media is the candidate (rotation kept the old one)",
      c2[:3] == ["work flush", "lbu package", "sweep"] and "mount remount,rw" in c2 and canon_now == changed_bytes and any(f.startswith("pipeos.") and f.endswith(".tar.gz") and f != "pipeos.apkovl.tar.gz" for f in os.listdir(MEDIA)),
      "rc=%s calls=%r out=%r media=%r" % (rc2, c2, out2[-200:], sorted(os.listdir(MEDIA))))
check("4 the flush precedes the package and the sweep follows the sha256 decision, in the text too (check-work-hot's order row reads the same file)",
      save.index('"$WORK_BIN" flush') < save.index('"$LBU" package') < save.index("sha256sum") < save.index('"$WORKSWEEP"'), "")
pipeos = open(PIPEOS).read()
lib = os.path.join(REPO, "overlay/usr/local/share/pipeos/lbu-canonical.sh")
dep = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-deploy-overlay")).read()
check("5 lbu_canonical (pipeos status/diff) lives in one shared file under usr/local/share/pipeos; pipeos sources it guarded (a missing helper is a reason on stderr, not a dead shell); deploy-overlay installs share/ before bin/, so no deploy has a window with the new bin and no helper",
      os.path.isfile(lib) and "lbu_canonical() {" in open(lib).read() and "lbu_canonical() {" not in pipeos.split("_lbu_lib=")[0]
      and '[ -r "$_lbu_lib" ]' in pipeos and dep.index("usr/local/share/pipeos\n") < dep.index("usr/local/bin\n"), "")
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
