#!/bin/sh
# ci-local.sh — run every step .github/workflows/ci.yml runs, here, in order.
#
# Sam, 2026-09-12: "be careful to always do all the checks locally first."
# Three pushes that day went red on things a local run would have caught —
# a control whose anchor no longer matched, a lint CI's clippy has that the
# workstation's did not. This is the one command that answers "would CI be
# green": it reads the workflow itself (no second list to drift), skips the
# runner's apt-get line, and stops at the first failing step with its name.
#
#   scripts/ci-local.sh            every step
#   scripts/ci-local.sh cluster    only steps whose name or command mentions "cluster"
#
# Needs: python3 (with PyYAML), shellcheck, mtools/dosfstools where a step
# uses them (`pacman -S shellcheck mtools dosfstools` on this workstation).
set -u
cd "$(dirname "$0")/.." || exit 1
filter=${1:-}

python3 - "$filter" <<'PY'
import subprocess, sys, time, yaml
flt = sys.argv[1]
ci = yaml.safe_load(open(".github/workflows/ci.yml"))
steps = [(j, s) for j, job in ci["jobs"].items() for s in job["steps"] if "run" in s]
ran = failed = 0
for job, s in steps:
    name = s.get("name", "(unnamed)")
    cmd = "\n".join(l for l in s["run"].split("\n") if "apt-get" not in l)
    if not cmd.strip():
        continue
    if flt and flt.lower() not in (name + cmd).lower():
        continue
    ran += 1
    t = time.time()
    p = subprocess.run(["bash", "-eo", "pipefail", "-c", cmd], capture_output=True, text=True)
    took = time.time() - t
    if p.returncode == 0:
        print("ok    %-70s %5.1fs" % (name[:70], took), flush=True)
    else:
        failed += 1
        print("FAIL  %-70s %5.1fs" % (name[:70], took), flush=True)
        out = (p.stdout + p.stderr).strip().split("\n")
        for l in out[-25:]:
            print("      " + l[:200])
        print("      (job %s) — fix this before pushing" % job)
        break
print("\nci-local: %d step(s) run, %d failed%s" % (ran, failed, "" if failed else " — CI would be green"))
sys.exit(1 if failed else 0)
PY
