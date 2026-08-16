#!/usr/bin/env python3
"""Probe for section 4 of 10-mk-chroot.sh — the abuild signing key. (pipeOS#90)

Runs the SHIPPED section, extracted verbatim from the real script, against a
throwaway tree. Two things are shimmed and nothing else:

  sudo    -> runs the command unprivileged
  chroot  -> simulates the only two effects the key logic depends on:
             /home/builder/.abuild exists, and abuild-keygen mints a key into
             it. adduser/addgroup/sudoers are not what this probe tests.

Every fake key carries its own content, so each row asserts WHICH key
survived, not merely how many did. A count is the one thing that stays right
when the wrong key wins: the failure this section exists to prevent is signing
with a stray key, and a stray key counts as one just like the real one.

Exit 0 if every row passes. Controls live in check-signing-key-controls.py.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "scripts", "10-mk-chroot.sh")

FLEET = "fleet@pipeos-1000000000.rsa"  # the key already trusted by flashed sticks
STRAY = "stray@pipeos-2000000000.rsa"  # what a buggy run minted on top of it

SUDO_SHIM = """#!/bin/sh
exec "$@"
"""

# The chroot shim mints into the SAME path the real abuild-keygen would, so
# the section's own copy logic is what moves the key around, not the shim.
CHROOT_SHIM = """#!/bin/sh
root=$1; shift
mkdir -p "$root/home/builder/.abuild"
case "$*" in
  *abuild-keygen*)
    # Real material, same as the real abuild-keygen -a: the section now
    # verifies the pair derives, so a marker file here would fail that check
    # for a reason the cold-start row is not about.
    k="$root/home/builder/.abuild/$PROBE_NEW_KEY"
    cp "$PROBE_NEW_PRIV" "$k"
    cp "$PROBE_NEW_PRIV.pub" "$k.pub"
    ;;
esac
exit 0
"""


def extract_section():
    """Section 4, from its banner to the line before the closing echo."""
    src = open(SCRIPT).read()
    m = re.search(r"^# 4\. builder user.*?(?=^echo \"chroot ready)",
                  src, re.S | re.M)
    if not m or "SIGNING_KEY_DIR" not in m.group(0):
        sys.exit(f"FAIL could not extract section 4 from {SCRIPT} (markers moved?)")
    return m.group(0)


SECTION = extract_section()


# Real RSA material, not marker text. Marker files were enough while every row
# was about control flow, but the section now DERIVES the public half and
# compares it (box2 on #92) — and against markers that check can only ever fail,
# which would have made rows 1-3 and 8 fail for a reason none of them is about.
# One genuine keypair per name, minted once and reused, so the cost is two
# genrsa calls for the whole suite.
_PAIRS = {}


def pair(name):
    if name not in _PAIRS:
        priv = subprocess.run(["openssl", "genrsa", "2048"], check=True,
                              capture_output=True).stdout
        pub = subprocess.run(["openssl", "rsa", "-pubout"], input=priv,
                             check=True, capture_output=True).stdout
        _PAIRS[name] = (priv, pub)
    return _PAIRS[name]


def putkey(d, name, pub_from=None, material=None):
    """Write key `name`.

    pub_from mints a MISMATCHED .pub from another key. material writes ANOTHER
    key's pair under this name — the same-name/different-material store that a
    name census cannot see (box0 on #92); the pair still derives, so only the
    census can catch it.
    """
    os.makedirs(d, exist_ok=True)
    priv, pub = pair(material or name)
    open(os.path.join(d, name), "wb").write(priv)
    open(os.path.join(d, name + ".pub"), "wb").write(
        pair(pub_from)[1] if pub_from else pub)


class Sandbox:
    def __init__(self, root):
        self.root = root
        self.out = os.path.join(root, "out")
        self.chroot = os.path.join(self.out, "chroot")
        self.abuild = os.path.join(self.chroot, "home/builder/.abuild")
        self.durable = os.path.join(root, "keys/pipeos")
        # The candidate config.sh did not pick. Always exported, because
        # config.sh always sets it — a row that only staged it when it mattered
        # would leave every other row testing a variable the real build has.
        self.alt = os.path.join(root, "alt/.pipeos/keys")
        self.keys = os.path.join(self.out, "keys")
        os.makedirs(os.path.join(self.chroot, "etc/apk/keys"), exist_ok=True)
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir, exist_ok=True)
        for name, body in (("sudo", SUDO_SHIM), ("chroot", CHROOT_SHIM)):
            p = os.path.join(bindir, name)
            open(p, "w").write(body)
            os.chmod(p, 0o755)
        self.bindir = bindir

    def slim_path(self):
        """A PATH with every tool the section uses EXCEPT openssl.

        Symlink-per-name rather than a stub called `openssl`, because the
        section asks `command -v openssl` — a stub that exits nonzero would
        still be found, and the row is about the branch taken when the checker
        is genuinely absent. (box3 on #92)
        """
        slim = os.path.join(self.root, "slimbin")
        os.makedirs(slim, exist_ok=True)
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if name == "openssl" or os.path.exists(os.path.join(slim, name)):
                    continue
                try:
                    os.symlink(os.path.join(d, name), os.path.join(slim, name))
                except OSError:
                    pass
        return slim

    def run(self, new_key=STRAY, no_openssl=False):
        # Stage the material the keygen shim will "mint", outside the stores so
        # the census never sees it before the section runs.
        mint = os.path.join(self.root, "mint")
        putkey(mint, new_key)
        env = dict(os.environ)
        base = self.slim_path() if no_openssl else env["PATH"]
        env.update(
            PATH=self.bindir + os.pathsep + base,
            CHROOT=self.chroot, OUT=self.out,
            SIGNING_KEY_DIR=self.durable,
            SIGNING_KEY_DIR_ALT=self.alt,
            USER=env.get("USER") or "builder",
            PROBE_NEW_KEY=new_key,
            PROBE_NEW_PRIV=os.path.join(mint, new_key),
        )
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + SECTION],
                           capture_output=True, text=True, env=env)
        self.rc, self.log = r.returncode, r.stdout + r.stderr
        return self

    def is_key(self, d, name):
        """The file is that key, not a same-named husk some copy left behind."""
        try:
            return open(os.path.join(d, name), "rb").read() == pair(name)[0]
        except OSError:
            return False

    def is_material(self, d, name, material):
        """The file at `name` is `material`'s key — which key won, by content."""
        try:
            return open(os.path.join(d, name), "rb").read() == pair(material)[0]
        except OSError:
            return False

    def has(self, d, name):
        return os.path.exists(os.path.join(d, name))

    def names(self, d):
        try:
            return sorted(f for f in os.listdir(d) if not f.endswith(".pub"))
        except OSError:
            return []


RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else f"  [{detail}]"))


SANDBOXES = []


def case(setup, new_key=STRAY, no_openssl=False):
    # The tree outlives the call on purpose: the assertions are what read it.
    # Cleaned up at exit, all at once.
    root = tempfile.mkdtemp(prefix="signkey-")
    SANDBOXES.append(root)
    s = Sandbox(root)
    setup(s)
    return s.run(new_key, no_openssl=no_openssl)


def cleanup():
    for root in SANDBOXES:
        for d, _, _ in os.walk(root):  # case 9 leaves a mode-500 directory
            os.chmod(d, 0o755)
        shutil.rmtree(root, ignore_errors=True)


# ── 1. cold start: nothing anywhere ──────────────────────────────────────
s = case(lambda s: None, new_key=FLEET)
check("1 cold start mints one key and lands it in both stores",
      s.rc == 0 and "generating a new one" in s.log
      and s.is_key(s.durable, FLEET) and s.is_key(s.keys, FLEET),
      f"rc={s.rc} durable={s.names(s.durable)} out={s.names(s.keys)}")

# ── 2. key only in the durable home, chroot wiped ────────────────────────
s = case(lambda s: putkey(s.durable, FLEET))
check("2 restores from the durable home instead of minting",
      s.rc == 0 and "restoring abuild signing key" in s.log
      and s.is_key(s.abuild, FLEET) and not s.has(s.abuild, STRAY),
      f"rc={s.rc} chroot={s.names(s.abuild)}")

# ── 3. the original bug: the key survives only in out/keys ───────────────
s = case(lambda s: putkey(s.keys, FLEET))
check("3 out/keys-only survivor is restored, not replaced",
      s.rc == 0 and s.is_key(s.abuild, FLEET) and not s.has(s.abuild, STRAY)
      and s.is_key(s.durable, FLEET),
      f"rc={s.rc} chroot={s.names(s.abuild)} durable={s.names(s.durable)}")

# ── 4. abuild.conf must name the key that was actually restored ──────────
s = case(lambda s: putkey(s.durable, FLEET))
conf = os.path.join(s.abuild, "abuild.conf")
got = open(conf).read().strip() if os.path.exists(conf) else "<missing>"
check("4 abuild.conf points at the restored key",
      got == f'PACKAGER_PRIVKEY="/home/builder/.abuild/{FLEET}"', got)

# ── 5. box3's case: stray in the chroot, fleet key in out/keys ───────────
# The restore never runs — the chroot already has *a* key — so only a guard on
# the WRITE path can catch this. Unguarded, the stray is seeded into the empty
# durable home and every later run restores it as if it were the fleet key.
def stray_vs_fleet(s):
    putkey(s.abuild, STRAY)
    putkey(s.keys, FLEET)


s = case(stray_vs_fleet)
check("5 stray-in-chroot vs fleet-in-out/keys stops the build, seeds nothing",
      s.rc != 0 and not s.has(s.durable, STRAY),
      f"rc={s.rc} durable={s.names(s.durable)}")

# ── 6. two keys in one store ─────────────────────────────────────────────
def two_in_durable(s):
    putkey(s.durable, FLEET)
    putkey(s.durable, STRAY)


s = case(two_in_durable)
check("6 two keys in one store refuses to guess",
      s.rc != 0 and "refusing to guess" in s.log, f"rc={s.rc}")

# ── 7. the stores disagree with each other ───────────────────────────────
def disagree(s):
    putkey(s.durable, FLEET)
    putkey(s.keys, STRAY)


s = case(disagree)
check("7 stores that disagree stop the build",
      s.rc != 0, f"rc={s.rc} — reconciled silently")

# ── 8. steady state: one key, everywhere, agreeing ───────────────────────
def steady(s):
    for d in (s.abuild, s.durable, s.keys):
        putkey(d, FLEET)


s = case(steady)
check("8 steady state is a no-op",
      s.rc == 0 and "generating a new one" not in s.log
      and s.names(s.durable) == [FLEET] and s.is_key(s.abuild, FLEET),
      f"rc={s.rc} durable={s.names(s.durable)}")

# ── 9. the durable home cannot be created ────────────────────────────────
# SIGNING_KEY_DIR defaults outside the repo, so a build host that is not this
# appliance is the normal case, not an exotic one. It must fail with the fix
# in the message rather than a bare `mkdir: permission denied` under set -e.
#
# The obstacle is a plain FILE where the parent directory should be, not a
# mode-500 directory: this suite runs as root on the appliance, and mode bits
# do not bind root. A control that only bites for unprivileged users is a
# control that never runs here.
def unwritable(s):
    blocked = os.path.join(s.root, "nope")
    open(blocked, "w").write("not a directory")
    s.durable = os.path.join(blocked, "keys")


s = case(unwritable, new_key=FLEET)
check("9 uncreatable durable home fails with the fix, not a stack trace",
      s.rc != 0 and "set SIGNING_KEY_DIR" in s.log, f"rc={s.rc} log={s.log[-200:]!r}")

# ── 10. the public key reaches the chroot's apk trust store ──────────────
s = case(lambda s: putkey(s.durable, FLEET))
check("10 restored public key is trusted inside the chroot",
      os.path.exists(os.path.join(s.chroot, "etc/apk/keys", FLEET + ".pub")),
      str(s.names(os.path.join(s.chroot, "etc/apk/keys"))))

# ── 11. the halves are from different keygens ────────────────────────────
# box2 on #92. Every check above this one compares FILENAMES, and the restore
# is a `*.rsa*` glob, so a store holding a .rsa and a .rsa.pub minted by
# different runs — a half-finished copy, a restore stitched from two backups —
# reaches the end of the section with the census silent. 30-build-apks.sh then
# signs with one key and 40-build-apkovl.sh ships the OTHER into the apkovl's
# /etc/apk/keys, so the first symptom is UNTRUSTED SIGNATURE on hardware that
# has already left the building. Same severity as a fresh key, no resemblance
# to one in the logs.
s = case(lambda s: putkey(s.durable, FLEET, pub_from=STRAY))
check("11 a private key and a .pub from another keygen stop the build",
      s.rc != 0 and "NOT a pair" in s.log and not s.has(s.keys, FLEET),
      f"rc={s.rc} out={s.names(s.keys)} log={s.log[-160:]!r}")

# ── 12. the private half arrives without its public half ─────────────────
# The same glob makes this reachable, and it is quieter still: nothing signs
# wrongly, but 40-build-apkovl.sh copies `out/keys/*.rsa.pub` into the apkovl
# and a missing file there means the sticks trust NOTHING — the build succeeds
# and the failure is deferred to first boot on flashed hardware.
def priv_only(s):
    putkey(s.durable, FLEET)
    os.remove(os.path.join(s.durable, FLEET + ".pub"))


s = case(priv_only)
check("12 a private key with no .pub half stops the build",
      s.rc != 0 and "no .pub half" in s.log, f"rc={s.rc} log={s.log[-160:]!r}")

# ── 13. the pair check passes for the right reason ───────────────────────
# Rows 11 and 12 are satisfied by a section that rejects EVERY key, which would
# also satisfy nothing else here — so this asserts the positive half explicitly
# rather than leaving it implied by rows 2 and 8 passing.
s = case(lambda s: putkey(s.durable, FLEET))
check("13 a matching pair is verified and says so",
      s.rc == 0 and "pair verified" in s.log, f"rc={s.rc} log={s.log[-160:]!r}")

# ── 14-17. where config.sh puts the durable store by default ─────────────
# Every row above exports SIGNING_KEY_DIR, so none of them can see the default
# — and the default was the blocker box2 raised on #92: `/work` exists only on
# a box, and a box cannot run 10-mk-chroot.sh at all (sudo chroot and apk are
# hard-banned there), so an unconditional /work default was a hard stop under
# set -e on the one machine that builds the fleet.
#
# The block is extracted from config.sh and run with /work redirected into the
# sandbox, which is what makes "the host has no /work" expressible here: this
# suite runs ON a box, where /work does exist, so a test that read the real
# path could only ever exercise one of the three tiers.
#
# Extracted by SECTION boundaries — banner to the next assignment — not by the
# shape of the current if-block. Matching the block would make this probe read
# "could not extract" as its answer the moment someone rewrites the tiers, and
# an extraction failure is exactly when the rows most need to run: control G
# replaces the block with a one-line default, and a probe that cannot parse
# that is a probe that cannot fail it.
CFG = open(os.path.join(REPO, "config.sh")).read()
m = re.search(r"^# ── the abuild signing key.*?(?=^PIPE_SRC=)", CFG, re.S | re.M)
if not m or "SIGNING_KEY_DIR" not in m.group(0):
    sys.exit("FAIL could not extract the SIGNING_KEY_DIR section from config.sh")


def resolve(home, work=None, export=None, mkdirs=(), keys=()):
    """Run the tier logic with /work redirected.

    Returns (chosen, alt, rc, root). `keys` puts a REAL key in the named
    sandbox-relative stores, which is the thing `mkdirs` cannot express: the
    tiers select on `-d`, so a row built only out of empty directories cannot
    tell "this store holds the fleet key" from "this directory exists" — and
    that gap is precisely what let the winner be a store with nothing in it.
    (box3 on #92)
    """
    root = tempfile.mkdtemp(prefix="signkey-cfg-")
    SANDBOXES.append(root)
    h = os.path.join(root, home)
    os.makedirs(h, exist_ok=True)
    w = os.path.join(root, work) if work else os.path.join(root, "absent")
    for d in mkdirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for d in keys:
        putkey(os.path.join(root, d), FLEET)
    block = m.group(0).replace("/work", w)
    env = dict(os.environ, HOME=h)
    env.pop("SIGNING_KEY_DIR", None)
    env.pop("SIGNING_KEY_DIR_ALT", None)
    if export:
        env["SIGNING_KEY_DIR"] = os.path.join(root, export)
    r = subprocess.run(["bash", "-c", "set -eu\n" + block +
                        '\nprintf "%s\\n%s" "$SIGNING_KEY_DIR" '
                        '"${SIGNING_KEY_DIR_ALT:-}"'],
                       capture_output=True, text=True, env=env)
    chosen, _, alt = r.stdout.partition("\n")
    return chosen, alt, r.returncode, root


got, alt, rc, root = resolve("home", work="work",
                            mkdirs=("work", "work/keys/pipeos"),
                            export="elsewhere/keys")
check("14 an explicit SIGNING_KEY_DIR wins over both fallbacks",
      rc == 0 and got == os.path.join(root, "elsewhere/keys"), f"{rc} {got!r}")
# An override picks the store; it does not make the other two stop existing,
# and a key left in one of them is the disagreement the census exists to catch.
check("14b an override still names both defaults as alternates",
      rc == 0 and alt.split() == [os.path.join(root, "home/.pipeos/keys"),
                                  os.path.join(root, "work/keys/pipeos")],
      f"{rc} alt={alt!r}")

got, alt, rc, root = resolve("home")
check("15 no /work (the build host): the durable store is under $HOME",
      rc == 0 and got == os.path.join(root, "home/.pipeos/keys"), f"{rc} {got!r}")

got, alt, rc, root = resolve("home", work="work", mkdirs=("work",))
check("16 /work present and writable (on a box): the ext4 workspace wins",
      rc == 0 and got == os.path.join(root, "work/keys/pipeos"), f"{rc} {got!r}")
check("16b the $HOME store it passed over is named as the alternate",
      rc == 0 and alt.split() == [os.path.join(root, "home/.pipeos/keys")],
      f"{rc} alt={alt!r}")

# The one that matters for a builder mid-migration: a store that already holds
# the fleet key is not abandoned because a $HOME directory happens to exist.
#
# The key is REAL here, and that is the point. This row used to stage two empty
# directories, so it passed identically whether the winner held the fleet key or
# nothing at all — it was written to catch exactly the case it structurally
# could not distinguish. (box3 on #92)
got, alt, rc, root = resolve("home", work="work",
                            mkdirs=("work", "work/keys/pipeos",
                                    "home/.pipeos/keys"),
                            keys=("work/keys/pipeos",))
check("17 an on-box store HOLDING THE KEY wins over an existing $HOME one",
      rc == 0 and got == os.path.join(root, "work/keys/pipeos")
      and os.path.exists(os.path.join(root, "work/keys/pipeos", FLEET)),
      f"{rc} {got!r}")

# The inverse, which is the reachable half: the winner is an empty directory and
# the LOSER holds the fleet key. The tiers still pick the empty one — selection
# is on `-d` and that is deliberate — so the whole guarantee rests on the loser
# being named, censused, and restorable. Rows 20-22 below are what that buys.
got, alt, rc, root = resolve("home", work="work",
                            mkdirs=("work", "work/keys/pipeos",
                                    "home/.pipeos/keys"),
                            keys=("home/.pipeos/keys",))
check("17b an EMPTY on-box store still wins, and the populated $HOME store "
      "is named, not lost",
      rc == 0 and got == os.path.join(root, "work/keys/pipeos")
      and alt.split() == [os.path.join(root, "home/.pipeos/keys")],
      f"{rc} chosen={got!r} alt={alt!r}")

# ── 18-19. the stores agree on the NAME and disagree on the KEY ──────────
# box0 on #92, and the last shape of the original bug left standing: the census
# collapsed to `basename | sort -u`, so an impostor sitting under the fleet
# key's own filename made n_keys=1 and every guard went quiet. The pair check
# added for row 11 does not see it either — the impostor is a perfectly good
# pair, just not the fleet's.
#
# Appended rather than renumbered so the control map (which names row numbers)
# keeps pointing at the same rows.
IMPOSTOR = "impostor@pipeos-3000000000.rsa"


# 18 is box0's repro A: the chroot already holds *a* key, so the restore is
# skipped entirely and only a write-path guard can catch it. Unguarded this is
# rc=0 and silent — out/keys clobbered with the impostor, which 40-build-
# apkovl.sh:22 then copies into every stick's /etc/apk/keys, while the real
# fleet key sits in the durable store that nothing will read again.
def same_name_different_key(s):
    putkey(s.abuild, FLEET, material=IMPOSTOR)
    putkey(s.durable, FLEET)


s = case(same_name_different_key)
check("18 same name, different material across stores stops the build",
      s.rc != 0 and not s.has(s.keys, FLEET)
      and s.is_material(s.durable, FLEET, FLEET),
      f"rc={s.rc} out={s.names(s.keys)} "
      f"durable-is-fleet={s.is_material(s.durable, FLEET, FLEET)}")


# 19 is the same disagreement on the RESTORE path — no key in the chroot, so
# the section would pick a store by precedence and copy a key it never
# compared. Precedence answers "which store", and the census is the only thing
# that establishes there was nothing to choose between.
def same_name_restore_path(s):
    putkey(s.durable, FLEET)
    putkey(s.keys, FLEET, material=IMPOSTOR)


s = case(same_name_restore_path)
check("19 same name, different material picks no store and restores nothing",
      s.rc != 0 and not s.has(s.abuild, FLEET),
      f"rc={s.rc} chroot={s.names(s.abuild)}")

# ── 20-22. the store the tier logic did NOT pick ─────────────────────────
# box3 on #92. Rows 1-19 all run with $SIGNING_KEY_DIR naming the store that
# holds the key, because the sandbox exports it — so every one of them passes
# while the OTHER candidate is invisible. config.sh selects on `-d`, directory
# existence, which is not key presence, so the store it passes over can be the
# store holding the fleet key. Row 17b stages exactly that state; these three
# are what the section does when it arrives.
#
# 20 is the mint-instead-of-restore path: unguarded this is rc=0, a brand new
# key, and every flashed stick rejecting the next build's packages — #90's
# headline defect, reached through the code #92 added to prevent it.
s = case(lambda s: putkey(s.alt, FLEET))
check("20 a key only in the passed-over store is restored, not re-minted",
      s.rc == 0 and "restoring abuild signing key" in s.log
      and "generating a new one" not in s.log
      and s.is_key(s.abuild, FLEET),
      f"rc={s.rc} chroot={s.names(s.abuild)}")

# ...and it migrates to the winner, so the next run finds it without the
# fallback. `cp -n` at the bottom of the section is what does this; a store that
# lost the tier test is not left orphaned holding the only copy.
check("20b and it migrates into the chosen store, so the next run needs no "
      "fallback",
      s.is_key(s.durable, FLEET),
      f"durable={s.names(s.durable)}")


# 21 is row 7's promise — "stores that disagree stop the build" — applied to the
# pair of stores the tier logic chooses BETWEEN. Before the alt store was
# censused these two disagreed and the build never found out: it restored from
# the winner and the loser's key sat unread.
def winner_vs_alt(s):
    putkey(s.durable, FLEET)
    putkey(s.alt, STRAY)


s = case(winner_vs_alt)
check("21 the chosen and passed-over stores disagreeing stops the build",
      s.rc != 0 and not s.is_key(s.abuild, FLEET)
      and not s.is_key(s.abuild, STRAY),
      f"rc={s.rc} chroot={s.names(s.abuild)}")


# 22 is the same disagreement one layer down — same filename, different
# material — so it is the MATERIAL census that has to reach the alt store, not
# just the name census. Two guards, two rows: 21 fails if the store is missing
# from KEY_STORES at all, 22 additionally fails if only the name census sees it.
def winner_vs_alt_same_name(s):
    putkey(s.durable, FLEET)
    putkey(s.alt, FLEET, material=IMPOSTOR)


s = case(winner_vs_alt_same_name)
check("22 same name, different material in the passed-over store stops the "
      "build",
      s.rc != 0 and not s.has(s.abuild, FLEET)
      and s.is_material(s.durable, FLEET, FLEET),
      f"rc={s.rc} chroot={s.names(s.abuild)} "
      f"durable-is-fleet={s.is_material(s.durable, FLEET, FLEET)}")

# ── 23. the one path where an unverified pair reaches the apkovl ─────────
# The pair check fails OPEN when openssl is missing: it warns and builds on,
# which is the right call — refusing to build over a missing checker is a new
# failure mode on hosts that are otherwise fine. But it is also the only branch
# where a pair nothing verified can reach 40-build-apkovl.sh and every stick's
# /etc/apk/keys, so the warning is the entire mitigation and nothing asserted it
# still prints. A silently-dropped warning here looks exactly like a verified
# key. (box3 on #92, non-blocking)
s = case(lambda s: putkey(s.durable, FLEET), no_openssl=True)
check("23 no openssl: the build continues and says the pair is UNVERIFIED",
      s.rc == 0 and "UNVERIFIED" in s.log
      and "pair verified" not in s.log and s.is_key(s.abuild, FLEET),
      f"rc={s.rc} log={s.log[-300:]!r}")

cleanup()
sys.exit(0 if all(RESULTS) else 1)
