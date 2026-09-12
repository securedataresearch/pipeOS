#!/usr/bin/env bash
# verify-image-generic.sh — refuse to publish an OPERATOR image (pipeOS#271).
#
# docs/fulfillment.md § Batch prep: the cluster's own sticks are built with
# AUTH_KEYS=… (the workstation's ssh key baked into the apkovl) and a
# `make stick` image carries a box's card; neither may ever become a release
# asset — an operator key in the published image is an operator key on every
# customer box. Nothing enforced it: on 2026-09-11 out/pipeos-usb.img was a
# key-baked build for most of the day and only a hand rebuild minutes before
# `make release` kept the key out of repo-2026.09.12-ddea944.
#
# Usage: verify-image-generic.sh IMG          (reads p1's apkovl with mtools)
#        verify-image-generic.sh --apkovl FILE (the apkovl itself)
# Exit 0 = generic (safe to publish). Exit 2 = operator image, the entry that
# tripped is on stderr. Exit 1 = could not evaluate (refuse, do not guess).
set -euo pipefail
. "$(dirname "$0")/../config.sh"

OVL=""
case "${1:-}" in
    --apkovl) OVL="${2:?--apkovl needs a file}" ;;
    "") echo "usage: $0 IMG | --apkovl FILE" >&2; exit 1 ;;
    *)  IMG="$1"
        [ -f "$IMG" ] || { echo "verify-image-generic: no such image: $IMG" >&2; exit 1; }
        command -v mcopy >/dev/null 2>&1 \
            || { echo "verify-image-generic: mtools (mcopy) is not installed — cannot read p1, refusing" >&2; exit 1; }
        TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
        # p1 starts at PART_OFFSET_MB — the same constant 50-build-image.sh
        # wrote it with — so the FAT is addressed as IMG@@offset
        MTOOLS_SKIP_CHECK=1 mcopy -i "$IMG@@$((PART_OFFSET_MB * 1024 * 1024))" -n ::/pipeos.apkovl.tar.gz "$TMP/apkovl.tar.gz" 2>/dev/null \
            || { echo "verify-image-generic: could not read pipeos.apkovl.tar.gz out of $IMG p1 — refusing" >&2; exit 1; }
        OVL="$TMP/apkovl.tar.gz" ;;
esac
[ -f "$OVL" ] || { echo "verify-image-generic: no such apkovl: $OVL" >&2; exit 1; }

LIST=$(tar -tzf "$OVL" 2>/dev/null) \
    || { echo "verify-image-generic: $OVL is not a readable tar.gz — refusing" >&2; exit 1; }

hit=""
# an ssh key baked for the operator (40-build-apkovl.sh AUTH_KEYS)
echo "$LIST" | grep -qxE '\.?/?root/\.ssh/authorized_keys' && hit="root/.ssh/authorized_keys (AUTH_KEYS build — the operator's ssh key)"
# a named box's card (make stick CARD=…): NICK set in the baked card.conf
if [ -z "$hit" ] && echo "$LIST" | grep -qxE '\.?/?etc/pipeos/card\.conf'; then
    card=$(echo "$LIST" | grep -xE '\.?/?etc/pipeos/card\.conf' | head -1)
    nick=$( (tar -xzOf "$OVL" "$card" 2>/dev/null || true) | sed -n 's/^NICK=//p' | head -1 | tr -d '"'"'"'')
    [ -n "$nick" ] && hit="etc/pipeos/card.conf names a box (NICK=$nick — a make stick image)"
fi
if [ -n "$hit" ]; then
    echo "verify-image-generic: OPERATOR IMAGE — $hit. Not publishable." >&2
    echo "verify-image-generic: rebuild generic: env -u AUTH_KEYS -u CARD make usb" >&2
    exit 2
fi
echo "verify-image-generic: generic (no operator key, no box card)"
