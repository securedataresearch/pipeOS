#!/usr/bin/env python3
"""Controls for check-vault.py: put each rule back to broken in a copy of
vault.py or the init script and assert the probe notices (the house rule
since #100)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VAULT = os.path.join(REPO, "overlay/usr/local/share/pipeos/web/vault.py")
INIT = os.path.join(REPO, "overlay/etc/init.d/pipeos-vault")
PROBE = os.path.join(HERE, "check-vault.py")

BREAKS = [
    ("A  the integrity check is skipped (decrypt whatever is there)", VAULT,
     '    if not hmac.compare_digest(hmac.new(keys64[32:], ct, "sha256").digest(), tag):\n        raise Locked("integrity check failed — wrong key or a damaged vault")\n',
     ''),
    ("B  the chassis is not part of the key (any machine opens it)", VAULT,
     '    return _kdf((env["seed"] + "|" + _ident_string(i or ident())).encode(), salt)',
     '    return _kdf(env["seed"].encode(), salt)'),
    ("C  migrate copies but never shreds", VAULT,
     'def _shred(path):\n    try:\n        n = os.path.getsize(path)', 'def _shred(path):\n    return\n    try:\n        n = os.path.getsize(path)'),
    ("D  the export files are world-readable", VAULT,
     '        _write_file(path, content if isinstance(content, bytes) else "".join(content))',
     '        _write_file(path, content if isinstance(content, bytes) else "".join(content), 0o644)'),
    ("E  the phrase is not checked (any phrase unlocks)", VAULT,
     '        except Locked:\n            raise Locked("that is not this vault\'s recovery phrase")',
     '        except Locked:\n            return _open(_chassis_keys(env), _unb64(env["slots"]["chassis"]["wrapped"]))'),
    ("F  the quote guard is gone from the sourced exports", VAULT,
     '    if any(c in v for c in "\'\\n\\r\\0"):\n        raise VaultError("a shell-sourced secret may not contain quotes or newlines")\n', ''),
    ("G  the boot migration never saves (the plaintext comes back next boot)", INIT,
     '			if /usr/local/bin/pipeos-save >/dev/null 2>&1; then', '			if true; then'),
    ("H  the key goes to openssl on argv", VAULT,
     '        p = subprocess.run(["openssl", "enc"] + args + ["-pass", "fd:%d" % r], input=data,\n                           capture_output=True, pass_fds=(r,))',
     '        p = subprocess.run(["openssl", "enc"] + args + ["-pass", "pass:" + key32.hex()], input=data,\n                           capture_output=True, pass_fds=(r,))'),
]

failed = False
for name, path, old, new in BREAKS:
    src = open(path).read()
    if src.count(old) != 1:
        sys.exit("control %s: anchor appears %d times — fix the controls before trusting them" % (name[0], src.count(old)))
    fd, tmp = tempfile.mkstemp(prefix="ckvault-ctl-")
    with os.fdopen(fd, "w") as f:
        f.write(src.replace(old, new, 1))
    env = dict(os.environ, **({"CHECK_VAULT_BIN": tmp} if path == VAULT else {"CHECK_VAULT_INIT": tmp}))
    p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True, env=env)
    os.unlink(tmp)
    fails = [l for l in p.stdout.splitlines() if l.startswith("FAIL")]
    print("%s\n   -> %d row(s) fail" % (name, len(fails)))
    for l in fails:
        print("      " + l[5:].split("  [")[0])
    if not fails:
        failed = True
        print("   !! the probe did not notice")
p = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
print("intact tree: " + p.stdout.strip().splitlines()[-1])
sys.exit(1 if failed or p.returncode else 0)
