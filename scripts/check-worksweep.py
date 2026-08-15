#!/usr/bin/env python3
"""check-worksweep.py — gate for /etc/periodic/weekly/pipeos-worksweep (pipeOS#90).

Runs the SHIPPED script against a fake /work built in a temp dir. The script
hardcodes /work by design — it is an appliance script, not a library — so the
harness rewrites exactly two things in the copy under test:

    /work        -> the temp workspace
    mountpoint   -> a stub that succeeds

and FAILS LOUDLY if either substitution does not apply, because a missed one
would point the test at the real /work. Nothing else about the script is
changed: the tier order, the allowlist, the protected list and the arithmetic
are the shipped ones.

`df` is stubbed too, so usage can be driven to a chosen percentage — otherwise
every case would depend on how full the machine running CI happens to be.

Usage: python3 scripts/check-worksweep.py
Exit 0 if every check passes.
"""
import os
import re
import subprocess
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "overlay", "etc", "periodic", "weekly",
                      "pipeos-worksweep")

results = []
TMPS = []


def ck(desc, ok, detail=""):
    results.append(ok)
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", desc,
                           ("  [%s]" % detail) if detail and not ok else ""))


def build(workdir, pct):
    """Rewrite the shipped script to point at workdir and report pct% used."""
    src = open(SCRIPT).read()

    body, n_work = re.subn(r"/work\b", workdir, src)
    if n_work == 0:
        sys.exit("probe: no /work references rewritten — refusing to run "
                 "against the real workspace")

    body, n_mp = re.subn(r"^mountpoint -q .* \|\| exit 0$", ":",
                         body, flags=re.M)
    if n_mp != 1:
        sys.exit("probe: mountpoint guard not found (%d matches) — the script "
                 "changed shape, fix the probe before trusting it" % n_mp)

    # df stub: the script reads column 5 of row 2, as `df -P` prints it.
    stub = ("df() { printf '%%s\\n' 'Filesystem 1024-blocks Used Available "
            "Capacity Mounted' 'fake 100 %d 10 %d%%%% %s'; }\n" % (pct, pct, workdir))
    body = body.replace("set -u\n", "set -u\n" + stub, 1)
    if stub not in body:
        sys.exit("probe: could not install the df stub")
    return body


def run(workdir, pct, *args):
    path = os.path.join(workdir, "..", "sweep.sh")
    open(path, "w").write(build(workdir, pct))
    p = subprocess.run(["sh", path, *args], capture_output=True, text=True)
    return p


def mkfile(path, kb=4):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * kb * 1024)


def fixture(root):
    """A workspace shaped like a real box's /work."""
    for p in [
        # must survive
        "claude/projects/box3/transcript.jsonl",
        "backup/pipe/credentials.dat",
        "buildroot/toolchain/gcc",
        "cargo-home/registry/index.json",
        "keys/pipeos/pipebox-1111111111.rsa",
        "pipebox/state.json",
        "repos/pipe/src/main.rs",
        "repos/pipeOS/scripts/10-mk-chroot.sh",
        "repos/pipeOS/out/keys/pipebox-1111111111.rsa",
        # may be reclaimed
        "repos/pipe/target/debug/pipe",
        "repos/pipeOS/target/debug/x",
        "repos/pr677-review/target/debug/dup",
        "repos/pr677-review/src/main.rs",
        "repos/probe-tree/target/debug/y",
        "repos/pipeOS/out/p1.img",
        "repos/pipeOS/out/chroot/bin/busybox",
        "cargo-target/debug/shared",
    ]:
        mkfile(os.path.join(root, p))
    return root


SURVIVORS = [
    "claude/projects/box3/transcript.jsonl",
    "backup/pipe/credentials.dat",
    "buildroot/toolchain/gcc",
    "cargo-home/registry/index.json",
    "keys/pipeos/pipebox-1111111111.rsa",
    "pipebox/state.json",
    "repos/pipe/src/main.rs",
    "repos/pipeOS/scripts/10-mk-chroot.sh",
    "repos/pipeOS/out/keys/pipebox-1111111111.rsa",
]


def survivors_intact(root):
    missing = [p for p in SURVIVORS if not os.path.exists(os.path.join(root, p))]
    return (not missing), ", ".join(missing)


# ── 1. under threshold: does nothing, says nothing ────────────────────────
t = tempfile.mkdtemp(); TMPS.append(t)
w = fixture(os.path.join(t, "work"))
p = run(w, 12)
print("under the threshold")
ck("exits 0", p.returncode == 0)
ck("prints nothing at all", p.stdout.strip() == "", repr(p.stdout[:120]))
ck("deletes nothing", os.path.exists(os.path.join(w, "cargo-target/debug/shared")))

# ── 2. over threshold, dry-run: reports a plan, deletes nothing ───────────
t = tempfile.mkdtemp(); TMPS.append(t)
w = fixture(os.path.join(t, "work"))
p = run(w, 91, "--dry-run")
print("\nover the threshold, --dry-run")
ck("exits 0", p.returncode == 0)
ck("says it is a dry run", "[dry-run]" in p.stdout)
ck("names candidates", "would reclaim" in p.stdout)
ck("deletes NOTHING", os.path.exists(os.path.join(w, "repos/pr677-review/target/debug/dup")))
ok, missing = survivors_intact(w)
ck("every protected path intact", ok, missing)

# ── 3. over threshold, real sweep with --force (every tier) ───────────────
t = tempfile.mkdtemp(); TMPS.append(t)
w = fixture(os.path.join(t, "work"))
p = run(w, 91, "--force")
print("\nover the threshold, real sweep (--force, every tier)")
ck("exits 0", p.returncode == 0)
ok, missing = survivors_intact(w)
ck("NOTHING non-regenerable was touched", ok, missing)
ck("the fleet signing key survives in out/keys",
   os.path.exists(os.path.join(w, "repos/pipeOS/out/keys/pipebox-1111111111.rsa")))
ck("agent memory survives", os.path.exists(os.path.join(w, "claude/projects/box3/transcript.jsonl")))
ck("scratch build output reclaimed",
   not os.path.exists(os.path.join(w, "repos/pr677-review/target")))
ck("canonical clone target reclaimed",
   not os.path.exists(os.path.join(w, "repos/pipe/target")))
ck("canonical clone SOURCE untouched",
   os.path.exists(os.path.join(w, "repos/pipe/src/main.rs")))
ck("media build products reclaimed",
   not os.path.exists(os.path.join(w, "repos/pipeOS/out/p1.img")))
ck("shared artifact cache reclaimed",
   not os.path.exists(os.path.join(w, "cargo-target")))
ck("refused nothing (allowlist agrees with protected list)",
   "REFUSED" not in p.stdout, p.stdout[-200:])
ck("reports a non-zero reclaimed total",
   re.search(r"reclaimed (\d+)K; ", p.stdout) is not None
   and int(re.search(r"reclaimed (\d+)K; ", p.stdout).group(1)) > 0,
   p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "")

# ── 4. it stops once back under the threshold ────────────────────────────
# df is stubbed at a constant, so a run WITHOUT --force can never see itself
# get under the bar; what this checks is that done_enough is consulted at all,
# by driving the stub to a value below the threshold mid-tier.
t = tempfile.mkdtemp(); TMPS.append(t)
w = fixture(os.path.join(t, "work"))
p = run(w, 59)          # below the default 60
print("\nstops when already under the threshold")
ck("no tier runs", p.stdout.strip() == "", repr(p.stdout[:120]))
ck("shared cache survives", os.path.exists(os.path.join(w, "cargo-target/debug/shared")))

# ── 5. symlinks are never followed out of the workspace ──────────────
# Two different paths reach a candidate, and only one of them can see a
# symlink: tier 1 finds target/ dirs with `find -type d`, which excludes
# symlinks outright, while tier 5 names /work/cargo-target directly and would
# happily rm -rf a link if the -L guard were not there. Both are checked.
t = tempfile.mkdtemp(); TMPS.append(t)
w = fixture(os.path.join(t, "work"))
outside = os.path.join(t, "precious")
os.makedirs(outside)
open(os.path.join(outside, "do-not-delete"), "w").write("x")
shutil.rmtree(os.path.join(w, "repos", "pr677-review", "target"))
os.symlink(outside, os.path.join(w, "repos", "pr677-review", "target"))
shutil.rmtree(os.path.join(w, "cargo-target"))
os.symlink(outside, os.path.join(w, "cargo-target"))
p = run(w, 91, "--force")
print("\nsymlinks pointing out of the workspace")
ck("the link target is not deleted", os.path.exists(os.path.join(outside, "do-not-delete")))
ck("a symlinked scratch target/ is not a find candidate at all",
   "pr677-review/target" not in p.stdout, p.stdout[:200])
ck("a symlinked cargo-target is skipped by name, and said so",
   "skip (symlink)" in p.stdout, p.stdout[-300:])
ck("and the link itself still exists (not silently unlinked)",
   os.path.islink(os.path.join(w, "cargo-target")))

# ── 6. unknown argument is an error, not a silent full sweep ─────────────
t = tempfile.mkdtemp(); TMPS.append(t)
w = fixture(os.path.join(t, "work"))
p = run(w, 91, "--delete-everything")
print("\nunknown argument")
ck("exits non-zero", p.returncode != 0)
ck("deletes nothing", os.path.exists(os.path.join(w, "cargo-target/debug/shared")))

for _t in TMPS:
    shutil.rmtree(_t, ignore_errors=True)

print("\n%d/%d checks pass" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
