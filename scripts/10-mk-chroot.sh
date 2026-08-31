#!/usr/bin/env bash
# Bootstrap an Alpine build chroot at out/chroot using apk.static (no docker).
# Idempotent: safe to re-run; skips bootstrap if the chroot already exists.
set -euo pipefail
. "$(dirname "$0")/../config.sh"

mkdir -p "$OUT"

# 1. Pick an apk: the host's own (Alpine host, e.g. pipeOS itself) or a
#    fetched apk.static (extracted unprivileged with bsdtar)
if command -v apk >/dev/null 2>&1; then
    APK=apk
elif [ -x "$OUT/apk.static" ]; then
    APK="$OUT/apk.static"
else
    echo "==> fetching apk-tools-static"
    apk_static_ver=$(curl -fsSL "$ALPINE_MAIN/$ALPINE_ARCH/" \
        | grep -o 'apk-tools-static-[0-9][^"]*\.apk' | sort -u | head -n1)
    [ -n "$apk_static_ver" ] || { echo "could not find apk-tools-static on mirror" >&2; exit 1; }
    curl -fsSL "$ALPINE_MAIN/$ALPINE_ARCH/$apk_static_ver" -o "$OUT/apk-tools-static.apk"
    bsdtar -xf "$OUT/apk-tools-static.apk" -C "$OUT" --strip-components=1 sbin/apk.static
    chmod +x "$OUT/apk.static"
    APK="$OUT/apk.static"
fi

# 2. Bootstrap the chroot
if [ ! -x "$CHROOT/bin/busybox" ]; then
    echo "==> bootstrapping Alpine chroot at $CHROOT"
    sudo "$APK" \
        -X "$ALPINE_MAIN" -X "$ALPINE_COMMUNITY" \
        -U --allow-untrusted -p "$CHROOT" --initdb --arch "$ALPINE_ARCH" \
        add alpine-base alpine-sdk sudo \
            python3 py3-pip python3-dev \
            gcc musl-dev libffi-dev openssl-dev \
            curl libgcc libstdc++ ripgrep
    printf '%s\n%s\n' "$ALPINE_MAIN" "$ALPINE_COMMUNITY" | sudo tee "$CHROOT/etc/apk/repositories" >/dev/null
fi

# 3. Mounts + resolv.conf (re-done every run; chroot-run.sh relies on these)
sudo mkdir -p "$CHROOT/proc" "$CHROOT/dev" "$CHROOT/pipeOS"
mountpoint -q "$CHROOT/proc" || sudo mount -t proc proc "$CHROOT/proc"
mountpoint -q "$CHROOT/dev"  || sudo mount --bind /dev "$CHROOT/dev"
mountpoint -q "$CHROOT/pipeOS" || sudo mount --bind "$PIPEOS_ROOT" "$CHROOT/pipeOS"
# pipe source, for on-box builds inside the chroot (20-build-pipe.sh fallback)
if [ -d "$PIPE_SRC" ]; then
    sudo mkdir -p "$CHROOT/pipe"
    mountpoint -q "$CHROOT/pipe" || sudo mount --bind "$PIPE_SRC" "$CHROOT/pipe"
fi
sudo cp /etc/resolv.conf "$CHROOT/etc/resolv.conf"

# 4. builder user + abuild signing key (one-time; resilient to partial runs)
#
# The copy at the bottom of this section only ever ran chroot -> out/keys.
# There was no way back: delete out/chroot — 1G, the first thing any disk
# sweep reaches for, and `make clean-chroot` does exactly that — and the
# keygen below minted a BRAND NEW key while the old one sat untouched in
# out/keys. A new key is not a rebuild, it is a fleet re-image: the sticks
# already flashed trust the old key and nothing else. So restore first,
# generate only when there is no key anywhere. (pipeOS#90)
ABUILD_DIR="$CHROOT/home/builder/.abuild"
# $SIGNING_KEY_DIR_ALT is the candidate config.sh did NOT pick — see the comment
# there. It is in the census for the same reason $ABUILD_DIR is: the store this
# run does not read from is exactly where a wrong-key surprise waits.
# An ARRAY end to end (pipeOS#111): the old space-joined string split a $HOME
# containing a space into nonexistent paths, and the census went quiet exactly
# where the wrong-key refusal was needed.
KEY_STORES=("$ABUILD_DIR" "$SIGNING_KEY_DIR" "$OUT/keys" "${SIGNING_KEY_DIR_ALT[@]}")

# Census first, over EVERY store, before we either read a key or write one.
# Guarding only the store we happen to restore from is not enough: a builder
# that already hit the bug above has a stray key in the chroot and the fleet
# key in out/keys, and the restore is skipped entirely because the chroot has
# a key. The backup at the bottom would then seed the STRAY key into the empty
# durable home, and every later run would restore it from there — one store,
# one key, guard silent, wrong key signing the fleet. So: if the stores
# disagree at all, stop and make a human choose. (pipeOS#90)
#
# `set -e` is on: every test here has to sit in a condition, or a store that
# simply does not exist yet takes the whole build down.
key_files=$(for d in "${KEY_STORES[@]}"; do
    ls "$d"/*.rsa 2>/dev/null || true
done)
key_names=$(printf '%s\n' "$key_files" \
    | while read -r f; do if [ -n "$f" ]; then basename "$f"; fi; done | sort -u)
n_keys=$(printf '%s\n' "$key_names" | grep -c . || true)
if [ "$n_keys" -gt 1 ]; then
    echo "$n_keys different abuild private keys across the key stores —" >&2
    echo "refusing to guess which one signs the fleet:" >&2
    for d in "${KEY_STORES[@]}"; do
        ls -l "$d"/*.rsa 2>/dev/null >&2 || true
    done
    echo "keep exactly one, put it in $SIGNING_KEY_DIR, and remove the others" >&2
    exit 1
fi

# The census above compares NAMES, and a name is not a key. Two stores holding
# `fleet@pipeos-1000000000.rsa` with different material disagree completely and
# count as one — which is the same shape as the bug this section exists to fix,
# one layer down. It is reachable the ordinary way: $SIGNING_KEY_DIR is
# hand-populated (the message above literally tells an operator to put a key
# there), and a truncated copy or a key pulled from the wrong backup lands
# under the name it always had. The restore is then skipped whenever the chroot
# holds *a* key, `cp $ABUILD_DIR/*.rsa* $OUT/keys/` overwrites the good copy,
# `cp -n` leaves the real key in the durable store where nothing will read it
# again, and 40-build-apkovl.sh:22 ships the impostor's public half into every
# stick's /etc/apk/keys. Silent, rc=0. So census the MATERIAL too, and make the
# comment above true as written: if the stores disagree at all, a human chooses.
# (box0 on #92)
_keysum=
for _c in sha256sum md5sum; do
    if [ -z "$_keysum" ] && command -v "$_c" >/dev/null 2>&1; then _keysum="$_c"; fi
done
if [ -z "$_keysum" ]; then
    # Same call as the openssl check below: a missing checker is not a reason
    # to refuse to build, but it IS a reason to say the guard is not running.
    echo "==> WARNING: no sha256sum or md5sum — the key stores are compared by" >&2
    echo "    NAME only; two stores could hold different keys under one name" >&2
elif [ "$n_keys" -eq 1 ]; then
    n_material=$(printf '%s\n' "$key_files" \
        | while read -r f; do if [ -n "$f" ]; then "$_keysum" "$f"; fi; done \
        | cut -d' ' -f1 | sort -u | grep -c . || true)
    if [ "$n_material" -gt 1 ]; then
        echo "the key stores hold $n_material DIFFERENT keys under one name" >&2
        echo "($key_names) — refusing to guess which one signs the fleet:" >&2
        for d in "${KEY_STORES[@]}"; do
            ls "$d"/*.rsa >/dev/null 2>&1 && "$_keysum" "$d"/*.rsa >&2 || true
        done
        echo "one of these is the key the flashed sticks already trust and the" >&2
        echo "others are not. Keep that one in $SIGNING_KEY_DIR with its .pub," >&2
        echo "remove the rest from the other stores, and re-run" >&2
        exit 1
    fi
fi

if ! ls "$ABUILD_DIR"/*.rsa >/dev/null 2>&1; then
    echo "==> creating builder user"
    sudo chroot "$CHROOT" /bin/sh -lc '
        adduser -D -s /bin/ash builder 2>/dev/null || true
        addgroup builder abuild 2>/dev/null || true
        mkdir -p /etc/sudoers.d
        echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
        install -d -o builder -g builder /home/builder/.abuild
    '
    key_src=
    # The alt store is LAST, so it changes nothing about which store wins when
    # more than one holds a key — the census has already established they hold
    # the same key, or the build stopped. It changes the one case that matters:
    # the key is ONLY in the candidate config.sh rejected, and without it here
    # the census passes (one key, one material) and this loop still finds
    # nothing, so the else branch mints a new key and re-images the fleet. The
    # backup at the bottom then copies it into $SIGNING_KEY_DIR with `cp -n`,
    # which is how a store that lost the tier test migrates to the winner
    # instead of being orphaned. (box3 on #92)
    for d in "$SIGNING_KEY_DIR" "$OUT/keys" "${SIGNING_KEY_DIR_ALT[@]}"; do
        if [ -z "$key_src" ] && ls "$d"/*.rsa >/dev/null 2>&1; then
            key_src="$d"
        fi
    done
    if [ -n "$key_src" ]; then
        # The census above already established there is one key name AND one
        # key's material in play, so this precedence picks a store, never a
        # key — which is the only reason a precedence order is safe here.
        priv="$key_names"
        echo "==> restoring abuild signing key $priv from $key_src"
        sudo sh -c "cp '$key_src'/*.rsa* '$ABUILD_DIR/'"
        # abuild-keygen -a writes this too; without it abuild-sign has a key
        # on disk it does not know about and signs with nothing.
        sudo sh -c "printf 'PACKAGER_PRIVKEY=\"/home/builder/.abuild/%s\"\n' '$priv' > '$ABUILD_DIR/abuild.conf'"
        # /bin/sh -lc, not a bare command: chroot resolves the command with
        # the HOST's PATH, which need not contain the chroot's busybox links
        # ("failed to run command 'chown'" on the first host this restore
        # branch ever actually ran on). Every other chroot call in this file
        # already goes through a login shell for the same reason.
        sudo chroot "$CHROOT" /bin/sh -lc 'chown -R builder:builder /home/builder/.abuild'
    else
        echo "==> no signing key in any store — generating a new one"
        echo "    (searched: $SIGNING_KEY_DIR $OUT/keys ${SIGNING_KEY_DIR_ALT[*]})"
        echo "    (every previously flashed stick will reject packages signed by it)"
        sudo chroot "$CHROOT" /bin/sh -lc 'su - builder -c "abuild-keygen -a -n"'
    fi
fi
# Whatever is in the chroot now — restored or freshly generated — is about to
# become the fleet's key twice over: 30-build-apks.sh signs the repo index with
# the private half, and 40-build-apkovl.sh copies the PUBLIC half straight into
# the apkovl's /etc/apk/keys, so every stick flashed after this trusts it.
#
# A pair that does not derive is exactly as fatal as a fresh key and looks like
# neither: the index is signed by one key, the sticks trust another, and the
# first symptom is an UNTRUSTED SIGNATURE at boot on hardware that is already
# in someone's hand. Nothing above catches it — the census compares FILENAMES,
# and `cp '$key_src'/*.rsa*` is a glob, so a store holding a .rsa and a .rsa.pub
# from different keygens (a half-finished copy, a restore from two backups)
# passes every check so far. One line closes it. (box2 on #92)
priv_file=$(ls "$ABUILD_DIR"/*.rsa 2>/dev/null | head -1 || true)
if [ -z "$priv_file" ]; then
    echo "no private key in $ABUILD_DIR after restore/keygen — cannot sign" >&2
    exit 1
elif [ ! -f "$priv_file.pub" ]; then
    echo "$priv_file has no .pub half — 40-build-apkovl.sh would ship nothing" >&2
    echo "for the sticks to trust; restore both halves or remove the private" >&2
    exit 1
elif command -v openssl >/dev/null 2>&1; then
    # `openssl pkey`, not `openssl rsa` (pipeOS#108): the pair check must not
    # assume the key's algorithm — pkey derives the public half of whatever
    # the private key actually is, and the byte-compare below is the measure.
    if sudo sh -c "openssl pkey -in '$priv_file' -pubout 2>/dev/null" \
        | cmp -s - "$priv_file.pub"; then
        echo "==> signing key pair verified: $(basename "$priv_file") derives its .pub"
    else
        echo "the private key and the .pub beside it are NOT a pair:" >&2
        ls -l "$priv_file" "$priv_file.pub" >&2
        echo "the repo index would be signed by one key and the sticks would" >&2
        echo "trust another. Restore both halves from one backup, or delete" >&2
        echo "the .pub and re-derive it: openssl pkey -in KEY -pubout > KEY.pub" >&2
        exit 1
    fi
else
    # Not fatal: openssl is not a build dependency today and refusing to build
    # over a missing checker would be a new failure mode on hosts that are fine.
    # Loud, because an unverified pair is the one thing this section cannot see.
    echo "==> WARNING: no openssl on this host — the key pair is UNVERIFIED" >&2
    echo "    (install openssl, or check by hand before flashing)" >&2
fi

# Back up in both directions, so a fresh keygen lands in the durable home too.
# The census at the top of this section is what makes the backup safe: it has
# already established that no store disagrees about which key that is.
mkdir -p "$OUT/keys"
# SIGNING_KEY_DIR defaults outside the repo, so unlike out/ it can land
# somewhere the build user cannot create — /work on a host that is not this
# appliance. Falling back to skipping it would quietly reinstate the bug this
# whole section exists to fix, so escalate once, then say what to set.
if [ ! -d "$SIGNING_KEY_DIR" ]; then
    mkdir -p "$SIGNING_KEY_DIR" 2>/dev/null \
        || sudo install -d -o "$USER" -m 700 "$SIGNING_KEY_DIR" \
        || { echo "cannot create the durable key store $SIGNING_KEY_DIR" >&2
             echo "set SIGNING_KEY_DIR to a path this build user can write" >&2
             exit 1; }
fi
sudo sh -c "cp $ABUILD_DIR/*.rsa* '$OUT/keys/'"
sudo sh -c "cp -n $ABUILD_DIR/*.rsa* '$SIGNING_KEY_DIR/'"
sudo chown "$USER" "$OUT/keys/"* "$SIGNING_KEY_DIR/"*
chmod 700 "$SIGNING_KEY_DIR"
# apk inside the chroot must also trust the key when installing test builds
sudo cp "$OUT/keys/"*.rsa.pub "$CHROOT/etc/apk/keys/"

echo "chroot ready: $CHROOT (helper: scripts/chroot-run.sh)"
