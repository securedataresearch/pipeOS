# zz-pipeos-claude.sh — in a user's shell, `claude` is this Machine's one
# master Claude (pipeos-claude-attach); root gets the real binary.
if [ "$(id -u 2>/dev/null)" != 0 ]; then
	claude() { /usr/local/bin/pipeos-claude-attach "$@"; }
	[ -S /run/pipeos/assistant/tmux.sock ] && [ -t 1 ] && \
		echo "claude  -> this Machine's Claude (shared session; detach with ctrl-b d)"
fi
