#!/usr/bin/env python3
"""Probe for scripts/verify-image-generic.sh (pipeOS#271): the guard that
keeps an OPERATOR image — the workstation's ssh key or a box's card baked
into the apkovl — out of a GitHub release. Builds three apkovls (generic,
key-baked, make-stick) and, with mtools present, a small FAT image at the
build's p1 offset holding each, then asserts the verdicts. Also pins that
80-publish-release.sh calls the guard before `gh release create` and that
50-build-image.sh stamps kind= — a guard nobody calls gates nothing.
Exit 0 if every row passes. Controls: check-release-guard-controls.py.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GUARD = os.environ.get("CHECK_RELEASE_GUARD_BIN",
                       os.path.join(HERE, "verify-image-generic.sh"))
PUBLISH = os.path.join(HERE, "80-publish-release.sh")
BUILD = os.path.join(HERE, "50-build-image.sh")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def apkovl(path, entries):
    with tarfile.open(path, "w:gz") as t:
        for name, content in entries.items():
            data = content.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o600
            t.addfile(ti, io.BytesIO(data))


def run(*args):
    p = subprocess.run(list(args), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


tmp = tempfile.mkdtemp(prefix="relguard.")
try:
    generic = os.path.join(tmp, "generic.tar.gz")
    keyed = os.path.join(tmp, "keyed.tar.gz")
    stick = os.path.join(tmp, "stick.tar.gz")
    base = {"etc/hostname": "pipeos\n", "etc/pipeos/card.conf": "# unprovisioned default\nMODEL=generic\n"}
    apkovl(generic, base)
    apkovl(keyed, dict(base, **{"root/.ssh/authorized_keys": "ssh-ed25519 AAAA operator\n"}))
    apkovl(stick, dict(base, **{"etc/pipeos/card.conf": "NICK=box4\nMODEL=fleet\n"}))

    rc, out = run(GUARD, "--apkovl", generic)
    check("generic apkovl passes (exit 0)", rc == 0, out.strip())
    rc, out = run(GUARD, "--apkovl", keyed)
    check("baked ssh key is refused (exit 2) and named", rc == 2 and "authorized_keys" in out, out.strip())
    rc, out = run(GUARD, "--apkovl", stick)
    check("make stick card (NICK set) is refused (exit 2) and named", rc == 2 and "NICK=box4" in out, out.strip())
    rc, out = run(GUARD, "--apkovl", os.path.join(tmp, "missing.tar.gz"))
    check("a missing apkovl refuses (exit 1), never passes", rc == 1, out.strip())
    with open(os.path.join(tmp, "junk.tar.gz"), "w") as f:
        f.write("not a tarball")
    rc, out = run(GUARD, "--apkovl", os.path.join(tmp, "junk.tar.gz"))
    check("an unreadable apkovl refuses (exit 1), never passes", rc == 1, out.strip())
    rc, out = run(GUARD)
    check("no argument is usage, exit 1", rc == 1 and "usage" in out, out.strip())

    # the image path: p1 at PART_OFFSET_MB (config.sh), FAT made the way the
    # build makes it (mformat + mcopy, no loop mounts)
    if shutil.which("mformat") and shutil.which("mcopy"):
        offset_mb = int(re.search(r"^PART_OFFSET_MB=(\d+)", open(os.path.join(REPO, "config.sh")).read(), re.M).group(1))
        for label, ovl, want_rc in (("generic", generic, 0), ("keyed", keyed, 2), ("stick", stick, 2)):
            img = os.path.join(tmp, label + ".img")
            p1 = os.path.join(tmp, label + ".p1")
            with open(p1, "wb") as f:
                f.truncate(8 * 1024 * 1024)
            env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
            subprocess.run(["mformat", "-i", p1, "-F", "::"], check=True, capture_output=True, env=env)
            subprocess.run(["mcopy", "-i", p1, ovl, "::/pipeos.apkovl.tar.gz"], check=True, capture_output=True, env=env)
            with open(img, "wb") as f:
                f.truncate((offset_mb + 8) * 1024 * 1024)
            subprocess.run(["dd", "if=" + p1, "of=" + img, "bs=1M", "seek=%d" % offset_mb, "conv=notrunc", "status=none"], check=True)
            rc, out = run(GUARD, img)
            check("image mode: %s image -> exit %d" % (label, want_rc), rc == want_rc, out.strip())
        empty = os.path.join(tmp, "empty.img")
        with open(empty, "wb") as f:
            f.truncate(4 * 1024 * 1024)
        rc, out = run(GUARD, empty)
        check("image mode: no FAT/apkovl in p1 refuses (exit 1), never passes", rc == 1, out.strip())
    else:
        print("SKIP image-mode rows: mtools not installed here")

    # wiring: the publisher calls the guard BEFORE gh release create, refuses on
    # a missing/newer .img, and the builder stamps kind=
    pub = open(PUBLISH).read()
    call = pub.find("verify-image-generic.sh")
    create = pub.find("gh release create")
    check("80-publish-release.sh runs the guard before `gh release create`", 0 < call < create)
    check("80-publish-release.sh refuses when the .img the .xz came from is missing or newer",
          "no $IMG_RAW beside it" in pub and '"$IMG_RAW" -nt "$IMG_XZ"' in pub)
    check("80-publish-release.sh refuses kind=operator in pipeos-image.txt", "kind=operator" in pub)
    bld = open(BUILD).read()
    check("50-build-image.sh stamps kind= from the same guard",
          'echo "kind=$IMAGE_KIND"' in bld and "verify-image-generic.sh" in bld)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("check-release-guard: %d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
