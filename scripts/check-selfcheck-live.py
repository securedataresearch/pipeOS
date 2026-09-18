#!/usr/bin/env python3
"""Probe for the live verdict (#290): `pipeos-selfcheck --live` is a hand-run
minus the heavy sections and plus exactly one write. Text-level, like
check-watchdog's wiring rows — the selfcheck itself needs a box. Exit 0 if
every row passes.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SC = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


def section(name):
    m = re.search(r"^# ---- %s\..*?(?=^# ---- )" % re.escape(name), SC, re.M | re.S)
    return m.group(0) if m else ""


check("1 --live is a mode of its own: LIVE=yes, BOOT stays no, HEALTH_LAST named",
      "    --live) LIVE=yes ;;" in SC and "BOOT=no LIVE=no" in SC and "HEALTH_LAST=/run/pipeos/health.last" in SC)
g5 = section("5g")
check("2 --live never compiles: the toolchain section is wrapped in the LIVE guard, first code line to last",
      g5.split("\n")[[i for i, l in enumerate(g5.split("\n")) if l.strip() and not l.startswith("#")][0]].startswith('if [ "$LIVE" != yes ]')
      and g5.rstrip().endswith("fi"), g5[-80:])
check("3 --live skips apk update, du over /work, and pipeos verify",
      'elif [ "$LIVE" != yes ]; then   # --live: no index fetch' in section("5e")
      and 'if [ "$LIVE" != yes ]; then   # --live names no culprit' in section("5")
      and 'if [ "$LIVE" = yes ]; then\n    :\nelif vout=$(pipeos verify 2>&1); then' in section("4"))
tail = SC[SC.index("# ---- 7. compose the report"):]
writes = re.findall(r"> *\"?\$?HEALTH_LAST", tail)
check("4 the report says health [live], lands atomically in HEALTH_LAST, and --live exits before the boot-only block",
      '_kind="health [live]"' in tail and 'mktemp "$HEALTH_LAST.XXXXXX"' in tail and 'printf \'%s\\n\' "$msg" > "$_hl"' in tail and 'mv -f "$_hl" "$HEALTH_LAST"' in tail
      and tail.index('if [ "$LIVE" = yes ]; then\n    mkdir -p /run/pipeos') < tail.index('[ "$BOOT" = yes ] || { rm -f "$R"; exit 0; }'))
hourly = os.path.join(REPO, "overlay/etc/periodic/hourly/pipeos-health")
lbu = open(os.path.join(REPO, "overlay/etc/apk/protected_paths.d/lbu.list")).read()
check("5 the hourly file execs --live and lbu.list carries it",
      os.path.exists(hourly) and "exec /usr/local/bin/pipeos-selfcheck --live" in open(hourly).read() and os.access(hourly, os.X_OK)
      and "+etc/periodic/hourly/pipeos-health" in lbu)
lanid = open(os.path.join(REPO, "overlay/usr/local/share/pipeos/web/lanid.py")).read()
webd = open(os.path.join(REPO, "overlay/usr/local/share/pipeos/web/webd.py")).read()
front = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos")).read()
check("6 every reader prefers the newer file: lanid.verdict_now, box_summary, self_entry, /api/status, pipeos status",
      "def verdict_now(" in lanid and webd.count("lanid.verdict_now(BOOT_REPORT, HEALTH_LAST)") >= 3
      and "/run/pipeos/health.last -nt /run/pipeos/boot-report" in front)

print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
