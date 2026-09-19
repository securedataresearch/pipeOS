#!/usr/bin/env python3
"""Probe for `pipeos unclaim` (single-box pass on zero, 2026-09-16): nobody's
box means nobody's. A fake /etc/pipeos, /root and /run tree; stubs for the
card generator, tls-init, the saver and reboot record what they were asked.
Exit 0 if every row passes.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PIPEOS = os.path.join(REPO, "overlay/usr/local/bin/pipeos")
SAVE = os.path.join(REPO, "overlay/usr/local/bin/pipeos-save")
RESULTS = []


def check(desc, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % detail))


D = tempfile.mkdtemp(prefix="ckunclaim-")
ETC, HOME, RUN, BIN, MARK = (os.path.join(D, x) for x in ("etc", "root", "run", "bin", "marks"))
for d in (ETC, HOME + "/.pipe/inbox", HOME + "/.claude", HOME + "/.hermes", RUN + "/web-sessions", RUN + "/secrets", BIN, MARK, ETC + "/tls"):
    os.makedirs(d, exist_ok=True)
owner = {"web-admin.conf": "HASH='x'\n", "users.json": "{}", "terminals.conf": "", "services.conf": "SERVICE_PIPE=on\n",
         "schedule.json": '{"jobs":[{"name":"nightly"}]}', "nas.conf": "NAS_S1_NAME='office'\n", "mounts.conf": "MOUNT_UUIDS=''\n",
         "assistant.conf": "x", "stream.conf": "x", "vault.sealed": "sealed", "support_key.pub": "ssh-ed25519 AAAA",
         "support.conf": "SUPPORT_RELAY=tunnel@relay\nSUPPORT_PORT=42001\n", "cluster.json": '{"v":2}', "provisioned": "",
         "selfupdate.conf": "IMAGE_UPDATE=auto\n", "card.conf": "NICK=box0\nNAME=zero\nROLE=GENERIC\nOWNER_NICK=sam\n"}
for k, v in owner.items():
    open(os.path.join(ETC, k), "w").write(v)
open(ETC + "/tls/ca.key", "w").write("old ca")
for k in ("identity.dat", "credentials.dat", "config.dat", "contacts.dat", "http.json", "policy.json"):
    open(HOME + "/.pipe/" + k, "w").write("x")
open(HOME + "/.pipe/inbox/msg", "w").write("x")
open(HOME + "/.claude/.credentials.json", "w").write("x"); open(HOME + "/.claude.json", "w").write("x")
open(HOME + "/.hermes/auth.json", "w").write("x")
open(RUN + "/cluster-ca.pem", "w").write("x"); open(RUN + "/cluster.status", "w").write("x")
open(ETC + "/vault-requests.json", "w").write('{"v":1,"requests":[{"id":"x","name":"jobs.old","why":"the old owner"}]}')
for name, body in (("pipebox-card", 'echo "$*" >> %s/card.log\n' % MARK),
                   ("pipeos-tls-init", 'echo tls >> %s/tls.log\nmkdir -p %s/tls && echo new > %s/tls/ca.key\n' % (MARK, ETC, ETC)),
                   ("pipeos-save", 'echo "UNCLAIM=${PIPEOS_SAVE_UNCLAIM:-} provisioned=$([ -f %s/provisioned ] && echo yes || echo no)" >> %s/save.log\n' % (ETC, MARK)),
                   ("reboot", 'echo reboot >> %s/reboot.log\n' % MARK)):
    with open(os.path.join(BIN, name), "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(os.path.join(BIN, name), 0o755)
WORK = os.path.join(D, "work")
for d in ("/.pipeos/ledger", "/.pipeos/schedule", "/pipebox/sessions", "/pipebox/webchat", "/pipebox/state", "/pipebox/jobs/drill", "/claude/projects/p1",
          "/claude/projects/-root/memory", "/backup/pipe", "/logs", "/home/office", "/repos/proj"):
    os.makedirs(WORK + d, exist_ok=True)
for f in ("/.pipeos/ledger/2026-09.jsonl", "/.pipeos/schedule/runs.log", "/claude/projects/p1/s.jsonl", "/claude/projects/-root/memory/MEMORY.md",
          "/pipebox/jobs/drill/prompt", "/backup/pipe/credentials.dat", "/backup/pipeos.apkovl.20260914.tar.gz",
          "/logs/selfcheck.log", "/.authorized_keys.backup", "/home/office/doc.txt", "/repos/proj/README", "/.pipeos/users.manifest"):
    open(WORK + f, "w").write("x")
os.makedirs(HOME + "/.ssh", exist_ok=True); open(HOME + "/.ssh/authorized_keys", "w").write("ssh-ed25519 AAAA old-owner\n")
env = dict(os.environ, PATH=BIN + ":" + os.environ["PATH"], PIPEOS_ETC=ETC, PIPEOS_ROOT_HOME=HOME, PIPEOS_RUN=RUN, PIPEOS_WORK=WORK,
           PIPEOS_TLS_INIT=BIN + "/pipeos-tls-init", PIPEOS_REBOOT_BIN=BIN + "/reboot", PIPEOS_CARD_GEN=BIN + "/pipebox-card",
           PIPEOS_SAVE_BIN=BIN + "/pipeos-save", PIPEOS_UNCLAIM_TEST="1")
p = subprocess.run(["sh", PIPEOS, "unclaim", "--yes"], capture_output=True, text=True, env=env)


def mark(n):
    f = os.path.join(MARK, n)
    return open(f).read() if os.path.exists(f) else ""


gone = ["web-admin.conf", "users.json", "terminals.conf", "services.conf", "schedule.json", "nas.conf", "mounts.conf",
        "assistant.conf", "stream.conf", "vault.sealed", "vault-requests.json", "support_key.pub", "cluster.json", "provisioned"]
left = [k for k in gone if os.path.exists(os.path.join(ETC, k))]
check("1 the owner's records go: claim, users, terminals, services, jobs, shares, mounts, assistant, stream, vault, support key, cluster membership, the provisioned marker",
      p.returncode == 0 and not left, "rc=%s left=%r out=%s" % (p.returncode, left, (p.stdout + p.stderr)[-300:]))
card = open(ETC + "/card.conf").read()
check("2 the card is nobody's (NAME, OWNER_NICK, and NICK on a GENERIC box cleared) and regenerated; selfupdate.conf and the card itself stay",
      "NAME=\n" in card and "OWNER_NICK=\n" in card and "NICK=\n" in card and "generate" in mark("card.log") and os.path.exists(ETC + "/selfupdate.conf"))
pipe_left = [k for k in ("identity.dat", "credentials.dat", "config.dat", "contacts.dat", "http.json", "inbox") if os.path.exists(HOME + "/.pipe/" + k)]
check("3 the sign-ins go: pipe identity, credentials, contacts and inbox; Claude's credentials and ~/.claude.json; hermes auth — policy.json (card-generated) stays",
      not pipe_left and not os.path.exists(HOME + "/.claude/.credentials.json") and not os.path.exists(HOME + "/.claude.json")
      and not os.path.exists(HOME + "/.hermes/auth.json") and os.path.exists(HOME + "/.pipe/policy.json"), repr(pipe_left))
check("4 the support port is unpinned but the relay setting stays; the cluster trust bundle and status go; the TLS dir is re-made with a NEW CA",
      "SUPPORT_PORT=\n" in open(ETC + "/support.conf").read() and "SUPPORT_RELAY=tunnel@relay" in open(ETC + "/support.conf").read()
      and not os.path.exists(RUN + "/cluster-ca.pem") and not os.path.exists(RUN + "/cluster.status")
      and open(ETC + "/tls/ca.key").read().strip() == "new" and "tls" in mark("tls.log"))
check("5 sessions and materialised secrets go; then ONE save in unclaim mode with the provisioned marker already gone; then the reboot",
      not os.path.exists(RUN + "/web-sessions") and not os.path.exists(RUN + "/secrets")
      and mark("save.log").strip() == "UNCLAIM=1 provisioned=no" and mark("reboot.log").strip() == "reboot", repr((mark("save.log"), mark("reboot.log"))))
save = open(SAVE).read()
check("6 pipeos-save's unclaim mode skips the provisioned guard, never short-circuits on identical content, writes the known-good too, and removes the rotations and the hostname-named apkovl",
      '[ -f /etc/pipeos/provisioned ] || [ -n "$UNCLAIM" ] || exit 0' in save and 'if [ -z "$UNCLAIM" ] && [ -f "$OVL" ]' in save
      and 'mv "$KNOWN_GOOD.new" "$KNOWN_GOOD"' in save and 'rm -f "$MEDIA"/pipeos.[0-9]*.tar.gz' in save
      and save.index('rm -f "$MEDIA/$(hostname).apkovl.tar.gz"') < save.index('if [ -n "$UNCLAIM" ]; then\n    # nobody')
      and not re.search(r"^\s*lbu commit", open(PIPEOS).read(), re.M))
gone_w = [f for f in ("/.pipeos/ledger/2026-09.jsonl", "/.pipeos/schedule/runs.log", "/pipebox/sessions", "/pipebox/webchat", "/pipebox/state", "/pipebox/jobs",
                      "/claude/projects/p1", "/claude/projects/-root", "/backup", "/logs/selfcheck.log", "/.authorized_keys.backup") if os.path.exists(WORK + f)]
check("8 the owner's private state on /data goes (ledger, schedule runs, sessions, chat, agent state, job dirs, transcripts AND memory, logs, the key backup, /data/backup's apkovl + pipe copies); the root ssh key goes; the hot-set dirs are emptied not removed; /data/home, /data/repos, users.manifest and an empty claude/projects stay",
      not gone_w and not os.path.exists(HOME + "/.ssh/authorized_keys") and os.path.isdir(WORK + "/.pipeos/ledger") and os.path.isdir(WORK + "/logs")
      and os.path.exists(WORK + "/home/office/doc.txt") and os.path.exists(WORK + "/repos/proj/README")
      and os.path.exists(WORK + "/.pipeos/users.manifest") and os.path.isdir(WORK + "/claude/projects"), repr((gone_w, os.path.exists(HOME + "/.ssh/authorized_keys"))))
sc = open(os.path.join(REPO, "overlay/usr/local/bin/pipeos-selfcheck")).read()
check("7 selfcheck: an unprovisioned box wants no pipe and no claude (nobody's box runs nothing)",
      '[ -f /etc/pipeos/provisioned ] || SERVICE_PIPE=off SERVICE_CLAUDE=off' in sc)
stale = {f: open(os.path.join(REPO, f)).read() for f in ("overlay/usr/local/bin/pipeos", "overlay/usr/local/bin/pipebox-card", "overlay/usr/local/bin/pipeos-selfcheck", "overlay/etc/issue", "overlay/etc/motd")}
bad = [f for f, t in stale.items() if "pipebox-setup" in t or "UNPROVISIONED" in t]
check("9 nobody's box speaks the wizard's language everywhere it is met: no 'UNPROVISIONED' or 'run pipebox-setup' in pipeos status, the console banner, its generator, or selfcheck",
      not bad and "chooses a password" in stale["overlay/usr/local/bin/pipeos"] and "choose a password" in stale["overlay/etc/issue"], repr(bad))

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
