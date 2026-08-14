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
if ! ls "$ABUILD_DIR"/*.rsa >/dev/null 2>&1; then
    echo "==> creating builder user"
    sudo chroot "$CHROOT" /bin/sh -lc '
        adduser -D -s /bin/ash builder 2>/dev/null || true
        addgroup builder abuild 2>/dev/null || true
        mkdir -p /etc/sudoers.d
        echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
        install -d -o builder -g builder /home/builder/.abuild
    '
    # `set -e` is on: every test here has to sit in a condition, or a store
    # that simply does not exist yet takes the whole build down.
    key_src=
    for d in "$SIGNING_KEY_DIR" "$OUT/keys"; do
        if [ -z "$key_src" ] && ls "$d"/*.rsa >/dev/null 2>&1; then
            key_src="$d"
        fi
    done
    if [ -n "$key_src" ]; then
        # More than one private key in the store means someone already hit the
        # bug above. Picking one silently is how you sign with the wrong key
        # and only find out on a box that will not install the package.
        n_keys=$(ls "$key_src"/*.rsa | wc -l)
        [ "$n_keys" = 1 ] || {
            echo "$key_src holds $n_keys private keys — refusing to guess which signs the fleet" >&2
            ls -l "$key_src"/*.rsa >&2
            exit 1
        }
        priv=$(basename "$(ls "$key_src"/*.rsa)")
        echo "==> restoring abuild signing key $priv from $key_src"
        sudo sh -c "cp '$key_src'/*.rsa* '$ABUILD_DIR/'"
        # abuild-keygen -a writes this too; without it abuild-sign has a key
        # on disk it does not know about and signs with nothing.
        sudo sh -c "printf 'PACKAGER_PRIVKEY=\"/home/builder/.abuild/%s\"\n' '$priv' > '$ABUILD_DIR/abuild.conf'"
        sudo chroot "$CHROOT" chown -R builder:builder /home/builder/.abuild
    else
        echo "==> no signing key in $SIGNING_KEY_DIR or $OUT/keys — generating a new one"
        echo "    (every previously flashed stick will reject packages signed by it)"
        sudo chroot "$CHROOT" /bin/sh -lc 'su - builder -c "abuild-keygen -a -n"'
    fi
fi
# Back up in both directions, so a fresh keygen lands in the durable home too.
mkdir -p "$OUT/keys" "$SIGNING_KEY_DIR"
sudo sh -c "cp $ABUILD_DIR/*.rsa* '$OUT/keys/'"
sudo sh -c "cp -n $ABUILD_DIR/*.rsa* '$SIGNING_KEY_DIR/'"
sudo chown "$USER" "$OUT/keys/"*
# apk inside the chroot must also trust the key when installing test builds
sudo cp "$OUT/keys/"*.rsa.pub "$CHROOT/etc/apk/keys/"

echo "chroot ready: $CHROOT (helper: scripts/chroot-run.sh)"
