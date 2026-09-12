#!/usr/bin/env python3
"""The operator verbs (#259): `pipeos schedule|usage|card set|secrets
phrase|assistant password` are the dashboard's forms as commands — the same
file, the same refusals, the same save — so an operator (or the owner's
agent on a workstation) drives a Machine over ssh with no dashboard login
and no hand edit of /etc. Every row runs the shipped scripts against a
throwaway tree through their seams; a stub pipeos-save counts the saves.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
PIPEOS = os.path.join(REPO, "overlay/usr/local/bin/pipeos")
SCHEDCTL = os.path.join(WEB, "schedctl.py")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="opverbs-")
BIN = os.path.join(D, "bin")
os.makedirs(BIN)
SAVES = os.path.join(D, "saves")
with open(os.path.join(BIN, "pipeos-save"), "w") as f:
    f.write("#!/bin/sh\necho save >> %s\n[ -z \"${STUB_SAVE_FAIL:-}\" ] || { echo 'save FAILED (stub)'; exit 1; }\n" % SAVES)
with open(os.path.join(BIN, "claude"), "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
with open(os.path.join(BIN, "pipebox-card"), "w") as f:
    # the generator stub: records the call, refuses a marked value like the real one refuses a bad cap
    f.write("#!/bin/sh\necho \"$*\" >> %s/gen.log\ngrep -q '^MONTHLY_CAP_USD=bad$' \"$3\" && exit 1\nexit 0\n" % D)
with open(os.path.join(BIN, "pipeos-schedule-run"), "w") as f:
    f.write("#!/bin/sh\necho \"$1\" >> %s/ran\n" % D)
for b in os.listdir(BIN):
    os.chmod(os.path.join(BIN, b), 0o755)
CONF = os.path.join(D, "schedule.json")
STATE = os.path.join(D, "state")
LOGS = os.path.join(D, "logs")
WORK = os.path.join(D, "work")
os.makedirs(os.path.join(WORK, "repos", "proj"))
os.makedirs(LOGS)
CARD = os.path.join(D, "card.conf")
PHRASE = os.path.join(D, "vault-phrase")
open(CARD, "w").write('NICK="box"\nOWNER_NICK="sam"\nAGENT_TIMEOUT_MIN="7"\n')

ENV = dict(os.environ, PATH=BIN + ":/usr/bin:/bin",   # not the workstation PATH: a hermes there would pass the "uninstalled" refusal
            PIPEOS_SCHED_CONF=CONF, PIPEOS_SCHED_STATE_DIR=STATE,
           PIPEOS_SCHED_LOGDIR=LOGS, PIPEOS_SCHED_RUN_BIN=os.path.join(BIN, "pipeos-schedule-run"),
           PIPEOS_SAVE_BIN=os.path.join(BIN, "pipeos-save"), PIPEOS_SCHED_WORK=WORK,
           PIPEOS_CARD=CARD, PIPEOS_CARD_GEN=os.path.join(BIN, "pipebox-card"), PIPEOS_VAULT_PHRASE=PHRASE,
           PIPEOS_LEDGER=os.path.join(D, "ledger-stub.py"))
with open(os.path.join(D, "ledger-stub.py"), "w") as f:
    f.write("import sys, os\nopen(os.path.join(%r, 'ledger.log'), 'a').write(' '.join(sys.argv[1:]) + '\\n')\nprint('{\"stub\": true}')\n" % D)


def sched(*args, env=None):
    p = subprocess.run([sys.executable, SCHEDCTL] + list(args), capture_output=True, text=True, env=env or ENV)
    return p.returncode, p.stdout + p.stderr


def pipeos(*args, stdin=None, env=None):
    p = subprocess.run(["sh", PIPEOS] + list(args), capture_output=True, text=True, env=env or ENV, input=stdin)
    return p.returncode, p.stdout + p.stderr


def saves():
    try:
        return len(open(SAVES).read().split())
    except OSError:
        return 0


def jobs():
    try:
        return json.load(open(CONF))["jobs"]
    except (OSError, ValueError):
        return []


# ── schedule ─────────────────────────────────────────────────────────────
rc, out = sched("add", "nightly", "--cron", "0 2 * * *", "--prompt", "  tidy the repo  ")
j = jobs()
check("1 schedule add writes the dashboard's shape — every key webd writes, prompt stripped, cwd blank, claude, notify on, enabled, fresh — and saves exactly once",
      rc == 0 and len(j) == 1 and j[0] == {"name": "nightly", "cwd": "", "backend": "claude", "notify": True, "enabled": True,
                                           "session": "fresh", "prompt": "tidy the repo", "cron": "0 2 * * *"}
      and saves() == 1 and "saved" in out and oct(os.stat(CONF).st_mode & 0o777) == "0o600", "rc=%s out=%s j=%r saves=%d" % (rc, out, j, saves()))
refusals = {
    "hostile name": sched("add", "../etc", "--cron", "0 2 * * *", "--prompt", "x"),
    "bad cron": sched("add", "b", "--cron", "99 * * * *", "--prompt", "x"),
    "shell in cron": sched("add", "b", "--cron", "0 2 * * *; rm -rf /", "--prompt", "x"),
    "no prompt": sched("add", "b", "--cron", "0 2 * * *"),
    "cwd outside": sched("add", "b", "--cron", "0 2 * * *", "--prompt", "x", "--cwd", "/etc"),
    "unknown backend": sched("add", "b", "--cron", "0 2 * * *", "--prompt", "x", "--backend", "gpt"),
    "uninstalled backend": sched("add", "b", "--cron", "0 2 * * *", "--prompt", "x", "--backend", "hermes"),
    "bad session": sched("add", "b", "--cron", "0 2 * * *", "--prompt", "x", "--session", "forever"),
    "unknown flag": sched("add", "b", "--cron", "0 2 * * *", "--prompt", "x", "--user", "root"),
    "duplicate": sched("add", "nightly", "--cron", "0 2 * * *", "--prompt", "x"),
    "set unknown": sched("set", "ghost", "--prompt", "x"),
}
bad = {k: v for k, v in refusals.items() if v[0] != 2}
check("2 every refusal the dashboard makes, this makes (rc 2): hostile name, bad/shell cron, no prompt, cwd outside /work, unknown/uninstalled assistant, bad session, unknown flag, duplicate add, set on a ghost — and none of them saved",
      not bad and len(jobs()) == 1 and saves() == 1, "bad=%r saves=%d" % ({k: v[1][-80:] for k, v in bad.items()}, saves()))
rc, out = sched("set", "nightly", "--cwd", os.path.join(WORK, "repos", "proj"), "--notify", "off", "--session", "continue")
j = jobs()[0]
check("3 schedule set changes only the given flags (cwd realpath'd under /work, notify off, continue) and keeps the rest",
      rc == 0 and j["cwd"] == os.path.realpath(os.path.join(WORK, "repos", "proj")) and j["notify"] is False and j["session"] == "continue"
      and j["cron"] == "0 2 * * *" and j["prompt"] == "tidy the repo" and saves() == 2, "rc=%s j=%r" % (rc, j))
rc_d, _ = sched("disable", "nightly"); en_d = jobs()[0]["enabled"]
rc_e, _ = sched("enable", "nightly"); en_e = jobs()[0]["enabled"]
rc_l, out_l = sched("ls")
check("4 disable/enable flip the flag and save; ls shows the job, its schedule in words, and 'never run'",
      rc_d == 0 and en_d is False and rc_e == 0 and en_e is True and saves() == 4 and rc_l == 0 and "nightly" in out_l and "never run" in out_l and "02:00" in out_l,
      "d=%s e=%s saves=%d ls=%s" % (en_d, en_e, saves(), out_l))
rc_r, out_r = sched("run", "nightly")
import time
time.sleep(0.3)
ran = open(os.path.join(D, "ran")).read().split() if os.path.exists(os.path.join(D, "ran")) else []
rc_rg, _ = sched("run", "ghost")
PAUSED = os.path.join(D, "paused")
open(PAUSED, "w").write("monthly cap USD 1 reached")
rc_rp, out_rp = sched("run", "nightly", env=dict(ENV, PIPEOS_SCHED_PAUSED=PAUSED))
os.unlink(PAUSED)
time.sleep(0.3)
ran_p = open(os.path.join(D, "ran")).read().split()
check("5 run starts the runner detached for a known job and refuses a ghost; under the cap's pause marker it refuses with the reason instead of saying 'started' (the runner would exit 75 anyway); no save",
      rc_r == 0 and ran == ["nightly"] and rc_rg == 2 and rc_rp == 2 and "paused" in out_rp and "USD 1" in out_rp and ran_p == ["nightly"] and saves() == 4,
      "rc=%s ran=%r ghost=%s paused=%s %s" % (rc_r, ran, rc_rg, rc_rp, out_rp))
open(os.path.join(LOGS, "schedule-nightly.log"), "w").write("\n".join("line %d" % i for i in range(60)) + "\n")
rc_lg, out_lg = sched("log", "nightly", "5")
rc_lx, out_lx = sched("log", "../../etc/passwd")
check("6 log prints the last N lines of the job's log and refuses a hostile name", rc_lg == 0 and out_lg.split("\n")[:5] == ["line 55", "line 56", "line 57", "line 58", "line 59"] and rc_lx == 2, repr((out_lg, out_lx)))
sched("add", "second", "--cron", "@daily", "--prompt", "x")
os.makedirs(os.path.join(STATE, "sessions"), exist_ok=True)
open(os.path.join(STATE, "sessions", "second"), "w").write("sid")
rc_rm, _ = sched("rm", "second")
rc_rm2, _ = sched("rm", "second")
check("7 rm drops the job and its session file, saves, and a second rm is a refusal",
      rc_rm == 0 and [x["name"] for x in jobs()] == ["nightly"] and not os.path.exists(os.path.join(STATE, "sessions", "second")) and rc_rm2 == 2 and saves() == 6,
      "rc=%s %s jobs=%r saves=%d" % (rc_rm, rc_rm2, [x["name"] for x in jobs()], saves()))
rc_f, out_f = sched("add", "third", "--cron", "@hourly", "--prompt", "x", env=dict(ENV, STUB_SAVE_FAIL="1"))
check("8 when pipeos-save fails the verb says so and exits non-zero (the change is live but would die at reboot)", rc_f != 0 and "saved: NO" in out_f, "rc=%s out=%s" % (rc_f, out_f))
sched("rm", "third")

# ── card set / usage cap ─────────────────────────────────────────────────
n0 = saves()
rc, out = pipeos("card", "set", "AGENT_TIMEOUT_MIN=9", "MONTHLY_CAP_USD=40")
card = open(CARD).read()
gen = open(os.path.join(D, "gen.log")).read()
check("9 card set rewrites an existing key in place, appends a key the card predates, regenerates once with --card, saves once",
      rc == 0 and 'AGENT_TIMEOUT_MIN=9\n' in card and card.count("MONTHLY_CAP_USD=40") == 1 and card.count("MONTHLY_CAP_USD") == 1
      and gen.count("generate --card " + CARD) == 1 and saves() == n0 + 1 and 'NICK="box"' in card, "rc=%s out=%s card=%r gen=%r" % (rc, out, card, gen))
bad = {
    "lowercase key": pipeos("card", "set", "nick=x"),
    "no equals": pipeos("card", "set", "NICK"),
    "pipe in value": pipeos("card", "set", "NICK=a|b"),
    "newline in value": pipeos("card", "set", "NICK=a\nb"),
    "no args": pipeos("card", "set"),
    "not set": pipeos("card", "show"),
}
bad = {k: v for k, v in bad.items() if v[0] != 2}
card2 = open(CARD).read()
check("10 card set refuses a lowercase key, a bare KEY, a | or newline in the value, no args, a verb other than set — and touches nothing",
      not bad and card2 == card and saves() == n0 + 1, "bad=%r" % {k: v[1][-60:] for k, v in bad.items()})
rc_bad, out_bad = pipeos("card", "set", "MONTHLY_CAP_USD=bad")
check("11 when the generator refuses the new value the verb fails loudly and does not save", rc_bad == 1 and "regeneration FAILED" in out_bad and saves() == n0 + 1, "rc=%s out=%s" % (rc_bad, out_bad))
pipeos("card", "set", "MONTHLY_CAP_USD=40")
n1 = saves()
rc_c, out_c = pipeos("usage", "cap", "25")
led = open(os.path.join(D, "ledger.log")).read()
rc_n, _ = pipeos("usage", "cap", "none")
card3 = open(CARD).read()
bad = [a for a in (("cap", "-1"), ("cap", "100001"), ("cap", "ten"), ("cap",), ("bogus",)) if pipeos("usage", *a)[0] != 2]
rc_t, out_t = pipeos("usage")
check("12 usage cap N writes MONTHLY_CAP_USD, regenerates, saves, then enforces the cap at once; cap none clears it; totals is the default verb; -1, 100001, 'ten', bare cap and a bogus verb are refused",
      rc_c == 0 and "MONTHLY_CAP_USD=25" in out_c and "enforce" in led and rc_n == 0 and "MONTHLY_CAP_USD=\n" in card3 and not bad
      and rc_t == 0 and "stub" in out_t and led.strip().split("\n")[-1] == "enforce" and saves() == n1 + 2,
      "c=%s %s n=%s card=%r led=%r bad=%r t=%s" % (rc_c, out_c, rc_n, card3, led, bad, out_t))

# ── secrets phrase ───────────────────────────────────────────────────────
rc_np, out_np = pipeos("secrets", "phrase")
open(PHRASE, "w").write("9c48-ce4f-c874-3ee3-c366-aefd-8f09-eeb3\n")
rc_p, out_p = pipeos("secrets", "phrase")
still = os.path.exists(PHRASE)
rc_a, out_a = pipeos("secrets", "phrase", "--ack")
gone = not os.path.exists(PHRASE)
rc_x, _ = pipeos("secrets", "bogus")
check("13 secrets phrase prints the pending recovery phrase and leaves it; --ack prints it once more and forgets the tmpfs copy; nothing pending is rc 1; a bogus verb rc 2; no save (tmpfs)",
      rc_np == 1 and rc_p == 0 and "9c48-ce4f" in out_p and still and rc_a == 0 and "9c48-ce4f" in out_a and "acked" in out_a and gone and rc_x == 2 and saves() == n1 + 2,
      "np=%s p=%s still=%s a=%s gone=%s x=%s" % (rc_np, (rc_p, out_p), still, (rc_a, out_a), gone, rc_x))

# ── assistant password (the shape only: the vault and rc-service are the box's) ──
src = open(PIPEOS).read()
rc_tty, out_tty = pipeos("assistant", "bogus")
check("14 assistant password reads stdin into the vault as assistant_pass for the assistant, EXPORTS it to /run (the service reads the export, which only the boot start wrote — two/three 2026-09-11), restarts pipeos-assistant with the lock fd closed, and saves; the init script no longer demands the pre-vault assistant.conf; a bogus verb is rc 2",
      "pipeos-vault set assistant_pass assistant" in src and "pipeos-vault export" in src.split("cmd_assistant()")[1].split("# ----")[0]
      and "rc-service pipeos-assistant restart" in src
      and 'eerror "the assistant terminal is enabled but $CONF does not exist' not in open(os.path.join(REPO, "overlay/etc/init.d/pipeos-assistant")).read() and "9>&-" in src.split("cmd_assistant()")[1].split("# ----")[0]
      and rc_tty == 2, "rc=%s out=%s" % (rc_tty, out_tty))

# ── nas account (pipeOS#270): the Storage page's share-only account as a verb ──
# Same module function as the dashboard (webd.nas_account_create). The unix
# half shells out to pipeos-user, absent here, so the create itself stops at
# that refusal — which is the point: nothing is written before it.
NASCTL = os.path.join(WEB, "nasctl.py")
def nas(*args, stdin=None):
    p = subprocess.run([sys.executable, NASCTL] + list(args), capture_output=True, text=True, env=ENV, input=stdin)
    return p.returncode, p.stdout + p.stderr
s0 = saves()
rc_a, out_a = nas("bogus")
rc_b, out_b = nas("account", "office", stdin="short")
rc_c, out_c = nas("account", "Bad Name", stdin="hunter22hunter")
rc_d, out_d = nas("account", "office", stdin="hunter22hunter")
check("16 nas account: a bogus verb, a short SMB password and a hostile name are refusals (rc 2) that write nothing; the create is the dashboard's nas_account_create (pipeos-user missing here stops it at the unix step, still nothing saved); the verb is wired in pipeos",
      rc_a == 2 and rc_b == 2 and "8 characters" in out_b and rc_c == 2 and "user name" in out_c
      and rc_d == 2 and "unix user" in out_d and saves() == s0
      and 'nas)         shift; exec python3 /usr/local/share/pipeos/web/nasctl.py "$@" ;;' in src,
      repr((rc_a, out_b, out_c, out_d, saves() - s0)))

# ── the wiring: every verb in the help, the skill names every verb ────────
helptext = "\n".join(l for l in src.split("\n")[:40])
skill = open(os.path.join(REPO, ".claude/skills/pipeos-fleet/SKILL.md")).read()
doc = open(os.path.join(REPO, "docs/fleet-ops.md")).read()
verbs = ("pipeos schedule", "pipeos usage", "pipeos card set", "pipeos secrets phrase", "pipeos assistant password", "pipeos nas account", "pipeos deploy-overlay", "pipeos vault", "pipeos wake", "pipeos work")
check("15 every operator verb is in pipeos's help, in the fleet skill and in docs/fleet-ops.md",
      all(v in helptext for v in verbs[:6]) and all(v in skill for v in verbs) and all(v in doc for v in verbs),
      "help=%r skill=%r doc=%r" % ([v for v in verbs[:6] if v not in helptext], [v for v in verbs if v not in skill], [v for v in verbs if v not in doc]))

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
