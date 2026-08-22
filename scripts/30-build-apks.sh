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

# ------------------------------------------------------- antigravity stage
# Optional payload (WITH_ANTIGRAVITY=1, see config.sh). Two things are staged:
# a pinned glibc sysroot and the vendor's glibc-only CLI binary.
#
# NOTE the asymmetry with the claude stage above, which is the whole reason
# this block exists rather than another install.sh call: claude's installer is
# asked for a musl build and asserted to have produced one. Antigravity's
# installer WOULD detect musl correctly and then fail, because upstream
# publishes no musl artifact — so the manifest is fetched directly and the
# binary is asserted to be glibc. Two opposite assertions, both present, so a
# silent upstream swap in either direction fails the build instead of shipping.
if [ "$WITH_ANTIGRAVITY" = 1 ]; then
    # -- glibc sysroot ------------------------------------------------------
    # Pinned by content, not by URL: a release asset can be re-uploaded.
    GLIBC_VERSION=2.35
    GLIBC_APK_URL="https://github.com/sgerrand/alpine-pkg-glibc/releases/download/2.35-r1/glibc-2.35-r1.apk"
    GLIBC_APK_SHA256=276f43ce9b2d5878422bca94ca94e882a7eb263abe171d233ac037201ffcaf06
    if [ ! -d "$OUT/glibc-stage/usr/glibc-compat" ]; then
        echo "==> staging glibc sysroot $GLIBC_VERSION (alpine-pkg-glibc)"
        mkdir -p "$OUT/glibc-stage"
        curl -fsSL "$GLIBC_APK_URL" -o "$OUT/glibc-stage/glibc.apk"
        echo "$GLIBC_APK_SHA256  $OUT/glibc-stage/glibc.apk" | sha256sum -c - \
            || { echo "glibc apk sha256 mismatch — refusing to package" >&2; exit 1; }
        # Only the glibc-compat prefix. The archive also carries a root-level
        # /lib/ld-linux-x86-64.so.2 that would make glibc the system loader.
        #
        # stderr to a file rather than the console: an apk is a tar with
        # APK-TOOLS.checksum.SHA1 extended headers, and GNU tar warns about
        # every one of them — 39 lines of noise that would train a reader to
        # skip the place real extraction errors appear. Shown in full on failure.
        tar -xzf "$OUT/glibc-stage/glibc.apk" -C "$OUT/glibc-stage" usr/glibc-compat \
            2>"$OUT/glibc-stage/tar.log" \
            || { cat "$OUT/glibc-stage/tar.log" >&2; echo "glibc extract failed" >&2; exit 1; }
    fi
    [ -x "$OUT/glibc-stage/usr/glibc-compat/lib/ld-linux-x86-64.so.2" ] \
        || { echo "glibc staging produced no loader" >&2; exit 1; }
    # The NSS modules are what make getaddrinfo work inside a glibc binary;
    # their absence is the classic bundled-glibc "DNS silently broken" failure.
    for _nss in libnss_dns.so.2 libnss_files.so.2; do
        [ -e "$OUT/glibc-stage/usr/glibc-compat/lib/$_nss" ] \
            || { echo "glibc staging is missing $_nss" >&2; exit 1; }
    done
    echo "==> staged glibc sysroot $GLIBC_VERSION"

    # -- antigravity binary -------------------------------------------------
    AGY_MANIFEST_URL="https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json"
    # Deliberately NOT jq: it is absent from scripts/00-host-setup.sh, so using
    # it here would add a silent host dependency that only shows up on whichever
    # machine happens not to have it. Three string reads out of a flat document
    # do not justify that; upstream's own install.sh parses this same manifest
    # with sed for the same reason. Every read is asserted non-empty below,
    # because the failure mode of a bad pattern is an empty string, and an empty
    # URL would otherwise sail into curl.
    _agy_key() {
        sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" \
            "$OUT/antigravity-stage/manifest.json" | head -n1
    }
    # THE STAMP IS WHY THIS IS NOT A SIMPLE "download if absent".
    #
    # The version was read from the manifest while the binary was reused from
    # whatever a previous run had left in place. Those are two different
    # artifacts, and upstream moves: staged 1.1.13 against a manifest that had
    # advanced to 1.1.18 would have built `antigravity-cli-1.1.18-r0.apk`
    # containing the 1.1.13 binary. A package whose version lies about its
    # payload, signed by us, shipped to strangers — and nothing would have
    # failed, because every individual step succeeded.
    #
    # So the staged payload carries a stamp of what it actually is, and the
    # version always comes from the same fetch as the bytes.
    mkdir -p "$OUT/antigravity-stage"
    _agy_stamp="$OUT/antigravity-stage/staged.version"
    if curl -fsSL "$AGY_MANIFEST_URL" -o "$OUT/antigravity-stage/manifest.json.new" 2>/dev/null; then
        mv "$OUT/antigravity-stage/manifest.json.new" "$OUT/antigravity-stage/manifest.json"
        AGY_VERSION=$(_agy_key version)
    else
        # Offline builds are legitimate (a box rebuilding itself), but only
        # against a payload that already said what it was. No stamp, no guess.
        rm -f "$OUT/antigravity-stage/manifest.json.new"
        AGY_VERSION=$(cat "$_agy_stamp" 2>/dev/null || true)
        [ -n "$AGY_VERSION" ] || { echo "cannot reach the antigravity manifest and nothing is staged" >&2; exit 1; }
        echo "==> antigravity: offline, using staged $AGY_VERSION"
    fi
    [ -n "$AGY_VERSION" ] || { echo "antigravity staging failed" >&2; exit 1; }
    if [ ! -f "$OUT/antigravity-stage/antigravity" ] \
       || [ "$(cat "$_agy_stamp" 2>/dev/null || true)" != "$AGY_VERSION" ]; then
        echo "==> staging Antigravity CLI $AGY_VERSION from vendor manifest"
        AGY_URL=$(_agy_key url)
        AGY_SHA512=$(_agy_key sha512)
        [ -n "$AGY_URL" ] && [ -n "$AGY_SHA512" ] \
            || { echo "could not read url/sha512 from the antigravity manifest" >&2; exit 1; }
        curl -fsSL "$AGY_URL" -o "$OUT/antigravity-stage/agy.tar.gz"
        echo "$AGY_SHA512  $OUT/antigravity-stage/agy.tar.gz" | sha512sum -c - \
            || { echo "antigravity payload sha512 mismatch — refusing to package" >&2; exit 1; }
        rm -f "$OUT/antigravity-stage/antigravity"
        tar -xzf "$OUT/antigravity-stage/agy.tar.gz" -C "$OUT/antigravity-stage" antigravity
        # Written LAST, so an interrupted stage leaves no stamp and re-stages
        # rather than claiming a version it does not have.
        echo "$AGY_VERSION" > "$_agy_stamp"
    fi
    # THE SYSROOT MUST ACTUALLY BE NEW ENOUGH FOR THE BINARY IT CARRIES.
    #
    # This was an unstated assumption until it moved under us: 1.1.13 required
    # at most GLIBC_2.25, 1.1.18 requires 2.26. The sysroot is 2.35, so both
    # are fine — but nothing compared them, and the vendor bumps this whenever
    # they like. If a future release crosses 2.35 the build would succeed, the
    # apk would install, and the box would fail at RUNTIME with a symbol-not-
    # found — the failure landing on a customer rather than here.
    #
    # Read with grep, not objdump/readelf: binutils is not in
    # scripts/00-host-setup.sh, and this must not become a host dependency
    # discovered on whichever machine lacks it. Verified to agree with
    # `objdump -T` on the binary it was written against.
    _agy_need=$(grep -ao 'GLIBC_[0-9]\+\.[0-9]\+' "$OUT/antigravity-stage/antigravity" \
        | sed 's/GLIBC_//' \
        | awk -F. '{v=$1*1000+$2; if(v>m){m=v;s=$0}} END{print s}')
    [ -n "$_agy_need" ] || { echo "could not read the glibc requirement of the staged antigravity binary" >&2; exit 1; }
    if ! awk -v need="$_agy_need" -v have="$GLIBC_VERSION" '
        BEGIN{ split(need,n,"."); split(have,h,".");
               exit !((n[1]*1000+n[2]) <= (h[1]*1000+h[2])) }'; then
        echo "antigravity $AGY_VERSION needs glibc $_agy_need but the sysroot is $GLIBC_VERSION" >&2
        echo "  -> bump pipeos-glibc, or (better) check whether upstream now ships a musl build" >&2
        exit 1
    fi
    echo "==> antigravity needs glibc $_agy_need, sysroot provides $GLIBC_VERSION"

    # The mirror of the claude assertion 30 lines up. If upstream ever starts
    # serving musl here, this fails loudly and the right fix is to delete this
    # whole block and pipeos-glibc with it — not to relax the check.
    file "$OUT/antigravity-stage/antigravity" | grep -q 'ld-linux-x86-64' \
        || { echo "staged antigravity is not the glibc build this packaging assumes" >&2; exit 1; }
    echo "==> staged antigravity $AGY_VERSION"
fi

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
if [ "$WITH_ANTIGRAVITY" = 1 ]; then
    VERS[pipeos-glibc]="$GLIBC_VERSION"
    VERS[antigravity-cli]="$AGY_VERSION"
fi
mkdir -p "$OUT/pipeos" "$OUT/repo"
for pkg in $PIPEOS_PKGS; do
    mkdir -p "$OUT/pipeos/$pkg"
    sed "s/^pkgver=.*/pkgver=${VERS[$pkg]}/" "$PIPEOS_ROOT/aports/$pkg/APKBUILD" > "$OUT/pipeos/$pkg/APKBUILD"
done

# builder (chroot uid) must be able to write into the bind-mounted repo dirs,
# and into the vendored hermes tree (setuptools writes egg-info into the source
# dir while resolving build requirements)
chmod -R a+rwX "$OUT/pipeos" "$OUT/repo" "$PIPEOS_ROOT/vendor" 2>/dev/null || true
# the staged payloads are read by abuild's package() as the builder uid
[ "$WITH_ANTIGRAVITY" = 1 ] && chmod -R a+rX "$OUT/glibc-stage" "$OUT/antigravity-stage" 2>/dev/null || true

for pkg in $PIPEOS_PKGS; do
    echo "==> abuild $pkg-${VERS[$pkg]}"
    "$CR" -u builder "cd /pipeOS/out/pipeos/$pkg && REPODEST=/pipeOS/out/repo abuild -r"
done

ls -lh "$OUT/repo/pipeos/$ALPINE_ARCH/"

# ---------------------------------------------------------------- extra repo
# The standard ISO's onboard repo only carries a subset of main — fetch every
# runtime dep into a second signed repo (apks/extra) on the boot partition.
# The fetch list IS overlay/etc/apk/world (single source of truth — a world
# entry with no fetched .apk is exactly how the image shipped a dangling
# chronyd service). Our own packages come from apks/pipeos, so they are
# subtracted here rather than looked for on the CDN.
# github-cli lives in community, hence the second --repository.
#
# The exclusion list is $PIPEOS_PKGS, not a literal — CLAUDE.md asks for world
# and this list to be kept "in lockstep", and a hardcoded alternation is a
# second place to forget. Adding a package to config.sh now updates both.
echo "==> building extra repo with runtime deps (from overlay/etc/apk/world)"
UTILS=$(grep -vxF -f <(printf '%s\n' $PIPEOS_PKGS) "$PIPEOS_ROOT/overlay/etc/apk/world")
mkdir -p "$OUT/repo/extra/$ALPINE_ARCH"; chmod -R a+rwX "$OUT/repo/extra" 2>/dev/null || true
"$CR" "apk fetch --recursive -o /pipeOS/out/repo/extra/$ALPINE_ARCH \
    --repository https://dl-cdn.alpinelinux.org/alpine/v3.24/community \
    $(echo $UTILS)"
"$CR" -u builder "cd /pipeOS/out/repo/extra/$ALPINE_ARCH && \
    apk index --rewrite-arch $ALPINE_ARCH -o APKINDEX.tar.gz *.apk && \
    abuild-sign APKINDEX.tar.gz"

echo "apk build complete"
