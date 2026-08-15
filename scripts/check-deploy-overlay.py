#!/usr/bin/env python3
"""Probe for pipeos-deploy-overlay (pipeOS#97).

No box can run the real thing: it writes /etc, calls pipeos-save and touches
the boot media, all of which are hard-banned for the resident agent, and the
build scripts are not linted or executed by CI either (pipeOS#98). So this
runs the SHIPPED script — not a copy, not a paraphrase — against a throwaway
git repo and a throwaway root, through the two seams the script declares:

    PIPEOS_DEPLOY_ROOT        relocate every install target
    PIPEOS_DEPLOY_NO_PERSIST  stop before --seed / pipeos-save / pipeos verify

The second is not a convenience. A relocated install followed by a real save
would persist the REAL root from a test, so the script refuses to relocate
without it, and row 13 is that refusal.

Every fake file carries its own content, so the rows assert WHICH bytes
landed, not merely that something did.

Exit 0 if every row passes. Controls live in check-deploy-overlay-controls.py.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "overlay/usr/local/bin/pipeos-deploy-overlay")

GIT = ["git", "-c", "user.email=probe@pipeos", "-c", "user.name=probe",
       "-c", "commit.gpgsign=false"]

# The fixture overlay. Two files that must deploy, one per-box generated file
# under each of the two NEVER prefixes, and a card.
FIXTURE = {
    "overlay/usr/local/bin/pipeos-thing": ("#!/bin/sh\necho v1\n", 0o755),
    "overlay/usr/local/share/pipeos/card/thing.tmpl": ("tmpl v1\n", 0o644),
    "overlay/etc/periodic/weekly/thing-weekly": ("#!/bin/sh\nweekly v1\n", 0o755),
    "overlay/etc/apk/protected_paths.d/lbu.list": ("+usr/local\n", 0o644),
    "overlay/root/.claude/CLAUDE.md": ("notes v1\n", 0o644),
    # never deployed, per-box generated:
    "overlay/etc/pipeos/pipebox.conf": ("REPO SIDE — must never land\n", 0o644),
    "overlay/root/.pipe/policy.json": ('{"allow":[]}\n', 0o644),
    "docs/cards/box9.card": ("NICK=box9\nROLE=BUILD\n", 0o644),
}

CARD_ONBOX = "NICK=box9\nROLE=BUILD\n"
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def write(path, content, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, mode)


def git(repo, *args):
    return subprocess.run(GIT + ["-C", repo] + list(args),
                          capture_output=True, text=True)


class Case:
    """A throwaway repo + a throwaway root, and the shipped script over both."""

    def __init__(self, fixture=None, card=CARD_ONBOX):
        self.dir = tempfile.mkdtemp(prefix="deployovl-")
        self.repo = os.path.join(self.dir, "repo")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.repo)
        os.makedirs(self.root)
        git(self.repo, "init", "-q", "-b", "main")
        for rel, (content, mode) in (fixture or FIXTURE).items():
            write(os.path.join(self.repo, rel), content, mode)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "fixture")
        self.commit = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if card is not None:
            write(os.path.join(self.root, "etc/pipeos/card.conf"), card)

    def run(self, *args, persist_fence=True, ref="main"):
        env = dict(os.environ)
        env["PIPEOS_REPO"] = self.repo
        env["PIPEOS_DEPLOY_ROOT"] = self.root
        if persist_fence:
            env["PIPEOS_DEPLOY_NO_PERSIST"] = "1"
        else:
            env.pop("PIPEOS_DEPLOY_NO_PERSIST", None)
        cmd = ["sh", SCRIPT, "--yes"]
        if ref:
            cmd += ["--from", ref]
        cmd += list(args)
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL)
        self.rc, self.log = r.returncode, r.stdout + r.stderr
        return self

    def live(self, rel):
        p = os.path.join(self.root, rel)
        try:
            return open(p).read()
        except OSError:
            return None

    def mode(self, rel):
        try:
            return oct(os.stat(os.path.join(self.root, rel)).st_mode & 0o777)
        except OSError:
            return None

    def stamp(self):
        return self.live("etc/pipeos/.overlay-stamp")


CASES = []


def case(**kw):
    c = Case(**kw)
    CASES.append(c)
    return c


# ── 1. cold deploy ───────────────────────────────────────────────────────
c = case().run()
check("1 cold deploy installs the overlay files with their modes",
      c.rc == 0
      and c.live("usr/local/bin/pipeos-thing") == "#!/bin/sh\necho v1\n"
      and c.mode("usr/local/bin/pipeos-thing") == "0o755"
      and c.live("etc/periodic/weekly/thing-weekly") == "#!/bin/sh\nweekly v1\n"
      and c.mode("root/.claude/CLAUDE.md") == "0o644",
      "rc=%d modes=%s/%s" % (c.rc, c.mode("usr/local/bin/pipeos-thing"),
                             c.mode("root/.claude/CLAUDE.md")))

# ── 2. the two NEVER prefixes ────────────────────────────────────────────
# The single most destructive thing this tool could do: the repo ships the
# UNPROVISIONED defaults for these (NICK=, allow: []), so copying them onto a
# live box costs it its identity and every pipe entitlement at once.
check("2 per-box generated files are never installed",
      c.live("etc/pipeos/pipebox.conf") is None
      and c.live("root/.pipe/policy.json") is None,
      "pipebox.conf=%r policy=%r" % (c.live("etc/pipeos/pipebox.conf"),
                                     c.live("root/.pipe/policy.json")))

# ── 3. the stamp ─────────────────────────────────────────────────────────
st = c.stamp() or ""
check("3 stamp records the commit and one sha256 per deployed file",
      ("commit " + c.commit) in st
      and "/usr/local/bin/pipeos-thing" in st
      and "/etc/pipeos/pipebox.conf" not in st
      and "/root/.pipe/policy.json" not in st,
      repr(st[:200]))

# ── 4. idempotence ───────────────────────────────────────────────────────
c2 = c.run()
check("4 a second run is a no-op",
      c2.rc == 0 and "0 new, 0 changed" in c2.log, "rc=%d" % c2.rc)

# ── 5. a hand-edited live file is restored ──────────────────────────────
write(os.path.join(c.root, "usr/local/bin/pipeos-thing"), "HAND EDIT\n", 0o755)
c3 = c.run()
check("5 a drifted live file is restored to the ref's content",
      c3.rc == 0 and c3.live("usr/local/bin/pipeos-thing") == "#!/bin/sh\necho v1\n"
      and "1 changed" in c3.log, "rc=%d" % c3.rc)

# ── 6. a file a PREVIOUS deploy installed, dropped by the new ref ───────
# Rewritten after box1's review. The old row hand-planted a file in the root
# and asserted it was called stale — which passed, and which was the defect:
# the scan walked $ROOT/etc/init.d and $ROOT/usr/local/bin whole, so on a real
# box every package-owned OpenRC script apk put there printed as stale too.
# The predicate is now the stamp, so the row has to build the state it claims
# to test: deploy a file, then deploy a ref that no longer has it.
c13 = Case()
CASES.append(c13)
write(os.path.join(c13.repo, "overlay/usr/local/bin/pipeos-oldthing"),
      "#!/bin/sh\nold\n", 0o755)
git(c13.repo, "add", "-A")
git(c13.repo, "commit", "-qm", "ship pipeos-oldthing")
c13.run()
assert c13.live("usr/local/bin/pipeos-oldthing") is not None, "row 6 setup"
os.remove(os.path.join(c13.repo, "overlay/usr/local/bin/pipeos-oldthing"))
git(c13.repo, "add", "-A")
git(c13.repo, "commit", "-qm", "drop pipeos-oldthing")
c13.run()
check("6 a file this tool deployed and the ref dropped is reported, NOT deleted",
      "stale usr/local/bin/pipeos-oldthing" in c13.log
      and c13.live("usr/local/bin/pipeos-oldthing") == "#!/bin/sh\nold\n",
      "log=%r" % c13.log[-300:])

# ── 6b. and a file this tool never deployed is NOT reported ─────────────
# box1's finding, as a row. usr/local/bin and etc/init.d are shared with apk:
# on a real box the old scan printed every package-owned file as stale, and
# nothing was ever deleted, but the one line the operator was reading for was
# buried under dozens that were not findings. Two files, one in each of the
# directories that made it worst.
write(os.path.join(c13.root, "usr/local/bin/apk-owned-binary"), "not ours\n", 0o755)
write(os.path.join(c13.root, "etc/init.d/some-package"), "#!/sbin/openrc-run\n", 0o755)
c14 = c13.run()
check("6b a live file no deploy installed is not called stale",
      "apk-owned-binary" not in c14.log and "some-package" not in c14.log
      and c14.live("usr/local/bin/apk-owned-binary") == "not ours\n",
      "log=%r" % c14.log[-300:])

# ── 6c. a box with no stamp says it cannot answer yet ───────────────────
# The cost of the fix above: before the first deploy there is no record of
# what this tool owns, so "no stale files" would be a claim it cannot make.
# Said out loud rather than left as a silent pass.
c15 = case().run("--dry-run")
check("6c with no previous stamp, the tool says stale detection starts here",
      "no previous deploy stamp" in c15.log, "log=%r" % c15.log[-300:])

# ── 7. the card gate refuses ─────────────────────────────────────────────
c5 = case(card="NICK=box9\nROLE=TEST\n").run()
check("7 an on-box card that differs from the repo card stops the deploy",
      c5.rc != 0 and "R19" in c5.log
      and c5.live("usr/local/bin/pipeos-thing") is None,
      "rc=%d installed=%r" % (c5.rc, c5.live("usr/local/bin/pipeos-thing")))

# ── 8. --install-card ────────────────────────────────────────────────────
c6 = case(card="NICK=box9\nROLE=TEST\n").run("--install-card")
check("8 --install-card takes the repo card and says generate was not run",
      c6.rc == 0 and c6.live("etc/pipeos/card.conf") == "NICK=box9\nROLE=BUILD\n"
      and "pipebox-card generate" in c6.log,
      "rc=%d card=%r" % (c6.rc, c6.live("etc/pipeos/card.conf")))

# ── 9. an unprovisioned box skips the gate rather than blocking ─────────
c7 = case(card="NICK=\n").run()
check("9 an unprovisioned box deploys with the card gate skipped",
      c7.rc == 0 and "card gate skipped" in c7.log
      and c7.live("usr/local/bin/pipeos-thing") is not None, "rc=%d" % c7.rc)

# ── 10. --dry-run writes nothing ─────────────────────────────────────────
c8 = case().run("--dry-run")
check("10 --dry-run writes no file and no stamp",
      c8.rc == 0 and c8.live("usr/local/bin/pipeos-thing") is None
      and c8.stamp() is None and "dry run" in c8.log, "rc=%d" % c8.rc)

# ── 11. a ref that does not exist ────────────────────────────────────────
c9 = case().run(ref="no-such-ref")
check("11 an unknown ref fails before touching anything",
      c9.rc != 0 and c9.live("usr/local/bin/pipeos-thing") is None, "rc=%d" % c9.rc)

# ── 12. an empty overlay must not read as 'nothing to change' ───────────
c10 = case(fixture={"README.md": ("no overlay here\n", 0o644)}, card=None).run()
check("12 a ref with no overlay files refuses instead of deploying nothing",
      c10.rc != 0 and "refusing to deploy nothing" in c10.log, "rc=%d" % c10.rc)

# ── 13. THE FENCE: relocated install must not reach the real box ────────
c11 = case().run(persist_fence=False)
check("13 a relocated run without NO_PERSIST=1 refuses (never reaches save)",
      c11.rc != 0 and "PIPEOS_DEPLOY_NO_PERSIST" in c11.log
      and c11.live("usr/local/bin/pipeos-thing") is None, "rc=%d" % c11.rc)

# ── 14. a symlink in the overlay is refused, not silently mishandled ────
c12 = Case()
CASES.append(c12)
os.symlink("/work/elsewhere", os.path.join(c12.repo, "overlay/usr/local/bin/pipeos-link"))
git(c12.repo, "add", "-A")
git(c12.repo, "commit", "-qm", "add a symlink")
c12.run()
check("14 a symlink in the overlay stops the deploy with a reason",
      c12.rc != 0 and "regular files only" in c12.log, "rc=%d" % c12.rc)

for c in CASES:
    shutil.rmtree(c.dir, ignore_errors=True)

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
