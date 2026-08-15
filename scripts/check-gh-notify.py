#!/usr/bin/env python3
"""Probe for pipeOS#88 — pipebox-gh-notify must not advance its cursor past an
issue it did not send.

Design notes, because a weak probe here is worse than none:

* It runs THE SHIPPED SCRIPT, not a transcription of it. The only edits made to
  the copy under test are five path/bootstrap lines (STATE, LOG, CURSOR, the
  lock file, and the /etc conf source), and the probe ASSERTS that each of the
  five substitutions actually applied — a silently-missed sed would otherwise
  point the test at the live cursor.
* `gh` and `pipe` are stubs on PATH. The gh stub records its own argv, so the
  search flags are an assertion rather than an assumption: the cursor maths is
  only sound if the search returns a time-ordered prefix, and nothing else in
  the script can check that the flag asking for one is still there.
* Every row states what it would catch. `scripts/check-gh-notify-controls.py`
  breaks the script three ways and each break must fail a DIFFERENT row, for
  its own reason — a probe that only ever passes has two explanations.

    python3 scripts/check-gh-notify.py
    python3 scripts/check-gh-notify-controls.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "overlay/usr/local/bin/pipebox-gh-notify")

SUBS = [
    (re.compile(r'^STATE=.*$', re.M), 'STATE="$PROBE_STATE"'),
    (re.compile(r'^LOG=.*$', re.M), 'LOG="$PROBE_LOG"'),
    (re.compile(r'^CURSOR=.*$', re.M), 'CURSOR="$PROBE_STATE/gh-cursor"'),
    (re.compile(r'^exec 9> /run/\S+$', re.M), 'exec 9> "$PROBE_STATE/lock"'),
    (re.compile(r'^\. /etc/pipeos/pipebox\.conf.*$', re.M), ':'),
]


def stage(tmp):
    src = open(SRC).read()
    out = src
    for pat, repl in SUBS:
        out, n = pat.subn(repl, out, count=1)
        if n != 1:
            raise SystemExit(f"probe cannot stage the script: no match for {pat.pattern!r}")
    if "/work/pipebox/state" in out or "/run/pipebox-gh-notify.lock" in out:
        raise SystemExit("probe staging left a live path in the copy under test")
    path = os.path.join(tmp, "gh-notify")
    open(path, "w").write(out)
    os.chmod(path, 0o755)
    return path, src


def stubs(tmp, issues, dm_rc=0):
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir, exist_ok=True)
    open(os.path.join(tmp, "issues.json"), "w").write(json.dumps(issues))
    gh = os.path.join(bindir, "gh")
    open(gh, "w").write(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" > {tmp}/gh-argv\n'
        f'cat {tmp}/issues.json\n'
    )
    os.chmod(gh, 0o755)
    pipe = os.path.join(bindir, "pipe")
    open(pipe, "w").write(
        "#!/bin/sh\n"
        f'printf "%s" "$3" > {tmp}/dm\n'
        f"exit {dm_rc}\n"
    )
    os.chmod(pipe, 0o755)
    return bindir


def issue(n, ts):
    return {
        "repository": {"nameWithOwner": "o/r"},
        "number": n,
        "title": f"issue {n}",
        "url": f"https://x/{n}",
        "updatedAt": ts,
    }


def run(issues, cursor="2026-08-01T00:00:00Z", dm_rc=0):
    tmp = tempfile.mkdtemp(prefix="ghnotify-")
    try:
        script, _ = stage(tmp)
        state = os.path.join(tmp, "state")
        os.makedirs(state)
        open(os.path.join(state, "gh-cursor"), "w").write(cursor + "\n")
        bindir = stubs(tmp, issues, dm_rc)
        env = dict(os.environ)
        env["PATH"] = bindir + ":" + env["PATH"]
        env["PROBE_STATE"] = state
        env["PROBE_LOG"] = os.path.join(tmp, "log")
        env["OWNER_NICK"] = "sam"
        env["GH_OWNER"] = "o"
        subprocess.run(["/bin/sh", script], env=env, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dmf = os.path.join(tmp, "dm")
        return {
            "cursor": open(os.path.join(state, "gh-cursor")).read().strip(),
            "dm": open(dmf).read() if os.path.exists(dmf) else None,
            "argv": open(os.path.join(tmp, "gh-argv")).read()
            if os.path.exists(os.path.join(tmp, "gh-argv")) else "",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ts(i):
    return "2026-08-10T00:%02d:00Z" % i


# --- multi-tick harness -----------------------------------------------------
# The single-tick stub above returns its whole fixture regardless of `since`,
# which is fine for the rows that inspect one batch. It cannot see a cursor
# that lands somewhere unrecoverable, because that only shows up on the NEXT
# query. So: a gh stub that is a real search — filter `> since`, sort ascending,
# truncate at --limit — driven to quiescence. (box0 on #95)
SEARCH_GH = '''#!/usr/bin/env python3
import json, os, re, sys
argv = " ".join(sys.argv[1:])
open(os.environ["PROBE_ARGV"], "w").write(argv)
m = re.search(r"--updated\\s+>(\\S+)", argv)
since = m.group(1) if m else ""
lim = int(re.search(r"--limit\\s+(\\d+)", argv).group(1))
items = json.load(open(os.environ["PROBE_ISSUES"]))
sel = sorted((i for i in items if i["updatedAt"] > since),
             key=lambda i: i["updatedAt"])
print(json.dumps(sel[:lim]))
'''

SEARCH_PIPE = '''#!/bin/sh
printf '%s\\n' "$3" >> "$PROBE_DMS"
exit 0
'''


def run_ticks(issues, cursor="2026-08-01T00:00:00Z", max_ticks=25):
    """Tick until the cursor stops moving. Returns (sent_numbers, cursors)."""
    tmp = tempfile.mkdtemp(prefix="ghnotify-ticks-")
    try:
        script, _ = stage(tmp)
        state = os.path.join(tmp, "state")
        os.makedirs(state)
        cf = os.path.join(state, "gh-cursor")
        open(cf, "w").write(cursor + "\n")
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        open(os.path.join(tmp, "issues.json"), "w").write(json.dumps(issues))
        for name, body in (("gh", SEARCH_GH), ("pipe", SEARCH_PIPE)):
            p = os.path.join(bindir, name)
            open(p, "w").write(body)
            os.chmod(p, 0o755)
        env = dict(os.environ)
        env.update(
            PATH=bindir + ":" + env["PATH"],
            PROBE_STATE=state, PROBE_LOG=os.path.join(tmp, "log"),
            PROBE_ISSUES=os.path.join(tmp, "issues.json"),
            PROBE_ARGV=os.path.join(tmp, "gh-argv"),
            PROBE_DMS=os.path.join(tmp, "dms"),
            OWNER_NICK="sam", GH_OWNER="o",
        )
        cursors = []
        for _ in range(max_ticks):
            subprocess.run(["/bin/sh", script], env=env, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            c = open(cf).read().strip()
            if cursors and cursors[-1] == c:
                break
            cursors.append(c)
        dmf = os.path.join(tmp, "dms")
        text = open(dmf).read() if os.path.exists(dmf) else ""
        sent = {int(l.split("#")[1].split()[0])
                for l in text.splitlines() if "http" in l and "#" in l}
        return sent, cursors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


rows = []


def check(name, catches, ok, detail=""):
    rows.append((name, bool(ok), catches, detail))


# --- 1. the search must ask for a time-ordered prefix ----------------------
# Without asc-by-updated the returned set is best-match: it has holes in time,
# and then NO cursor value is safe, because an unreturned issue can be older
# than one that was sent.
r = run([issue(1, ts(1))])
check("search asks for --sort updated --order asc",
      "a future edit dropping the ordering flags, which silently un-sounds "
      "every cursor assertion below",
      "--sort updated" in r["argv"] and "--order asc" in r["argv"],
      r["argv"].strip()[:120])

# --- 2. under the DM cap: everything sent, cursor free to move to now -------
few = [issue(i, ts(i)) for i in range(1, 4)]
r = run(few)
check("3 found, 3 sent, cursor past them all",
      "a fix that holds the cursor back even when nothing was held, stalling "
      "the channel on a repeated batch",
      r["cursor"] > ts(3) and "of 3 changed" not in (r["dm"] or ""),
      f"cursor={r['cursor']}")

# --- 3. over the cap: cursor stops at the last SENT issue -------------------
many = [issue(i, ts(i)) for i in range(1, 16)]
r = run(many)
sent_lines = [l for l in (r["dm"] or "").splitlines() if "#" in l and "http" in l]
check("15 found, 10 sent, cursor == 10th issue's updatedAt",
      "THE ORIGINAL DEFECT: cursor advancing to now and discarding the 5 that "
      "were never sent",
      r["cursor"] == ts(10) and len(sent_lines) == 10,
      f"cursor={r['cursor']} lines={len(sent_lines)}")

check("the DM says 10 of 15, not a bare list",
      "a window that reads as complete — the #658 class this issue is about",
      "10 of 15 changed issues" in (r["dm"] or ""),
      (r["dm"] or "").splitlines()[0] if r["dm"] else "no dm")

# --- 4. the tie boundary ----------------------------------------------------
# Issues 10, 11 and 12 share a second. Cutting at exactly 10 and then querying
# `> ts(10)` would drop 11 and 12 forever, so the batch must extend over the
# tie. This row is the reason the batch is not a plain array slice.
tied = [issue(i, ts(i)) for i in range(1, 10)] + \
       [issue(i, ts(10)) for i in (10, 11, 12)] + \
       [issue(i, ts(i)) for i in range(13, 16)]
r = run(tied)
sent_lines = [l for l in (r["dm"] or "").splitlines() if "http" in l]
check("issues sharing the boundary second all ride in the same batch",
      "a plain [0:n] slice, which loses every issue that shares the cut "
      "timestamp because the next query uses strict >",
      len(sent_lines) == 12 and r["cursor"] == ts(10),
      f"sent={len(sent_lines)} cursor={r['cursor']}")

# --- 5. at the search limit -------------------------------------------------
full = [issue(i, ts(i)) for i in range(1, 21)]
r = run(full)
check("a full-limit result says the true total is unknown",
      "reporting 20 as if it were the count, when it is only what the search "
      "would return — the doubly-truncated tick in the issue",
      "true total is unknown" in (r["dm"] or ""),
      (r["dm"] or "").splitlines()[1] if r["dm"] and len(r["dm"].splitlines()) > 1 else "")

check("at the search limit the cursor still stops at the last sent issue",
      "advancing past issues the search never returned",
      r["cursor"] == ts(10),
      f"cursor={r['cursor']}")

# --- 6. a failed DM must not move the cursor --------------------------------
r = run(many, dm_rc=1)
check("a failed DM leaves the cursor where it was",
      "losing a whole batch to one offline tick",
      r["cursor"] == "2026-08-01T00:00:00Z",
      f"cursor={r['cursor']}")

# --- 7. nothing found -------------------------------------------------------
r = run([])
check("an empty tick sends no DM and advances the cursor",
      "either DMing an empty list, or never advancing on a quiet channel so "
      "`since` grows unboundedly stale",
      r["dm"] is None and r["cursor"] > ts(20),
      f"cursor={r['cursor']} dm={r['dm']!r}")

# --- 8. the invariant, stated directly --------------------------------------
# Restated as a property over the two windowed cases rather than a constant, so
# it keeps meaning if MAX_ITEMS changes.
bad = []
for label, data in (("15 distinct", many), ("tie at the boundary", tied), ("at limit", full)):
    r = run(data)
    shown = [l for l in (r["dm"] or "").splitlines() if "http" in l]
    nums = {int(l.split("#")[1].split()[0]) for l in shown}
    by_num = {i["number"]: i["updatedAt"] for i in data}
    unsent = [t for n, t in by_num.items() if n not in nums]
    if any(t <= r["cursor"] for t in unsent):
        bad.append(f"{label}: cursor={r['cursor']} covers an unsent issue")
check("across every windowed case, no unsent issue is behind the cursor",
      "any future rewrite of the batch maths that re-opens the drop",
      not bad, "; ".join(bad))

# --- 9. the SEARCH_LIMIT boundary, driven to quiescence ---------------------
# box0's finding on #95. The tie extension guards the cut at MAX_ITEMS; nothing
# guarded the cut at SEARCH_LIMIT. When the search returns its full limit, its
# last timestamp may itself be a truncated tie group, and advancing onto it
# makes `>` skip every issue that fell off the page — permanently, which is
# #88's own mechanism reappearing one boundary over.
#
# Reachable, not theoretical: a bulk relabel or a batch of card moves updates
# many issues within one second.
LIMIT = 20
# The degenerate shape first, because it bounds what the other rows may claim.
# 25 issues in one second against a 20-item page is NOT fixable by cursor
# arithmetic: a strict `>` cursor at second granularity has no value between
# "repeat this page" and "skip part of a second nothing enumerated". Five of
# these are unreachable whatever the cursor does.
#
# So the property is not losslessness — it is that the script neither claims
# those five were sent nor silently steps over them. It holds still and says
# it cannot advance. A stuck channel that announces it is stuck is a bug
# report; a stuck channel that looks quiet is the #88 failure again.
one_second = [issue(i, ts(10)) for i in range(1, 26)]
sent, cursors = run_ticks(one_second)
missed = sorted({i["number"] for i in one_second} - sent)
r = run(one_second[:LIMIT])
check("25 issues in ONE second: the cursor never steps over the unreachable",
      "advancing onto a second the search proved nothing about — the #88 "
      "mechanism one boundary over, where the skip is silent and permanent",
      set(cursors) == {"2026-08-01T00:00:00Z"} and missed == [21, 22, 23, 24, 25]
      and "cannot page inside a single second" in (r["dm"] or ""),
      f"unreachable: {missed} cursors={cursors}")

# The likelier shape: 9 distinct seconds, then a tie that fills the page. The
# extension swallows the whole page, so held=0 and only the at_cap guard is
# left to notice the last second is unverified.
mixed = [issue(i, ts(i)) for i in range(1, 10)] + \
        [issue(i, ts(10)) for i in range(10, 26)]
sent, cursors = run_ticks(mixed)
missed = sorted({i["number"] for i in mixed} - sent)
check("9 distinct + 16 sharing the boundary second: none are lost",
      "the milder shape of the same defect, where the tie extension makes "
      "held=0 and hides that the page was truncated",
      not missed, f"never sent: {missed} cursors={cursors}")

# The control for both rows above. Same count, same limit, distinct seconds —
# no tie can be truncated, so this must drain and quiesce even with the guard
# removed. Without it, rows 9 and 10 could be passing because the script simply
# never advances its cursor at all.
distinct = [issue(i, ts(i)) for i in range(1, 26)]
sent, cursors = run_ticks(distinct)
missed = sorted({i["number"] for i in distinct} - sent)
check("control: 25 distinct seconds drains and the cursor still advances",
      "a 'fix' that never moves the cursor — lossless and useless, and it "
      "would pass both rows above",
      not missed and len(cursors) > 1 and cursors[-1] >= ts(25),
      f"never sent: {missed} cursors={cursors}")

# --- 11. the degenerate case, stated rather than left implicit --------------
# When the entire page is one second there is NO safe cursor: any value either
# repeats the page or skips part of a second that was never fully seen. The
# script holds the cursor still and says so. Noisy is a choice; lossy is a bug.
r = run([issue(i, ts(10)) for i in range(1, 21)])
check("a page that is entirely one second holds the cursor and says so",
      "silently advancing past an unverified second, or advancing while the "
      "DM claims the total is unknown",
      r["cursor"] == "2026-08-01T00:00:00Z"
      and "cannot page inside a single second" in (r["dm"] or ""),
      f"cursor={r['cursor']}")

width = max(len(n) for n, _, _, _ in rows)
fails = 0
for name, ok, catches, detail in rows:
    print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    if not ok:
        print(f"      would catch: {catches}")
        fails += 1
print(f"\n{len(rows) - fails}/{len(rows)}")
sys.exit(1 if fails else 0)
