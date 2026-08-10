#!/bin/sh
# audit-policy.sh — check a box's LIVE pipe capability policy against its card.
#
# pipe#650 item 9, the audit half. Run by an OPERATOR, on the box, from a
# shell that is not the agent's.
#
# WHY THIS IS NOT A pipeos-selfcheck SECTION, which is the obvious place for
# it and the wrong one:
#
#   1. The agent cannot read the file. `Read(/root/.pipe/**)` is in the
#      generated deny list and in the mandate's hard bans, because that
#      directory holds the sign-in key. A check the agent runs cannot open
#      the thing it is checking.
#   2. Even if it could, a box reporting on its own fences is the weakest
#      possible evidence. The failure this exists to catch is "the policy is
#      not what anyone thinks it is" — and a compromised or misconfigured box
#      is exactly the one whose self-report you cannot use.
#   3. There is NO command that reports the effective policy. Searched:
#      pipe has no `policy` subcommand, and the only policy-related IPC verbs
#      in the whole workspace are Cmd::PolicyPending and Cmd::PolicyDecide —
#      pending prompts and approve/deny, not the ruleset. So this reads the
#      FILE. (`Capability::ALL`'s doc comment in pipe-policy claims a
#      `policy show` exists; it does not. Filed separately.)
#
# So: an operator runs this. That is not a workaround, it is the finding —
# a box cannot audit its own capability grants, and pretending otherwise
# would put a check in the fleet that always passes.
#
#   ./scripts/audit-policy.sh                     # this box, card at /etc/pipeos
#   ./scripts/audit-policy.sh docs/cards/box3.card /root/.pipe/policy.json
#
# Exit 0 clean, 1 divergence, 2 could not check (says which).

set -u

CARD=${1:-/etc/pipeos/card.conf}
POLICY=${2:-/root/.pipe/policy.json}
LABEL=pipebox

rc=0
ok()   { printf 'ok    %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; rc=1; }
note() { printf '      %s\n' "$*"; }

# One loop below runs as the RHS of a pipeline, which POSIX puts in a SUBSHELL —
# so an `rc=1` set inside it dies with that subshell and the script would exit 0
# having printed FAIL. A file is the only thing that crosses that boundary here.
RC_MARK=$(mktemp) || { printf 'audit-policy: mktemp failed\n' >&2; exit 2; }
rm -f "$RC_MARK"
trap 'rm -f "$RC_MARK"' EXIT INT TERM

command -v jq >/dev/null 2>&1 || { printf 'audit-policy: jq is required\n' >&2; exit 2; }

[ -r "$CARD" ]   || { printf 'audit-policy: card not readable: %s\n' "$CARD" >&2; exit 2; }
# An unreadable policy file is NOT a pass and not an error to shrug at: the
# daemon falls back to its built-in defaults when the file is missing, quietly,
# so "no file" is a real and different policy rather than an absent one.
if [ ! -r "$POLICY" ]; then
    printf 'FAIL  no policy file at %s\n' "$POLICY"
    printf '      the daemon is running on PolicySet::default, which DENIES send.bbs —\n'
    printf '      this box cannot post to its cohort board. Run: pipebox-card generate\n'
    exit 1
fi

# The card is parsed, never sourced — same rule as pipebox-card, same reason.
card_caps=$(sed -n 's/^PIPE_CAPS=//p' "$CARD" | tr -d '"' | tr ',' '\n' | sed '/^$/d' | sort)
live_caps=$(jq -r --arg l "$LABEL" '.agent[$l].allow // empty | .[]' "$POLICY" | sort)

if [ "$card_caps" = "$live_caps" ]; then
    ok "allow list matches the card ($(echo "$card_caps" | tr '\n' ' '))"
else
    bad "allow list DIVERGES from the card"
    note "card: $(echo "$card_caps" | tr '\n' ' ')"
    note "live: $(echo "$live_caps" | tr '\n' ' ')"
fi

# The ceiling again, checked against what is ACTUALLY ON DISK rather than
# against what the generator would have written. This is the half that catches
# a hand-edit, and a hand-edit is the whole reason item 9 exists.
#
# INVERTED: ITERATE WHAT THE POLICY GRANTS, NOT WHAT WE THINK EXISTS.
#
# Two earlier cuts and both were the same defect getting smaller. First it
# hardcoded three names — identity, admin, moderate — so it checked three of
# the eight capabilities no card can grant. Then it derived the ceiling but
# hardcoded the fourteen-name universe to subtract it from, **in a different
# repository from `Capability::ALL`** — under a comment claiming there was
# only one list. That is the pipe#682 argument reproduced one layer up, by me,
# one PR later, and it fails in the WORST direction: a capability added to
# pipe later is absent from the inventory, so the loop never tests it, so a
# hand-edit granting it passes clean. Exactly what the derivation was for.
#
# So there is no inventory. Read the grants off the file and flag anything the
# ceiling does not permit. Nothing to keep in sync, it catches capabilities
# invented after this script was written — which the previous version
# structurally could not — and it subsumes the separate `*` check, because
# `*` is not in the ceiling either. (box3, reviewing #58; the inversion is
# theirs.)
CEILING=$(sed -n 's/^PIPE_CAPS_CEILING="\(.*\)"$/\1/p' \
    "$(dirname "$0")/../overlay/usr/local/bin/pipebox-card" 2>/dev/null \
    || true)
if [ -z "$CEILING" ]; then
    printf 'audit-policy: cannot read PIPE_CAPS_CEILING from pipebox-card\n' >&2
    exit 2
fi
# WHICH pipebox-card, and it is not necessarily the one that wrote this file:
# the ceiling comes from the REPO checkout, while a live policy.json was
# written by the INSTALLED generator. Audit a box running an older generator
# than your tree and you are validating against a ceiling that generator never
# had. Instrument versus thing, one more time — the repo copy is authoritative
# for what a card MAY grant today, which is the question being asked, but say
# so rather than letting the reader assume they are the same file.
# READ, NOT WORD-SPLIT, and the reason is a defect box3 found in the version
# directly above this one. That loop was `for granted in $(jq ...)`, unquoted,
# under `set -u` alone — so a policy granting `*` was PATHNAME-EXPANDED against
# the working directory. `$granted` became filenames, never `*`, and the branch
# written for the single worst case was dead code. It replaced a `jq index("*")`
# check that was correct.
#
# Measured, same construct, two directories:
#
#   cwd with alpha+beta   granted=[read] granted=[alpha] granted=[beta] granted=[loop.sh]
#   "empty" cwd           granted=[read] granted=[loop.sh]
#
# An unmatched glob stays literal in POSIX sh, so the natural expectation is
# "it only breaks in a populated directory". It does not: the script itself is
# a file, so the glob ALWAYS matches something and `*` never survives.
#
# `set -f` fixes it and is what box3 proposed. This uses a read loop instead
# because the fix is then LOCAL to the thing that needed it — no global shell
# option whose next reader has to work out what depends on it — and because
# `read` does no splitting or globbing at all rather than disabling one of two.
jq -r --arg l "$LABEL" '.agent[$l].allow // [] | .[]' "$POLICY" \
| while IFS= read -r granted; do
    [ -n "$granted" ] || continue
    permitted=0
    for allowed in $CEILING; do
        [ "$granted" = "$allowed" ] && { permitted=1; break; }
    done
    [ "$permitted" = 1 ] && continue
    printf 'FAIL  the live policy GRANTS %s to the agent\n' "'$granted'"
    if [ "$granted" = "*" ]; then
        printf "      '*' is every capability at once — every fence in the card is void\n"
    else
        printf '      no card can produce this — it was hand-edited or the file is not ours\n'
    fi
    # A pipeline runs its RHS in a subshell, so `bad`'s rc=1 would be lost.
    # The marker file carries the failure back out; checked below.
    : > "$RC_MARK"
done
[ -e "$RC_MARK" ] && rc=1

# THE CONFIRM LIST, which the first cut never looked at and which is what makes
# the loop above bite. Precedence is deny > confirm > allow, so a hand-edit
# that merely ADDS file.send to allow is still stopped by confirm. The edit
# that defeats the human gate MOVES it — out of confirm, into allow — and that
# one passed the first version of this audit clean, because nothing verified
# confirm had survived. It is the single most valuable hand-edit to make and
# was the single one not checked.
#
# Derived from the TEMPLATE for the same reason the ceiling is derived from the
# generator: the confirm list is not this script's to know. Hardcoding those
# five names here would be the third instance of the defect box3 caught above —
# an inventory in one repo that must agree with a definition in another, with
# no mechanism keeping them together. This reads the shipped template.
CONFIRM_TMPL="$(dirname "$0")/../overlay/usr/local/share/pipeos/card/policy.json.tmpl"
EXPECTED_CONFIRM=$(sed -e 's/@@PIPE_ALLOW@@/"x"/' "$CONFIRM_TMPL" 2>/dev/null \
    | jq -r '.agent.pipebox.confirm // [] | .[]' 2>/dev/null || true)
if [ -z "$EXPECTED_CONFIRM" ]; then
    printf 'audit-policy: cannot read the confirm list from %s\n' "$CONFIRM_TMPL" >&2
    exit 2
fi
for gated in $EXPECTED_CONFIRM; do
    if ! jq -e --arg l "$LABEL" --arg c "$gated" \
        '(.agent[$l].confirm // []) | index($c)' "$POLICY" >/dev/null 2>&1; then
        bad "'$gated' is missing from the agent's confirm list"
        note "the human gate on it is gone; check whether it moved into allow"
    fi
done

# The operator's own access. A policy with no `default` key deserializes to
# empty allow lists and decide() default-denies, so this locks the human out
# of their own CLI while leaving the agent working — a failure that looks like
# "pipe is broken" rather than like a policy mistake.
if jq -e '.default.allow | index("*")' "$POLICY" >/dev/null 2>&1; then
    ok "the human at the terminal still has their shell"
else
    bad "the policy does not grant the human — YOUR OWN CLI IS FENCED"
fi

# Divergence between file and running daemon. The daemon builds its
# PolicyEngine once, at DaemonState construction (pipe-daemon state/mod.rs),
# and never re-reads — so an edited file is not a changed policy until the
# daemon restarts. Reported rather than acted on: restarting a service is the
# operator's call and is hard-banned for the agent.
#
# GATED ON $POLICY BEING THE PATH THE DAEMON ACTUALLY READS. Auditing a file
# somewhere else — a fixture, a copy, another box's policy pulled over for
# comparison — and then timestamping it against this machine's daemon compares
# two unrelated things and reports a divergence that does not exist. Caught by
# the probe, which audits generated fixtures under /tmp and got a FAIL on a
# file the daemon had never heard of.
LIVE_POLICY=/root/.pipe/policy.json
if [ "$POLICY" != "$LIVE_POLICY" ]; then
    note "skip: $POLICY is not the daemon's own path, so 'is it in force' does not apply"
elif ! command -v pgrep >/dev/null 2>&1; then
    note "skip: no pgrep, so 'is it in force' is unanswerable"
elif ! dpid=$(pgrep -f 'pipe.*daemon' | head -1) || [ -z "$dpid" ]; then
    note "skip: no running pipe daemon found, so 'is it in force' is unanswerable"
elif [ "$POLICY" -nt "/proc/$dpid" ]; then
    bad "policy.json is NEWER than the running daemon — the file on disk is not in force"
    note "the daemon reads policy once at startup; restart it to apply"
else
    ok "policy.json predates the running daemon (it is the one in force)"
fi

[ "$rc" = 0 ] && printf 'audit-policy: PASS\n' || printf 'audit-policy: FAIL\n'
exit "$rc"
