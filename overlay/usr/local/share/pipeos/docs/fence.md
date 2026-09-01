# The agent fence

A resident agent works inside a fence the machine enforces — the agent's own
settings and the pipe daemon's policy, both generated at setup. Whatever it
is asked, the agent cannot:

- **Touch the boot media** — no `lbu`, `apk`, `mount`, `dd`, partition
  tools, `/etc`, or the media itself.
- **Control services** — no `rc-*`, reboot, poweroff, `pipe shutdown`,
  `pipe set`.
- **Read credentials** — `/root/.pipe`, `/root/.ssh`, the GitHub and Claude
  credential files.
- **Act socially on its own** — no joining rooms or lobbies, adding
  contacts, or sending files on its own initiative. Those are yours to
  confirm.

It may always read and report the box's own health when you ask.

**A refusal is the box working.** When the agent answers that something is
outside its fence, that is the correct completion of the task — it reports
the boundary instead of routing around it. If you need the fenced thing
done, do it yourself in the dashboard or over SSH: the dashboard is the
control plane, and it is yours, not the agent's.
