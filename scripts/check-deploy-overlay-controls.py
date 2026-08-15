#!/usr/bin/env python3
"""Controls for the pipeOS#97 deploy-overlay probe.

A probe that only ever passes has two explanations. Each control below breaks
ONE property of the shipped script and must make a DIFFERENT row fail, for its
own reason. pipeos-deploy-overlay is restored after every run.

The two that matter most are A and G: A removes the second gate that keeps
per-box generated files off a box, and G removes the fence that keeps a
relocated test run from persisting the REAL root.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
P = os.path.join(REPO, "overlay/usr/local/bin/pipeos-deploy-overlay")
PROBE = os.path.join(HERE, "check-deploy-overlay.py")
orig = open(P).read()

EXCLUDED = """excluded() {
    for n in $NEVER; do
        case "$1" in "$n"|"$n"/*) return 0 ;; esac
    done
    return 1
}"""

FENCE = """if [ -n "$ROOT" ] && [ "$NO_PERSIST" != 1 ]; then"""

MODEGUARD = """    case "$mode" in
        100644|100755) : ;;"""

# The deploy list and the NEVER filter are two gates on the same danger, and
# testing them needs both directions. While the LIST holds, removing the
# filter changes nothing observable — the first version of these controls
# claimed to test the filter and was really testing the list, and said so by
# passing the probe untouched. So: A0 widens the list alone and the probe must
# still be GREEN (that is the second gate doing its job, and it is the whole
# reason the second gate exists); A widens the list AND removes the filter,
# and row 2 must then fail.
WIDEN = ("DEPLOY_PATHS=\"usr/local/bin", "DEPLOY_PATHS=\"etc/pipeos\nroot/.pipe\nusr/local/bin")

controls = [
    ("A0: the deploy list is widened to include the per-box paths "
     "(the NEVER filter alone must still hold)",
     lambda s: s.replace(*WIDEN), "pass"),

    ("A: the list is widened AND the NEVER second gate is removed",
     lambda s: s.replace(*WIDEN).replace(EXCLUDED, "excluded() { return 1; }")),

    ("B: the card gate warns instead of refusing",
     lambda s: s.replace('        die "on-box card differs', '        say "on-box card differs')),

    ("C: installed files all get mode 644",
     lambda s: s.replace('    m=$([ "$mode" = 100755 ] && echo 755 || echo 644)',
                         "    m=644")),

    ("D: no stamp is written",
     lambda s: s.replace('} > "$STAMP.new" && mv -f "$STAMP.new" "$STAMP" || die "cannot write $STAMP"',
                         '} > /dev/null')),

    ("E: --dry-run is ignored",
     lambda s: s.replace('    say "dry run — nothing written."\n    exit 0',
                         '    say "dry run — nothing written."')),

    ("F: an empty overlay listing is accepted",
     lambda s: s.replace('[ -s "$listing" ] || die "no overlay files found at $REF — refusing to deploy nothing"',
                         ":")),

    ("G: the relocated-run fence is removed (a test could persist the real box)",
     lambda s: s.replace(FENCE, "if false; then")),

    ("H: any git mode is installed as a regular file",
     lambda s: s.replace(MODEGUARD, "    case \"$mode\" in\n        100644|100755|120000) : ;;")),

    ("I: stale files are deleted instead of reported",
     lambda s: s.replace(
         "printf 'stale %s (deployed here, absent at %s — NOT removed)\\n' \"$rel\" \"$REF\"",
         "rm -f \"$ROOT/$rel\"")),

    # J is the review defect itself, put back. Before box1's CHANGES the scan
    # walked $ROOT/$p whole, so on a real box every apk-owned file under
    # etc/init.d and usr/local/bin printed as stale. Row 6b is the row that
    # says so, and this is what proves 6b can fail — without it, 6b passes for
    # any implementation, including the broken one.
    ("J: the stale scan walks the live tree again instead of the stamp",
     lambda s: s.replace(
         'if [ -f "$STAMP" ]; then\n'
         '    # Stamp body lines are `<sha256>  /<rel>`; the header lines are not.\n'
         '    sed -n \'s|^[0-9a-f]\\{64\\}  /||p\' "$STAMP" | while read -r rel; do',
         'if true; then\n'
         '    for p in $DEPLOY_PATHS; do [ -d "$ROOT/$p" ] && '
         'find "$ROOT/$p" -type f; done | sed "s|^$ROOT/||" | while read -r rel; do')),
]

rc = 0
seen = {}
for entry in controls:
    name, f = entry[0], entry[1]
    expect = entry[2] if len(entry) > 2 else "fail"
    mut = f(orig)
    if mut == orig:
        print("--- %s: CONTROL DID NOT APPLY (probe is testing nothing here)" % name)
        rc = 1
        continue
    open(P, "w").write(mut)
    try:
        r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    finally:
        open(P, "w").write(orig)
    fails = [l.split()[1] for l in r.stdout.splitlines() if l.startswith("FAIL")]
    print("--- %s: rows %s fail" % (name, fails or "(none)"))
    for l in r.stdout.splitlines():
        if l.startswith("FAIL"):
            print("   ", l.rstrip())
    if expect == "pass":
        if fails:
            print("    EXPECTED GREEN — the surviving gate did not hold")
            rc = 1
        continue
    if not fails:
        print("    CONTROL PASSED THE PROBE — those rows prove nothing")
        rc = 1
    seen[name] = set(fails)

items = list(seen.items())
for i in range(len(items)):
    for j in range(i + 1, len(items)):
        if items[i][1] and items[i][1] == items[j][1]:
            print("--- %r and %r fail the same rows %s — not independent"
                  % (items[i][0], items[j][0], sorted(items[i][1])))
            rc = 1

sys.exit(rc)
