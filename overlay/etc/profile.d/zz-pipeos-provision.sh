# pipeOS first-boot provisioning. Sourced on interactive login (console or
# ssh). Runs once; sets the marker /etc/pipeos/provisioned when done.
# After that, prints a one-line nudge until the pipe presence is configured.

_pipeos_provision() {
    [ "$(id -u)" = 0 ] || return 0
    case "$-" in *i*) ;; *) return 0 ;; esac

    if [ -f /etc/pipeos/provisioned ]; then
        # already provisioned — nudge only if the agent isn't reachable yet
        if PIPE_NO_SPAWN=1 pipe status 2>/dev/null | grep -q '^nick: anon' \
           || ! grep -q '^OWNER_NICK=".' /etc/pipeos/pipebox.conf 2>/dev/null; then
            printf '\n>>> your agent is not on pipe yet — run: pipebox-setup\n\n'
        fi
        return 0
    fi

    printf '\n=== pipeOS setup (runs once, ~3 minutes) ===\n\n'

    # 1. Replace the default root password (shipped as "pipeos")
    echo '--- 1/3: pick a root password'
    while ! passwd root; do echo 'try again'; done

    # 2. Optional ssh public key
    echo '--- 2/3: ssh key (paste a public key, or Enter to skip)'
    printf 'key> '; read -r _key
    if [ -n "$_key" ]; then
        mkdir -p /root/.ssh && chmod 700 /root/.ssh
        echo "$_key" >> /root/.ssh/authorized_keys
        chmod 600 /root/.ssh/authorized_keys
    fi

    # 3. Claude Code login (the agent's brain — without this nothing answers)
    echo '--- 3/3: connect Claude (a browser link appears; exit claude when done)'
    printf 'run claude login now? [Y/n] '; read -r _a
    case "$_a" in n|N) ;; *) claude || true ;; esac

    touch /etc/pipeos/provisioned
    # This save must be LOUD on failure (the same rule pipebox-setup step 6
    # already follows): it used to be `lbu commit >/dev/null 2>&1`, so a box
    # where persistence failed printed the same text as one where it worked,
    # and the new password + ssh key silently evaporated at the next reboot.
    if command -v pipeos-save >/dev/null 2>&1 && pipeos-save; then
        echo '(state saved)'
    elif lbu commit; then
        echo '(state saved — lbu fallback)'
    else
        echo '!!! STATE NOT SAVED — your password and key exist only in RAM and'
        echo '!!! are gone at the next reboot. The output above says why; fix it'
        echo '!!! and run: pipeos save'
    fi

    # The pipe presence: one key + one nick, handled by its own re-runnable wizard
    printf '\nLast thing — put your agent on pipe.\n'
    pipebox-setup

    printf '\npipeOS is ready. State autosaves every 15 min and at shutdown.\n\n'
}

_pipeos_provision
unset -f _pipeos_provision
