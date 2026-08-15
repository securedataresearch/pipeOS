#!/usr/bin/env python3
"""Controls for the pipeOS#90 signing-key probe.

A probe that only ever passes has two explanations. Each control below breaks
ONE property of the shipped section and must make a DIFFERENT row fail, for
its own reason — that is what says the rows are wired to the code rather than
to each other. 10-mk-chroot.sh is restored after each run.

Control A is not hypothetical: it is exactly the code as first reviewed on
pipeOS#92, and the row it fails is the one box3 found by hand.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
P = os.path.join(REPO, "scripts", "10-mk-chroot.sh")
CFG = os.path.join(REPO, "config.sh")
PROBE = os.path.join(HERE, "check-signing-key.py")
orig = open(P).read()
cfg_orig = open(CFG).read()

CENSUS_SCOPE = 'KEY_STORES="$ABUILD_DIR $SIGNING_KEY_DIR $OUT/keys"'
CONF_LINE = """        sudo sh -c "printf 'PACKAGER_PRIVKEY=\\"/home/builder/.abuild/%s\\"\\n' '$priv' > '$ABUILD_DIR/abuild.conf'\""""
MKDIR_BLOCK = """if [ ! -d "$SIGNING_KEY_DIR" ]; then
    mkdir -p "$SIGNING_KEY_DIR" 2>/dev/null \\
        || sudo install -d -o "$USER" -m 700 "$SIGNING_KEY_DIR" \\
        || { echo "cannot create the durable key store $SIGNING_KEY_DIR" >&2
             echo "set SIGNING_KEY_DIR to a path this build user can write" >&2
             exit 1; }
fi"""
TRUST_LINE = 'sudo cp "$OUT/keys/"*.rsa.pub "$CHROOT/etc/apk/keys/"'

# The pair check spans a comment block and an if/elif chain, so it is matched by
# its boundaries rather than pasted here: a copy would rot into a control that
# silently stops applying the next time the message wording changes. The
# harness already treats "did not apply" as a failure, but only if the anchors
# are small enough to survive an edit that is not about them.
_start = 'priv_file=$(ls "$ABUILD_DIR"/*.rsa 2>/dev/null | head -1 || true)'
_end = "    echo \"    (install openssl, or check by hand before flashing)\" >&2\nfi"
if _start not in orig or _end not in orig:
    sys.exit("FAIL cannot locate the pair-verification block in 10-mk-chroot.sh")
PAIR_BLOCK = orig[orig.index(_start):orig.index(_end) + len(_end)]

controls = [
    # The reviewed-but-wrong version: census only reaches the stores we might
    # RESTORE from, never the chroot we back up FROM.
    ("A: census skips the chroot (the state pipeOS#92 shipped for review)",
     lambda s: s.replace(CENSUS_SCOPE, 'KEY_STORES="$SIGNING_KEY_DIR $OUT/keys"')),

    # A key on disk that abuild does not know about signs with nothing.
    ("B: abuild.conf is never written",
     lambda s: s.replace(CONF_LINE, "        :")),

    # Back to the bare mkdir that dies under set -e on a non-appliance host.
    ("C: durable home created with a bare mkdir -p",
     lambda s: s.replace(MKDIR_BLOCK, 'mkdir -p "$SIGNING_KEY_DIR"')),

    # The original pipeOS#90 bug: always mint, never restore.
    ("D: always keygen, never restore",
     lambda s: s.replace('    if [ -n "$key_src" ]; then', "    if false; then")),

    # The chroot's own apk must trust the key to install test builds.
    ("E: public key never reaches the chroot trust store",
     lambda s: s.replace(TRUST_LINE, ":")),

    # The state #92 shipped for review: names are compared, material never is,
    # so halves from two different keygens ride through to the apkovl.
    ("F: no pair verification (the state pipeOS#92 shipped for review)",
     lambda s: s.replace(PAIR_BLOCK, 'priv_file=$(ls "$ABUILD_DIR"/*.rsa '
                         "2>/dev/null | head -1 || true)")),
]

# One control lives in the other file: the tier logic that decides WHERE the
# durable store is. Restoring the unconditional /work default is box2's
# blocker on #92 verbatim.
CFG_BLOCK_START = 'if [ -z "${SIGNING_KEY_DIR:-}" ]; then'
cfg_controls = [
    ("G: unconditional /work default (box2's blocker on pipeOS#92)",
     lambda s: re.sub(r'^if \[ -z "\$\{SIGNING_KEY_DIR:-\}" \]; then\n.*?^fi$',
                      'SIGNING_KEY_DIR="${SIGNING_KEY_DIR:-/work/keys/pipeos}"',
                      s, flags=re.S | re.M)),
]

rc = 0
seen = {}
for name, f, path, src in ([(n, f, P, orig) for n, f in controls]
                           + [(n, f, CFG, cfg_orig) for n, f in cfg_controls]):
    mut = f(src)
    if mut == src:
        print(f"--- {name}: CONTROL DID NOT APPLY (probe is testing nothing here)")
        rc = 1
        continue
    open(path, "w").write(mut)
    try:
        r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    finally:
        open(path, "w").write(src)
    fails = [l.split()[1] for l in r.stdout.splitlines() if l.startswith("FAIL")]
    print(f"--- {name}: rows {fails or '(none)'} fail")
    for l in r.stdout.splitlines():
        if l.startswith("FAIL"):
            print("   ", l.rstrip())
    if not fails:
        print("    CONTROL PASSED THE PROBE — those rows prove nothing")
        rc = 1
    seen[name] = set(fails)

# Two controls that fail exactly the same rows are one control wearing two
# hats: nothing distinguishes the properties they claim to test.
items = list(seen.items())
for i in range(len(items)):
    for j in range(i + 1, len(items)):
        if items[i][1] and items[i][1] == items[j][1]:
            print(f"--- {items[i][0]!r} and {items[j][0]!r} fail the same rows "
                  f"{sorted(items[i][1])} — not independent")
            rc = 1

sys.exit(rc)
