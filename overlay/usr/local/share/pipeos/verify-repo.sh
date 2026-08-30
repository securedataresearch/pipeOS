#!/bin/sh
# verify-repo.sh DIR... — is this apk repo coherent enough to boot from?
#
# For each repo dir (the arch level, e.g. .../pipeos/x86_64): every package
# the index names must exist as a file (here or in a sibling noarch/), no
# unreadable apks. Orphan files are reported but not fatal. Understands both
# index formats in use: apk-2 tar APKINDEX (pipeos repo, abuild-signed) and
# apk-3 ADB (extra repo, apk mkndx) — see repo CLAUDE.md on never mixing the
# two recipes. Exit nonzero = do NOT ship/boot from this repo.
#
# Shared check: the image build (50-build-image.sh) gates the repo->media
# copy on it, and pipeos sync-media gates the live media swap on it.

rc=0
for dir in "$@"; do
    idx="$dir/APKINDEX.tar.gz"
    if [ ! -f "$idx" ]; then
        echo "verify-repo: $dir: no APKINDEX.tar.gz" >&2
        rc=1
        continue
    fi
    tmpd=$(mktemp -d)
    bad_dir=0
    if tar -xzOf "$idx" APKINDEX > "$tmpd/raw" 2>/dev/null && [ -s "$tmpd/raw" ]; then
        awk -v RS='' '{p="";v="";n=split($0,L,"\n")
            for(i=1;i<=n;i++){if(L[i]~/^P:/)p=substr(L[i],3);if(L[i]~/^V:/)v=substr(L[i],3)}
            if(p!=""&&v!="")print p"-"v".apk"}' "$tmpd/raw" | sort -u > "$tmpd/indexed"
    elif apk adbdump "$idx" > "$tmpd/raw" 2>/dev/null && [ -s "$tmpd/raw" ]; then
        awk '/^ *- name: /{n=$3} /^ *version: /{if(n!=""){print n"-"$2".apk"; n=""}}' \
            "$tmpd/raw" | sort -u > "$tmpd/indexed"
    else
        echo "verify-repo: $dir: cannot parse index (neither apk-2 tar nor ADB)" >&2
        rc=1; rm -rf "$tmpd"
        continue
    fi
    # noarch subpackages live in a sibling dir but appear in this index
    { ls "$dir" 2>/dev/null; ls "$dir/../noarch" 2>/dev/null; } | \
        grep '\.apk$' | sort -u > "$tmpd/present"
    miss=$(comm -23 "$tmpd/indexed" "$tmpd/present" | tr '\n' ' ')
    orph=$(comm -13 "$tmpd/indexed" "$tmpd/present" | tr '\n' ' ')
    if [ -n "$miss" ]; then
        echo "verify-repo: $dir: indexed but missing on disk: $miss" >&2
        bad_dir=1
    fi
    [ -n "$orph" ] && echo "verify-repo: $dir: not in index (dead weight): $orph" >&2
    bad=""
    for f in "$dir"/*.apk "$dir/../noarch"/*.apk; do
        [ -f "$f" ] || continue
        tar -tzf "$f" >/dev/null 2>&1 || bad="$bad $(basename "$f")"
    done
    if [ -n "$bad" ]; then
        echo "verify-repo: $dir: unreadable apk(s):$bad" >&2
        bad_dir=1
    fi
    if [ "$bad_dir" = 0 ]; then
        echo "verify-repo: $dir: OK ($(wc -l < "$tmpd/indexed") indexed, $(wc -l < "$tmpd/present") on disk)"
    else
        rc=1
    fi
    rm -rf "$tmpd"
done
exit $rc
