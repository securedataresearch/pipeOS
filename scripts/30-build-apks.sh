#!/usr/bin/env bash
# Build the three pipeOS packages inside the Alpine chroot and assemble the
# signed local repo (out/repo/pipeos/x86_64) plus a pre-seeded apk cache
# (out/cache) so first boot needs no network.
set -euo pipefail
. "$(dirname "$0")/../config.sh"
CR="$PIPEOS_ROOT/scripts/chroot-run.sh"

mountpoint -q "$CHROOT/proc" || { echo "run 10-mk-chroot.sh first" >&2; exit 1; }

# ---------------------------------------------------------------- claude stage
# Run the official installer inside the chroot so it picks the musl artifact.
# This is the riskiest external dependency — do it first.
if [ ! -d "$OUT/claude-stage/.local/share/claude/versions" ]; then
    echo "==> staging Claude Code musl build via official install.sh"
    "$CR" 'apk add --quiet bash && mkdir -p /pipeOS/out/claude-stage && HOME=/pipeOS/out/claude-stage CLAUDE_INSTALL_ALLOW_SUDO=1 bash -c "curl -fsSL https://claude.ai/install.sh | bash"'
fi
# each entry in versions/ is the versioned binary itself
CLAUDE_VERSION=$(ls "$OUT/claude-stage/.local/share/claude/versions" | sort -V | tail -n1)
[ -n "$CLAUDE_VERSION" ] || { echo "claude staging failed" >&2; exit 1; }
file "$OUT/claude-stage/.local/share/claude/versions/$CLAUDE_VERSION" | grep -q 'ld-musl' \
    || { echo "staged claude is not a musl build" >&2; exit 1; }
echo "==> staged claude $CLAUDE_VERSION"

# ---------------------------------------------------------------- hermes vendor
echo "==> vendoring hermes-agent source"
mkdir -p "$PIPEOS_ROOT/vendor"
rsync -a --delete \
    --exclude .git --exclude venv --exclude __pycache__ --exclude '*.egg-info' \
    "$HERMES_SRC/" "$PIPEOS_ROOT/vendor/hermes-agent/"
# Alpine 3.24 ships Python 3.14; upstream pins <3.14. Its pinned deps all
# publish 3.14 wheels, so relax the ceiling in our vendored copy.
sed -i 's/requires-python = ">=3.11,<3.14"/requires-python = ">=3.11,<3.15"/' \
    "$PIPEOS_ROOT/vendor/hermes-agent/pyproject.toml"
grep -q '<3.15' "$PIPEOS_ROOT/vendor/hermes-agent/pyproject.toml" \
    || { echo "requires-python patch failed" >&2; exit 1; }
HERMES_VERSION=$(grep -m1 '^version' "$PIPEOS_ROOT/vendor/hermes-agent/pyproject.toml" | cut -d'"' -f2)

# ---------------------------------------------------------------- pipe payload
[ -f "$OUT/payloads/pipe" ] || { echo "run 20-build-pipe.sh first" >&2; exit 1; }
PIPE_VERSION=$(cat "$OUT/payloads/pipe.version")

# ---------------------------------------------------------------- abuild all
# Work copies live under out/pipeos/<pkg> so REPODEST repo name is "pipeos".
declare -A VERS=( [pipe]="$PIPE_VERSION" [claude-code]="$CLAUDE_VERSION" [hermes-agent]="$HERMES_VERSION" )
mkdir -p "$OUT/pipeos" "$OUT/repo"
for pkg in pipe claude-code hermes-agent; do
    mkdir -p "$OUT/pipeos/$pkg"
    sed "s/^pkgver=.*/pkgver=${VERS[$pkg]}/" "$PIPEOS_ROOT/aports/$pkg/APKBUILD" > "$OUT/pipeos/$pkg/APKBUILD"
done

# builder (chroot uid) must be able to write into the bind-mounted repo dirs,
# and into the vendored hermes tree (setuptools writes egg-info into the source
# dir while resolving build requirements)
chmod -R a+rwX "$OUT/pipeos" "$OUT/repo" "$PIPEOS_ROOT/vendor" 2>/dev/null || true

for pkg in pipe claude-code hermes-agent; do
    echo "==> abuild $pkg-${VERS[$pkg]}"
    "$CR" -u builder "cd /pipeOS/out/pipeos/$pkg && REPODEST=/pipeOS/out/repo abuild -r"
done

ls -lh "$OUT/repo/pipeos/$ALPINE_ARCH/"

# ---------------------------------------------------------------- extra repo
# The standard ISO's onboard repo only carries a subset of main — fetch every
# runtime dep into a second signed repo (apks/extra) on the boot partition.
# The fetch list IS overlay/etc/apk/world (single source of truth — a world
# entry with no fetched .apk is exactly how the image shipped a dangling
# chronyd service). The three pipeos-built packages come from apks/pipeos.
# github-cli lives in community, hence the second --repository.
echo "==> building extra repo with runtime deps (from overlay/etc/apk/world)"
UTILS=$(grep -vE '^(pipe|claude-code|hermes-agent)$' "$PIPEOS_ROOT/overlay/etc/apk/world")
mkdir -p "$OUT/repo/extra/$ALPINE_ARCH"; chmod -R a+rwX "$OUT/repo/extra" 2>/dev/null || true
"$CR" "apk fetch --recursive -o /pipeOS/out/repo/extra/$ALPINE_ARCH \
    --repository https://dl-cdn.alpinelinux.org/alpine/v3.24/community \
    $(echo $UTILS)"
"$CR" -u builder "cd /pipeOS/out/repo/extra/$ALPINE_ARCH && \
    apk index --rewrite-arch $ALPINE_ARCH -o APKINDEX.tar.gz *.apk && \
    abuild-sign APKINDEX.tar.gz"

echo "apk build complete"
