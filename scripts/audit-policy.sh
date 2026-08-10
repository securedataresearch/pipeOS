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
# DERIVED FROM THE CEILING, NOT RESTATED. The first cut hardcoded three names
# — identity, admin, moderate — and so checked three of the EIGHT capabilities
# no card can grant. The five it missed (room.create, room.accept, file.send,
# file.accept, contacts.write) are the ones the generator's own comment
# describes most alarmingly: file.send reads an arbitrary local path off the
# machine, contacts.write pins a peer key and poisons trust grading.
#
# Restating it was also two lists that must agree, maintained separately in
# different files — the exact shape I argued against in pipe#682 and was right
# about, reproduced here by me one PR later. Now there is one list and this
# reads it. (box3, reviewing #58.)
CEILING=$(sed -n 's/^PIPE_CAPS_CEILING="\(.*\)"$/\1/p' \
    "$(dirname "$0")/../overlay/usr/local/bin/pipebox-card" 2>/dev/null \
    || true)
if [ -z "$CEILING" ]; then
    printf 'audit-policy: cannot read PIPE_CAPS_CEILING from pipebox-card\n' >&2
    exit 2
fi
ALL_CAPS="read send.dm send.room send.lobby send.bbs room.create room.accept
file.send file.accept contacts.write identity moderate claim admin"
for cap in $ALL_CAPS; do
    grantable=0
    for allowed in $CEILING; do
        [ "$cap" = "$allowed" ] && { grantable=1; break; }
    done
    [ "$grantable" = 1 ] && continue
    if jq -e --arg l "$LABEL" --arg c "$cap" \
        '(.agent[$l].allow // []) | index($c)' "$POLICY" >/dev/null 2>&1; then
        bad "the live policy GRANTS '$cap' to the agent"
        note "no card can produce this — it was hand-edited or the file is not ours"
    fi
done
if jq -e --arg l "$LABEL" '(.agent[$l].allow // []) | index("*")' "$POLICY" >/dev/null 2>&1; then
    bad "the live policy grants '*' to the agent — every fence in the card is void"
fi

# THE CONFIRM LIST, which the first cut never looked at and which is what makes
# the loop above bite. Precedence is deny > confirm > allow, so a hand-edit
# that merely ADDS file.send to allow is still stopped by confirm. The edit
# that defeats the human gate MOVES it — out of confirm, into allow — and that
# one passed the first version of this audit clean, because nothing verified
# confirm had survived. It is the single most valuable hand-edit to make and
# was the single one not checked.
for gated in file.send file.accept room.create room.accept contacts.write; do
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
