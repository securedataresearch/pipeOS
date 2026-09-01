#!/usr/bin/env python3
"""Probe for pipeos-backup (#180): the shipped script, run against a fake
root, a stub `lbu package`, a fake /proc/mounts and a recording rsync stub —
the seams the script exposes for exactly this. Nothing here touches the real
/media/usb, /root or /work.

Rows cover the refusals (unmounted destination, unclaimed box, staged
rollback, corrupt candidate), the bundle (sha in the manifest, key copies,
canonical never absent across a rerun), the filesystem-aware rsync flags,
and that the .last stamp is written only after every step succeeded.

Exit 0 if every row passes. Controls live in check-backup-controls.py.
"""
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.environ.get("CHECK_BACKUP_BIN",
                     os.path.join(REPO, "overlay/usr/local/bin/pipeos-backup"))
RESULTS = []
TMPS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def box(provisioned=True, rollback=False):
    """A fake root: claimed box, pipe keys, a work disk, boot media."""
    d = tempfile.mkdtemp(prefix="ckbk-")
    TMPS.append(d)
    if provisioned:
        write(d + "/etc/pipeos/provisioned", "")
    if rollback:
        write(d + "/etc/pipeos/rollback-pending", "")
    write(d + "/etc/pipeos/card.conf", "NICK=probe\nROLE=GENERIC\n")
    write(d + "/etc/pipeos/.overlay-stamp", "commit abc123def456\nref main\n")
    write(d + "/root/.pipe/identity.dat", "ID")
    write(d + "/root/.pipe/credentials.dat", "CRED")
    write(d + "/work/data.txt", "precious")
    write(d + "/work/cache/junk", "x")
    write(d + "/media/usb/pipeos-image.txt", "built=2026-09-01T00:00:00Z\n")
    write(d + "/media/usb/boot/vmlinuz-lts", "K")
    os.makedirs(d + "/run", exist_ok=True)
    # the fixture apkovl the lbu stub hands back: three world entries
    fx = d + "/fixture"
    write(fx + "/etc/apk/world", "a\nb\nc\n")
    write(fx + "/root/.pipe/identity.dat", "ID")
    write(d + "/bin/lbu-stub",
          "#!/bin/sh\ntar -C %s -czf \"$1\" etc root\n" % fx, 0o755)
    write(d + "/bin/lbu-corrupt", "#!/bin/sh\necho garbage > \"$1\"\n", 0o755)
    # rsync stub: records argv, mirrors with cp, or fails when asked to
    write(d + "/bin/rsync-stub",
          "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + d + "/rsync.argv\n"
          "[ -n \"${RSYNC_FAIL:-}\" ] && exit 1\n"
          "for a; do :; done\nsrc=\"$(eval echo \\${$(($#-1))})\"; dst=\"$a\"\n"
          "mkdir -p \"$dst\" && cp -r \"$src\". \"$dst\" 2>/dev/null; exit 0\n", 0o755)
    return d


def mounts_file(d, dest, fs):
    p = d + "/mounts"
    with open(p, "w") as f:
        f.write("tmpfs / tmpfs rw 0 0\n/dev/sdx1 %s %s rw 0 0\n" % (dest, fs))
    return p


def run(d, args, dest_fs="ext4", dest=None, lbu="lbu-stub", env=None):
    dest = dest or d + "/ext"
    os.makedirs(dest, exist_ok=True)
    e = dict(os.environ)
    e.update({
        "PIPEOS_BACKUP_ROOT": d,
        "PIPEOS_BACKUP_LBU": d + "/bin/" + lbu,
        "PIPEOS_BACKUP_MOUNTS": mounts_file(d, dest, dest_fs),
        "PIPEOS_BACKUP_RSYNC": d + "/bin/rsync-stub",
    })
    if env:
        e.update(env)
    p = subprocess.run(["sh", BIN] + args, capture_output=True, text=True, env=e)
    return p.returncode, p.stdout + p.stderr


def argv_of(d):
    try:
        return open(d + "/rsync.argv").read()
    except OSError:
        return ""


# ── 1. destination must be a mountpoint ──────────────────────────────────
d = box()
os.makedirs(d + "/nowhere")
rc, out = run(d, [d + "/nowhere"])          # not in the mounts file
check("1 a destination that is not a mountpoint is refused",
      rc != 0 and "not a mounted disk" in out and not os.path.exists(d + "/nowhere/pipeos-backup"),
      repr(out))

# ── 2. an unclaimed box has no identity ──────────────────────────────────
d = box(provisioned=False)
rc, out = run(d, [d + "/ext"])
check("2 an unclaimed box is refused, nothing written",
      rc != 0 and "not claimed" in out and not os.path.exists(d + "/ext/pipeos-backup"), repr(out))

# ── 3. a staged rollback is refused ──────────────────────────────────────
d = box(rollback=True)
rc, out = run(d, [d + "/ext"])
check("3 a staged rollback is refused", rc != 0 and "rollback" in out, repr(out))

# ── 4. a corrupt candidate never lands ───────────────────────────────────
d = box()
rc, out = run(d, [d + "/ext"], lbu="lbu-corrupt")
check("4 a candidate that is not a tarball is refused",
      rc != 0 and "integrity" in out and not os.path.exists(d + "/ext/pipeos-backup/probe/identity/pipeos.apkovl.tar.gz"),
      repr(out))

# ── 5. the bundle: sha, manifest, key copies, work mirrored, .last ────────
d = box()
rc, out = run(d, [d + "/ext"])
idd = d + "/ext/pipeos-backup/probe/identity"
bundle = idd + "/pipeos.apkovl.tar.gz"
ok5 = rc == 0 and os.path.exists(bundle)
manifest = open(idd + "/MANIFEST").read() if ok5 else ""
sha = subprocess.run(["sha256sum", bundle], capture_output=True, text=True).stdout.split()[0] if ok5 else ""
check("5 a full run lands the bundle, a manifest naming its sha256 and the box, the pipe keys, and the .last stamp",
      ok5 and ("bundle_sha256=" + sha) in manifest and "nick=probe" in manifest
      and "world_count=3" in manifest and "overlay_commit=abc123def456" in manifest
      and "holds this box's keys" in manifest
      and open(idd + "/pipe/credentials.dat").read() == "CRED"
      and os.path.exists(d + "/ext/pipeos-backup/probe/.last")
      and os.path.exists(d + "/ext/pipeos-backup/probe/work/data.txt"),
      repr(out) + manifest)
check("5b the pipe key copies are owner-only",
      ok5 and stat.S_IMODE(os.stat(idd + "/pipe/credentials.dat").st_mode) & 0o077 == 0,
      oct(os.stat(idd + "/pipe/credentials.dat").st_mode) if ok5 else "no run")

# ── 6. rerun keeps the previous bundle; canonical present throughout ─────
first = sha
write(d + "/fixture/etc/apk/world", "a\nb\nc\nd\n")
rc, out = run(d, [d + "/ext"])
sha2 = subprocess.run(["sha256sum", bundle], capture_output=True, text=True).stdout.split()[0]
prev = idd + "/pipeos.apkovl.prev.tar.gz"
psha = subprocess.run(["sha256sum", prev], capture_output=True, text=True).stdout.split()[0] if os.path.exists(prev) else ""
check("6 a rerun rotates the previous bundle to .prev and writes a new canonical",
      rc == 0 and sha2 != first and psha == first, "rc=%s cur=%s prev=%s" % (rc, sha2[:8], psha[:8]))

# ── 7. --identity-only mirrors nothing ───────────────────────────────────
d = box()
rc, out = run(d, [d + "/ext", "--identity-only"])
check("7 --identity-only lands the bundle and touches no work or media mirror",
      rc == 0 and os.path.exists(d + "/ext/pipeos-backup/probe/identity/MANIFEST")
      and not os.path.exists(d + "/ext/pipeos-backup/probe/work")
      and argv_of(d) == "", repr(out) + argv_of(d))

# ── 8. rsync flags follow the destination filesystem ─────────────────────
d = box()
rc, out = run(d, [d + "/ext"], dest_fs="vfat")
av = argv_of(d)
check("8 a FAT destination gets the no-perms flags (and the note)",
      rc == 0 and "--no-perms" in av and "-rtL" in av and "-a" not in av.split("\n")
      and "file modes are not kept" in out, av + out)
d = box()
rc, out = run(d, [d + "/ext"], dest_fs="ext4")
av = argv_of(d)
check("8b an ext4 destination gets -a --delete and the build-output exclude",
      rc == 0 and "-a" in av.split("\n") and "--delete" in av and "/repos/*/out" in av, av)

# ── 9. .last only after every step succeeded ─────────────────────────────
d = box()
rc, out = run(d, [d + "/ext"], env={"RSYNC_FAIL": "1"})
check("9 a failed mirror leaves no .last stamp and reports the failure",
      rc != 0 and "rsync failed" in out and not os.path.exists(d + "/ext/pipeos-backup/probe/.last")
      and "step=failed" in open(d + "/run/pipeos/backup.state").read(), repr(out))

# ── 10. a passphrase seals the bundle and writes no plaintext keys ────────
d = box()
write(d + "/pass", "hunter2\n")
rc, out = run(d, [d + "/ext", "--identity-only", "--passphrase-file", d + "/pass"])
idd = d + "/ext/pipeos-backup/probe/identity"
enc = idd + "/pipeos.apkovl.tar.gz.enc"
dec_ok = False
if os.path.exists(enc):
    p = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-in", enc,
                        "-out", d + "/dec.tar.gz", "-pass", "file:" + d + "/pass"], capture_output=True)
    dec_ok = p.returncode == 0 and tarfile.is_tarfile(d + "/dec.tar.gz")
check("10 --passphrase-file writes a sealed bundle that decrypts, and no plaintext bundle or key copies",
      rc == 0 and dec_ok and not os.path.exists(idd + "/pipeos.apkovl.tar.gz")
      and not os.path.exists(idd + "/pipe") and "encrypted=1" in open(idd + "/MANIFEST").read(),
      repr(out))

for t in TMPS:
    shutil.rmtree(t, ignore_errors=True)
n = len(RESULTS)
print("%d/%d" % (sum(RESULTS), n))
sys.exit(0 if all(RESULTS) else 1)
