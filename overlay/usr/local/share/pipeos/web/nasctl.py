#!/usr/bin/env python3
"""pipeos nas — the Storage page's account form as a command (pipeOS#270,
the operator-verb rule of #259: the same code, the same refusals, the
same save; no hand edit over ssh).

  printf '%s' SMBPASSWORD | pipeos nas account NAME   create a share-only
        account (unix login for samba's passdb only: /sbin/nologin, no key,
        no terminal, no dashboard sign-in) and set its SMB password

Seams for the probe: PIPEOS_SAVE_BIN (the save to run), and webd's own
module paths (check-webd redirects them the same way).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webd  # noqa: E402


def die(msg, rc=2):
    sys.stderr.write("pipeos nas: %s\n" % msg)
    sys.exit(rc)


def main(argv):
    if len(argv) < 1 or argv[0] != "account":
        die("usage: printf '%s' SMBPASSWORD | pipeos nas account NAME")
    if len(argv) != 2:
        die("usage: printf '%s' SMBPASSWORD | pipeos nas account NAME")
    if sys.stdin.isatty():
        die("the SMB password comes on stdin: printf '%s' PW | pipeos nas account NAME")
    name = argv[1].strip()
    pw = sys.stdin.read().rstrip("\n")
    code, payload = webd.nas_account_create(name, pw, by="operator")
    if code != 200:
        die(payload, 2)
    for p in payload.get("problems") or []:
        sys.stderr.write("pipeos nas: %s\n" % p)
    print("share-only account %s: created%s" % (name, "" if not payload.get("problems") else " (see above)"))
    save_bin = os.environ.get("PIPEOS_SAVE_BIN", "pipeos-save")
    rc, out = webd.run([save_bin], timeout=300)
    if rc != 0:
        sys.stderr.write("saved: NO — run pipeos save (%s)\n" % out.strip()[-200:])
        sys.exit(1)
    print("saved")


if __name__ == "__main__":
    main(sys.argv[1:])
