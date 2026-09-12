#!/usr/bin/env bash
# verify-image-generic.sh — refuse to publish an OPERATOR image (pipeOS#271).
#
# docs/fulfillment.md § Batch prep: the cluster's own sticks are built with
# AUTH_KEYS=… (the workstation's ssh key baked into the apkovl), or with
# ROOT_LOGIN=password (a console root password), or as `make stick` (a box's
# card); none may ever become a release asset — an operator key or password
# in the published image is that key or password on every customer box.
# Nothing enforced it: on 2026-09-11 out/pipeos-usb.img was a key-baked build
# for most of the day and only a hand rebuild minutes before `make release`
# kept the key out of repo-2026.09.12-ddea944.
#
# Usage: verify-image-generic.sh IMG           (reads p1's apkovl with mtools)
#        verify-image-generic.sh --apkovl FILE  (the apkovl itself)
# Exit 0 = generic (safe to publish). Exit 2 = operator image, the entry that
# tripped is on stderr. Exit 1 = could not evaluate — refuse, do not guess.
# Every test here is written so a failure of the test itself is exit 1, never
# a pass: this gate inverts silently otherwise (a review caught the first cut
# passing a key-baked apkovl once its listing outgrew a pipe buffer).
set -euo pipefail
. "${PIPEOS_CONFIG:-$(dirname "$0")/../config.sh}"

cannot() { echo "verify-image-generic: $* — refusing" >&2; exit 1; }

OVL=""
case "${1:-}" in
    --apkovl) OVL="${2:?--apkovl needs a file}" ;;
    "") echo "usage: $0 IMG | --apkovl FILE" >&2; exit 1 ;;
    *)  IMG="$1"
        [ -f "$IMG" ] || cannot "no such image: $IMG"
        command -v mcopy >/dev/null 2>&1 || cannot "mtools (mcopy) is not installed, cannot read p1"
        TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
        # p1 starts at PART_OFFSET_MB — the same constant 50-build-image.sh
        # wrote it with — so the FAT is addressed as IMG@@offset
        MTOOLS_SKIP_CHECK=1 mcopy -i "$IMG@@$((PART_OFFSET_MB * 1024 * 1024))" -n ::/pipeos.apkovl.tar.gz "$TMP/apkovl.tar.gz" 2>/dev/null \
            || cannot "could not read pipeos.apkovl.tar.gz out of $IMG p1"
        OVL="$TMP/apkovl.tar.gz" ;;
esac
[ -f "$OVL" ] || cannot "no such apkovl: $OVL"

LIST=$(tar -tzf "$OVL" 2>/dev/null) || cannot "$OVL is not a readable tar.gz"
[ -n "$LIST" ] || cannot "$OVL lists nothing"

# has ENTRY (regex on the listing)? 0 yes, 1 no, anything else is a broken test
has() {
    local rc=0
    grep -qxE "$1" <<<"$LIST" || rc=$?
    [ "$rc" -le 1 ] || cannot "grep failed (rc $rc) while looking for $1"
    return "$rc"
}
# extract one member to stdout, tolerating the ./ prefix variants
member() {
    local m
    m=$(grep -xE "\.?/?$1" <<<"$LIST" | head -1) || true
    [ -n "$m" ] || return 1
    tar -xzOf "$OVL" "$m" 2>/dev/null
}

hit=""
# 1. an ssh key baked for the operator (40-build-apkovl.sh AUTH_KEYS)
if has '\.?/?root/\.ssh/authorized_keys'; then
    hit="root/.ssh/authorized_keys (AUTH_KEYS build — the operator's ssh key)"
fi
# 2. a console root password (ROOT_LOGIN=password): root's shadow field is
#    a hash instead of the client image's '*' (or '!')
if [ -z "$hit" ] && has '\.?/?etc/shadow'; then
    rootpw=$(member 'etc/shadow' | awk -F: '$1=="root"{print $2; exit}') \
        || cannot "could not read etc/shadow out of the apkovl"
    case "$rootpw" in
        ''|'*'|'!'|'!!'|'!*') ;;
        *) hit="etc/shadow: root has a password hash (ROOT_LOGIN=password build)" ;;
    esac
fi
# 3. a named box's card (make stick CARD=…): NICK set in the baked card.conf
if [ -z "$hit" ] && has '\.?/?etc/pipeos/card\.conf'; then
    nick=$(member 'etc/pipeos/card.conf' | sed -n 's/^NICK=//p' | head -1 | tr -d '"'"'"'') \
        || cannot "could not read etc/pipeos/card.conf out of the apkovl"
    [ -n "$nick" ] && hit="etc/pipeos/card.conf names a box (NICK=$nick — a make stick image)"
fi
if [ -n "$hit" ]; then
    echo "verify-image-generic: OPERATOR IMAGE — $hit. Not publishable." >&2
    echo "verify-image-generic: rebuild generic: env -u AUTH_KEYS -u CARD -u ROOT_LOGIN make usb" >&2
    exit 2
fi
echo "verify-image-generic: generic (no operator key, no root password, no box card)"
