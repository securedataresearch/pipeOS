#!/usr/bin/env python3
"""check-firewall.py — the LAN-only packet filter (2026-10-13): the generator
renders one ruleset, refuses a CIDR that is not one, loads atomically (check,
then apply), and the wiring is pinned: the service, the boot runlevel, the
lbu list, world, IPv6 off, the sshd/fstab posture, the mdns image copy, the
flash merge carrying the boot runlevel. No root, no network: nft is a stub
that logs its argv (and the real `nft -c` runs when the host has it and sudo
answers without a prompt). Exit 0 if every row passes.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FW = os.path.join(REPO, "overlay/usr/local/bin/pipeos-firewall")
RESULTS = []


def check(desc, ok, detail=""):
    ok = bool(ok)
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + desc + ("" if ok else "  [%s]" % str(detail)[:400]))


def rd(rel):
    with open(os.path.join(REPO, rel)) as f:
        return f.read()


D = tempfile.mkdtemp(prefix="ckfw-")
CONF = os.path.join(D, "firewall.conf")
NFTLOG = os.path.join(D, "nft.argv")
NFT = os.path.join(D, "nft")
SAVE = os.path.join(D, "pipeos-save")
RENDERED = os.path.join(D, "run", "firewall.nft")
with open(NFT, "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + NFTLOG + "\n"
            "case \"$1 $2\" in '-c -f') [ -f " + D + "/fail-check ] && exit 1 ;; esac\nexit 0\n")
with open(SAVE, "w") as f:
    f.write("#!/bin/sh\necho saved >> " + D + "/save.log\n")
os.chmod(NFT, 0o755); os.chmod(SAVE, 0o755)
ENV = dict(os.environ, PIPEOS_FIREWALL_CONF=CONF, PIPEOS_FIREWALL_NFT=NFT,
           PIPEOS_FIREWALL_RENDERED=RENDERED, PIPEOS_FIREWALL_SAVE=SAVE)


def fw(*args, conf="FIREWALL=on\nALLOW_FROM=\"\"\n"):
    with open(CONF, "w") as f:
        f.write(conf)
    p = subprocess.run(["sh", FW] + list(args), capture_output=True, text=True, env=ENV)
    return p.returncode, p.stdout, p.stderr


def nftlog():
    try:
        return open(NFTLOG).read().splitlines()
    except OSError:
        return []


# ── 1. the ruleset ────────────────────────────────────────────────────────
rc, out, err = fw("render")
inp = out[out.index("chain input {"):out.index("chain forward")] if "chain input {" in out else ""
rules = [l.strip() for l in inp.splitlines() if l.strip() and not l.strip().startswith("type ") and l.strip() not in ("}", "chain input {")]
check("1 render: table inet pipeos, input drop, forward drop, output accept, lo, conntrack, DHCP, the four private ranges, LAN accept, drop last",
      rc == 0 and "table inet pipeos" in out
      and "hook input priority filter; policy drop" in out and "hook forward priority filter; policy drop" in out
      and "hook output priority filter; policy accept" in out
      and "iif lo accept" in out and "ct state established,related accept" in out
      and "udp sport 67 udp dport 68 accept" in out
      and all(c in out for c in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))
      and "ip saddr @lan accept" in out and rules and rules[-1].startswith("counter drop")
      and not any("accept" in l and ("ip6" in l or "ipv6" in l) for l in rules),
      (rc, err, rules[-2:]))

rc, out, err = fw("render", conf='FIREWALL=on\nALLOW_FROM="100.64.0.0/10 203.0.113.7"\n')
check("2 ALLOW_FROM adds its ranges to the trusted set",
      rc == 0 and "100.64.0.0/10" in out and "203.0.113.7" in out and out.count("elements") == 1, out[:300])

bad = []
for tok in ('203.0.113.0/24; flush ruleset', "999.1.1.1", "10.0.0.0/33", "evil.example", "10.0.0.0/8 }"):
    rc, out, err = fw("render", conf='FIREWALL=on\nALLOW_FROM="%s"\n' % tok)
    bad.append((tok, rc, len(out)))
check("3 a token that is not an IPv4 CIDR is refused with exit 2 and nothing rendered",
      all(rc == 2 and n == 0 for _, rc, n in bad), bad)

# ── 2. loading ────────────────────────────────────────────────────────────
for p in (NFTLOG, D + "/fail-check"):
    try:
        os.unlink(p)
    except OSError:
        pass
rc, out, err = fw("apply")
log = nftlog()
check("4 apply checks (-c -f FILE) and then loads (-f FILE), the same file, in that order; the file is the rendered ruleset",
      rc == 0 and len(log) == 2 and log[0] == "-c -f %s.new" % RENDERED and log[1] == "-f %s" % RENDERED
      and os.path.exists(RENDERED) and "table inet pipeos" in open(RENDERED).read(), (rc, log, err))

open(D + "/fail-check", "w").close()
os.unlink(NFTLOG)
rc, out, err = fw("apply")
log = nftlog()
os.unlink(D + "/fail-check")
check("5 a ruleset that fails the check is never loaded (no -f call), exit nonzero",
      rc != 0 and log == ["-c -f %s.new" % RENDERED], (rc, log))

os.unlink(NFTLOG)
rc, out, err = fw("apply", conf="FIREWALL=off\n")
check("6 FIREWALL=off flushes and says so (every port open), exit 0",
      rc == 0 and nftlog() == ["flush ruleset"] and "off" in out, (rc, out, nftlog()))

os.unlink(NFTLOG)
rc, out, err = fw("allow", "10.9.0.0/16", "100.64.0.0/10")
conf = open(CONF).read()
check("7 allow appends to ALLOW_FROM, applies, and saves once",
      rc == 0 and 'ALLOW_FROM="10.9.0.0/16 100.64.0.0/10"' in conf and nftlog()[-1].startswith("-f ")
      and open(D + "/save.log").read() == "saved\n", (rc, conf, nftlog(), err))
rc, out, err = fw("allow", "nope")
check("8 allow refuses a non-CIDR before touching the conf", rc == 2 and 'ALLOW_FROM=""' in open(CONF).read(), (rc, err))

# ── 3. the real nft, when the host has it ─────────────────────────────────
rc, out, err = fw("render")
real = shutil.which("nft")
if real:
    rp = os.path.join(D, "real.nft")
    open(rp, "w").write(out)
    p = subprocess.run(["sudo", "-n", "nft", "-c", "-f", rp], capture_output=True, text=True)
    if p.returncode == 0:
        check("9 the rendered ruleset passes the real `nft -c`", True)
    elif "password" in (p.stderr or "").lower() or "sudo" in (p.stderr or "").lower() or "not permitted" in (p.stderr or "").lower():
        print("skip 9 the real `nft -c` needs root here (sudo did not answer without a prompt)")
    else:
        check("9 the rendered ruleset passes the real `nft -c`", False, p.stderr[-300:])
else:
    print("skip 9 no nft on this host")

# ── 4. wiring ─────────────────────────────────────────────────────────────
init = rd("overlay/etc/init.d/pipeos-firewall")
check("10 the service runs before net, after sysctl, provides firewall, applies through the generator, turns IPv6 off",
      "before net" in init and "after sysctl" in init and "provide firewall" in init
      and "/usr/local/bin/pipeos-firewall apply" in init and "disable_ipv6=1" in init
      and (os.stat(os.path.join(REPO, "overlay/etc/init.d/pipeos-firewall")).st_mode & stat.S_IXUSR))
build = rd("scripts/40-build-apkovl.sh")
boot = next((l for l in build.splitlines() if l.startswith("mk_runlevel boot")), "")
check("11 the image enrols pipeos-firewall in the BOOT runlevel", "pipeos-firewall" in boot, boot)
lbu = rd("overlay/etc/apk/protected_paths.d/lbu.list")
check("12 lbu.list carries the init script, the seam and the sysctl",
      all(("+" + p) in lbu for p in ("etc/init.d/pipeos-firewall", "etc/pipeos/firewall.conf", "etc/sysctl.d/pipeos.conf")))
check("13 nftables is in world (so the extra repo carries it offline)", "\nnftables\n" in "\n" + rd("overlay/etc/apk/world"))
sysctl = rd("overlay/etc/sysctl.d/pipeos.conf")
check("14 IPv6 off in sysctl.d: all and default disabled, lo kept",
      bool(re.search(r"net\.ipv6\.conf\.all\.disable_ipv6\s*=\s*1", sysctl)) and bool(re.search(r"net\.ipv6\.conf\.default\.disable_ipv6\s*=\s*1", sysctl))
      and bool(re.search(r"net\.ipv6\.conf\.lo\.disable_ipv6\s*=\s*0", sysctl)))
sshd = rd("overlay/etc/ssh/sshd_config")
check("15 sshd posture: inet only, no forwarding, few tries, key-only stays",
      all(l in sshd for l in ("AddressFamily inet", "AllowTcpForwarding no", "AllowAgentForwarding no", "X11Forwarding no", "MaxAuthTries 3", "LoginGraceTime 30", "PermitRootLogin prohibit-password", "PasswordAuthentication no")))
check("16 the boot media is root-only (fmask/dmask 0077)", "fmask=0077,dmask=0077" in rd("overlay/etc/fstab"))
check("17 mdns reads the image descriptor from root's copy in /run, which the init script makes",
      'IMAGE_TXT = "/run/pipeos/image.txt"' in rd("overlay/usr/local/share/pipeos/web/mdnsd.py")
      and "/run/pipeos/image.txt" in rd("overlay/etc/init.d/pipeos-mdns"))
check("18 pipeos dispatches the firewall verb and status has the row; selfcheck probes the table",
      "firewall)" in rd("overlay/usr/local/bin/pipeos") and "table inet pipeos" in rd("overlay/usr/local/bin/pipeos")
      and "nft list table inet pipeos" in rd("overlay/usr/local/bin/pipeos-selfcheck"))
flash = rd("overlay/usr/local/bin/pipeos-flash")
check("19 the flash merge carries the build-owned boot runlevel and posture files from the image",
      "IMAGE_PATHS=" in flash and "etc/runlevels/boot" in flash and "etc/sysctl.d" in flash and "etc/fstab" in flash)
if shutil.which("shellcheck"):
    p = subprocess.run(["shellcheck", "-S", "error", "-s", "sh", os.path.join(REPO, "overlay/etc/init.d/pipeos-firewall")], capture_output=True, text=True)
    check("20 the init script shellchecks (init.d is outside the CI glob)", p.returncode == 0, p.stdout[-300:])

shutil.rmtree(D, ignore_errors=True)
print("%d/%d" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
