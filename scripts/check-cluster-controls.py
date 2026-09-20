#!/usr/bin/env python3
"""Controls for check-cluster.py (pipeOS#222, #211).

Each control breaks ONE property of cluster.py or webd.py in a private copy
of the tree (controls_lib) and must make a DIFFERENT row fail, for its own
reason. The copies run in parallel; the shipped files are never touched.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WEB = os.path.join(REPO, "overlay/usr/local/share/pipeos/web")
C = os.path.join(WEB, "cluster.py")
W = os.path.join(WEB, "webd.py")
PROBE = os.path.join(HERE, "check-cluster.py")
orig = {p: open(p).read() for p in (C, W)}

OLD_H = '    res = {mid: ("rebooting" if st == 200 else "%s" % ((b.get("error") if isinstance(b, dict) else "") or st))\n           for mid, (st, b) in fanout(others, "POST", "/api/reboot", {}).items()}\n    res[v["self"]] = local_reboot()'
NEW_H = '    res = {v["self"]: local_reboot()}\n    time.sleep(0.5)\n    res.update({mid: ("rebooting" if st == 200 else "%s" % ((b.get("error") if isinstance(b, dict) else "") or st))\n           for mid, (st, b) in fanout(others, "POST", "/api/reboot", {}).items()})'

OLD_D = '        if not cluster.take_join_token(pw):\n            if u is None or not check_hash(pw, u.get("hash")):\n                time.sleep(AUTH_DELAY)\n                return self.err(403, "wrong password for this Machine")\n'
OLD_J = '        if u is None or not check_hash(body.get("password") or "", u.get("hash")):\n            time.sleep(AUTH_DELAY)\n            return self.err(403, "wrong password for this member")\n'
OLD_K = '    try:\n        os.unlink(JOIN_TOKEN)\n    except OSError:\n        pass\n    return bool(candidate)'
NEW_K = '    return bool(candidate)'
OLD_L = '    if ident.get("claimed"):\n        raise ClusterError'
NEW_L = '    if False:\n        raise ClusterError'

controls = [
    ("A: the listener never asks for a client certificate (no member is ever admitted)", C,
     lambda s: s.replace("        ctx.verify_mode = ssl.CERT_OPTIONAL\n", "        ctx.verify_mode = ssl.CERT_NONE\n"), ["3"]),

    ("B: the listener is not restarted when the list changes (a removed member's certificate keeps working)", W,
     lambda s: s.replace("cluster.ON_CHANGE.append(lambda: HTTPS.get(\"server\") is not None and start_https(init=False))\n", "\n")
                .replace("        if HTTPS.get(\"server\") is not None and cur != HTTPS.get(\"bundle\"):\n", "        if False:\n"), ["4"]),

    ("C: the member is read off the certificate's position in the list, not off which CA signed it (any member's cert is attributed to the first member)", C,
     lambda s: s.replace("                if p.returncode == 0:\n                    who = mid\n                    break\n",
                         "                who = mid\n                break\n"), ["3"]),

    ("D: the join does not check the password", W,
     lambda s: s.replace(OLD_D, ""), ["3"]),

    ("E: the caller does not verify the answering certificate (any server on that port is 'a member')", C,
     lambda s: s.replace("        ctx.load_verify_locations(cafile=cafile)\n        ctx.verify_mode = ssl.CERT_REQUIRED\n",
                         "        ctx.verify_mode = ssl.CERT_NONE\n"), ["2"]),

    ("G: the page marks a member that did not answer as awake (a dead box reads as fine)", C,
     lambda s: s.replace('        s["awake"] = r["self"] or "error" not in s\n', '        s["awake"] = True\n'), ["14"]),

    ("H: reboot-all reboots the box that asked first (the page goes dark before the others are told)", C,
     lambda s: s.replace(OLD_H, NEW_H), ["16"]),

    ("I: the service switch answers ok for an id that is not a member", C,
     lambda s: s.replace('        res[i] = "not a member"\n', '        res[i] = "ok"\n'), ["15"]),

    ("J: add-request does not check the member's password (anyone on the LAN joins a box to the cluster)", W,
     lambda s: s.replace(OLD_J, ""), ["17"]),

    ("K: the join token is not single-use (a captured token joins again later)", C,
     lambda s: s.replace(OLD_K, NEW_K), ["17"]),

    ("L: adopt does not refuse a Machine that is already claimed (the owner's guard)", C,
     lambda s: s.replace(OLD_L, NEW_L), ["18"]),

    ("M: the summary reads the boot report even when a newer live verdict exists (the page lies between reboots, #290)", W,
     lambda s: s.replace("    verdict, vsrc, vage = lanid.verdict_now(BOOT_REPORT, HEALTH_LAST)\n",
                         "    verdict, vsrc, vage = lanid.verdict_line(BOOT_REPORT), \"boot\", 0\n"), ["13b"]),

    ("N: the idlest pick ignores busy (an agent lands on a box mid-job, #300)", C,
     lambda s: s.replace('    ok = [r for r in rows if r.get("awake") and not r.get("busy")]\n', '    ok = [r for r in rows if r.get("awake")]\n'), ["20"]),

    ("O: a placement starts the agent on the box that was asked, whatever member was named (#300)", C,
     lambda s: s.replace("    if mid == me:\n        st, out = local_start(spec)\n", "    if True:\n        st, out = local_start(spec)\n"), ["19"]),

    ("P: a grey member's agents are shown as they last were, not as last-known (a dead box's agent reads as running, #300)", C,
     lambda s: s.replace('                s["agents"] = [dict(a, running=None, last_status="") for a in last.get("agents", []) if isinstance(a, dict)]\n                s["agents_stale"] = True\n',
                         '                s["agents"] = [a for a in last.get("agents", []) if isinstance(a, dict)]\n                s["agents_stale"] = False\n'), ["21"]),

    ("Q: a placement the member refuses at run time is not saved (the job it wrote vanishes at the next boot, #300)", W,
     lambda s: s.replace('        if out.get("changed"):          # the job is written even when the run was refused — so it is saved either way\n',
                         '        if st == 200 and out.get("changed"):\n'), ["19b"]),

    ("V3: a member's certificate is held to no allowlist (it adds a user, reads the vault — the front door, #314)", W,
     lambda s: s.replace('        p = self.peer()\n        if not p or valid_session(self.cookie_token()):\n            return ""\n', '        return ""\n        p = self.peer()\n        if not p or valid_session(self.cookie_token()):\n            return ""\n'), ["25"]),

    ("V4: a member's copy overwrites a secret this box set itself (#301)", W,
     lambda s: s.replace('        if mine and not (mine.get("by") or "").startswith("cluster:"):\n', '        if False:\n'), ["25"]),

    ("V1: the requester's receive takes a copy under any request id (a member plants a secret as an 'answer' to a request never raised, #301)", W,
     lambda s: s.replace('            if rec is None or _req_live(rec) != "pending" or rec.get("name") != name:\n                return self.err(404, "no open request %s for %s on this Machine" % (rid, name))\n            if peer["peer"] not in (rec.get("holders") or []):\n',
                         '            if rec is None:\n                rec = {"holders": [peer["peer"]]}\n            if False:\n'), ["24"]),

    ("V2: approve ignores that a request is already decided (a denied or done request is approved again, #301)", W,
     lambda s: s.replace('        live = _req_live(rec)\n        if live != "pending":\n', '        live = _req_live(rec)\n        if False:\n'), ["24"]),

    ("V6: the requester takes a request's copy from a member the request did not name (a non-holder plants its own value, #301)", W,
     lambda s: s.replace('            if peer["peer"] not in (rec.get("holders") or []):\n                return self.err(403, "%s is not a holder this request named" % peer["peer"])\n', ''), ["27"]),

    ("V5: approve ignores a request's expiry (#301)", W,
     lambda s: s.replace('    if r.get("state") == "pending" and int(r.get("expires_at") or 0) < now:\n        return "expired"\n', '    if False:\n        return "expired"\n'), ["24"]),

    ("F: the reader does not drop a member seen in another cluster", C,
     lambda s: s.replace("        if pid in d[\"members\"] and pid != self_id() and p.get(\"cl\") and p[\"cl\"] != d[\"id\"]:\n",
                         "        if False:\n"), ["9"]),

    # ---- the review pass on PR #344 found both of these in the sign-in view;
    # these are the controls that keep them found.
    ("T: the cluster's sign-in gather is open to any signed-in session (a viewer reads every Machine's roster)", W,
     lambda s: s.replace('        if self._peer_guard(allow_self=True) is None:\n'
                         '            return\n'
                         '        self.send(200, cluster.users(users_here))',
                         '        self.send(200, cluster.users(users_here))'), ["19c3"]),

    ("U: any 200 counts as a member's sign-in list, whatever the body (an unattributable answer and an older member's 404 both read as '0 sign-ins')", C,
     lambda s: s.replace('        if st == 200 and isinstance(b, dict) and isinstance(b.get("users"), list) and not err:\n'
                         '            rows.append({"id": r["id"], "name": r["name"], "self": False, "users": b["users"]})',
                         '        if st == 200 and isinstance(b, dict):\n'
                         '            rows.append({"id": r["id"], "name": r["name"], "self": False, "users": b.get("users") or []})'), ["19c4"]),
]

sys.path.insert(0, HERE)
import controls_lib  # noqa: E402


def one(ctl):
    name, path, f, must = ctl
    mut = f(orig[path])
    if mut == orig[path]:
        return name, None, must
    root = controls_lib.sandbox({path: mut})
    try:
        _, out = controls_lib.run_probe(PROBE, root=root)
    finally:
        controls_lib.cleanup(root)
    return name, controls_lib.fails_in(out), must


rc = 0
for name, fails, must in controls_lib.pmap(one, controls):
    if fails is None:
        print("--- %s: CONTROL DID NOT APPLY" % name)
        rc = 1
        continue
    missing = [m for m in must if m not in fails]
    print("--- %s: rows %s fail%s" % (name, fails or "(none)", "" if not missing else "  — EXPECTED %s TO FAIL" % missing))
    if missing:
        rc = 1
sys.exit(rc)
