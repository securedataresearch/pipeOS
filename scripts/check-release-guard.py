#!/usr/bin/env python3
"""Probe for the release guard (pipeOS#271): scripts/verify-image-generic.sh
keeps an OPERATOR image — the workstation's ssh key, a console root password
or a box's card baked into the apkovl — out of a GitHub release.

Rows: three apkovls (generic / key / root password / stick card) through the
guard's --apkovl mode; the same inside small FAT images at the build's p1
offset (image mode; needs mtools); the guard's fail-closed edges (missing,
unreadable, empty listing, no FAT); and the publisher itself, run end to end
against a throwaway out/ (OUT= seam in config.sh) with a stub `gh` on PATH
that records its argv: a generic .xz is attached, an operator .xz is NOT and
`gh release create` still runs for the apk repo alone, and the .xz — not the
.img beside it — is what is inspected.
Exit 0 if every row passes. Controls: check-release-guard-controls.py.
"""
import io
import lzma
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GUARD = os.environ.get("CHECK_RELEASE_GUARD_BIN", os.path.join(HERE, "verify-image-generic.sh"))
PUBLISH = os.path.join(HERE, "80-publish-release.sh")
BUILD = os.path.join(HERE, "50-build-image.sh")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def targz(path, entries):
    with tarfile.open(path, "w:gz") as t:
        for name, content in entries.items():
            data = content.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o600
            t.addfile(ti, io.BytesIO(data))


def run(*args, env=None, cwd=None):
    p = subprocess.run(list(args), capture_output=True, text=True, env=env, cwd=cwd)
    return p.returncode, p.stdout + p.stderr


SHADOW_LOCKED = "root:*:19000:0:::::\nbin:!::0:::::\n"
SHADOW_PW = "root:$6$saltsalt$hashhashhashhash:19000:0:::::\nbin:!::0:::::\n"
BASE = {"etc/hostname": "pipeos\n", "etc/shadow": SHADOW_LOCKED,
        "etc/pipeos/card.conf": "# unprovisioned default\nMODEL=generic\n"}

tmp = tempfile.mkdtemp(prefix="relguard.")
try:
    ovl = {}
    for label, extra in (("generic", {}),
                         ("keyed", {"root/.ssh/authorized_keys": "ssh-ed25519 AAAA operator\n"}),
                         ("rootpw", {"etc/shadow": SHADOW_PW}),
                         ("stick", {"etc/pipeos/card.conf": "NICK=box4\nMODEL=fleet\n"})):
        ovl[label] = os.path.join(tmp, label + ".tar.gz")
        targz(ovl[label], dict(BASE, **extra))
    # a big one: the listing must outgrow a pipe buffer and STILL trip
    big = dict(BASE, **{"root/.ssh/authorized_keys": "ssh-ed25519 AAAA operator\n"})
    big.update({"usr/share/filler/%05d/some-long-file-name-to-fill-the-listing.txt" % i: "" for i in range(6000)})
    ovl["bigkeyed"] = os.path.join(tmp, "bigkeyed.tar.gz")
    targz(ovl["bigkeyed"], big)

    rc, out = run(GUARD, "--apkovl", ovl["generic"])
    check("generic apkovl passes (exit 0)", rc == 0, out.strip())
    rc, out = run(GUARD, "--apkovl", ovl["keyed"])
    check("baked ssh key is refused (exit 2) and named", rc == 2 and "authorized_keys" in out, out.strip())
    rc, out = run(GUARD, "--apkovl", ovl["rootpw"])
    check("a console root password (ROOT_LOGIN=password) is refused (exit 2) and named",
          rc == 2 and "root has a password" in out, out.strip())
    rc, out = run(GUARD, "--apkovl", ovl["stick"])
    check("make stick card (NICK set) is refused (exit 2) and named", rc == 2 and "NICK=box4" in out, out.strip())
    rc, out = run(GUARD, "--apkovl", ovl["bigkeyed"])
    check("a key in an apkovl whose listing outgrows a pipe buffer is STILL refused (exit 2) — no fail-open under pipefail",
          rc == 2 and "authorized_keys" in out, "rc=%s %s" % (rc, out.strip()[-160:]))
    rc, out = run(GUARD, "--apkovl", os.path.join(tmp, "missing.tar.gz"))
    check("a missing apkovl refuses (exit 1), never passes", rc == 1, out.strip())
    with open(os.path.join(tmp, "junk.tar.gz"), "w") as f:
        f.write("not a tarball")
    rc, out = run(GUARD, "--apkovl", os.path.join(tmp, "junk.tar.gz"))
    check("an unreadable apkovl refuses (exit 1), never passes", rc == 1, out.strip())
    rc, out = run(GUARD)
    check("no argument is usage, exit 1", rc == 1 and "usage" in out, out.strip())

    offset_mb = int(re.search(r"^PART_OFFSET_MB=(\d+)", open(os.path.join(REPO, "config.sh")).read(), re.M).group(1))

    def fat_image(path, apkovl_path, size_mb=8):
        p1 = path + ".p1"
        with open(p1, "wb") as f:
            f.truncate(size_mb * 1024 * 1024)
        env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
        subprocess.run(["mformat", "-i", p1, "-F", "::"], check=True, capture_output=True, env=env)
        subprocess.run(["mcopy", "-i", p1, apkovl_path, "::/pipeos.apkovl.tar.gz"], check=True, capture_output=True, env=env)
        with open(path, "wb") as f:
            f.truncate((offset_mb + size_mb) * 1024 * 1024)
        subprocess.run(["dd", "if=" + p1, "of=" + path, "bs=1M", "seek=%d" % offset_mb, "conv=notrunc", "status=none"], check=True)
        os.unlink(p1)

    have_mtools = bool(shutil.which("mformat") and shutil.which("mcopy"))
    if have_mtools:
        for label, want_rc in (("generic", 0), ("keyed", 2), ("rootpw", 2), ("stick", 2)):
            img = os.path.join(tmp, label + ".img")
            fat_image(img, ovl[label])
            rc, out = run(GUARD, img)
            check("image mode: %s image -> exit %d" % (label, want_rc), rc == want_rc, out.strip())
        empty = os.path.join(tmp, "empty.img")
        with open(empty, "wb") as f:
            f.truncate(4 * 1024 * 1024)
        rc, out = run(GUARD, empty)
        check("image mode: no FAT/apkovl in p1 refuses (exit 1), never passes", rc == 1, out.strip())
    else:
        print("SKIP image-mode rows: mtools not installed here")

    # ---- the publisher, end to end, against a throwaway out/ and a stub gh
    if have_mtools and shutil.which("xz"):
        def publish(label_xz, label_img=None):
            """Stage OUT with a minimal signed-looking repo and the given image
            (.xz from label_xz; a raw .img from label_img beside it, to prove the
            .xz is what is inspected). Returns (rc, output, gh argv lines)."""
            out_dir = os.path.join(tmp, "out-" + label_xz + (label_img or ""))
            repo_dir = os.path.join(out_dir, "repo/pipeos/x86_64")
            os.makedirs(repo_dir)
            targz(os.path.join(repo_dir, "APKINDEX.tar.gz"), {"APKINDEX": "P:foo\nV:1.0-r0\n\n"})
            targz(os.path.join(repo_dir, "foo-1.0-r0.apk"), {".PKGINFO": "pkgname = foo\n"})
            img = os.path.join(tmp, label_xz + ".img")
            with open(img, "rb") as f, lzma.open(os.path.join(out_dir, "pipeos-usb.img.xz"), "wb") as z:
                shutil.copyfileobj(f, z)
            if label_img:
                shutil.copy(os.path.join(tmp, label_img + ".img"), os.path.join(out_dir, "pipeos-usb.img"))
            bindir = os.path.join(out_dir, "bin")
            os.makedirs(bindir)
            log = os.path.join(out_dir, "gh.log")
            with open(os.path.join(bindir, "gh"), "w") as f:
                f.write("#!/bin/sh\necho \"$@\" >> %s\n" % log)
            os.chmod(os.path.join(bindir, "gh"), 0o755)
            env = dict(os.environ, OUT=out_dir, PATH=bindir + ":" + os.environ["PATH"])
            rc, text = run("bash", PUBLISH, env=env, cwd=REPO)
            gh = open(log).read() if os.path.exists(log) else ""   # one call, its notes span lines
            return rc, text, gh

        rc, text, gh = publish("generic")
        check("publisher: a generic .xz is inspected, attached, and gh release create runs with it",
              rc == 0 and gh.count("release create") == 1 and "pipeos-usb.img.xz" in gh
              and "pipeos-usb.img.xz.sha256" in gh and "including flashable image" in text,
              "rc=%s gh=%r %s" % (rc, gh, text.strip()[-300:]))
        rc, text, gh = publish("keyed")
        check("publisher: an operator .xz is REFUSED — not attached, said loudly, the apk repo still publishes alone",
              rc == 0 and gh.count("release create") == 1 and "pipeos-usb.img.xz" not in gh
              and "OPERATOR" in text and "authorized_keys" in text,
              "rc=%s gh=%r %s" % (rc, gh, text.strip()[-300:]))
        rc, text, gh = publish("keyed", label_img="generic")
        check("publisher: the .xz is what is inspected — a generic .img beside an operator .xz does not launder it",
              rc == 0 and gh.count("release create") == 1 and "pipeos-usb.img.xz" not in gh and "OPERATOR" in text,
              "rc=%s gh=%r %s" % (rc, gh, text.strip()[-300:]))
        check("publisher: the scratch decompressed image is removed afterwards",
              not any(os.path.exists(os.path.join(tmp, d, ".release-check.img")) for d in os.listdir(tmp) if d.startswith("out-")))
    else:
        print("SKIP publisher rows: mtools/xz not installed here")

    bld = open(BUILD).read()
    check("50-build-image.sh stamps kind= from the guard's three exits (0 generic, 2 operator, else refuse the build)",
          'echo "kind=$IMAGE_KIND"' in bld and "verify-image-generic.sh" in bld
          and "0) IMAGE_KIND=generic" in bld and "2) IMAGE_KIND=operator" in bld
          and "refusing to build an image from an apkovl the guard cannot read" in bld)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("check-release-guard: %d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
