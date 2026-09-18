#!/usr/bin/env python3
"""check-status-diff: `pipeos diff` and the status line count what a SAVE
would change — the live tree packaged now against the canonical apkovl —
not `lbu status`, which is an apk audit and lists every apkovl-added file
as "A" forever (pipeOS#307: "260 uncommitted change(s)" beside a passing
verify on zero). A stub `lbu package` tars a fixture tree; the canonical
apkovl is a tar of the same tree. No root, nothing outside a tempdir.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PIPEOS = os.environ.get("CHECK_PIPEOS_BIN", os.path.join(REPO, "overlay/usr/local/bin/pipeos"))
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="ckdiff-")
LIVE = os.path.join(D, "live")
MEDIA = os.path.join(D, "media")
BIN = os.path.join(D, "bin")
os.makedirs(os.path.join(LIVE, "etc/pipeos")); os.makedirs(MEDIA); os.makedirs(BIN)
for rel, body in (("etc/pipeos/card.conf", "NICK=box\n"), ("etc/pipeos/services.conf", "SERVICE_CLAUDE=on\n"), ("etc/hostname", "pipeos-a4e0\n")):
    with open(os.path.join(LIVE, rel), "w") as f:
        f.write(body)
    os.utime(os.path.join(LIVE, rel), (1_700_000_000, 1_700_000_000))


def pack(dst):
    with tarfile.open(dst, "w:gz") as t:
        for root, _, files in os.walk(LIVE):
            for n in files:
                p = os.path.join(root, n)
                t.add(p, arcname=os.path.relpath(p, LIVE))


OVL = os.path.join(MEDIA, "pipeos.apkovl.tar.gz")
pack(OVL)
with open(os.path.join(BIN, "lbu"), "w") as f:      # `lbu package OUT` — the live tree as an apkovl
    f.write("#!/bin/sh\n[ \"$1\" = package ] || exit 1\ncd %s && tar -czf \"$2\" .\n" % LIVE)
os.chmod(os.path.join(BIN, "lbu"), 0o755)
ENV = dict(os.environ, PIPEOS_MEDIA=MEDIA, PIPEOS_LBU=os.path.join(BIN, "lbu"))


def diff():
    p = subprocess.run(["sh", PIPEOS, "diff"], capture_output=True, text=True, env=ENV)
    return p.returncode, p.stdout + p.stderr


rc0, out0 = diff()
check("1 a live tree that packages to the canonical apkovl has no uncommitted changes (rc 0, says so) — even though every file here is one `lbu status` would list as Added",
      rc0 == 0 and "no uncommitted changes" in out0, repr((rc0, out0)))

with open(os.path.join(LIVE, "etc/pipeos/schedule.json"), "w") as f:
    f.write("{}\n")
with open(os.path.join(LIVE, "etc/pipeos/card.conf"), "a") as f:
    f.write("NAME=solo\n")
os.utime(os.path.join(LIVE, "etc/pipeos/card.conf"), (1_700_000_000 + 3600, 1_700_000_000 + 3600))
os.unlink(os.path.join(LIVE, "etc/hostname"))
rc1, out1 = diff()
lines = sorted(l for l in out1.splitlines() if l[:2] in ("+ ", "- ", "~ "))
check("2 a new file, an edited file and a removed file are named, once each, with their kind (+ new, ~ changed, - gone); nothing else is listed",
      rc1 == 0 and lines == ["+ etc/pipeos/schedule.json", "- etc/hostname", "~ etc/pipeos/card.conf"], repr((rc1, out1)))

src = open(PIPEOS).read()
i = src.index("cmd_status()"); j = src.index("cmd_verify()")
status_body = src[i:j]
check("3 the status line counts the same comparison (uncommitted_list), never `lbu status | wc -l`; a comparison that cannot be made is a WARN that says so, not a count",
      "uncommitted_list" in status_body and "lbu status" not in status_body and "cannot tell what a save would change" in status_body, "")

os.unlink(OVL)
rc2, out2 = diff()
check("4 with no canonical apkovl on the media, diff refuses (rc 2) and says why instead of listing the whole tree",
      rc2 == 2 and "cannot compare" in out2, repr((rc2, out2)))

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
