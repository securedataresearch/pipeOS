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
# "signature" specifically: the entry-read fallback also refuses a zeroed
# file, and a row that accepted either message could not see the signature
# check removed (control A caught exactly that on CI, 2026-09-01).
check("2 a non-GPT file is refused by the signature check, by name",
      rc != 0 and "signature" in out, repr(out))

# ── 3. fits: the rule, all four sides ────────────────────────────────────
rows = [
    ("3a image start must be 2048", "fits 4096 100 2048 200", False, "not 2048"),
    ("3b disk start must be 2048", "fits 2048 100 4096 200", False, "hand-partitioned"),
    ("3c a bigger image is refused toward --to", "fits 2048 300 2048 200", False, "apply --to"),
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
}, ["a", "c", "optional-extra"])
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
      and sorted(g("etc/apk/world").split()) == ["a", "b", "c", "optional-extra"],
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

fence = froot + "/run/pipeos/flash-pending"
save_src = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-save")).read()
stop_src = open(os.path.join(REPO, "overlay/etc/local.d/pipeos-autosave.stop")).read()
check("6b an in-place apply leaves /run/pipeos/flash-pending (the media holds the merged apkovl, RAM is the old system), pipeos-save refuses on it, and the shutdown autosave steps aside — two rebooted into its OLD overlay off the new image once (pipeOS#276)",
      os.path.exists(fence) and open(fence).read().startswith("applied ") and "saves are fenced until the reboot" in out
      and "if [ -f /run/pipeos/flash-pending ]; then" in save_src and "exit 1" in save_src.split("flash-pending")[1][:400]
      and "[ -f /run/pipeos/flash-pending ] && exit 0" in stop_src,
      "fence=%s out=%s" % (os.path.exists(fence), out[-200:]))

# ── 7. the NO_MOUNT seam is fenced ───────────────────────────────────────
rc, out = sh("sh %s check" % BIN, env={"PIPEOS_FLASH_NO_MOUNT": "1"})
check("7 NO_MOUNT without a dev override is refused outright",
      rc == 2 and "refusing" in out, repr(out))
# The box's tar is busybox: GNU-only flags pass every host-side probe and
# die on the first real run (2026-09-01, basho_box, mid-one-shot). Pin it.
import re as _re
src = "\n".join(l.split("#", 1)[0] for l in open(BIN).read().splitlines())
check("8 no GNU-only tar flags — busybox is what ships",
      not _re.search(r"tar[^\n]*--(owner|group|exclude=)", src), "GNU tar flag in pipeos-flash")

rc, out = sh("sh %s check" % BIN, env={"PIPEOS_FLASH_ROOT": "/tmp/x"})
check("7b a relocated root without NO_MOUNT is refused outright",
      rc == 2 and "refusing" in out, repr(out))


# ── 9-16. `apply --to`: a SPARE disk (#182) ──────────────────────────────
# The disk guards are 70-flash.sh's, ported; they read $SYS and $MOUNTS so
# each runs here against a fixture. DEV_OVERRIDE is set so the `-b` test is
# skipped — the names below are not devices on this machine.
def fakesys(d, disks):
    """disks: {"sda": (size_sectors, ["sda1", "sda2"]), ...}"""
    sys_ = os.path.join(d, "sys")
    for disk, (size, parts) in disks.items():
        os.makedirs(os.path.join(sys_, "block", disk), exist_ok=True)
        cb = os.path.join(sys_, "class/block", disk)
        os.makedirs(cb, exist_ok=True)
        open(os.path.join(cb, "size"), "w").write("%d\n" % size)
        for pn in parts:
            os.makedirs(os.path.join(sys_, "block", disk, pn), exist_ok=True)
            os.makedirs(os.path.join(sys_, "class/block", pn), exist_ok=True)
    return sys_


def fakemounts(d, lines):
    p = os.path.join(d, "mounts")
    open(p, "w").write("".join(l + "\n" for l in lines))
    return p


d = newdir()
guard_env = {"PIPEOS_FLASH_DEV": os.path.join(d, "nodev")}
ok9 = True
for dev, want in [("/dev/sda", True), ("/dev/nvme0n1", True), ("/dev/sda1", False),
                  ("/dev/nvme0n1p1", False), ("/dev/loop0", False), ("/dev/sdab", True)]:
    rc, out = run_fns('whole_disk "%s"' % dev, guard_env)
    ok9 = ok9 and (rc == 0) == want and (want or "not a whole disk" in out)
check("9 whole_disk takes /dev/sdX and /dev/nvmeXn1 and refuses partitions and loops, by name", ok9, "")

sysd = fakesys(d, {"sda": (60000000, ["sda1", "sda2"]), "sdb": (60000000, ["sdb1"]),
                   "sdc": (60000000, []), "nvme0n1": (500000000, ["nvme0n1p1", "nvme0n1p2"])})
box_mounts = fakemounts(d, ["tmpfs / tmpfs rw 0 0", "/dev/sda1 /media/usb vfat ro 0 0",
                            "/dev/sda2 /work ext4 rw 0 0", "/dev/sdb1 /media/ext/sdb1 ext4 rw 0 0"])
genv = dict(guard_env, PIPEOS_FLASH_SYS=sysd, PIPEOS_FLASH_MOUNTS=box_mounts)
rc1, out1 = run_fns('not_mounted /dev/sdb', genv)
rc2, out2 = run_fns('not_mounted /dev/sdc', genv)
check("10 a disk with a mounted partition is refused, naming the mount; an idle one passes",
      rc1 != 0 and "is mounted" in out1 and "/media/ext/sdb1" in out1 and rc2 == 0, repr((out1, out2)))

rc1, out1 = run_fns('not_system_disk /dev/sda', genv)
rc2, out2 = run_fns('not_system_disk /dev/sdc', genv)
root_mounts = fakemounts(d, ["/dev/nvme0n1p2 / ext4 rw 0 0", "/dev/sda1 /media/usb vfat ro 0 0",
                             "/dev/sda2 /work ext4 rw 0 0"])
rc3, out3 = run_fns('not_system_disk /dev/nvme0n1', dict(genv, PIPEOS_FLASH_MOUNTS=root_mounts))
blind = fakemounts(d, ["tmpfs / tmpfs rw 0 0"])
rc4, out4 = run_fns('not_system_disk /dev/sdc', dict(genv, PIPEOS_FLASH_MOUNTS=blind))
check("11 the disk behind /media/usb is refused as the boot media, the disk behind / as root, and an unknown /media/usb refuses rather than guesses",
      rc1 != 0 and "boot media" in out1 and rc2 == 0
      and rc3 != 0 and "root filesystem" in out3
      and rc4 != 0 and "rather than guessing" in out4, repr((out1, out2, out3, out4)))

img = mkimg(d)   # 16MB = 32768 sectors
small = fakesys(d, {"sdd": (1000, [])})
rc1, out1 = run_fns('big_enough /dev/sdd "%s"' % img, dict(genv, PIPEOS_FLASH_SYS=small))
rc2, out2 = run_fns('big_enough /dev/sdc "%s"' % img, genv)
check("12 a disk smaller than the image is refused with both sizes named; a bigger one passes",
      rc1 != 0 and "needs 16MB" in out1 and rc2 == 0, repr((out1, out2)))

src = open(BIN).read()
check("13 target_p1 refuses when LABEL=PIPEOS is not the device mounted at /media/usb (two sticks attached) — source row, findfs cannot run here",
      re.search(r'\$2=="/media/usb".*?remove the spare', src, re.S) is not None, "hardening text missing")

# ── 14. the full --to apply against a fake disk ───────────────────────────
d = newdir()
img = mkimg(d, size_mb=16, p1_size_mb=8)
with open(img, "r+b") as f:
    f.seek(1024 * 1024)
    f.write(b"P1" * (4 * 1024 * 1024))
fake = os.path.join(d, "fakedisk")
with open(fake, "wb") as f:
    f.write(b"\xee" * (48 * 1024 * 1024))
froot = os.path.join(d, "root")
os.makedirs(froot + "/etc/pipeos", exist_ok=True)
open(froot + "/etc/pipeos/provisioned", "w").write("")
p1mnt = os.path.join(d, "p1mnt")
os.makedirs(p1mnt + "/apks/pipeos/x86_64"); os.makedirs(p1mnt + "/boot")
shutil.copy(generic, p1mnt + "/pipeos.apkovl.tar.gz")
open(p1mnt + "/apks/pipeos/x86_64/APKINDEX.tar.gz", "w").write("")
open(p1mnt + "/boot/vmlinuz-lts", "w").write("K")
open(p1mnt + "/pipeos.20260101.tar.gz", "w").write("rotation")
to_env = {"PIPEOS_FLASH_DEV": fake, "PIPEOS_FLASH_NO_MOUNT": "1", "PIPEOS_FLASH_ROOT": froot,
          "PIPEOS_FLASH_GENERIC": generic, "PIPEOS_FLASH_IDENTITY": ident, "PIPEOS_FLASH_P1MNT": p1mnt}
rc, out = sh('sh %s apply --to "%s" --yes --image "%s"' % (BIN, fake, img), env=to_env)
data = open(fake, "rb").read()
imgb = open(img, "rb").read()
table = subprocess.run(["sfdisk", "-d", fake], capture_output=True, text=True).stdout
parts = re.findall(r"^\S+ : start=\s*(\d+), size=\s*(\d+), type=([0-9A-F-]+)", table, re.M)
merged = sorted(os.listdir(froot + "/work/.pipeos/flash")) if os.path.isdir(froot + "/work/.pipeos/flash") else []
merged = [m for m in merged if m.startswith("merged-") and m.endswith(".tar.gz")]
def same(a, b):
    try:
        return open(a, "rb").read() == open(b, "rb").read()
    except OSError:
        return False
mpath = os.path.join(froot, "work/.pipeos/flash", merged[0]) if merged else ""
# the carve rewrites the protective MBR and the GPT headers (primary CRC, the
# backup relocated to the disk's end), so byte-equality is asserted over the
# p1 payload and the gap — not over the 33-sector GPT structures at either end.
MB = 1024 * 1024
GPT = 33 * 512
cond14 = {
    "rc": rc == 0,
    "p1 payload": data[MB:len(imgb) - GPT] == imgb[MB:len(imgb) - GPT],
    "rest untouched": data[len(imgb):len(data) - GPT] == b"\xee" * (len(data) - len(imgb) - GPT),
    "two partitions": len(parts) == 2 and parts[0][:2] == ("2048", "16384") and int(parts[1][0]) >= 18432
                      and parts[1][2].startswith("0FC63DAF"),
    "canonical == merged": bool(mpath) and same(p1mnt + "/pipeos.apkovl.tar.gz", mpath),
    "known-good == merged": bool(mpath) and same(p1mnt + "/pipeos.known-good.tar.gz", mpath),
    "rotations gone": not os.path.exists(p1mnt + "/pipeos.20260101.tar.gz"),
}
check("14 apply --to writes the whole image at offset 0, leaves the rest, carves p2 as Linux after p1, and installs the merged apkovl as canonical and known-good on the new p1",
      all(cond14.values()),
      "failed: %s parts=%r out=%s" % ([k for k, v in cond14.items() if not v], parts, out[-300:]))
check("14c apply --to does NOT fence this box's saves — its own media was not touched",
      not os.path.exists(froot + "/run/pipeos/flash-pending"))
check("14b the swap procedure is printed, with the removal rule and the restore-work verb, and this box's flash.applied is untouched",
      rc == 0 and "REMOVE the old stick" in out and "restore-work" in out
      and not os.path.exists(froot + "/work/.pipeos/flash.applied")
      and "step=done" in open(froot + "/run/pipeos/flash.state").read(),
      out[-600:])

# ── 15. the typed device path gates the write ─────────────────────────────
with open(fake, "wb") as f:
    f.write(b"\xee" * (48 * 1024 * 1024))
p = subprocess.run(["sh", BIN, "apply", "--to", fake, "--image", img], input="/dev/wrong\n",
                   capture_output=True, text=True, env=dict(os.environ, **to_env))
data = open(fake, "rb").read()
check("15 a mistyped device path aborts before the write, and the disk is untouched",
      p.returncode != 0 and "did not match" in (p.stdout + p.stderr) and data == b"\xee" * len(data),
      repr((p.returncode, (p.stdout + p.stderr)[-200:], data[:4])))

# ── 16. the option and the seam are fenced ────────────────────────────────
rc1, out1 = sh('sh %s apply --to' % BIN, env=to_env)
noseam = dict(to_env); del noseam["PIPEOS_FLASH_P1MNT"]
with open(fake, "wb") as f:
    f.write(b"\xee" * (48 * 1024 * 1024))
rc2, out2 = sh('sh %s apply --to "%s" --yes --image "%s"' % (BIN, fake, img), env=noseam)
check("16 --to without a device is refused, and --to under NO_MOUNT without P1MNT dies naming the seam",
      rc1 != 0 and "needs a device" in out1 and rc2 != 0 and "P1MNT" in out2, repr((out1[-100:], out2[-200:])))

for t in TMPS:
    shutil.rmtree(t, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
