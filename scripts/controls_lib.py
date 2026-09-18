"""controls_lib — run a probe's controls in parallel, each against its own copy
of the tree (pipeOS, 2026-09-18: the cluster step alone took 19 minutes
because 17 controls each reran a 65 s probe one after another, in place).

    sandbox({path: text})      a private copy of overlay/ + scripts/ + docs/
                               with those files replaced; returns its root
    run_probe(probe, env, root) the probe, from `root`'s copy when given
    pmap(fn, items)            fn over items on a thread per core, results
                               in order — CONTROLS_JOBS=1 for the old serial run

A control that edits the shipped file in place and restores it afterwards
cannot run beside another, and a kill mid-run leaves the tree mutated (the
reason "don't edit while the review runs" is in memory). A copy has neither
problem, and the probes already isolate their own state (mkdtemp, port 0).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
COPIED = ("overlay", "scripts", "docs")     # docs: the reference cards a probe reads


def sandbox(files):
    root = tempfile.mkdtemp(prefix="controls-")
    for d in COPIED:
        shutil.copytree(os.path.join(REPO, d), os.path.join(root, d), symlinks=True)
    os.symlink(os.path.join(REPO, ".git"), os.path.join(root, ".git"))   # read-only: `git ls-files` in the copy
    for path, text in files.items():
        rel = os.path.relpath(path, REPO)
        assert not rel.startswith(".."), path
        with open(os.path.join(root, rel), "w") as f:
            f.write(text)
    return root


def run_probe(probe, env=None, root=None):
    """(returncode, stdout) of `probe` — the copy under `root` when given."""
    p = os.path.join(root, os.path.relpath(probe, REPO)) if root else probe
    r = subprocess.run([sys.executable, p], capture_output=True, text=True, env=env, cwd=root or REPO)
    return r.returncode, r.stdout + ("" if r.returncode == 0 or not r.stderr else "\n" + r.stderr[-800:])


def fails_in(stdout):
    return [l.split()[1] for l in stdout.splitlines() if l.startswith("FAIL")]


def pmap(fn, items):
    jobs = int(os.environ.get("CONTROLS_JOBS") or 0) or min(max(len(items), 1), os.cpu_count() or 2)
    if jobs <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        return list(ex.map(fn, items))


def cleanup(root):
    shutil.rmtree(root, ignore_errors=True)
