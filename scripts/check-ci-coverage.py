#!/usr/bin/env python3
"""check-ci-coverage: every probe in scripts/ is wired into CI, or named here.

pipeOS#109's finding was structural: a probe that nobody runs gates nothing,
and the repo had four of them — including the rows over the fleet's root of
trust. Wiring them in fixes today; this gate fixes tomorrow, by failing CI
the moment a new check-*.py lands without a workflow step running it.

A probe may be consciously excluded, but only by name and with a reason —
an allowlist entry is a visible debt, not a silent hole.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CI = ROOT / ".github/workflows/ci.yml"

# name -> reason. Every entry here is a probe CI does NOT run, on purpose.
KNOWN_UNGATED = {
    # 12/14 rows fail against the post-#88 pipebox-gh-notify: the probe's
    # fake-gh stubs no longer match the script's call shapes, so the cursor
    # never moves and every accounting row reads as lost mail. Fleet-dormant
    # machinery; re-align the probe when the fleet wakes (see pipeOS#109's
    # closing comment) rather than gating CI on a known-rotted harness.
    "check-gh-notify.py": "probe rotted against the #88 rewrite; fleet dormant",
}

ci_text = CI.read_text()
probes = sorted(p.name for p in (ROOT / "scripts").glob("check-*.py"))
probes = [p for p in probes if p != "check-ci-coverage.py"]

fails = 0
for p in probes:
    wired = re.search(rf"scripts/{re.escape(p)}\b", ci_text)
    excused = p in KNOWN_UNGATED
    if wired and excused:
        print(f"FAIL  {p}: wired into CI but still on the KNOWN_UNGATED list — remove the excuse")
        fails += 1
    elif wired:
        print(f"ok    {p}: wired into CI")
    elif excused:
        print(f"ok    {p}: consciously ungated ({KNOWN_UNGATED[p]})")
    else:
        print(f"FAIL  {p}: no CI step runs it — a probe nobody runs gates nothing (pipeOS#109)")
        fails += 1

stale = set(KNOWN_UNGATED) - set(probes)
for s in sorted(stale):
    print(f"FAIL  KNOWN_UNGATED names {s}, which does not exist")
    fails += 1

if fails:
    print(f"check-ci-coverage: FAIL ({fails})")
    sys.exit(1)
print(f"check-ci-coverage: PASS ({len(probes)} probes accounted for)")
