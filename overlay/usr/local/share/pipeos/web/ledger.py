#!/usr/bin/env python3
"""ledger — every model call this Machine makes, costed (#246).

    pipeos-ledger-ingest ingest            tail the transcripts into the ledger
    pipeos-ledger-ingest totals [--json]   today / 7 days / 30 days, by actor
    pipeos-ledger-ingest enforce           the monthly cap: DM at 80%, pause at 100%

Nobody on a Machine could say what it spent. The owner found out from the
bill. This reads what Claude Code already writes — every `assistant` line
in /work/claude/projects/*/*.jsonl carries the model and a usage block —
and appends one row per API call to /work/.pipeos/ledger/YYYY-MM.jsonl:

  {ts, sid, id, source:"transcript", actor:{kind,name}, backend, provider,
   model, in, out, cache_read, cache_w5m, cache_w1h, cost_usd|null,
   est:true, cwd}

Append-only JSONL, one file per month: a torn last line is skipped and
nothing else is at risk; the cap is a sum over exactly one file; retention
is rm. Dedupe by message id — a transcript writes one line per content
block per API turn, all carrying the same id and the same usage, so a
naive sum overcounts about 2×. A cursor (inode, offset, the last ids seen)
per transcript keeps ingest incremental; a file that shrank or changed
inode starts over and the id ring catches the repeat.

WHO SPENT IT: a session id that a scheduled job logged in runs.log is
`job:<name>`; one a pipe peer's listener session holds is `listener:<nick>`;
the dashboard chat's own id is `dashboard`; a transcript in the cohort
watcher's cwd is `watch`; in the master session's cwd `assistant`; anything
else `other:<dir>`.

HOW MUCH: rates.json ships with the overlay — the published per-million
prices — and every number is an ESTIMATE the provider's bill overrides. A
model the table does not know is counted and shown as unpriced.

THE CAP: MONTHLY_CAP_USD from the card (0 = none). At 80% one DM to the
owner per month; at 100% a `paused` marker the schedule tick honours, and
one more DM. Under the cap again (raised, or a new month) the marker goes.
An attached session is only ever warned, never cut.

Seams (the probe, never production): PIPEOS_LEDGER_DIR, _TRANSCRIPTS,
_RATES, _CONF, _RUNS, _SESSIONS, _WEBCHAT_SID, _PIPE (the DM binary), _NOW
(an ISO timestamp standing in for the clock).
"""

import datetime
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import time

LEDGER_DIR = os.environ.get("PIPEOS_LEDGER_DIR", "/work/.pipeos/ledger")
TRANSCRIPTS = os.environ.get("PIPEOS_LEDGER_TRANSCRIPTS", "/work/claude/projects")
RATES = os.environ.get("PIPEOS_LEDGER_RATES", "/usr/local/share/pipeos/rates.json")
CONF = os.environ.get("PIPEOS_LEDGER_CONF", "/etc/pipeos/pipebox.conf")
RUNS_LOG = os.environ.get("PIPEOS_LEDGER_RUNS", "/work/.pipeos/schedule/runs.log")
SESSIONS_DIR = os.environ.get("PIPEOS_LEDGER_SESSIONS", "/work/pipebox/sessions")
WEBCHAT_SID = os.environ.get("PIPEOS_LEDGER_WEBCHAT_SID", "/work/pipebox/webchat/.dashboard-sid")
PIPE_BIN = os.environ.get("PIPEOS_LEDGER_PIPE", "pipe")
SEEN_RING = 256
KEEP_MONTHS = 13
WARN_PCT = 80


def _now():
    v = os.environ.get("PIPEOS_LEDGER_NOW")
    if v:
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    except (ValueError, AttributeError, TypeError):
        return None


class Ledger:
    def __init__(self, dir=LEDGER_DIR, transcripts=TRANSCRIPTS, rates=RATES, conf=CONF,
                 runs_log=RUNS_LOG, sessions_dir=SESSIONS_DIR, webchat_sid=WEBCHAT_SID, pipe_bin=PIPE_BIN):
        self.dir = dir
        self.transcripts = transcripts
        self.rates_path = rates
        self.conf = conf
        self.runs_log = runs_log
        self.sessions_dir = sessions_dir
        self.webchat_sid = webchat_sid
        self.pipe_bin = pipe_bin
        self._rates = None
        self._totals_key = None
        self._totals = None

    # ---- rates ----------------------------------------------------------------

    def rates(self):
        if self._rates is None:
            try:
                with open(self.rates_path) as f:
                    self._rates = json.load(f).get("per_mtok", {})
            except (OSError, ValueError):
                self._rates = {}
        return self._rates

    def rate_for(self, model):
        """Exact id, then the id without a trailing -YYYYMMDD, then the longest
        family prefix in the table; None when nothing fits."""
        r = self.rates()
        if not model:
            return None
        m = model.lower()
        if m in r:
            return r[m]
        base = re.sub(r"-\d{8}$", "", m)
        if base in r:
            return r[base]
        best = None
        for k in r:
            if base.startswith(k) and (best is None or len(k) > len(best)):
                best = k
        return r.get(best) if best else None

    def cost(self, model, row):
        r = self.rate_for(model)
        if r is None:
            return None
        usd = (row["in"] * r.get("in", 0) + row["out"] * r.get("out", 0) + row["cache_read"] * r.get("cache_read", 0)
               + row["cache_w5m"] * r.get("cache_w5m", 0) + row["cache_w1h"] * r.get("cache_w1h", 0)) / 1e6
        return round(usd, 6)

    # ---- attribution ---------------------------------------------------------

    def _job_sids(self):
        out = {}
        try:
            with open(self.runs_log) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        out[parts[1]] = parts[2]
        except OSError:
            pass
        return out

    def _listener_sids(self):
        out = {}
        try:
            for n in os.listdir(self.sessions_dir):
                if n.startswith("."):
                    continue
                try:
                    with open(os.path.join(self.sessions_dir, n)) as f:
                        out[f.read().strip()] = n
                except OSError:
                    pass
        except OSError:
            pass
        return out

    def _dashboard_sid(self):
        try:
            with open(self.webchat_sid) as f:
                return f.read().strip()
        except OSError:
            return ""

    def actor_for(self, sid, cwd, maps):
        jobs, listeners, dash = maps
        if sid and sid in jobs:
            return {"kind": "job", "name": jobs[sid]}
        if sid and sid in listeners:
            return {"kind": "listener", "name": listeners[sid]}
        if sid and dash and sid == dash:
            return {"kind": "dashboard", "name": ""}
        c = (cwd or "").rstrip("/")
        if c == "/work/pipebox":
            return {"kind": "watch", "name": ""}
        if c == "/work/pipebox/webchat":
            return {"kind": "assistant", "name": ""}
        return {"kind": "other", "name": os.path.basename(c) or c}

    # ---- ingest --------------------------------------------------------------

    def _cursor_path(self):
        return os.path.join(self.dir, "cursor.json")

    def _lock(self):
        os.makedirs(self.dir, exist_ok=True)
        f = open(os.path.join(self.dir, ".lock"), "a+")
        fcntl.flock(f, fcntl.LOCK_EX)
        return f

    def ingest(self):
        """Tail every transcript into the ledger. Returns the number of new
        rows. Safe to call from a thread and from the CLI at once (a lock)."""
        lk = self._lock()
        try:
            return self._ingest_locked()
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)
            lk.close()

    def _ingest_locked(self):
        try:
            with open(self._cursor_path()) as f:
                cursor = json.load(f)
        except (OSError, ValueError):
            cursor = {}
        maps = (self._job_sids(), self._listener_sids(), self._dashboard_sid())
        new = 0
        out_files = {}
        for path in sorted(glob.glob(os.path.join(self.transcripts, "*", "*.jsonl"))):
            try:
                st = os.stat(path)
            except OSError:
                continue
            c = cursor.get(path, {})
            off = c.get("off", 0)
            seen = list(c.get("seen", []))
            if c.get("ino") != st.st_ino or st.st_size < off:
                off = 0   # start over, but KEEP the id ring: it is what stops a rewrite double-counting
            if st.st_size == off:
                cursor[path] = {"ino": st.st_ino, "off": off, "seen": seen[-SEEN_RING:]}
                continue
            try:
                with open(path, "rb") as f:
                    f.seek(off)
                    data = f.read()
            except OSError:
                continue
            # split leaves either "" (a complete file) or the torn tail as the
            # last element; neither is a line to consume — the tail waits for
            # its newline, and counting the "" overshot the offset by one
            # byte, which made the next pass think the file had shrunk
            lines = data.split(b"\n")[:-1]
            consumed = sum(len(l) + 1 for l in lines)
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                u = msg.get("usage") or {}
                mid = msg.get("id") or ""
                if not u or not mid or mid in seen:
                    continue
                seen.append(mid)
                cc = u.get("cache_creation") or {}
                w5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
                w1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
                if not cc:
                    w5 = int(u.get("cache_creation_input_tokens") or 0)
                row = {"ts": d.get("timestamp") or _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "sid": d.get("sessionId") or "", "id": mid, "source": "transcript",
                       "actor": self.actor_for(d.get("sessionId"), d.get("cwd"), maps),
                       "backend": "claude", "provider": "anthropic", "model": msg.get("model") or "",
                       "in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
                       "cache_read": int(u.get("cache_read_input_tokens") or 0), "cache_w5m": w5, "cache_w1h": w1,
                       "est": True, "saved_tokens": None, "cwd": d.get("cwd") or "",
                       "sidechain": bool(d.get("isSidechain"))}
                row["cost_usd"] = self.cost(row["model"], row)
                ts = _parse_ts(row["ts"]) or _now()
                month = ts.strftime("%Y-%m")
                out_files.setdefault(month, []).append(json.dumps(row, separators=(",", ":")))
                new += 1
            cursor[path] = {"ino": st.st_ino, "off": off + consumed, "seen": seen[-SEEN_RING:]}
        for month, rows in out_files.items():
            with open(os.path.join(self.dir, month + ".jsonl"), "a") as f:
                f.write("\n".join(rows) + "\n")
        tmp = self._cursor_path() + ".new"
        with open(tmp, "w") as f:
            json.dump(cursor, f)
        os.replace(tmp, self._cursor_path())
        with open(os.path.join(self.dir, ".last-ingest"), "w") as f:
            f.write(str(int(time.time())))
        self._prune()
        self._totals_key = None
        return new

    def _prune(self):
        files = sorted(glob.glob(os.path.join(self.dir, "*.jsonl")))
        for p in files[:-KEEP_MONTHS] if len(files) > KEEP_MONTHS else []:
            try:
                os.unlink(p)
            except OSError:
                pass

    # ---- reading ---------------------------------------------------------------

    def rows(self, months):
        for m in months:
            try:
                with open(os.path.join(self.dir, m + ".jsonl")) as f:
                    for line in f:
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue   # a torn line
            except OSError:
                continue

    def totals(self):
        """today / 7d / 30d, by actor over 30d, a 30-day daily series, the
        unpriced count, and the cap state. Cached on the files' size+mtime."""
        now = _now()
        months = sorted({(now - datetime.timedelta(days=d)).strftime("%Y-%m") for d in (0, 15, 31)})
        key = tuple((m, self._stat(m)) for m in months) + (self._conf_stat(),)
        if self._totals_key == key and self._totals is not None:
            return self._totals
        t0 = {"usd": 0.0, "calls": 0, "in": 0, "out": 0, "cache_read": 0}
        today, d7, d30 = dict(t0), dict(t0), dict(t0)
        month = dict(t0)
        by_actor = {}
        daily = [0.0] * 30
        unpriced = 0
        last_ts = ""
        day0 = now.date()
        for r in self.rows(months):
            ts = _parse_ts(r.get("ts", ""))
            if ts is None:
                continue
            age = (day0 - ts.date()).days
            usd = r.get("cost_usd")
            if usd is None:
                unpriced += 1 if age < 30 else 0
                usd = 0.0
            if r.get("ts", "") > last_ts:
                last_ts = r["ts"]
            if ts.strftime("%Y-%m") == now.strftime("%Y-%m"):
                _acc(month, r, usd)
            if age < 0 or age >= 30:
                continue
            _acc(d30, r, usd)
            daily[29 - age] += usd
            if age < 7:
                _acc(d7, r, usd)
            if age == 0:
                _acc(today, r, usd)
            a = r.get("actor") or {}
            k = a.get("kind", "other") + (":" + a["name"] if a.get("name") else "")
            by_actor.setdefault(k, dict(t0))
            _acc(by_actor[k], r, usd)
        for d in (today, d7, d30, month) + tuple(by_actor.values()):
            d["usd"] = round(d["usd"], 4)
        cap = self.cap()
        out = {"today": today, "d7": d7, "d30": d30, "month": month,
               "by_actor": dict(sorted(by_actor.items(), key=lambda kv: -kv[1]["usd"])),
               "daily": [round(x, 4) for x in daily], "unpriced": unpriced, "estimate": True,
               "last_row_ts": last_ts, "last_ingest_ts": self._last_ingest(),
               "cap": {"usd": cap, "spent": month["usd"], "pct": (round(month["usd"] * 100 / cap) if cap else 0),
                       "warned": os.path.exists(self._marker("warned", now)),
                       "paused": self.paused_text()}}
        self._totals_key, self._totals = key, out
        return out

    def _stat(self, month):
        try:
            st = os.stat(os.path.join(self.dir, month + ".jsonl"))
            return (st.st_size, int(st.st_mtime))
        except OSError:
            return None

    def _conf_stat(self):
        try:
            st = os.stat(self.conf)
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    def _last_ingest(self):
        try:
            with open(os.path.join(self.dir, ".last-ingest")) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    # ---- the cap ----------------------------------------------------------------

    def conf_value(self, key):
        try:
            with open(self.conf) as f:
                m = re.search(r'^%s="?([^"\n]*)"?$' % re.escape(key), f.read(), re.M)
        except OSError:
            return ""
        return m.group(1).strip() if m else ""

    def cap(self):
        v = self.conf_value("MONTHLY_CAP_USD")
        return int(v) if v.isdigit() else 0

    def _marker(self, kind, now):
        return os.path.join(self.dir, "%s-%s" % (kind, now.strftime("%Y-%m")))

    def paused_path(self):
        return os.path.join(self.dir, "paused")

    def paused_text(self):
        try:
            with open(self.paused_path()) as f:
                return f.read().strip()
        except OSError:
            return ""

    def dm(self, text):
        owner = self.conf_value("OWNER_NICK")
        if not owner:
            return False
        env = dict(os.environ, HOME="/root", PIPE_AGENT="pipebox", PIPE_NO_SPAWN="1")
        try:
            p = subprocess.run([self.pipe_bin, "dm", owner, text], capture_output=True, timeout=30, env=env)
            return p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def enforce_cap(self):
        """Compare this month's spend to the cap. Idempotent: one DM per
        month per threshold (markers), the pause marker written once and
        removed the moment the box is under the cap again."""
        now = _now()
        cap = self.cap()
        t = self.totals()
        spent = t["month"]["usd"]
        os.makedirs(self.dir, exist_ok=True)
        state = {"cap": cap, "spent": spent, "warned": False, "paused": False, "changed": False}
        if not cap:
            if os.path.exists(self.paused_path()):
                os.unlink(self.paused_path())
                state["changed"] = True
            return state
        pct = spent * 100 / cap
        warned = self._marker("warned", now)
        paused_m = self._marker("paused", now)
        if pct >= 100:
            if not os.path.exists(self.paused_path()):
                with open(self.paused_path(), "w") as f:
                    f.write("monthly cap USD %d reached %s (spent %.2f); scheduled jobs resume on the 1st or when the cap is raised under Usage\n"
                            % (cap, now.strftime("%Y-%m-%d"), spent))
                state["changed"] = True
            if not os.path.exists(paused_m):
                self.dm("usage: this Machine reached its monthly cap (USD %.2f of %d) — scheduled jobs are paused; interactive sessions keep working. Raise the cap under Usage to resume." % (spent, cap))
                open(paused_m, "w").close()
            state["paused"] = True
        else:
            if os.path.exists(self.paused_path()):
                os.unlink(self.paused_path())
                state["changed"] = True
        if pct >= WARN_PCT and not os.path.exists(warned):
            if pct < 100:
                self.dm("usage: USD %.2f of this Machine's %d monthly cap (%d%%) — scheduled jobs pause at 100%%" % (spent, cap, pct))
            open(warned, "w").close()
            state["warned"] = True
        self._totals_key = None
        return state


def _acc(d, r, usd):
    d["usd"] += usd
    d["calls"] += 1
    d["in"] += int(r.get("in") or 0)
    d["out"] += int(r.get("out") or 0)
    d["cache_read"] += int(r.get("cache_read") or 0)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().split("\n\n")[0])
        return 0 if argv else 2
    L = Ledger()
    try:
        if argv[0] == "ingest":
            print("ingested %d new row(s)" % L.ingest())
            return 0
        if argv[0] == "totals":
            t = L.totals()
            if "--json" in argv:
                print(json.dumps(t))
                return 0
            for k in ("today", "d7", "d30", "month"):
                print("%-6s USD %8.4f  %5d calls  in %d out %d cached %d" % (k, t[k]["usd"], t[k]["calls"], t[k]["in"], t[k]["out"], t[k]["cache_read"]))
            for a, d in t["by_actor"].items():
                print("  %-24s USD %8.4f  %5d calls" % (a, d["usd"], d["calls"]))
            if t["unpriced"]:
                print("unpriced calls (model not in rates.json): %d" % t["unpriced"])
            c = t["cap"]
            print("cap: %s" % ("none" if not c["usd"] else "USD %d, spent %.2f (%d%%)%s" % (c["usd"], c["spent"], c["pct"], " — PAUSED" if c["paused"] else "")))
            return 0
        if argv[0] == "enforce":
            print(json.dumps(L.enforce_cap()))
            return 0
        print("unknown verb: " + argv[0], file=sys.stderr)
        return 2
    except OSError as e:
        print("ledger: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
