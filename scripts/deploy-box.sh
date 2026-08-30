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
PATHS="usr/local/bin usr/local/share/pipeos etc/init.d etc/periodic etc/local.d etc/profile.d etc/apk/protected_paths.d/lbu.list root/.claude/CLAUDE.md"

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

manifest=$(mktemp)
(cd "$stage/overlay" && find . -type f | sed 's|^\./||' | sort | while read -r f; do
    sha256sum "$f" | sed 's|  \./|  |'
done) > "$manifest"

n=$(wc -l < "$manifest")
echo "deploy-box: $n files @ $commit ($cdate) -> $HOST"
echo "  $subject"
if [ "$DRY" = yes ]; then
    sed 's/^/  /' "$manifest"
    echo "dry run — nothing shipped."
    exit 0
fi

{
    echo "commit $commit"
    echo "ref HEAD"
    echo "commit_date $cdate"
    echo "deployed_at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "deployed_by $(id -un)@$(hostname) (deploy-box push)"
    echo "#  sha256 of every file this deploy is responsible for"
    cat "$manifest"
} > "$stage/overlay-stamp"

tar -C "$stage/overlay" -cf "$stage/payload.tar" .
# One remote transaction: extract, stamp, restart the web surface (its code
# just changed; supervised services keep the old inode otherwise — the same
# reason deploy-overlay restarts things), save, verify. pipe-daemon and the
# listener are deliberately NOT restarted here: a push deploy must not drop a
# live pipe session; restart them by hand if their code changed.
ssh "$HOST" '
set -e
tar -C / -xf - </dev/stdin
for svc in pipeos-web pipeos-mdns; do
    rc-service "$svc" status >/dev/null 2>&1 && rc-service "$svc" restart >/dev/null 2>&1 || true
done
' < "$stage/payload.tar"

# the stamp travels separately (it is not part of the overlay tree), then one
# save covers files + stamp together, and verify has the last word.
ssh "$HOST" '
cat > /etc/pipeos/.overlay-stamp
pipeos save >/dev/null 2>&1 || echo "WARNING: pipeos save failed — deploy is live in RAM only"
pipeos verify 2>&1 | tail -1
' < "$stage/overlay-stamp"
echo "deploy-box: done — stamped $commit on $HOST"
