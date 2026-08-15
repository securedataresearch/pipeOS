#!/usr/bin/env python3
"""check-cohort-watch.py — gate for pipebox-cohort-watch's predicates (pipeOS#100).

Right now it covers exactly one: `conversation_exists()`, the predicate that
decides whether a wake resumes its session or starts cold. That predicate was
reachable and permanently false across the whole fleet — `find` does not follow
a symlinked start path, and #80 made `/root/.claude/projects` a symlink on every
box — so `--resume` never fired and the log misattributed it to pipeOS#51.

WHY THIS EXTRACTS THE FUNCTION RATHER THAN RUNNING THE SCRIPT. The watcher's
body talks to the relay, to `gh` and to `claude` from its first lines; running
it in CI would test the network, not the predicate. So the harness slices the
function's own text out of the SHIPPED file and runs THAT under /bin/sh, and
FAILS LOUDLY if the slice does not match exactly once — a predicate that changed
shape must break this probe rather than quietly stop being covered.

The control is the pre-fix predicate (same text, trailing slash removed): it
must FAIL the symlink rows and PASS everything else. Without that, every row
here would pass against the bug and the probe would be asserting nothing —
which is precisely what the #51 test did by staging only a real directory.

Usage: python3 scripts/check-cohort-watch.py
Exit 0 if every check passes.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHER = os.path.join(HERE, "..", "overlay", "usr", "local", "bin",
                       "pipebox-cohort-watch")

SID = "62b4bd0b-1111-2222-3333-444455556666"
OTHER = "aaaabbbb-cccc-dddd-eeee-ffff00001111"

results = []


def ck(desc, ok, detail=""):
    results.append(ok)
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", desc,
                           ("  [%s]" % detail) if detail and not ok else ""))


def extract():
    """Slice conversation_exists() out of the shipped watcher."""
    src = open(WATCHER).read()
    m = re.findall(r"^conversation_exists\(\) \{\n.*?^\}\n", src,
                   flags=re.M | re.S)
    if len(m) != 1:
        sys.exit("probe: expected exactly 1 conversation_exists() definition in "
                 "%s, found %d — the predicate changed shape, fix the probe "
                 "before trusting it" % (WATCHER, len(m)))
    return m[0]


def decontrol(fn):
    """The pre-fix predicate: the same function with the trailing slash gone.

    Asserted rather than best-effort. If this substitution stops applying, the
    control is silently identical to the fix and every row below passes for the
    wrong reason.
    """
    body, n = re.subn(r'find "\$CLAUDE_PROJECTS/"', 'find "$CLAUDE_PROJECTS"', fn)
    if n != 1:
        sys.exit("probe: could not build the pre-fix control (%d substitutions) "
                 "— the fix is not the trailing slash any more, so this probe "
                 "no longer proves what it claims" % n)
    return body


def ask(fn, projects, sid):
    """Run the extracted predicate against one staged layout. -> True/False."""
    script = (
        'CLAUDE_PROJECTS="$1"\n'
        + fn
        + 'if conversation_exists "$2"; then echo yes; else echo no; fi\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        p = subprocess.run(["sh", path, projects, sid],
                           capture_output=True, text=True)
        out = p.stdout.strip()
        if out not in ("yes", "no"):
            sys.exit("probe: predicate printed %r (stderr: %s)"
                     % (out, p.stderr.strip()))
        return out == "yes"
    finally:
        os.unlink(path)


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("{}\n")


# --------------------------------------------------------------- fixtures
# Each row stages a directory tree and returns the path to hand the predicate
# as $CLAUDE_PROJECTS. `want` is the answer the FIXED predicate must give.
#
# Rows 1 and 9 are the symlink layout — the layout every box actually has, and
# the one the #51 test never staged.

def f_symlink(root):
    """#80's documented layout: <projects> is a symlink to the real store."""
    real = os.path.join(root, "work", "claude", "projects")
    touch(os.path.join(real, "-work-pipebox", SID + ".jsonl"))
    link = os.path.join(root, "home", ".claude", "projects")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(real, link)
    return link


def f_realdir(root):
    """The pre-#80 layout: a real directory. Must not regress."""
    p = os.path.join(root, "projects")
    touch(os.path.join(p, "-work-pipebox", SID + ".jsonl"))
    return p


def f_symlink_shallow(root):
    """Symlink layout with the jsonl at depth 1 — the -mindepth decoy."""
    real = os.path.join(root, "work", "claude", "projects")
    touch(os.path.join(real, SID + ".jsonl"))
    link = os.path.join(root, "home", ".claude", "projects")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(real, link)
    return link


def f_real_shallow(root):
    p = os.path.join(root, "projects")
    touch(os.path.join(p, SID + ".jsonl"))
    return p


def f_symlink_deep(root):
    """Depth 3 — the -maxdepth bound, checked through the symlink."""
    real = os.path.join(root, "work", "claude", "projects")
    touch(os.path.join(real, "-work-pipebox", "sub", SID + ".jsonl"))
    link = os.path.join(root, "home", ".claude", "projects")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(real, link)
    return link


def f_missing(root):
    return os.path.join(root, "nope", "projects")


def f_dangling(root):
    """A symlink whose target does not exist — must answer no, not error."""
    link = os.path.join(root, "projects")
    os.symlink(os.path.join(root, "gone"), link)
    return link


def f_empty(root):
    p = os.path.join(root, "projects")
    os.makedirs(os.path.join(p, "-work-pipebox"))
    return p


# desc, fixture, sid, want-from-fixed, want-from-control
ROWS = [
    ("1  symlinked projects dir, transcript at depth 2 -> yes  (#100)",
     f_symlink, SID, True, False),
    ("2  real projects dir, transcript at depth 2 -> yes  (#51's layout)",
     f_realdir, SID, True, True),
    ("3  symlinked dir, WRONG session id -> no",
     f_symlink, OTHER, False, False),
    ("4  real dir, wrong session id -> no",
     f_realdir, OTHER, False, False),
    ("5  symlinked dir, transcript at depth 1 -> no  (-mindepth holds)",
     f_symlink_shallow, SID, False, False),
    ("6  real dir, transcript at depth 1 -> no  (-mindepth, #71's decoy)",
     f_real_shallow, SID, False, False),
    ("7  symlinked dir, transcript at depth 3 -> no  (-maxdepth holds)",
     f_symlink_deep, SID, False, False),
    ("8  projects dir absent -> no",
     f_missing, SID, False, False),
    ("9  projects is a DANGLING symlink -> no",
     f_dangling, SID, False, False),
    ("10 projects dir present but empty -> no",
     f_empty, SID, False, False),
    ("11 empty session id -> no  (guard, never reaches find)",
     f_symlink, "", False, False),
]


def main():
    fn = extract()
    ctl = decontrol(fn)

    print("conversation_exists() — shipped predicate")
    fixed_answers = []
    for desc, fixture, sid, want, _ in ROWS:
        with tempfile.TemporaryDirectory() as root:
            got = ask(fn, fixture(root), sid)
        fixed_answers.append(got)
        ck(desc, got == want, "got %s want %s" % (got, want))

    # THE CONTROL. Its job is to fail — specifically row 1, and ONLY row 1.
    # If it fails nothing, the trailing slash is not what this probe measures.
    # If it fails more than row 1, the fix changed behaviour it should not have.
    print("\ncontrol: the pre-fix predicate (no trailing slash) must answer no "
          "on the symlink row and agree everywhere else")
    ctl_answers = []
    for row, (desc, fixture, sid, _, want_ctl) in enumerate(ROWS, 1):
        with tempfile.TemporaryDirectory() as root:
            got = ask(ctl, fixture(root), sid)
        ctl_answers.append(got)
        ck("%-2s control -> %s" % (row, "yes" if want_ctl else "no"),
           got == want_ctl, "got %s want %s" % (got, want_ctl))

    # MEASURED, not read back off the table above. Comparing the two declared
    # `want` columns would assert only that I typed them differently; comparing
    # what the two predicates actually ANSWERED is what shows the slice under
    # test is the thing that changed behaviour.
    diverged = [ROWS[i][0] for i in range(len(ROWS))
                if fixed_answers[i] != ctl_answers[i]]
    ck("fix and control diverge on exactly the symlink row, measured",
       len(diverged) == 1 and diverged[0] is ROWS[0][0],
       "diverged on %d: %s" % (len(diverged), diverged))

    n_fail = results.count(False)
    print("\ncheck-cohort-watch: %s (%d/%d)"
          % ("PASS" if n_fail == 0 else "FAIL", len(results) - n_fail,
             len(results)))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
