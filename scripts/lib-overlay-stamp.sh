#!/usr/bin/env bash
# lib-overlay-stamp.sh — the one writer of an overlay stamp's body.
#
# /etc/pipeos/.overlay-stamp answers "which overlay is this box running, and
# is any of it hand-edited since?" (pipeOS#97). Three things write one:
# pipeos-deploy-overlay on the box, scripts/deploy-box.sh from a workstation,
# and — since the live-disk work (#179) — the image build itself, so a box
# flashed from a published image is not "an overlay of unknown age" the
# moment it boots. One function, sourced by the two host-side writers, so the
# format cannot drift between them. (pipeos-deploy-overlay keeps its own copy
# of the same eight lines: it ships on the box and cannot source this file.)
#
#   overlay_stamp ROOT COMMIT REF CDATE BY PATHS...
#
# ROOT is a staged overlay root (its usr/, etc/, root/ live directly under
# it); PATHS are the overlay-relative paths the stamp is responsible for.
# Emits the stamp on stdout: five header lines, a comment, then one
# "<sha256>  <path>" line per regular file, sorted by path — exactly what
# pipeos-deploy-overlay writes.
overlay_stamp() {
    local root=$1 commit=$2 ref=$3 cdate=$4 by=$5
    shift 5
    echo "commit $commit"
    echo "ref $ref"
    echo "commit_date $cdate"
    echo "deployed_at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "deployed_by $by"
    echo "#  sha256 of every file this deploy is responsible for"
    local p
    for p in "$@"; do
        [ -e "$root/$p" ] || continue
        if [ -d "$root/$p" ]; then
            (cd "$root" && find "$p" -type f)
        else
            echo "$p"
        fi
    done | sort -u | while read -r f; do
        # relative paths, as the stamp has always carried them
        (cd "$root" && sha256sum "$f")
    done
}
