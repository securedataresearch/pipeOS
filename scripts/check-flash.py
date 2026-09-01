#!/usr/bin/env python3
"""Probe for pipeos-flash (#181). The apply path cannot run whole here — it
mounts, unmounts and dd's a box's boot media — so the guard functions run
against fixtures through the script's own seams (PIPEOS_FLASH_DEV, _SYS,
_MOUNTS, _NO_MOUNT, _GENERIC, _IDENTITY), and one full `apply --image` runs
against a sparse fake device, asserting the write's exact bounds.

The fixture image is partitioned with the SAME sfdisk stanza the build uses
(50-build-image.sh), so the geometry parser is tested against the real
layout, not a paraphrase of it. DEPLOY_PATHS/NEVER must match
pipeos-deploy-overlay's — a merge that drifts from the deployer's idea of
"the overlay" is the defect this fleet keeps refinding.

Exit 0 if every row passes. Controls: check-flash-controls.py.
"""
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.environ.get("CHECK_FLASH_BIN",
                     os.path.join(REPO, "overlay/usr/local/bin/pipeos-flash"))
DEPLOYER = os.path.join(REPO, "overlay/usr/local/bin/pipeos-deploy-overlay")
RESULTS = []
TMPS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def sh(script, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=e)
    return p.returncode, p.stdout + p.stderr


def mkimg(d, size_mb=16, p1_size_mb=8, start_mb=1):
    """A fixture disk image partitioned exactly as 50-build-image.sh does."""
    img = os.path.join(d, "fix.img")
    with open(img, "wb") as f:
        f.truncate(size_mb * 1024 * 1024)
    stanza = "label: gpt\nstart=%d, size=%d, type=uefi, name=\"PIPEOS\"\n" % (
        start_mb * 2048, p1_size_mb * 2048)
    p = subprocess.run(["sfdisk", "-q", img], input=stanza,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return img


def apkovl(d, name, files, world):
    root = os.path.join(d, name + ".root")
    for path, content in files.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write(content)
    w = os.path.join(root, "etc/apk/world")
    os.makedirs(os.path.dirname(w), exist_ok=True)
    open(w, "w").write("\n".join(world) + "\n")
    out = os.path.join(d, name + ".apkovl.tar.gz")
    with tarfile.open(out, "w:gz") as t:
        for entry in sorted(os.listdir(root)):
            t.add(os.path.join(root, entry), arcname=entry)
    return out


def newdir():
    d = tempfile.mkdtemp(prefix="ckfl-")
    TMPS.append(d)
    return d


def run_fns(body, env=None):
    """Source the shipped script's functions (its library seam), run body."""
    e = {"_PIPEOS_FLASH_LIB": "1"}
    if env:
        e.update(env)
    return sh(". %s\n%s" % (BIN, body), e)


# ── 1. geometry: the build's own layout parses ───────────────────────────
d = newdir()
img = mkimg(d)
rc, out = run_fns('image_p1_geometry "%s"' % img)
check("1 the build's sfdisk layout parses to start=2048 size=16384",
      rc == 0 and out.strip().split("\n")[-1] == "2048 16384", repr(out))

# ── 2. not a GPT: refused ────────────────────────────────────────────────
plain = os.path.join(d, "plain.img")
open(plain, "wb").write(b"\0" * 4096)
rc, out = run_fns('image_p1_geometry "%s"' % plain)
check("2 a non-GPT file is refused by the signature check",
      rc != 0 and "GPT" in out, repr(out))

# ── 3. fits: the rule, all four sides ────────────────────────────────────
rows = [
    ("3a image start must be 2048", "fits 4096 100 2048 200", False, "not 2048"),
    ("3b disk start must be 2048", "fits 2048 100 4096 200", False, "hand-partitioned"),
    ("3c a bigger image is refused toward --to", "fits 2048 300 2048 200", False, "flash --to"),
    ("3d smaller-or-equal fits", "fits 2048 200 2048 200", True, ""),
]
for desc, call, want_ok, want_msg in rows:
    rc, out = run_fns(call)
    ok = (rc == 0) == want_ok and (want_msg in out if want_msg else True)
    check(desc, ok, repr(out))

# ── 4. DEPLOY_PATHS and NEVER match the deployer's ───────────────────────
def block(path, name):
    src = open(path).read()
    m = re.search(r'^%s="([^"]*)"' % name, src, re.M | re.S)
    return m.group(1).split() if m else None
check("4 DEPLOY_PATHS and NEVER are the deployer's, verbatim",
      block(BIN, "DEPLOY_PATHS") == block(DEPLOYER, "DEPLOY_PATHS")
      and block(BIN, "NEVER") == block(DEPLOYER, "NEVER"),
      "%s vs %s" % (block(BIN, "DEPLOY_PATHS"), block(DEPLOYER, "DEPLOY_PATHS")))

# ── 5. the merge: provenance per path, world union, stamp from the image ──
d = newdir()
ident = apkovl(d, "ident", {
    "etc/pipeos/card.conf": "NICK=probe\n",
    "root/.pipe/identity.dat": "MYKEY",
    "usr/local/bin/pipeos-thing": "OLD",
    "etc/pipeos/.overlay-stamp": "commit old\n",
}, ["a", "b"])
generic = apkovl(d, "generic", {
    "etc/pipeos/card.conf": "NICK=\n",
    "usr/local/bin/pipeos-thing": "NEW",
    "usr/local/bin/pipeos-newtool": "NEWTOOL",
    "etc/pipeos/.overlay-stamp": "commit image\n",
}, ["a", "c", "antigravity-cli"])
merged = os.path.join(d, "merged.tar.gz")
rc, out = run_fns('merge_apkovl "%s" "%s" "%s"' % (ident, generic, merged))
got = {}
if rc == 0:
    with tarfile.open(merged) as t:
        for m in t.getmembers():
            if m.isfile():
                got[m.name.lstrip("./")] = t.extractfile(m).read().decode()
def g(k):
    return got.get(k) or got.get("./" + k, "")
check("5 the merge takes the image's overlay, keeps the box's identity, unions the world, and carries the image's stamp",
      rc == 0
      and g("usr/local/bin/pipeos-thing") == "NEW"
      and g("usr/local/bin/pipeos-newtool") == "NEWTOOL"
      and g("root/.pipe/identity.dat") == "MYKEY"
      and g("etc/pipeos/card.conf") == "NICK=probe\n"
      and g("etc/pipeos/.overlay-stamp") == "commit image\n"
      and sorted(g("etc/apk/world").split()) == ["a", "antigravity-cli", "b", "c"],
      repr((rc, out, sorted(got)))[:400])

# ── 6. a full apply against a fake device: exact bounds, nothing past ────
d = newdir()
img = mkimg(d, size_mb=16, p1_size_mb=8)
# stamp recognisable bytes into the image's p1 span
with open(img, "r+b") as f:
    f.seek(1024 * 1024)
    f.write(b"P1" * (4 * 1024 * 1024))
fake = os.path.join(d, "fakedev")
with open(fake, "wb") as f:
    f.truncate(12 * 1024 * 1024)
    f.seek(0)
    f.write(b"\xee" * (12 * 1024 * 1024))
froot = os.path.join(d, "root")
os.makedirs(froot + "/etc/pipeos", exist_ok=True)
open(froot + "/etc/pipeos/provisioned", "w").write("")
rc, out = sh('sh %s apply --yes --image "%s"' % (BIN, img), env={
    "PIPEOS_FLASH_DEV": fake, "PIPEOS_FLASH_NO_MOUNT": "1",
    "PIPEOS_FLASH_ROOT": froot,
    "PIPEOS_FLASH_GENERIC": generic, "PIPEOS_FLASH_IDENTITY": ident,
})
data = open(fake, "rb").read()
want = 8 * 1024 * 1024
check("6 apply writes exactly the image's p1 bytes at offset 0 of the device and nothing past",
      rc == 0 and data[:2] == b"P1" and data[:want] == open(img, "rb").read()[1024 * 1024:1024 * 1024 + want]
      and data[want:] == b"\xee" * (len(data) - want),
      "rc=%s head=%r out=%s" % (rc, data[:4], out[-300:]))

# ── 7. the NO_MOUNT seam is fenced ───────────────────────────────────────
rc, out = sh("sh %s check" % BIN, env={"PIPEOS_FLASH_NO_MOUNT": "1"})
check("7 NO_MOUNT without a dev override is refused outright",
      rc == 2 and "refusing" in out, repr(out))
rc, out = sh("sh %s check" % BIN, env={"PIPEOS_FLASH_ROOT": "/tmp/x"})
check("7b a relocated root without NO_MOUNT is refused outright",
      rc == 2 and "refusing" in out, repr(out))

for t in TMPS:
    shutil.rmtree(t, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
