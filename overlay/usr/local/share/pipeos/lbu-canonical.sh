#!/bin/sh
# lbu-canonical.sh — run lbu's status/diff against OUR apkovl (pipeOS#307, #318).
#
# lbu compares the live tree with <hostname>.apkovl.tar.gz in LBU_BACKUPDIR;
# pipeOS's canonical is pipeos.apkovl.tar.gz, which lbu never looks at, so a
# bare `lbu status` lists every file as Added forever. This hands lbu a tmpfs
# dir holding a hostname-named symlink to the canonical, with mount/umount
# shimmed for that dir (lbu.conf's LBU_MEDIA makes lbu try both).
#
# Sourced by `pipeos` (status, diff) and `pipeos-save` (the nothing-changed
# gate). Needs $OVL (the canonical) and $LBU (lbu, or a probe's stub) set.
# rc 2 + a reason on stderr when the comparison cannot be made.

lbu_canonical() {
    if [ "$(id -u)" != 0 ] && [ -z "${PIPEOS_LBU:-}" ]; then
        echo "compare needs root (lbu package reads root's files)" >&2; return 2
    fi
    if [ ! -f "$OVL" ] || ! tar -tzf "$OVL" >/dev/null 2>&1; then
        echo "no readable canonical apkovl on the media (pipeos verify)" >&2; return 2
    fi
    _d=$(mktemp -d) || return 2
    ln -s "$OVL" "$_d/$(hostname).apkovl.tar.gz"
    mkdir -p "$_d/bin"
    for _m in mount umount; do
        # shellcheck disable=SC2016  # the shim's own $_a/$@ must reach its file unexpanded
        printf '#!/bin/sh\nfor _a; do [ "$_a" = "%s" ] && exit 0; done\nexec /bin/%s "$@"\n' "$_d" "$_m" > "$_d/bin/$_m"
        chmod 755 "$_d/bin/$_m"
    done
    _out=$(PATH="$_d/bin:$PATH" LBU_BACKUPDIR=$_d "$LBU" "$@" 2>"$_d/err")
    _rc=$?
    _err=$(cat "$_d/err" 2>/dev/null)
    rm -rf "$_d"
    if [ -n "$_err" ]; then
        echo "lbu: $_err" >&2; return 2
    fi
    printf '%s\n' "$_out"
    return $_rc
}
