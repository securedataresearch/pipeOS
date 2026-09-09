#!/usr/bin/env python3
"""Probe for pipeos-restore-work (#182): the shipped script against a fake
root, a fake /proc/mounts, a recording rsync stub and a recording mount stub
— the seams the script exposes for exactly this. Nothing here touches the
real /work or any device.

Rows cover source resolution (backup root -> work/, a plain dir, a device
mounted read-only), the emptiness rule and --force, the additive rsync (no
--delete, the four excludes), the own-disk and running-/work refusals, the
users.manifest exception, and the fence (deny entries in both settings
files, the dispatch line in `pipeos`).

Exit 0 if every row passes. Controls: check-restore-work-controls.py.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.environ.get("CHECK_RESTORE_BIN",
                     os.path.join(REPO, "overlay/usr/local/bin/pipeos-restore-work"))
SETTINGS = os.environ.get("CHECK_RESTORE_SETTINGS", ":".join([
    os.path.join(REPO, "overlay/etc/pipeos/pipebox-settings.json"),
    os.path.join(REPO, "overlay/usr/local/share/pipeos/card/pipebox-settings.json.tmpl"),
])).split(":")
FRONT = os.path.join(REPO, "overlay/usr/local/bin/pipeos")
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


def box(work_files=None):
    """A fake root with a mounted /work (empty by the rule unless work_files),
    a backup tree on an external, stubs for rsync, mount and blkid."""
    d = tempfile.mkdtemp(prefix="ckrw-")
    TMPS.append(d)
    for sub in ("repos", "logs", "cache", "claude/projects", "pipebox", "backup", "home", "lost+found",
                ".pipeos/flash"):
        os.makedirs(d + "/work/" + sub, exist_ok=True)
    write(d + "/work/.pipeos/flash/x", "staging")
    write(d + "/work/logs/y", "log")
    write(d + "/work/cache/z", "cache")
    for path, content in (work_files or {}).items():
        write(d + "/work/" + path, content)
    write(d + "/ext/pipeos-backup/probe/identity/MANIFEST", "pipeos identity backup\n")
    write(d + "/ext/pipeos-backup/probe/work/data.txt", "precious")
    write(d + "/ext/pipeos-backup/probe/work/.pipeos/users.manifest", "sam:1000\n")
    write(d + "/plain/other.txt", "plain")
    os.makedirs(d + "/run", exist_ok=True)
    # rsync stub: records argv, then runs the real rsync (the excludes and
    # the no---delete rule are what the rows assert on) — or fails when asked
    write(d + "/bin/rsync-stub",
          "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + d + "/rsync.argv\n"
          "[ -n \"${RSYNC_FAIL:-}\" ] && exit 1\n"
          "exec rsync \"$@\"\n", 0o755)
    # mount stub: records argv; a read-only `mount` (the device source) fills
    # MP with the fixture tree named by MOUNT_FIXTURE, a read-write one (the
    # --onto destination) leaves MP empty; `umount MP` is recorded only
    write(d + "/bin/mount-stub",
          "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + d + "/mount.argv\necho -- >> " + d + "/mount.argv\n"
          "[ \"$1\" = umount ] && exit 0\n"
          "for a; do :; done\nmkdir -p \"$a\"\n"
          "case \" $* \" in *' ro '*) cp -r \"${MOUNT_FIXTURE:?}\"/. \"$a\" ;; esac\nexit 0\n", 0o755)
    write(d + "/bin/blkid-stub", "#!/bin/sh\necho \"${BLKID_TYPE:-ext4}\"\n", 0o755)
    write(d + "/fakeblock", "")
    write(d + "/fakeblock2", "")
    return d


def mounts_file(d, extra=()):
    p = d + "/mounts"
    with open(p, "w") as f:
        f.write("tmpfs / tmpfs rw 0 0\n/dev/sda2 %s/work ext4 rw 0 0\n/dev/sdx1 %s/ext ext4 rw 0 0\n" % (d, d))
        for line in extra:
            f.write(line + "\n")
    return p


def run(d, args, env=None, extra_mounts=()):
    e = dict(os.environ)
    e.update({
        "PIPEOS_RESTORE_ROOT": d,
        "PIPEOS_RESTORE_MOUNTS": mounts_file(d, extra_mounts),
        "PIPEOS_RESTORE_RSYNC": d + "/bin/rsync-stub",
        "PIPEOS_RESTORE_MOUNT": d + "/bin/mount-stub",
        "PIPEOS_RESTORE_BLKID": d + "/bin/blkid-stub",
        "PIPEOS_RESTORE_BLOCK": d + "/fakeblock",
        "MOUNT_FIXTURE": d + "/ext/pipeos-backup/probe/work",
    })
    if env:
        e.update(env)
    p = subprocess.run(["sh", BIN] + args, capture_output=True, text=True, env=e)
    return p.returncode, p.stdout + p.stderr


def argv_of(d, name="rsync"):
    try:
        return open(d + "/%s.argv" % name).read()
    except OSError:
        return ""


# ── 1. usage ─────────────────────────────────────────────────────────────
d = box()
rc, out = run(d, [])
check("1 no source is a usage error, nothing run", rc == 2 and "usage" in out and argv_of(d) == "", repr(out))

# ── 2-5. source resolution ───────────────────────────────────────────────
d = box()
rc, out = run(d, [d + "/ext/pipeos-backup/probe"])
av = argv_of(d)
check("2 a backup root resolves to its work/ and says so",
      rc == 0 and "using" in out and (d + "/ext/pipeos-backup/probe/work/") in av.split("\n"), repr(out) + av)
d = box()
rc, out = run(d, [d + "/ext/pipeos-backup/probe/work"])
check("3 the work/ directory itself is used as-is",
      rc == 0 and (d + "/ext/pipeos-backup/probe/work/") in argv_of(d).split("\n"), repr(out))
d = box()
rc, out = run(d, [d + "/plain"])
check("4 a plain directory (no identity/, no work/) is used as-is",
      rc == 0 and (d + "/plain/") in argv_of(d).split("\n"), repr(out))
d = box()
rc, out = run(d, [d + "/nowhere"])
check("5 a missing source is refused by name, nothing run",
      rc != 0 and "no such source" in out and argv_of(d) == "", repr(out))

# ── 6-7. the emptiness rule and --force ──────────────────────────────────
d = box()
rc, out = run(d, [d + "/plain"])
check("6 a /work that is empty by the rule (workspace dirs, claude/projects, .pipeos, logs, cache) proceeds without --force",
      rc == 0 and "restored" in out, repr(out))
d = box(work_files={"repos/foo/README": "x"})
rc, out = run(d, [d + "/plain"])
ok7a = rc != 0 and "not empty" in out and "repos/foo" in out and argv_of(d) == ""
rc, out = run(d, [d + "/plain", "--force"])
check("7 a /work with content is refused naming the first entry; --force merges over it",
      ok7a and rc == 0 and argv_of(d) != "", repr(out))
d = box(work_files={"stray.txt": "x"})
rc, out = run(d, [d + "/plain"])
check("7b a stray top-level file counts as content", rc != 0 and "stray.txt" in out, repr(out))

# ── 8. the rsync is additive with the four excludes ─────────────────────
d = box()
rc, out = run(d, [d + "/plain"])
lines = argv_of(d).split("\n")
check("8 rsync gets -a and the four excludes, and never --delete",
      rc == 0 and "-a" in lines and "--delete" not in lines
      and all(x in lines for x in ("/lost+found", "/.pipeos", "/logs", "/cache")), argv_of(d))

# ── 9. a failed rsync is a failed run ─────────────────────────────────────
d = box()
rc, out = run(d, [d + "/plain"], env={"RSYNC_FAIL": "1"})
check("9 an rsync failure is reported and the state says failed",
      rc != 0 and "rsync failed" in out and "step=failed" in open(d + "/run/pipeos/restore-work.state").read(),
      repr(out))

# ── 10-11. a device source: mounted read-only, released; own disk refused ─
d = box()
rc, out = run(d, [d + "/fakeblock"])
mav = argv_of(d, "mount")
first = mav.split("--")[0].split("\n")
check("10 a device source is mounted read-only as ext4 on a temp mountpoint, restored from, and unmounted after",
      rc == 0 and first[0] == "mount" and "-o" in first and "ro" in first and "ext4" in first
      and (d + "/run/pipeos/restore-src/") in argv_of(d).split("\n")
      and "umount\n" + d + "/run/pipeos/restore-src" in mav, repr(out) + mav)
d = box()
rc, out = run(d, [d + "/fakeblock"], extra_mounts=[], env={"PIPEOS_RESTORE_BLOCK": "/dev/sda2"})
rc2, out2 = run(d, ["/dev/sda2"], env={"PIPEOS_RESTORE_BLOCK": "/dev/sda2"})
check("11 the device behind the running /work is refused as a source",
      rc2 != 0 and "own work disk" in out2 and argv_of(d) == "", repr(out2))
d = box()
rc, out = run(d, [d + "/fakeblock"], extra_mounts=[d + "/fakeblock " + d + "/media/ext/old ext4 ro 0 0"])
check("11b a device that is already mounted is refused toward the directory",
      rc != 0 and "is mounted at" in out and argv_of(d) == "", repr(out))

# ── 12. --onto: a spare ext4 partition, mounted rw; the running /work refused
d = box()
rc, out = run(d, [d + "/plain", "--onto", d + "/fakeblock"])
mav = argv_of(d, "mount")
check("12 --onto mounts the spare partition rw and restores into it",
      rc == 0 and "mount\n-t\next4\n" + d + "/fakeblock\n" + d + "/run/pipeos/restore-dst" in mav
      and (d + "/run/pipeos/restore-dst/") in argv_of(d).split("\n"), repr(out) + mav)
d = box()
rc, out = run(d, [d + "/plain", "--onto", "/dev/sda2"], env={"PIPEOS_RESTORE_BLOCK": "/dev/sda2"})
check("12b --onto the running /work's device is refused", rc != 0 and "running /work" in out and argv_of(d) == "", repr(out))
d = box()
rc, out = run(d, [d + "/plain", "--onto", d + "/fakeblock"], env={"BLKID_TYPE": "vfat"})
check("12c --onto a non-ext4 partition is refused", rc != 0 and "not ext4" in out and argv_of(d) == "", repr(out))

# ── 13. users.manifest: carried over only when the destination has none ──
d = box()
rc, out = run(d, [d + "/ext/pipeos-backup/probe"])
got = open(d + "/work/.pipeos/users.manifest").read() if os.path.exists(d + "/work/.pipeos/users.manifest") else ""
d2 = box()
write(d2 + "/work/.pipeos/users.manifest", "keep:1001\n")
rc2, out2 = run(d2, [d2 + "/ext/pipeos-backup/probe"])
kept = open(d2 + "/work/.pipeos/users.manifest").read()
check("13 users.manifest is carried over when absent and left alone when present",
      rc == 0 and got == "sam:1000\n" and "carried over" in out and rc2 == 0 and kept == "keep:1001\n",
      repr((out, got, kept)))

# ── 14. the fence and the front door ─────────────────────────────────────
deny_ok = all('"Bash(pipeos restore-work*)"' in open(p).read() and '"Bash(pipeos-restore-work*)"' in open(p).read()
              for p in SETTINGS)
front = open(FRONT).read()
check("14 both settings files deny the verb, and `pipeos` dispatches and documents it",
      deny_ok and "restore-work) shift; exec /usr/local/bin/pipeos-restore-work" in front
      and "pipeos restore-work SRC" in front, "deny=%s" % deny_ok)

for t in TMPS:
    shutil.rmtree(t, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
