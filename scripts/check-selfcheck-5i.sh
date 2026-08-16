#!/bin/sh
# check-selfcheck-5i.sh — rows for pipeos-selfcheck section 5i (pipeOS#79).
#
# WHY THIS EXISTS AS A SCRIPT RATHER THAN AS A HAND-RUN.
#
# 5i is a DETECTOR, and the review of #91 found both of its halves failing
# OPEN on a live box: the overlay half was skipped entirely by one `-d` test
# and printed nothing, and the version half went silent whenever `origin/main`
# was unreachable. A detector whose failure mode is silence cannot be verified
# by observing that it is quiet — the only useful question is "does it go loud
# when its own condition is violated", and that has to be asked by deliberately
# constructing the condition.
#
# It also cannot be asked on a box. `pipeos-selfcheck` is on every box's hard
# ban list (it touches boot media), and the boxes' sandboxes refuse an
# extracted-snippet run — box0 and box3 each hit that wall reviewing #91. CI is
# the one instrument in this fleet that can execute this section, so the rows
# live where they can actually run.
#
# METHOD, and its two substitutions, stated because they are the honest limit:
# the section is extracted by its own `# ---- 5i.` / `# ---- 6.` boundaries
# (aborting if either marker moves), `warnn`/`crit` are shimmed to stdout, and
# exactly two paths are rewritten to point at a fixture — `/usr/local/bin` and
# `/work/repos`. Nothing else is touched. So this proves the section's control
# flow and its messages, not its behaviour inside a full `pipeos-selfcheck`.
set -u

SRC=${1:-overlay/usr/local/bin/pipeos-selfcheck}
[ -f "$SRC" ] || { echo "no such file: $SRC" >&2; exit 2; }

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

sed -n '/^# ---- 5i\./,/^# ---- 6\./p' "$SRC" | sed '$d' > "$T/5i.raw"
if ! grep -q '_repo_dir' "$T/5i.raw" || ! grep -q '_ov_repo' "$T/5i.raw"; then
    echo "EXTRACTION FAILED — the 5i/6 boundary markers moved. Fix this script"
    echo "rather than deleting it: a silently-empty extraction is the same"
    echo "fail-open shape the section itself was fixed for."
    exit 2
fi
echo "extracted $(wc -l < "$T/5i.raw") lines of section 5i"

pass=0; fail=0
want()   { if grep -q "$2" "$T/out"; then echo "ok   $1"; pass=$((pass+1))
           else echo "FAIL $1 — wanted /$2/, got:"; sed 's/^/       /' "$T/out"; fail=$((fail+1)); fi; }
reject() { if grep -q "$2" "$T/out"; then echo "FAIL $1 — must NOT match /$2/:"; sed 's/^/       /' "$T/out"; fail=$((fail+1))
           else echo "ok   $1"; pass=$((pass+1)); fi; }
silent() { if [ -s "$T/out" ]; then echo "FAIL $1 — wanted silence, got:"; sed 's/^/       /' "$T/out"; fail=$((fail+1))
           else echo "ok   $1"; pass=$((pass+1)); fi; }

ROOT=$T/repos; INST=$T/inst; UP=$T/upstream
mkdir -p "$ROOT" "$INST" "$UP/overlay/usr/local/bin"

git init -q -b main "$UP"
git -C "$UP" config user.email ci@pipeos.local
git -C "$UP" config user.name ci
printf 'v1\n' > "$UP/overlay/usr/local/bin/alpha"
printf 'v1\n' > "$UP/overlay/usr/local/bin/beta"
git -C "$UP" add -A && git -C "$UP" commit -qm one

git clone -q "$UP" "$ROOT/pipeOS"

# main moves after the clone: `alpha` changes and `gamma` appears. This is the
# #79 condition — merged, and neither in this box's worktree nor installed.
printf 'v2 MERGED UPSTREAM\n' > "$UP/overlay/usr/local/bin/alpha"
printf 'gamma\n' > "$UP/overlay/usr/local/bin/gamma"
git -C "$UP" add -A && git -C "$UP" commit -qm two
git -C "$ROOT/pipeOS" fetch -q origin

run() {
    sed -e "s#/usr/local/bin#$INST#g" -e "s#/work/repos#$ROOT#g" "$T/5i.raw" > "$T/5i.sh"
    {   echo 'warnn() { echo "warn: $*"; }'
        echo 'crit()  { echo "CRITICAL: $*"; }'
        echo "REPOS='$1'"
        cat "$T/5i.sh"
    } > "$T/go.sh"
    # The pipe half is exercised by its own row below; filtered here so the
    # overlay rows assert on the overlay half alone.
    /bin/sh "$T/go.sh" 2>&1 | grep -v 'cannot compare the installed pipe' > "$T/out"
}

echo
echo "--- 1. the #79 case: merged upstream, installed matches the old checkout"
printf 'v1\n' > "$INST/alpha"; printf 'v1\n' > "$INST/beta"
run pipeOS
want   "merged-and-never-deployed is REPORTED"        'differ from origin/main: alpha'
want   "a file merged since the checkout is visible"  'NOT INSTALLED.*gamma'
reject "no false 'local edit' claim"                  'local overlay edits'

echo
echo "--- 2. normal mid-work: a local edit, installed equals origin/main"
printf 'v2 MERGED UPSTREAM\n' > "$INST/alpha"
printf 'v1\n' > "$INST/beta"
printf 'gamma\n' > "$INST/gamma"
printf 'v1 EDITED LOCALLY\n' > "$ROOT/pipeOS/overlay/usr/local/bin/beta"
run pipeOS
want   "the local edit is named as local"             'local overlay edits not in origin/main: beta'
reject "and is NOT reported as unshipped"             'differ from origin/main'

echo
echo "--- 3. a clean box says nothing"
git -C "$ROOT/pipeOS" checkout -q -- overlay/usr/local/bin/beta
run pipeOS
silent "clean box is silent"

echo
echo "--- 4. box0's condition: the card names no pipeOS checkout"
run pipe
want   "says it cannot check rather than going quiet" 'cannot compare the installed overlay'

echo
echo "--- 5. REPOS unset entirely"
run ""
want   "still says it cannot check"                   'cannot compare the installed overlay'

echo
echo "--- 6. the pipe half names its own blindness"
mkdir -p "$ROOT/pipe"
sed -e "s#/usr/local/bin#$INST#g" -e "s#/work/repos#$ROOT#g" "$T/5i.raw" > "$T/5i.sh"
{   echo 'warnn() { echo "warn: $*"; }'
    echo 'crit()  { echo "CRITICAL: $*"; }'
    echo "REPOS='pipe,pipeOS'"
    cat "$T/5i.sh"
} > "$T/go.sh"
/bin/sh "$T/go.sh" > "$T/out" 2>&1
want   "a declared non-repo is named, not skipped"    'cannot compare the installed pipe'

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
