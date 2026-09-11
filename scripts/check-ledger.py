#!/usr/bin/env python3
"""Probe for the usage ledger (#246): ledger.py through its seams — fixture
transcripts shaped like Claude Code's (one line per content block, the same
message id and usage on each; a sidechain; a user line; a line with no
usage; the 5m/1h cache split; an unknown model; a torn tail), a fixture
rate table, a runs.log and a listener session for attribution, and a
stub `pipe` for the cap's DMs. No root, nothing outside a tempdir.

Exit 0 if every row passes. Controls: check-ledger-controls.py.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
LEDGER = os.environ.get("CHECK_LEDGER_BIN", os.path.join(WEB, "ledger.py"))
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="ckledger-")
spec = importlib.util.spec_from_file_location("ledger", LEDGER)
lg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lg)

NOW = "2026-09-10T18:00:00Z"
os.environ["PIPEOS_LEDGER_NOW"] = NOW
TR = os.path.join(D, "projects")
LDIR = os.path.join(D, "ledger")
CONF = os.path.join(D, "pipebox.conf")
RUNS = os.path.join(D, "runs.log")
SESS = os.path.join(D, "sessions")
WSID = os.path.join(D, ".dashboard-sid")
RATES = os.path.join(D, "rates.json")
PIPE = os.path.join(D, "pipe")
os.makedirs(SESS)
json.dump({"updated": "2026-09-10", "per_mtok": {
    "claude-opus-5": {"in": 5, "out": 25, "cache_read": 0.5, "cache_w5m": 6.25, "cache_w1h": 10},
    "claude-sonnet-5": {"in": 2, "out": 10, "cache_read": 0.2, "cache_w5m": 2.5, "cache_w1h": 4},
}}, open(RATES, "w"))
open(CONF, "w").write('NICK="box"\nOWNER_NICK="sam"\nMONTHLY_CAP_USD=""\n')
open(PIPE, "w").write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + D + "/pipe.argv\n")
os.chmod(PIPE, 0o755)

SID_JOB = "11111111-1111-4111-8111-111111111111"
SID_LISTENER = "22222222-2222-4222-8222-222222222222"
SID_DASH = "33333333-3333-4333-8333-333333333333"
SID_ASSIST = "44444444-4444-4444-8444-444444444444"
SID_OTHER = "55555555-5555-4555-8555-555555555555"
open(RUNS, "w").write("2026-09-10T02:00:00Z %s nightly ok\n" % SID_JOB)
open(os.path.join(SESS, "alice"), "w").write(SID_LISTENER + "\n")
open(WSID, "w").write(SID_DASH + "\n")


def line(sid, cwd, mid, model, ts, inp=100, out=50, cr=1000, w5=0, w1=0, legacy_cc=None, sidechain=False, typ="assistant", usage=True):
    u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr}
    if legacy_cc is not None:
        u["cache_creation_input_tokens"] = legacy_cc
    else:
        u["cache_creation_input_tokens"] = w5 + w1
        u["cache_creation"] = {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}
    d = {"type": typ, "sessionId": sid, "cwd": cwd, "timestamp": ts, "isSidechain": sidechain,
         "message": {"id": mid, "model": model, "usage": u} if usage else {"id": mid, "model": model}}
    return json.dumps(d) + "\n"


def write_transcript(rel, lines, torn=""):
    p = os.path.join(TR, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("".join(lines) + torn)
    return p


def L():
    return lg.Ledger(dir=LDIR, transcripts=TR, rates=RATES, conf=CONF, runs_log=RUNS, sessions_dir=SESS, webchat_sid=WSID, pipe_bin=PIPE)


# session A: a job, 5 API turns each written 2-3 times, one sidechain, a user line, a usage-less line
A = []
for i in range(5):
    mid = "msg_A%d" % i
    ts = "2026-09-10T02:0%d:00Z" % i
    for _ in range(2 if i % 2 else 3):
        A.append(line(SID_JOB, "/work/pipebox/jobs/nightly", mid, "claude-opus-5", ts, inp=1000, out=200, cr=10000, w5=500, w1=0))
A.append(line(SID_JOB, "/work/pipebox/jobs/nightly", "msg_Aside", "claude-opus-5", "2026-09-10T02:06:00Z", inp=10, out=10, cr=0, sidechain=True))
A.append(line(SID_JOB, "/work/pipebox/jobs/nightly", "msg_Auser", "claude-opus-5", "2026-09-10T02:07:00Z", typ="user"))
A.append(line(SID_JOB, "/work/pipebox/jobs/nightly", "msg_Anousage", "claude-opus-5", "2026-09-10T02:08:00Z", usage=False))
pA = write_transcript("-work-pipebox-jobs-nightly/%s.jsonl" % SID_JOB, A)
# session B: unknown model, the listener, a 1h cache write, legacy cache field
B = [line(SID_LISTENER, "/work/pipebox", "msg_B0", "claude-mystery-9", "2026-09-10T10:00:00Z", inp=100, out=100, cr=0),
     line(SID_LISTENER, "/work/pipebox", "msg_B1", "claude-sonnet-5-20260901", "2026-09-10T10:01:00Z", inp=1000, out=1000, cr=0, w5=0, w1=2000),
     line(SID_LISTENER, "/work/pipebox", "msg_B2", "claude-sonnet-5", "2026-09-10T10:02:00Z", inp=0, out=0, cr=0, legacy_cc=4000)]
pB = write_transcript("-work-pipebox/%s.jsonl" % SID_LISTENER, B)
# dashboard, assistant, other, and old rows (35 days ago) — plus a torn tail
C = [line(SID_DASH, "/work/pipebox/webchat", "msg_C0", "claude-opus-5", "2026-09-10T12:00:00Z", inp=1000, out=0, cr=0),
     line(SID_ASSIST, "/work/pipebox/webchat", "msg_C1", "claude-opus-5", "2026-09-09T12:00:00Z", inp=1000, out=0, cr=0),
     line(SID_OTHER, "/work/repos/thing", "msg_C2", "claude-opus-5", "2026-08-21T12:00:00Z", inp=1000, out=0, cr=0),
     line(SID_OTHER, "/work/repos/thing", "msg_C3", "claude-opus-5", "2026-08-06T12:00:00Z", inp=1000000, out=0, cr=0)]
pC = write_transcript("-work-pipebox-webchat/%s.jsonl" % SID_DASH, C, torn='{"type":"assistant","message":{"id":"msg_torn","usage":{"input_tokens":5')

# ── 1. ingest: distinct ids, attribution, cost ──────────────────────────
n = L().ingest()
rows = list(L().rows(["2026-09", "2026-08"]))
byid = {r["id"]: r for r in rows}
check("1 ingest writes one row per distinct message id (5 turns written 12 times = 5 rows), counts the sidechain, skips user and usage-less lines and the torn tail",
      n == 13 and len(rows) == 13 and set(byid) == {"msg_A0", "msg_A1", "msg_A2", "msg_A3", "msg_A4", "msg_Aside", "msg_B0", "msg_B1", "msg_B2", "msg_C0", "msg_C1", "msg_C2", "msg_C3"},
      "n=%d ids=%r" % (n, sorted(byid)))
a0 = byid["msg_A0"]
# opus: 1000 in @5 + 200 out @25 + 10000 cr @0.5 + 500 w5 @6.25 per MTok
want = (1000 * 5 + 200 * 25 + 10000 * 0.5 + 500 * 6.25) / 1e6
b1 = byid["msg_B1"]
want_b1 = (1000 * 2 + 1000 * 10 + 2000 * 4) / 1e6
b2 = byid["msg_B2"]
check("2 cost to the cent: input, output, cache reads, 5m and 1h writes priced per model; a dated model id resolves to its family; a legacy cache field without the split is priced as 5m",
      abs(a0["cost_usd"] - want) < 1e-9 and a0["cache_w5m"] == 500 and abs(b1["cost_usd"] - want_b1) < 1e-9 and b1["cache_w1h"] == 2000
      and b2["cache_w5m"] == 4000 and abs(b2["cost_usd"] - 4000 * 2.5 / 1e6) < 1e-9,
      "a0=%r b1=%r b2=%r" % (a0["cost_usd"], b1["cost_usd"], b2))
check("3 attribution: a job's sid -> job:<name>; a listener session -> listener:<nick>; the dashboard's own sid -> dashboard; the master session's cwd -> assistant; anything else other:<dir>",
      a0["actor"] == {"kind": "job", "name": "nightly"} and byid["msg_B0"]["actor"] == {"kind": "listener", "name": "alice"}
      and byid["msg_C0"]["actor"] == {"kind": "dashboard", "name": ""} and byid["msg_C1"]["actor"] == {"kind": "assistant", "name": ""}
      and byid["msg_C2"]["actor"] == {"kind": "other", "name": "thing"},
      repr({k: byid[k]["actor"] for k in ("msg_A0", "msg_B0", "msg_C0", "msg_C1", "msg_C2")}))
check("4 an unknown model is counted with cost null (unpriced), never dropped", byid["msg_B0"]["cost_usd"] is None and byid["msg_B0"]["in"] == 100, repr(byid["msg_B0"]))
check("5 rows land in the month file their timestamp names", os.path.exists(os.path.join(LDIR, "2026-08.jsonl")) and byid["msg_C3"]["ts"].startswith("2026-08"), "")

# ── 6-8. the cursor ────────────────────────────────────────────────────
n2 = L().ingest()
check("6 a second ingest with nothing new adds zero rows", n2 == 0 and len(list(L().rows(["2026-09"]))) == 11, "n2=%d" % n2)
with open(pA, "a") as f:
    f.write(line(SID_JOB, "/work/pipebox/jobs/nightly", "msg_A5", "claude-opus-5", "2026-09-10T02:09:00Z"))
    f.write(line(SID_JOB, "/work/pipebox/jobs/nightly", "msg_A5", "claude-opus-5", "2026-09-10T02:09:00Z"))
    f.write(line(SID_JOB, "/work/pipebox/jobs/nightly", "msg_A6", "claude-opus-5", "2026-09-10T02:10:00Z"))
n3 = L().ingest()
check("7 appending two new turns (one written twice) yields exactly two rows", n3 == 2, "n3=%d" % n3)
with open(pC, "a") as f:
    f.write(',"output_tokens":5,"cache_read_input_tokens":0},"model":"claude-opus-5"},"sessionId":"%s","cwd":"/work/pipebox/webchat","timestamp":"2026-09-10T12:30:00Z"}\n' % SID_DASH)
n4 = L().ingest()
check("8 the torn tail is ingested once its newline arrives — exactly once", n4 == 1 and "msg_torn" in {r["id"] for r in L().rows(["2026-09"])}, "n4=%d" % n4)
# rewrite A from scratch (same content, new inode/size) -> no duplicates
content = open(pA).read()
os.unlink(pA)
with open(pA, "w") as f:
    f.write(content)
n5 = L().ingest()
check("9 a transcript rewritten with a new inode (same ids) adds no duplicates", n5 == 0, "n5=%d" % n5)

# ── 10. totals ─────────────────────────────────────────────────────────
t = L().totals()
check("10 totals: today counts only today's rows, 7 days includes yesterday, 30 days includes 20 days ago but not 35, the month only September; unpriced = 1; daily[29] is today; by_actor keys are kind:name",
      t["today"]["calls"] == 13 and t["d7"]["calls"] == 14 and t["d30"]["calls"] == 15 and t["unpriced"] == 1
      and abs(t["daily"][29] - t["today"]["usd"]) < 1e-6 and "job:nightly" in t["by_actor"] and "listener:alice" in t["by_actor"] and "dashboard" in t["by_actor"]
      and t["month"]["calls"] == 14 and t["cap"]["usd"] == 0 and t["estimate"] is True,
      "today=%r d7=%r d30=%r month=%r unpriced=%r actors=%r" % (t["today"]["calls"], t["d7"]["calls"], t["d30"]["calls"], t["month"]["calls"], t["unpriced"], sorted(t["by_actor"])))

# ── 11-14. the cap ─────────────────────────────────────────────────────
def set_cap(v):
    open(CONF, "w").write('NICK="box"\nOWNER_NICK="sam"\nMONTHLY_CAP_USD="%s"\n' % v)


def dms():
    try:
        return open(D + "/pipe.argv").read().splitlines()
    except OSError:
        return []


# a big row so whole-dollar caps have room: 3M input tokens of opus = $15
with open(pA, "a") as f:
    f.write(line(SID_JOB, "/work/pipebox/jobs/nightly", "msg_big", "claude-opus-5", "2026-09-10T03:00:00Z", inp=3000000, out=0, cr=0))
L().ingest()
spent = L().totals()["month"]["usd"]
cap_warn = int(spent / 0.85)   # ~85% spent
set_cap(cap_warn)
s1 = L().enforce_cap()
s2 = L().enforce_cap()
check("11 at 80% one DM names the spend and the cap; a second enforce sends nothing more; no pause",
      s1["warned"] and "80" not in "" and len(dms()) == 1 and "dm sam usage:" in dms()[0] and "monthly cap" in dms()[0]
      and not s2["warned"] and len(dms()) == 1 and not os.path.exists(os.path.join(LDIR, "paused")),
      "s1=%r s2=%r dms=%r" % (s1, s2, dms()))
cap_over = int(spent)   # 100%+ of a whole-dollar cap
set_cap(cap_over)
s3 = L().enforce_cap()
ptxt = L().paused_text()
check("12 at 100% the paused marker is written with the cap and the date, and one DM says jobs are paused",
      s3["paused"] and ("monthly cap USD %d reached 2026-09-10" % cap_over) in ptxt and len(dms()) == 2 and "paused" in dms()[1],
      "s3=%r ptxt=%r dms=%r" % (s3, ptxt, dms()))
s4 = L().enforce_cap()
check("13 a repeated enforce at 100% keeps the marker and sends no more DMs", s4["paused"] and len(dms()) == 2, repr(s4))
set_cap(100000)
s5 = L().enforce_cap()
check("14 raising the cap above the spend removes the pause at once; a cap of 0 also means no pause",
      not s5["paused"] and not os.path.exists(os.path.join(LDIR, "paused")) and L().totals()["cap"]["usd"] == 100000
      and (set_cap("") or True) and not L().enforce_cap()["paused"], repr(s5))
os.environ["PIPEOS_LEDGER_NOW"] = "2026-10-01T00:00:00Z"
set_cap(1)
s6 = L().enforce_cap()
check("15 a new month is a fresh slate: spend is 0 of the cap, no pause, no DM", not s6["paused"] and not s6["warned"] and len(dms()) == 2 and L().totals()["month"]["calls"] == 0, repr(s6))
os.environ["PIPEOS_LEDGER_NOW"] = NOW

# ── 16. the CLI ────────────────────────────────────────────────────────
env = dict(os.environ, PIPEOS_LEDGER_DIR=LDIR, PIPEOS_LEDGER_TRANSCRIPTS=TR, PIPEOS_LEDGER_RATES=RATES, PIPEOS_LEDGER_CONF=CONF,
           PIPEOS_LEDGER_RUNS=RUNS, PIPEOS_LEDGER_SESSIONS=SESS, PIPEOS_LEDGER_WEBCHAT_SID=WSID, PIPEOS_LEDGER_PIPE=PIPE)
p = subprocess.run([sys.executable, LEDGER, "totals", "--json"], capture_output=True, text=True, env=env)
p2 = subprocess.run([sys.executable, LEDGER, "totals"], capture_output=True, text=True, env=env)
check("16 the CLI prints totals as JSON (for selfcheck) and as text, and ingest reports a count",
      p.returncode == 0 and json.loads(p.stdout)["today"]["calls"] > 0 and p2.returncode == 0 and "job:nightly" in p2.stdout
      and "ingested" in subprocess.run([sys.executable, LEDGER, "ingest"], capture_output=True, text=True, env=env).stdout,
      p.stdout[:200] + p.stderr[:200])

# ── 17. the wiring ─────────────────────────────────────────────────────
rates = json.load(open(os.path.join(REPO, "overlay/usr/local/share/pipeos/rates.json")))
sweep = open(os.path.join(REPO, "overlay/etc/periodic/weekly/pipeos-worksweep")).read()
card = open(os.path.join(REPO, "overlay/etc/pipeos/card.conf")).read()
check("17 the shipped rate table names its date and prices every current family with all five columns; the worksweep spares /work/.pipeos; the card declares MONTHLY_CAP_USD",
      rates.get("updated") and all(set(v) == {"in", "out", "cache_read", "cache_w5m", "cache_w1h"} for v in rates["per_mtok"].values())
      and {"claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"} <= set(rates["per_mtok"])
      and "/work/.pipeos|/work/.pipeos/*" in sweep and "\nMONTHLY_CAP_USD=" in card, "")

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
