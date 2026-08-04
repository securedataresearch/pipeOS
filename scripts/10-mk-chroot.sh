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
if ! ls "$CHROOT"/home/builder/.abuild/*.rsa >/dev/null 2>&1; then
    echo "==> creating builder user + abuild key"
    sudo chroot "$CHROOT" /bin/sh -lc '
        adduser -D -s /bin/ash builder 2>/dev/null || true
        addgroup builder abuild 2>/dev/null || true
        mkdir -p /etc/sudoers.d
        echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
        su - builder -c "abuild-keygen -a -n"
    '
fi
mkdir -p "$OUT/keys"
sudo sh -c "cp $CHROOT/home/builder/.abuild/*.rsa* '$OUT/keys/'"
sudo chown "$USER" "$OUT/keys/"*
# apk inside the chroot must also trust the key when installing test builds
sudo cp "$OUT/keys/"*.rsa.pub "$CHROOT/etc/apk/keys/"

echo "chroot ready: $CHROOT (helper: scripts/chroot-run.sh)"
