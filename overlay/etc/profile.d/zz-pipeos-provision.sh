# pipeOS first-boot provisioning. Sourced on interactive login (console or
# ssh). Runs once; sets the marker /etc/pipeos/provisioned when done.

_pipeos_provision() {
    [ -f /etc/pipeos/provisioned ] && return 0
    [ "$(id -u)" = 0 ] || return 0
    case "$-" in *i*) ;; *) return 0 ;; esac

    printf '\n=== pipeOS first-boot provisioning ===\n'
    printf 'This walkthrough runs once, then the system autosaves state every 15 min.\n\n'

    # 1. Replace the default root password (shipped as "pipeos")
    echo '--- 1/5: change the default root password'
    while ! passwd root; do echo 'try again'; done

    # 2. Optional ssh public key
    echo '--- 2/5: ssh authorized key (paste a public key, or leave empty to skip)'
    printf 'key> '; read -r _key
    if [ -n "$_key" ]; then
        mkdir -p /root/.ssh && chmod 700 /root/.ssh
        echo "$_key" >> /root/.ssh/authorized_keys
        chmod 600 /root/.ssh/authorized_keys
    fi

    # 3. Claude Code login
    echo '--- 3/5: Claude Code login (interactive; exit claude when done, or ctrl-c to skip)'
    printf 'run claude login now? [Y/n] '; read -r _a
    case "$_a" in n|N) ;; *) claude || true ;; esac

    # 4. pipe identity
    echo '--- 4/5: pipe identity (creates ~/.pipe on first run; ctrl-c to skip)'
    printf 'initialize pipe now? [Y/n] '; read -r _a
    case "$_a" in n|N) ;; *) pipe status || pipe --help || true ;; esac

    # 5. hermes API keys
    echo '--- 5/5: hermes API keys (stored in /root/.hermes/.env; empty to skip)'
    mkdir -p /root/.hermes
    for var in ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY; do
        printf '%s> ' "$var"; read -r _v
        [ -n "$_v" ] && echo "$var=$_v" >> /root/.hermes/.env
    done
    [ -f /root/.hermes/.env ] && chmod 600 /root/.hermes/.env

    touch /etc/pipeos/provisioned
    echo '--- committing state to NVMe (lbu commit)...'
    lbu commit && echo 'done.'
    printf '\npipeOS is provisioned. State autosaves every 15 min and at shutdown.\n'
    printf 'Manual save any time: lbu commit\n\n'
}

_pipeos_provision
unset -f _pipeos_provision
