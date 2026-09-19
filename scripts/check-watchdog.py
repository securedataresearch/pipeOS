#!/usr/bin/env python3
"""Probe for the hardware watchdog (#247): /etc/init.d/watchdog (ours, over
busybox-openrc's) reads the card and arms, or leaves the device closed, and
says which in its state file; the wiring around it (runlevel, lbu.list,
the verb in the help, the card field in the generator, the boot report's
cause line). No root, no device: busybox `watchdog` is a stub that logs its
argv, the device is a fixture path, openrc's verbs are shell no-ops.

Exit 0 if every row passes.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
INIT = os.path.join(REPO, "overlay/etc/init.d/watchdog")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="ckwd-")
WD = os.path.join(D, "watchdog")
with open(WD, "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + D + "/wd.argv\n")
os.chmod(WD, 0o755)
CARD = os.path.join(D, "card.conf")
STATE = os.path.join(D, "run", "watchdog.state")
DEV = os.path.join(D, "watchdog0")
open(DEV, "w").close()

body = "\n".join(l for l in open(INIT).read().splitlines() if not l.startswith("#!"))
SRC = os.path.join(D, "wd.sh")
open(SRC, "w").write(body)


def run(card_value, dev=DEV, conf=""):
    open(CARD, "w").write("NICK=\nNAME=\n" + ("WATCHDOG=%s\n" % card_value if card_value is not None else ""))
    for p in (D + "/wd.argv", STATE):
        if os.path.exists(p):
            os.unlink(p)
    # openrc runs conf.d first, then the script, then start_pre and (rc 0) the command
    script = ("einfo() { :; }; ewarn() { :; }; ebegin() { :; }; eend() { return $1; }\n"
              + conf +
              "PIPEOS_WD_BIN=%s PIPEOS_WD_CARD=%s PIPEOS_WD_DEV=%s PIPEOS_WD_STATE=%s\n"
              "export PIPEOS_WD_BIN PIPEOS_WD_CARD PIPEOS_WD_DEV PIPEOS_WD_STATE\n"
              ". %s\nif start_pre; then $command $command_args; fi\n" % (WD, CARD, dev, STATE, SRC))
    p = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    argv = [a for a in open(D + "/wd.argv").read().split("\n") if a] if os.path.exists(D + "/wd.argv") else []
    state = open(STATE).read().strip() if os.path.exists(STATE) else ""
    return p.returncode, argv, state


rc, argv, state = run(None)
rc2, argv2, state2 = run("kernel")
check("1 an empty card and WATCHDOG=kernel both arm: busybox watchdog -T 60 -t 20 -F <dev>, state says armed",
      rc == 0 and argv == ["-T 60 -t 20 -F " + DEV] and state.startswith("armed 60 20 ") and argv2 == argv and state2 == state,
      "rc=%s argv=%r state=%r" % (rc, argv, state))
rc, argv, state = run("off")
rc3, argv3, state3 = run("OFF")
check("2 WATCHDOG=off starts nothing and the state file says off (case-insensitive)",
      argv == [] and state == "off" and argv3 == [] and state3 == "off", "argv=%r state=%r %r" % (argv, state, state3))
rc, argv, state = run("kernel", conf="WATCHDOG_TIMEOUT=45\nWATCHDOG_INTERVAL=15\n")
check("3 conf.d's WATCHDOG_TIMEOUT / WATCHDOG_INTERVAL are the knobs the command uses",
      argv == ["-T 45 -t 15 -F " + DEV] and state.startswith("armed 45 15 "), "argv=%r state=%r" % (argv, state))

conf = open(os.path.join(REPO, "overlay/etc/conf.d/watchdog")).read()
build = open(os.path.join(REPO, "scripts/40-build-apkovl.sh")).read()
boot_line = next((l for l in build.splitlines() if l.startswith("mk_runlevel boot")), "")
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read()
front = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
gen = open(os.path.join(REPO, "overlay/usr/local/bin/pipebox-card")).read()
card = open(os.path.join(REPO, "overlay/etc/pipeos/card.conf")).read()
tmpl = open(os.path.join(REPO, "overlay/usr/local/share/pipeos/card/pipebox.conf.tmpl")).read()
sc = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
check("4 conf.d ships 60 s / 20 s on /dev/watchdog0; watchdog is in the boot runlevel; the init script is in lbu.list",
      "WATCHDOG_TIMEOUT=60" in conf and "WATCHDOG_INTERVAL=20" in conf and 'WATCHDOG_DEV="/dev/watchdog0"' in conf
      and boot_line.split()[-1] == "watchdog" and "+etc/init.d/watchdog" in lbu, "boot=%r" % boot_line)
check("5 the card field exists end to end: declared, allowed, kernel|off enum, rendered, shown, defaulted in card.conf and the template; the verb is in the help and dispatched",
      "WATCHDOG=" in gen.split("\n", 70)[-1][:0] + gen and "WATCHDOG) ;;" in gen and "WATCHDOG must be kernel or off" in gen
      and "s|@@WATCHDOG@@|$WATCHDOG|g" in gen and "WOL WATCHDOG MONTHLY_CAP_USD CLUSTER_CAP_USD" in gen
      and "\nWATCHDOG=\n" in card and 'WATCHDOG="@@WATCHDOG@@"' in tmpl
      and "pipeos watchdog kernel|off|status" in front and "watchdog)    shift; cmd_watchdog" in front)
check("6 the boot report can name the watchdog: a pstore record reads as a panic the dog rebooted, an unclean stop with the armed flag names the dog or the plug, and the flag is boot-written",
      "kernel panic — the watchdog rebooted us" in sc and "hard reset: watchdog (armed, 60 s) or power loss" in sc
      and "WD_FLAG=/work/.pipeos/watchdog-armed" in sc and ': > "$WD_FLAG"' in sc and "# ---- 1cx." in sc)

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
