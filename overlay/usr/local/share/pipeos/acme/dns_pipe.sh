#!/bin/sh
# dns_pipe.sh — acme.sh DNS-01 hook for a pipeOS Machine (pipeOS#286).
#
# acme.sh calls dns_pipe_add FULLDOMAIN TXTVALUE before validation and
# dns_pipe_rm afterwards (`--dns /usr/local/share/pipeos/acme/dns_pipe.sh`;
# the function prefix is this file's basename). Each is one signed POST to
# the relay's /machines/dns, which writes only this Machine's own
# _acme-challenge record in the pipe.online zone. Environment from
# pipeos-tls-public: PIPEOS_PUBLIC_RELAY, PIPEOS_PUBLIC_MAC, PIPEOS_MACHINE_KEY.
#
# Same canonical bytes as every signed call to the relay
# (pipe-protocol/http_sign.rs, domain pipe-machine-v1).

_dp_hex() { od -An -tx1 | tr -d ' \n'; }

_dp_post() {   # BODY -> http status
    _path=/machines/dns
    _ts=$(date -u +%s); _nonce=$(head -c 16 /dev/urandom | _dp_hex)
    _msg=$(mktemp) || return 1
    printf '%s\n%s\n%s\n%s\n%s\n%s' "pipe-machine-v1" "$_path" "$PIPEOS_PUBLIC_MAC" "$_ts" "$_nonce" "$1" > "$_msg"
    _sig=$(openssl pkeyutl -sign -inkey "$PIPEOS_MACHINE_KEY" -rawin -in "$_msg" 2>/dev/null | _dp_hex)
    rm -f "$_msg"
    [ -n "$_sig" ] || return 1
    _code=$(curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -X POST "${PIPEOS_PUBLIC_RELAY%/}$_path" \
        -H "content-type: application/json" -H "x-pipe-machine: $PIPEOS_PUBLIC_MAC" \
        -H "x-pipe-ts: $_ts" -H "x-pipe-nonce: $_nonce" -H "x-pipe-sig: $_sig" --data-binary "$1" 2>/dev/null)
    [ "$_code" = 200 ]
}

dns_pipe_add() {   # fulldomain txtvalue
    _dp_post "{\"mac\": \"$PIPEOS_PUBLIC_MAC\", \"op\": \"add\", \"rr\": \"TXT\", \"name\": \"$1\", \"value\": \"$2\"}" \
        || { echo "dns_pipe: the relay refused the challenge record" >&2; return 1; }
    # give the zone a moment before acme.sh asks the resolvers
    _i=0
    while [ $_i -lt 12 ]; do
        dig +short +time=3 +tries=1 "$1" TXT @ns1.digitalocean.com 2>/dev/null | grep -q "$2" && return 0
        _i=$((_i + 1)); sleep 5
    done
    return 0
}

dns_pipe_rm() {    # fulldomain txtvalue
    _dp_post "{\"mac\": \"$PIPEOS_PUBLIC_MAC\", \"op\": \"rm\", \"rr\": \"TXT\", \"name\": \"$1\", \"value\": \"$2\"}"
    return 0
}
