#!/bin/sh
# check-cards.sh — the repo's drift gate for #650.
#
# Three things, in order of how badly they bite:
#
#   1. every card in docs/cards/ and the shipped default renders without error
#      and produces valid JSON settings
#   2. the generator is byte-stable — rendering the same card twice gives the
#      same bytes. Without this, `pipebox-card verify` cannot distinguish a
#      hand edit from a generator that simply never repeats itself.
#   3. the checked-in overlay config IS the output of the shipped card. This is
#      what stops the repo drifting from its own templates the way the fleet
#      drifted from the repo.

set -eu
cd "$(dirname "$0")/.."

CARDTOOL=overlay/usr/local/bin/pipebox-card
TMPLDIR=overlay/usr/local/share/pipeos/card
SHIPPED=overlay/etc/pipeos/card.conf
# Tracks the generator's own OUTPUTS in pipebox-card — including
# root/.pipe/policy.json (pipeOS#94: it was generated but ungated here, and
# the committed copy drifted; the 2026-08-31 `make cards` realigned it and
# this line is what keeps it aligned).
OUTPUTS="etc/pipeos/pipebox.conf etc/pipeos/pipebox-settings.json etc/pipeos/mandate.md etc/profile.d/10-pipebox-env.sh etc/hostname etc/issue etc/motd etc/network/interfaces root/.pipe/policy.json"

fails=0
say()  { printf '%s\n' "$*"; }
ok()   { printf 'ok    %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }

gen() { # gen CARD DESTDIR
    sh "$CARDTOOL" generate --card "$1" --root "$2" --templates "$TMPLDIR" >/dev/null
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM

for card in "$SHIPPED" docs/cards/*.card; do
    [ -f "$card" ] || continue
    name=$(basename "$card")

    a="$tmp/a-$name"; b="$tmp/b-$name"
    mkdir -p "$a" "$b"

    if ! gen "$card" "$a" 2>"$tmp/err"; then
        bad "$name: generate failed — $(head -1 "$tmp/err")"
        continue
    fi

    if command -v jq >/dev/null 2>&1; then
        if jq -e . "$a/etc/pipeos/pipebox-settings.json" >/dev/null 2>&1; then
            ok "$name: settings are valid JSON"
        else
            bad "$name: generated settings are not valid JSON"
        fi
        # the deny list is the hard-ban fence; a card that can drop an entry
        # from it is a card that can quietly unarm the mandate
        for must in 'Bash(lbu*)' 'Bash(apk *)' 'Bash(mount*)' 'Bash(rc-*)' \
                    'Read(/root/.pipe/**)' 'Edit(/etc/**)' 'Edit(/media/usb/**)'; do
            if jq -e --arg m "$must" '.permissions.deny | index($m)' \
                 "$a/etc/pipeos/pipebox-settings.json" >/dev/null 2>&1; then :; else
                bad "$name: hard ban missing from deny list: $must"
            fi
        done
    fi

    gen "$card" "$b"
    stable=1
    for rel in $OUTPUTS; do
        cmp -s "$a/$rel" "$b/$rel" || { bad "$name: not byte-stable: $rel"; stable=0; }
    done
    [ "$stable" = 1 ] && ok "$name: byte-stable across two renders"
done

# 3. the committed overlay must equal the shipped card's output
drift=0
for rel in $OUTPUTS; do
    if ! cmp -s "$tmp/a-card.conf/$rel" "overlay/$rel"; then
        bad "overlay/$rel is not what the shipped card generates"
        say "      run: make cards"
        drift=1
    fi
done
[ "$drift" = 0 ] && ok "committed overlay matches the shipped card"

# 4. the Foreman charter has exactly one text (#39).
#
# `docs/foreman.md` is the citable document; the copy beside the templates is
# what ships and what the generator reads into every mandate. Two files, so
# this gate is what makes them one text — without it the box-facing charter
# could drift from the one Sam blessed and nothing would say so, which is
# pipe#668's defect class aimed at the mandate itself.
if cmp -s docs/foreman.md "$TMPLDIR/foreman.md"; then
    ok "the Foreman charter ships exactly as docs/foreman.md says it does"
else
    bad "$TMPLDIR/foreman.md differs from docs/foreman.md"
    say "      the mandate would teach a Foreman charter Sam did not bless"
    say "      run: cp docs/foreman.md $TMPLDIR/foreman.md"
fi

# 5. the generated environment must actually REACH the agent (#90, box0 on #93)
#
# Check 3 proves the env file is generated correctly and ships in the overlay.
# It says nothing about whether anything reads it, and for a while nothing did.
# /etc/profile sources /etc/profile.d/* for LOGIN shells only, and no agent
# launch is a login shell — crond/init -> launcher -> claude -p -> sh -c. box0
# measured it: a profile.d export was simply absent from the agent's own
# environment while checks 1-3 were green.
#
# The launcher list is DISCOVERED, not written down here. A hardcoded pair is
# exactly what goes stale the day someone adds a third way to start the agent,
# which is the same failure this check exists to catch.
ENVFILE=etc/profile.d/10-pipebox-env.sh
# A launcher is a script that actually RUNS the agent. Plain `claude -p` also
# appears in comments and in selfcheck's log messages, so match the invocation
# — piped into a timeout — on a line that is not a comment.
launchers=
for f in overlay/usr/local/bin/*; do
    if sed 's/#.*//' "$f" | grep -qE 'timeout [^|]* claude -p'; then
        launchers="$launchers $f"
    fi
done
if [ -z "$launchers" ]; then
    bad "found no agent launcher in overlay/usr/local/bin — this check went blind"
else
    for launcher in $launchers; do
        if grep -q "^\[ -r /$ENVFILE \] && \. /$ENVFILE" "$launcher"; then
            ok "$(basename "$launcher") sources /$ENVFILE"
        else
            bad "$(basename "$launcher") launches the agent without sourcing /$ENVFILE"
            say "      the agent it starts gets none of the generated environment"
        fi
    done
fi

# 6. the two exports the fleet's disk budget depends on, and their SCOPE
#
# Checks 3 and 5 cover "the file is what the card generates" and "something
# sources it". Neither reads what it says, so dropping an export in the
# template — or moving one — is invisible to both: the committed copy still
# matches the generator, and the launchers still source it.
#
# Scope is the part worth asserting rather than eyeballing. CARGO_TARGET_DIR
# must stay INSIDE the mountpoint guard: with /work unmounted it would point
# cargo at a tmpfs path on a diskless box and put gigabytes in RAM.
# CARGO_INCREMENTAL must stay OUTSIDE it, because it is a size policy rather
# than a location, and the run where /work did NOT mount is the one where
# space is scarcest — silently reverting to incremental there is backwards.
envf="overlay/$ENVFILE"
# The env file owns CARGO_INCREMENTAL (size policy, unconditional). The
# TARGET DIR moved to the cargo PATH shim (pipeOS#115 / pipe#791): a static
# export is precisely the cross-checkout share that let one worktree run
# another's test binary, so the gate now asserts its ABSENCE here and the
# shim's presence + keying there.
inc_ln=$(grep -c '^export CARGO_INCREMENTAL$' "$envf" || true)
if [ "${inc_ln:-0}" -eq 0 ]; then
    bad "$ENVFILE no longer exports CARGO_INCREMENTAL"
elif grep -q 'export CARGO_TARGET_DIR' "$envf"; then
    bad "CARGO_TARGET_DIR is exported by the env file — the cargo shim owns it (pipeOS#115)"
else
    ok "env file: CARGO_INCREMENTAL exported, no static CARGO_TARGET_DIR"
fi
SHIM=overlay/usr/local/bin/cargo
if [ ! -f "$SHIM" ]; then
    bad "cargo shim missing at $SHIM"
elif ! grep -q 'git rev-parse --show-toplevel' "$SHIM"     || ! grep -q 'mountpoint -q /work' "$SHIM"     || ! grep -q '/work/cargo-target/\$h' "$SHIM"; then
    bad "cargo shim does not key the target dir on the checkout under the /work guard"
else
    ok "cargo shim keys CARGO_TARGET_DIR per checkout, guarded on /work"
fi

if [ "$fails" = 0 ]; then
    say "check-cards: PASS"
else
    say "check-cards: FAIL ($fails)"
    exit 1
fi
