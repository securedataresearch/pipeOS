#!/usr/bin/env python3
"""Probe for the two overlay-age readers this PR bolts onto every boot (#97).

box1's review of #102 named the gap exactly: `pipeos-deploy-overlay` is an
operator verb run by hand and it has fourteen probe rows, while the two pieces
that run on EVERY box at EVERY boot — `pipeos:overlay_behind` and
`pipeos-selfcheck` section 5cc — had none. Both of the defects found on review
were in those two, which is what having no rows buys you.

Neither can be executed whole here: `pipeos status` reads /media/usb and the
apkovl, and selfcheck's other sections start services and touch the boot media,
all hard-banned. So this does what the #100 probe did — it SLICES THE SHIPPED
TEXT out of each file and runs that, so the thing under test is the code that
ships and not a paraphrase of it. Each slice is located by anchors that must
match exactly once; a shape change kills the probe loudly instead of quietly
testing nothing.

The git states below are built for real — real commits, real merges, a real
unmerged branch — because the defect in `overlay_behind` was invisible to any
amount of reading and appeared the moment the function ran against an
ancestor-of-tip commit.

Exit 0 if every row passes. Controls live in check-overlay-freshness-controls.py.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PIPEOS = os.path.join(REPO, "overlay/usr/local/bin/pipeos")
SELFCHECK = os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")

GIT = ["git", "-c", "user.email=probe@pipeos", "-c", "user.name=probe",
       "-c", "commit.gpgsign=false"]

RESULTS = []
TMPS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def slice_out(path, start, end, what):
    """Return the shipped text between two anchors, or die naming why."""
    src = open(path).read()
    m = re.search(start + r"(.*?)" + end, src, re.S | re.M)
    if not m:
        sys.exit("probe: could not slice %s out of %s — the file changed "
                 "shape, fix the probe before trusting it" % (what, path))
    if len(re.findall(start, src, re.S | re.M)) != 1:
        sys.exit("probe: %s anchor is not unique in %s" % (what, path))
    return m.group(0)


# The shipped function, verbatim, from `#` comment header to its closing brace.
OVERLAY_BEHIND = slice_out(PIPEOS, r"^overlay_behind\(\) \{", r"^\}$",
                           "overlay_behind")
# The shipped selfcheck section, from its banner to the next section's.
SECTION_5CC = slice_out(SELFCHECK, r"^# ---- 5cc\.", r"^# ---- 5d\.",
                        "selfcheck section 5cc")


def git(repo, *args):
    return subprocess.run(GIT + ["-C", repo] + list(args),
                          capture_output=True, text=True)


def commit(repo, msg, days_ago=0):
    """A commit whose author and committer dates are `days_ago` in the past.

    Epoch form rather than "9 days ago": this box's git rejects approxidate in
    GIT_AUTHOR_DATE ("fatal: invalid date format"), and a probe that only runs
    where the phrasing happens to parse is not a gate.
    """
    when = "@%d +0000" % int(time.time() - days_ago * 86400)
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    with open(os.path.join(repo, "f"), "a") as f:
        f.write(msg + "\n")
    subprocess.run(GIT + ["-C", repo, "add", "-A"], capture_output=True)
    subprocess.run(GIT + ["-C", repo, "commit", "-qm", msg],
                   capture_output=True, env=env)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def newrepo():
    d = tempfile.mkdtemp(prefix="ovlfresh-")
    TMPS.append(d)
    r = os.path.join(d, "repo")
    os.makedirs(r)
    git(r, "init", "-q", "-b", "main")
    return r


def sh(script, **env):
    e = dict(os.environ)
    e.update({k: str(v) for k, v in env.items()})
    p = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                       env=e, stdin=subprocess.DEVNULL)
    return p.stdout + p.stderr


def run_behind(repo, deployed, base_ref="origin/main"):
    """Run the SHIPPED overlay_behind over a real repo."""
    return sh("REPO=%s\nBASE_REF=%s\n%s\noverlay_behind %s\n"
              % (repo, base_ref, OVERLAY_BEHIND, deployed)).strip()


def run_5cc(repo, stamp_text, crit_days=3, base_ref="origin/main"):
    """Run the SHIPPED section 5cc with the report functions stubbed.

    The stubs record which SEVERITY each finding got, because that is the
    thing under test in row 5: `crit` gates the known-good promotion and
    `critstate` deliberately does not.
    """
    d = tempfile.mkdtemp(prefix="ovl5cc-")
    TMPS.append(d)
    stamp = os.path.join(d, "stamp")
    if stamp_text is not None:
        open(stamp, "w").write(stamp_text)
    prelude = (
        'warnn() { echo "WARN: $*"; }\n'
        'crit() { echo "CRIT: $*"; }\n'
        'critstate() { echo "CRITSTATE: $*"; }\n'
        'PIPEOS_REPO=%s\nPIPEOS_BASE_REF=%s\nOVERLAY_CRIT_DAYS=%d\n'
        % (repo, base_ref, crit_days))
    body = SECTION_5CC.replace("_ov_stamp=/etc/pipeos/.overlay-stamp",
                               "_ov_stamp=%s" % stamp, 1)
    if stamp not in body:
        sys.exit("probe: could not point section 5cc at the test stamp")
    return sh(prelude + body).strip()


def stamp_for(commit_sha):
    return ("commit %s\nref main\ncommit_date 2026-08-15\n"
            "deployed_at 2026-08-15T00:00:00Z\ndeployed_by probe@probe\n"
            % commit_sha)


# ── the repo every row shares: main with history, plus an unmerged branch ──
R = newrepo()
c_old = commit(R, "one", days_ago=9)
c_mid = commit(R, "two", days_ago=6)
c_tip = commit(R, "three", days_ago=0)
# `origin/main` without a remote: the boot path never fetches, and what it
# reads is a local ref. A real box has one because it cloned; here it is made
# directly, which is the same object either way.
git(R, "update-ref", "refs/remotes/origin/main", c_tip)
# The state box1 found: a commit cut off main's tip that was never merged.
git(R, "checkout", "-q", "-b", "feature", c_tip)
c_unmerged = commit(R, "unmerged work", days_ago=0)
git(R, "checkout", "-q", "main")

# ── 1. current ────────────────────────────────────────────────────────────
out = run_behind(R, c_tip)
check("1 a box deployed at origin/main says nothing at all",
      out == "", repr(out))

# ── 2. behind ─────────────────────────────────────────────────────────────
out = run_behind(R, c_old)
check("2 a box two commits behind names the count and the oldest gap",
      "2 commit(s) behind origin/main" in out and "6d old" in out, repr(out))

# ── 3. THE DEFECT box1 FOUND ─────────────────────────────────────────────
# `rev-list --count c..tip` is 0 whenever tip is an ANCESTOR of c, so this
# state — a box running a feature commit cut from the tip — read as current.
# The count is not merely wrong; it can never say the thing that matters.
out = run_behind(R, c_unmerged)
check("3 a box running an UNMERGED commit says so, and does not read as current",
      "never merged" in out and "NOT on origin/main" in out, repr(out))

# ── 4. and it still says so after main moves on ──────────────────────────
# The bug's second face: once main advances the count becomes 1 and the box
# reports "1 commit behind", which is true and beside the point. The unmerged
# finding is the graver one and must survive the count being non-zero.
c_after = commit(R, "four", days_ago=0)
git(R, "update-ref", "refs/remotes/origin/main", c_after)
out = run_behind(R, c_unmerged)
check("4 'never merged' outranks 'n behind' once main has moved",
      "never merged" in out and "behind" not in out, repr(out))
git(R, "update-ref", "refs/remotes/origin/main", c_tip)

# ── 5. THE OTHER DEFECT: severity vs the promotion gate ──────────────────
# A 9-day-old undeployed commit is over the 3-day CRIT threshold. It must be
# reported at critical severity AND must not be a `crit`, because `crit` is
# what gates known-good promotion in section 6 — whose own rule is that a
# finding which does not make the state bad must not freeze known-good.
out = run_5cc(R, stamp_for(c_old), crit_days=3)
check("5 a stale overlay is CRITICAL but not the kind that gates promotion",
      "CRITSTATE:" in out and "CRIT:" not in out.replace("CRITSTATE:", ""),
      repr(out))

# ── 6. under the threshold it is a warning ───────────────────────────────
out = run_5cc(R, stamp_for(c_mid), crit_days=30)
check("6 under OVERLAY_CRIT_DAYS it warns rather than escalating",
      out.startswith("WARN:") and "overlay is behind" in out, repr(out))

# ── 7. current: silent ───────────────────────────────────────────────────
out = run_5cc(R, stamp_for(c_tip))
check("7 a current box reports nothing from 5cc", out == "", repr(out))

# ── 8. no stamp is a finding, not a pass ─────────────────────────────────
out = run_5cc(R, None)
check("8 a box with no deploy record is reported, not passed",
      "WARN:" in out and "hand-copied" in out, repr(out))

# ── 9. the unmerged state, through selfcheck too ─────────────────────────
# Same defect, second consumer. A fix applied in one file and not the other is
# the shape this fleet keeps hitting, so both readers get the row.
out = run_5cc(R, stamp_for(c_unmerged))
check("9 selfcheck also refuses to call an unmerged overlay current",
      "never merged" in out, repr(out))

# ── 10. a stamp naming a commit this checkout does not have ──────────────
out = run_5cc(newrepo(), stamp_for(c_old))
check("10 an unknown deployed commit says it cannot answer, not that all is well",
      "cannot tell how old" in out or "not in the local checkout" in out,
      repr(out))

# ── 11. never fetches ────────────────────────────────────────────────────
# This runs at boot, before the network is up. A fetch there is a full DNS and
# connect timeout on every boot — the same reason /etc/apk/repositories stays
# local-only. Asserted against the shipped text of both slices, ignoring
# comments: both slices say the word "fetch" while explaining why they do not
# do it, so matching the raw text would pass for the wrong reason.
_code = "\n".join(l for l in (OVERLAY_BEHIND + "\n" + SECTION_5CC).splitlines()
                  if not l.lstrip().startswith("#"))
check("11 neither reader ever runs git fetch",
      re.search(r"\bfetch\b", _code) is None, repr(_code[:200]))

for d in TMPS:
    shutil.rmtree(d, ignore_errors=True)

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
