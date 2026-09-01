#!/usr/bin/env bash
# deploy-box.sh — push a committed overlay to a box that has NO repo checkout.
#
# `pipeos deploy-overlay` is the deploy tool, but it runs ON the box, from the
# box's own checkout — which a GENERIC client box (basho0-class) deliberately
# does not have. This is the push-side equivalent for exactly those boxes:
# same file set (DEPLOY_PATHS), same NEVER list, same commit discipline (ships
# git HEAD, never the working tree), same stamp, so `pipeos status` and
# selfcheck can age the box's overlay afterwards.
#
# It is also the single, auditable unit an operator allowlists for their
# agent (e.g. `Bash(scripts/deploy-box.sh:*)`) instead of blanket ssh/scp:
# the script decides what ships and where it lands, not the command line.
#
# usage: scripts/deploy-box.sh <host> [--dry-run]
#   <host>  ssh target, e.g. root@192.168.254.68 (key auth must already work)
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=${1:-}
DRY=no
[ "${2:-}" = --dry-run ] && DRY=yes
[ -n "$HOST" ] || { echo "usage: scripts/deploy-box.sh <root@host> [--dry-run]" >&2; exit 1; }

# The same set pipeos-deploy-overlay owns, plus etc/profile.d (overlay-shipped
# login config). etc/pipeos and root/.pipe are per-box state and are simply
# never in this list; etc/ssh is posture, changed deliberately, not by deploy.
PATHS="usr/local/bin usr/local/share/pipeos etc/init.d etc/periodic etc/local.d etc/profile.d etc/doas.d etc/apk/protected_paths.d/lbu.list root/.claude/CLAUDE.md"

commit=$(git rev-parse HEAD)
cdate=$(git show -s --format=%cs HEAD)
subject=$(git show -s --format=%s HEAD)
if ! git diff --quiet HEAD -- overlay/; then
    echo "NOTE: overlay/ has uncommitted changes — deploying HEAD ($commit), not them." >&2
fi

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

# HEAD, not the working tree: what ships is what is committed, reviewable,
# and reproducible — the discipline every deploy tool here shares.
for p in $PATHS; do
    git archive "$commit" -- "overlay/$p" 2>/dev/null | tar -C "$stage" -xf - || true
done
[ -d "$stage/overlay" ] || { echo "nothing to ship (bad HEAD?)" >&2; exit 1; }

# NEVER list (mirrors pipeos-deploy-overlay): per-box generated files must not
# ride a deploy. 10-pipebox-env.sh is card-generated on the box even though a
# copy lives in overlay/etc/profile.d — shipping it trips selfcheck's
# "hand-edited since generation" CRITICAL (basho_box, 2026-08-31).
rm -f "$stage/overlay/etc/profile.d/10-pipebox-env.sh"

. "$(dirname "$0")/lib-overlay-stamp.sh"
# shellcheck disable=SC2086  # PATHS is a word list on purpose
overlay_stamp "$stage/overlay" "$commit" HEAD "$cdate" "$(id -un)@$(hostname) (deploy-box push)" $PATHS \
    > "$stage/overlay-stamp"
manifest=$(mktemp)
grep -E '^[0-9a-f]{64}  ' "$stage/overlay-stamp" > "$manifest"

n=$(wc -l < "$manifest")
echo "deploy-box: $n files @ $commit ($cdate) -> $HOST"
echo "  $subject"
if [ "$DRY" = yes ]; then
    sed 's/^/  /' "$manifest"
    echo "dry run — nothing shipped."
    exit 0
fi

# --owner=0/--group=0 or the box's sshd dies: this tar is built by an
# unprivileged user, and without the override its directory entries carry
# that uid — extracting as root then chowns /, /root, /etc and /usr to a
# LAN workstation's user id, and sshd's StrictModes refuses root's key with
# "bad ownership or modes for directory /root". That was pipeOS#148: six
# lockouts, four power cycles, one long night — the deploy tool was the
# ghost. (40-build-apkovl.sh does the same dance for the same reason.)
tar -C "$stage/overlay" --numeric-owner --owner=0 --group=0 \
    -cf "$stage/payload.tar" .
# One remote transaction: extract, stamp, restart the web surface (its code
# just changed; supervised services keep the old inode otherwise — the same
# reason deploy-overlay restarts things), save, verify. pipe-daemon and the
# listener are deliberately NOT restarted here: a push deploy must not drop a
# live pipe session; restart them by hand if their code changed.
ssh "$HOST" '
set -e
tar -C / -xf - </dev/stdin
# the payload necessarily carries parent dir entries (./, ./root/, ./etc/…);
# owner is forced to 0 at creation, but their 755 mode still lands on
# extraction — put the credential home back the way the image ships it
chmod 700 /root
for svc in pipeos-web pipeos-mdns; do
    rc-service "$svc" status >/dev/null 2>&1 && rc-service "$svc" restart >/dev/null 2>&1 || true
done
' < "$stage/payload.tar"

# the stamp travels separately (it is not part of the overlay tree), then one
# save covers files + stamp together, and verify has the last word.
ssh "$HOST" '
cat > /etc/pipeos/.overlay-stamp
# keep the box checkout able to age the stamp (status/selfcheck compare the
# stamped commit against origin/main — a stale checkout reports "commit not
# in the local checkout" instead of an age)
[ -d /work/repos/pipeOS/.git ] && git -C /work/repos/pipeOS fetch -q origin 2>/dev/null || true
pipeos save >/dev/null 2>&1 || echo "WARNING: pipeos save failed — deploy is live in RAM only"
pipeos verify 2>&1 | tail -1
' < "$stage/overlay-stamp"
echo "deploy-box: done — stamped $commit on $HOST"
