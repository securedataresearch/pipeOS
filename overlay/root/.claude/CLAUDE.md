# pipeOS — machine notes

This host is **pipeOS**: a diskless Alpine Linux 3.24.1 appliance (x86_64, running as
root, hostname `pipeos`). It ships three custom packages: `claude-code`,
`hermes-agent`, `pipe`.

## 1. The root filesystem is tmpfs. Nothing you do persists by default.

Every boot rebuilds `/` from RAM:

1. initramfs finds the boot media by `LABEL=PIPEOS`, mounts it at `/media/usb`
2. it extracts `pipeos.apkovl.tar.gz` over the tmpfs root — this restores `/etc`
   and the specific `/root/...` paths listed in `/etc/apk/protected_paths.d/lbu.list`
3. `apk` then installs everything named in `/etc/apk/world`, resolving from
   `/etc/apk/repositories`

**A package survives reboot only if all three hold:**

- **(a)** its name is in the committed `/etc/apk/world`, **and**
- **(b)** its `.apk` *and every dependency's* is in a repo with a **valid signed
  index**, **and**
- **(c)** `lbu commit` has been run

Missing **(b)** is the silent failure mode: `apk add` works, `lbu commit` succeeds,
and after reboot the package is simply gone with a quiet unsatisfiable-constraint
line in the boot log. Always verify **across a reboot**, never just in the live
session.

Corollary: **every installed package costs RAM on every boot.** A toolchain like
`alpine-sdk` is ~300-400 MB resident; Go or Rust ~1 GB. Check `free -m` after any
`apk add`. Prefer building in a chroot on the ext4 workspace over installing into
the tmpfs root.

## 2. A file must be in a persisted path or it does not exist at boot

`/etc/apk/protected_paths.d/lbu.list` is the authority. `+` = saved into the
apkovl, `-` = excluded. **`/root` itself is not persisted** — only the explicit
`+root/...` entries. So:

- `/root/.claude/...` persists. `/root/CLAUDE.md` would **not**.
- Any directory outside `/etc` that must exist at boot needs either a `+` line in
  `lbu.list` or an `/etc/local.d/*.start` script that creates it. A plain
  `/etc/fstab` mount of a path that doesn't exist fails with "mount point does not
  exist".

## 3. `/media/usb` is vfat and mounted read-only

```
LABEL=PIPEOS  /media/usb  vfat  ro  0 0
```

- **Never check out a git repo onto it.** vfat has no symlinks, no exec bit, no
  ownership, and is case-insensitive. Git will be permanently dirty on mode
  changes, fail on any symlink, and can corrupt case-colliding paths.
- **Never persist a git tree or build artifacts into the apkovl.** With
  `BACKUP_LIMIT=3` that is 4 copies on this partition. Filling it during an
  `lbu commit` can truncate the apkovl — the main brick risk on this box.
- Remount rw only for repo/apkovl work, and remount ro immediately after:
  `mount -o remount,rw /media/usb` ... `sync` ... `mount -o remount,ro /media/usb`
- `df -h /media/usb` before every write.

## 4. apk is local-repo-only, and must stay that way

```
/media/usb/apks
/media/usb/apks/pipeos
/media/usb/apks/extra
```

**Do not add a network repo to `/etc/apk/repositories`.** The reason is mechanical,
not philosophical: boot-time `apk` runs from the initramfs *before* `networking`
has brought up eth0 and gotten a DHCP lease. A network repo means a full
resolver/connect timeout on every boot, every repo, and it silently reintroduces a
network dependency into an appliance built to have none.

Use the network as a **one-shot** to seed the local mirror instead:

```sh
mount -o remount,rw /media/usb
apk fetch --recursive --arch x86_64 -o /media/usb/apks/extra/x86_64 \
  --repository https://dl-cdn.alpinelinux.org/alpine/v3.24/main \
  <pkg> <its-deps-named-explicitly>
# re-index + sign (see §5), then:
apk add <pkg>
lbu commit
mount -o remount,ro /media/usb
```

The `/x86_64/` path element is required — apk appends `/$arch/` to each repo
entry, so `.apk` files sitting directly in `apks/extra/` are invisible to it.
Name dependencies explicitly: `apk fetch -R` has historically skipped deps that
are already installed on the running system.

## 5. apk-tools is 3.x, not 2.x

`/sbin/apk` links `libapk.so.3.0.0` and exports `apk_query_main` / `adb_*`.

This means the recipe in most Alpine documentation — `apk index` followed by
`abuild-sign` producing `APKINDEX.tar.gz` — **is the 2.x path and may not apply
here.** apk 3.x uses `apk mkndx` / `apk adbsign` and an ADB-format index.

Do not run a half-remembered index command against the boot media. Check
`apk mkndx --help` / `apk adbsign --help`, and **mirror
`/media/usb/apks/pipeos/x86_64/` exactly** — that is a working, boot-tested signed
local repo on this very machine, so its layout and index filename are ground truth.

The index must be *signed* by a key in `/etc/apk/keys`. `--allow-untrusted` is not
a workaround, because you do not control the flags the initramfs passes at boot.

## 6. lbu workflow — always dry-run before committing

```sh
lbu status                    # what has changed since the last commit
lbu package /tmp/probe        # write a CANDIDATE apkovl to tmpfs — free, non-destructive
tar -tzf /tmp/probe/pipeos.apkovl.tar.gz | grep <thing-you-expect>
lbu commit                    # only after the probe looks right
```

- `BACKUP_LIMIT=3`: three commits rotate the known-good apkovl out of existence.
  `pipeos.apkovl.tar.gz.baseline` sits outside that rotation — do not delete it.
- Change **one subsystem per commit**. A bad commit to `/etc/fstab`,
  `/etc/apk/repositories`, or `/etc/inittab` is the realistic way to lose a boot.
- Recovery: the media is vfat, so it mounts on any laptop. Restore
  `pipeos.apkovl.tar.gz.baseline` over `pipeos.apkovl.tar.gz`.

## 7. Known gaps (update as these are fixed)

Former gaps, all fixed and boot-verified (2026-08-04):

- `bash` is installed (real GNU bash 5.3, in `world`) — Claude Code's Bash tool
  works, and root's login shell is bash.
- Agent memory persists: `/root/.claude/projects` is a **symlink** to
  `/work/claude/projects` on the ext4 partition (`/dev/sda2` → `/work`), so
  transcripts and file-based memory survive reboot without touching the apkovl.
- `+root/.abuild` and `+root/.config/gh` are in `lbu.list`.
- Userland is GNU-ish now: `coreutils`, `findutils`, `diffutils`, `sed`, `gawk`,
  `gh`, plus `git`, `ripgrep`, `jq`, `python3`, `curl`, `vim`, `tmux`, `rsync`,
  `openssh`, network tooling.

Remaining caveats:

- **`/bin/grep` is ugrep 7.5.0, not GNU grep** (Alpine's `grep` package). Mostly
  compatible, but don't assume exact GNU semantics for exotic flags.
- `tar` is still busybox — deliberately, see §8.
- Still absent: `nodejs`, `alpine-sdk`/`abuild`. Builds happen in a chroot on the
  ext4 workspace (`/work`), not on the tmpfs root.

The full remediation plan is at `/root/.claude/plans/robust-riding-mountain.md`.

## 8. Don't install GNU tar

It lands at `/usr/bin/tar`, which precedes busybox's `/bin/tar` in `PATH`. `lbu`
would start using it — changing the behaviour of the one command that can brick
this box. Skip unless something genuinely requires it.
