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
OUTPUTS="etc/pipeos/pipebox.conf etc/pipeos/pipebox-settings.json etc/pipeos/mandate.md"

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

if [ "$fails" = 0 ]; then
    say "check-cards: PASS"
else
    say "check-cards: FAIL ($fails)"
    exit 1
fi
