#!/usr/bin/env python3
"""check-save-gate (pipeOS#318): pipeos-save's nothing-changed gate compares
against OUR canonical apkovl. The old gate ran a bare `lbu status`, which looks
for <hostname>.apkovl.tar.gz — a file pipeOS never writes — so it listed every
file as Added and never fired: every 15-minute save ran the flush, the sweep
and a full `lbu package` before the sha256 skip. The gate now goes through the
shared lbu-canonical.sh (LBU_BACKUPDIR link dir, mount/umount shims), and a
gate that cannot answer falls through to the full save.

A stub lbu records its calls and answers what a row tells it to; the media is
a temp dir holding a canonical apkovl; seams: PIPEOS_MEDIA, PIPEOS_LBU,
PIPEOS_PROVISIONED, PIPEOS_SAVE_LOCK, PIPEOS_SAVE_UNCLAIM.
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
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", desc, "" if ok else "  <- " + detail))


D = tempfile.mkdtemp(prefix="savegate-")
MEDIA = os.path.join(D, "media"); os.makedirs(MEDIA)
BIN = os.path.join(D, "bin"); os.makedirs(BIN)
OVL = os.path.join(MEDIA, "pipeos.apkovl.tar.gz")
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as t:
    ti = tarfile.TarInfo("etc/hostname"); data = b"box\n"; ti.size = len(data); t.addfile(ti, io.BytesIO(data))
open(OVL, "wb").write(buf.getvalue())
PROV = os.path.join(D, "provisioned"); open(PROV, "w").close()
CALLS = os.path.join(D, "lbu.calls"); ANSWER = os.path.join(D, "lbu.answer"); STDERR = os.path.join(D, "lbu.stderr")
with open(os.path.join(BIN, "lbu"), "w") as f:
    f.write("#!/bin/sh\n"
            "echo \"$1 backupdir=${LBU_BACKUPDIR:-unset}\" >> %s\n"
            "case \"$1\" in\n"
            "  status)\n"
            "    [ -n \"$LBU_BACKUPDIR\" ] || { echo 'stub: LBU_BACKUPDIR unset' >&2; exit 9; }\n"
            "    [ -L \"$LBU_BACKUPDIR/$(hostname).apkovl.tar.gz\" ] || { echo 'stub: no hostname-named link to the canonical' >&2; exit 9; }\n"
            "    mount \"$LBU_BACKUPDIR\" && umount \"$LBU_BACKUPDIR\" || { echo 'stub: the mount/umount shims are missing' >&2; exit 9; }\n"
            "    [ -f %s ] && { cat %s >&2; exit 1; }\n"
            "    cat %s 2>/dev/null; exit 0 ;;\n"
            "  package) cp %s \"$2\"; exit 0 ;;\n"   # the candidate == the canonical -> the sha256 skip ends the save cleanly
            "esac\n" % (CALLS, STDERR, STDERR, ANSWER, OVL))
os.chmod(os.path.join(BIN, "lbu"), 0o755)


def run(unclaim=False):
    for p in (CALLS,):
        try:
            os.unlink(p)
        except OSError:
            pass
    env = dict(os.environ, PIPEOS_MEDIA=MEDIA, PIPEOS_LBU=os.path.join(BIN, "lbu"), PIPEOS_PROVISIONED=PROV,
               PIPEOS_SAVE_LOCK=os.path.join(D, "lock"))
    if unclaim:
        env["PIPEOS_SAVE_UNCLAIM"] = "1"
    p = subprocess.run(["sh", SAVE], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def calls():
    try:
        return [l.split()[0] for l in open(CALLS).read().splitlines()]
    except OSError:
        return []


# 1. a clean tree: the gate answers and the save ends before any packaging
open(ANSWER, "w").write("")
rc1, out1 = run(); c1 = calls()
check("1 a clean tree (lbu status against the canonical says nothing) ends the save before the flush, the sweep and `lbu package` run: rc 0, lbu called once, with LBU_BACKUPDIR, the hostname link and the mount shims",
      rc1 == 0 and c1 == ["status"], "rc=%s calls=%r out=%r" % (rc1, c1, out1[-200:]))

# 2. a changed tree: lbu status has a row -> the save goes on to package (and the sha256 skip ends it)
open(ANSWER, "w").write("U etc/pipeos/card.conf\n")
rc2, out2 = run(); c2 = calls()
check("2 a changed tree (one lbu status row) goes on to `lbu package`", rc2 == 0 and c2 == ["status", "package"], "rc=%s calls=%r out=%r" % (rc2, c2, out2[-200:]))

# 3. a gate that cannot answer is not a skip: lbu on stderr -> the full save runs
open(STDERR, "w").write("lbu: something about the media\n")
rc3, out3 = run(); c3 = calls()
os.unlink(STDERR)
check("3 a gate that cannot answer (lbu on stderr) falls through to the full save — never a silent skip", rc3 == 0 and c3 == ["status", "package"], "rc=%s calls=%r" % (rc3, c3))

# 4. the unclaim save never consults the gate (the provisioned marker is already gone — that IS the change)
open(ANSWER, "w").write("")
os.unlink(PROV)
rc4, out4 = run(unclaim=True); c4 = calls()
check("4 the unclaim save (PIPEOS_SAVE_UNCLAIM=1, no provisioned marker) skips the gate and packages", "status" not in c4 and "package" in c4, "rc=%s calls=%r out=%r" % (rc4, c4, out4[-200:]))

# 5. the helper is one file, sourced by both callers relative to themselves
pipeos = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
save = open(SAVE).read()
lib = os.path.join(REPO, "overlay/usr/local/share/pipeos/lbu-canonical.sh")
check("5 lbu_canonical lives in one shared file, sourced by pipeos and pipeos-save relative to $0 (the checkout and the box find it alike); neither defines its own copy",
      os.path.isfile(lib) and 'share/pipeos/lbu-canonical.sh' in pipeos and 'share/pipeos/lbu-canonical.sh' in save
      and "lbu_canonical() {" in open(lib).read() and "lbu_canonical() {" not in pipeos and "lbu_canonical() {" not in save, "")

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
