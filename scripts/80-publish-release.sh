#!/usr/bin/env bash
# 80-publish-release.sh — publish the signed pipeos apk repo as a GitHub
# Release, the fleet/client update origin (`make release`).
#
# This is the publisher half #116 found missing: the consumer
# (pipeos-selfupdate, UPDATE_RELEASE_URL mode) fetches SHA256SUMS as its cheap
# change probe, then pipeos-repo.tar.gz, verifies the digest, and feeds the
# existing verified/atomic/rollback pipeline. Publishing from the build
# workstation on purpose: the abuild signing key lives here (config.sh,
# outside the repo) and never reaches CI.
#
# Trust model: public hosting leaks nothing that matters — every apk and the
# index are signed by the fleet key, and each box's apk verifies signatures
# against its own /etc/apk/keys at install time. SHA256SUMS only protects
# transport integrity/atomicity of the snapshot.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

REPO_DIR="$OUT/repo/pipeos/x86_64"
[ -f "$REPO_DIR/APKINDEX.tar.gz" ] || { echo "no built repo at $REPO_DIR — run make apks" >&2; exit 1; }

"$PIPEOS_ROOT/scripts/verify-repo.sh" "$REPO_DIR"

# The tag names the content: date plus the repo state that built it.
sha=$(git -C "$PIPEOS_ROOT" rev-parse --short HEAD)
if ! git -C "$PIPEOS_ROOT" diff --quiet HEAD; then
    echo "WARNING: working tree is dirty — the tag will name $sha but the apks may not match it." >&2
fi
tag="repo-$(date -u +%Y.%m.%d)-$sha"

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
tar -C "$REPO_DIR" -czf "$stage/pipeos-repo.tar.gz" .
(cd "$stage" && sha256sum pipeos-repo.tar.gz > SHA256SUMS)

pkgs=$(tar -tzf "$stage/pipeos-repo.tar.gz" | grep -c '\.apk$')
notes="Signed pipeos apk repo snapshot ($pkgs packages) built at $sha.
Consumed by pipeos-selfupdate (UPDATE_RELEASE_URL mode); boxes verify the
sha256 in transit and every package signature at install."

echo "publishing $tag ($pkgs packages) ..."
gh release create "$tag" \
    "$stage/pipeos-repo.tar.gz" "$stage/SHA256SUMS" \
    --title "$tag" --notes "$notes" --latest
echo "published: boxes with UPDATE_RELEASE_URL pointed at .../releases/latest/download pick it up on their next daily run."
