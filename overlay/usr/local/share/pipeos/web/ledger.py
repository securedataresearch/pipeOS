#!/usr/bin/env python3
"""ledger — every model call this Machine makes, costed (#246).

    pipeos-ledger-ingest ingest            tail the transcripts into the ledger
    pipeos-ledger-ingest totals [--json]   today / 7 days / 30 days, by actor
    pipeos-ledger-ingest enforce           the caps: DM at 80%, pause at 100%, each naming itself
    pipeos-ledger-ingest paused [--job NAME]   why NAME (or everything) may not run now — empty = go

Nobody on a Machine could say what it spent. The owner found out from the
bill. This reads what Claude Code already writes — every `assistant` line
in /data/claude/projects/*/*.jsonl carries the model and a usage block —
and appends one row per API call to /data/.pipeos/ledger/YYYY-MM.jsonl:

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

THE CAPS (#246, #302): three scopes, most restrictive wins, and every pause
names the cap that bit. The BOX cap is MONTHLY_CAP_USD from the card (0 =
none): at 80% one DM to the owner per month; at 100% the box is paused and
one more DM. An AGENT cap is `cap_usd` on the job in schedule.json: at 100%
of its own month-to-date that one agent is paused (one DM naming it), the
rest keep running. The CLUSTER cap (CLUSTER_CAP_USD, #302 part 2) pauses
the box against every member's month-to-date summed. State is one file,
`paused.json` ({"box","cluster","agents":{name}} → {scope,name,cap,spent,
since,text}), plus the plain `paused` marker every older reader already
honours, written iff the box or the cluster is paused. `paused_for(doc,
name)` answers "may this job run?" in the order cluster → box → agent and
hands back the text of whichever cap said no. Under a cap again (raised,
or a new month) its entry goes. An attached session is only ever warned,
never cut.

Seams (the probe, never production): PIPEOS_LEDGER_DIR, _TRANSCRIPTS,
_RATES, _CONF, _RUNS, _SESSIONS, _WEBCHAT_SID, _SCHEDULE (the job list,
for agent caps), _CLUSTER_LAST (the members' last-known notes, for the
cluster cap), _PIPE (the DM binary), _NOW (an ISO timestamp standing in
for the clock).
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

LEDGER_DIR = os.environ.get("PIPEOS_LEDGER_DIR", "/data/.pipeos/ledger")
TRANSCRIPTS = os.environ.get("PIPEOS_LEDGER_TRANSCRIPTS", "/data/claude/projects")
RATES = os.environ.get("PIPEOS_LEDGER_RATES", "/usr/local/share/pipeos/rates.json")
CONF = os.environ.get("PIPEOS_LEDGER_CONF", "/etc/pipeos/pipebox.conf")
RUNS_LOG = os.environ.get("PIPEOS_LEDGER_RUNS", "/data/.pipeos/schedule/runs.log")
SESSIONS_DIR = os.environ.get("PIPEOS_LEDGER_SESSIONS", "/data/pipebox/sessions")
WEBCHAT_SID = os.environ.get("PIPEOS_LEDGER_WEBCHAT_SID", "/data/pipebox/webchat/.dashboard-sid")
SCHEDULE = os.environ.get("PIPEOS_LEDGER_SCHEDULE", "/etc/pipeos/schedule.json")
CLUSTER_LAST = os.environ.get("PIPEOS_LEDGER_CLUSTER_LAST", "/data/pipeos/cluster/last")   # what each other member last said (cluster.py)
PIPE_BIN = os.environ.get("PIPEOS_LEDGER_PIPE", "pipe")
# The bulk volume. One name (pipeOS#330).
VOLUME_ROOTS = ("/data",)
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
                 runs_log=RUNS_LOG, sessions_dir=SESSIONS_DIR, webchat_sid=WEBCHAT_SID, pipe_bin=PIPE_BIN,
                 schedule=SCHEDULE, cluster_last=CLUSTER_LAST, members=None):
        self.dir = dir
        self.transcripts = transcripts
        # <root>/claude/projects -> <root>; the probe points transcripts at a
        # tmpdir, where this is simply that tmpdir's grandparent and matches
        # nothing, which is the right answer there.
        self.root = os.path.dirname(os.path.dirname(transcripts.rstrip("/")))
        self.rates_path = rates
        self.conf = conf
        self.schedule = schedule
        self.cluster_last = cluster_last
        self.members = members      # callable -> the OTHER members' ids, or None = every note counts (the probe)
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
        # Under the volume root, whichever name it is mounted at. These were
        # the flip still says /data and one written after says /data, and
        # both are the same directory — so the root comes off and the rest
        # is what identifies the actor.
        c = self._under_root((cwd or "").rstrip("/"))
        if c == "pipebox":
            return {"kind": "watch", "name": ""}
        if c == "pipebox/webchat":
            return {"kind": "assistant", "name": ""}
        return {"kind": "other", "name": os.path.basename(c) or c}

    def _under_root(self, path):
        """`path` relative to the bulk volume, under either of its names.
        Anything not on the volume comes back unchanged."""
        for root in (self.root,) + VOLUME_ROOTS:
            if root and path == root:
                return ""
            if root and path.startswith(root + "/"):
                return path[len(root) + 1:]
        return path

    # ---- ingest --------------------------------------------------------------

    def _cursor_path(self):
        return os.path.join(self.dir, "cursor.json")

    @staticmethod
    def _key(path):
        """A cursor key: the transcript's place inside the transcripts dir,
        never its absolute path.

        Keys were absolute until pipeOS#330. An absolute key ties the cursor
        to where the volume is mounted, and a cursor whose keys stop matching
        means off=0 with an empty id ring for every transcript: the next
        ingest re-appends the whole history and month-to-date spend roughly
        doubles, which is enough to trip the box, agent and cluster caps and
        pause them. The glob yields <project>/<file>.jsonl, so the last two
        components identify a transcript on their own."""
        parts = path.rstrip("/").split("/")
        return "/".join(parts[-2:]) if len(parts) >= 2 else path

    def _load_cursor(self):
        try:
            with open(self._cursor_path()) as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        cursor = {}
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            key = self._key(k)
            old = cursor.get(key)
            if old is None:
                cursor[key] = v
                continue
            # Both names for the same transcript (a box that ingested under
            # /data and again under /data). Keep the further-read offset and
            # the union of the id rings: reading less than we already have
            # is what double-counts.
            keep, other = (v, old) if v.get("off", 0) >= old.get("off", 0) else (old, v)
            seen = list(other.get("seen") or []) + list(keep.get("seen") or [])
            merged = dict(keep)
            merged["seen"] = seen[-SEEN_RING:]
            cursor[key] = merged
        return cursor

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
        cursor = self._load_cursor()
        maps = (self._job_sids(), self._listener_sids(), self._dashboard_sid())
        new = 0
        out_files = {}
        for path in sorted(glob.glob(os.path.join(self.transcripts, "*", "*.jsonl"))):
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = self._key(path)
            c = cursor.get(key, {})
            off = c.get("off", 0)
            seen = list(c.get("seen", []))
            if c.get("ino") != st.st_ino or st.st_size < off:
                off = 0   # start over, but KEEP the id ring: it is what stops a rewrite double-counting
            if st.st_size == off:
                cursor[key] = {"ino": st.st_ino, "off": off, "seen": seen[-SEEN_RING:]}
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
            cursor[key] = {"ino": st.st_ino, "off": off + consumed, "seen": seen[-SEEN_RING:]}
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
        key = tuple((m, self._stat(m)) for m in months) + (self._conf_stat(), self._file_stat(self.schedule), self._file_stat(self.cluster_last))
        if self._totals_key == key and self._totals is not None:
            # the cap's live half is not a function of the files the key
            # watches: a lifted cap removes the paused marker without a new
            # row, and /api/status kept saying "paused" for up to a minute
            # (the single-box pass on zero, 2026-09-16)
            self._refresh_live(self._totals["cap"], now)
            return self._totals
        t0 = {"usd": 0.0, "calls": 0, "in": 0, "out": 0, "cache_read": 0}
        today, d7, d30 = dict(t0), dict(t0), dict(t0)
        month = dict(t0)
        by_actor = {}
        month_by_actor = {}
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
            a = r.get("actor") or {}
            k = a.get("kind", "other") + (":" + a["name"] if a.get("name") else "")
            if ts.strftime("%Y-%m") == now.strftime("%Y-%m"):
                _acc(month, r, usd)
                month_by_actor.setdefault(k, dict(t0))
                _acc(month_by_actor[k], r, usd)
            if age < 0 or age >= 30:
                continue
            _acc(d30, r, usd)
            daily[29 - age] += usd
            if age < 7:
                _acc(d7, r, usd)
            if age == 0:
                _acc(today, r, usd)
            by_actor.setdefault(k, dict(t0))
            _acc(by_actor[k], r, usd)
        for d in (today, d7, d30, month) + tuple(by_actor.values()) + tuple(month_by_actor.values()):
            d["usd"] = round(d["usd"], 4)
        cap = self.cap()
        ccap = self.cluster_cap()
        peers, prow, silent = self.peers_month_usd(now)
        cspent = round(month["usd"] + peers, 4)
        agents = {}
        for name, acap in self.agent_caps().items():
            spent = month_by_actor.get("job:" + name, t0)["usd"]
            agents[name] = {"usd": acap, "spent": spent, "pct": (round(spent * 100 / acap) if acap else 0), "paused": ""}
        out = {"today": today, "d7": d7, "d30": d30, "month": month,
               "by_actor": dict(sorted(by_actor.items(), key=lambda kv: -kv[1]["usd"])),
               "month_by_actor": dict(sorted(month_by_actor.items(), key=lambda kv: -kv[1]["usd"])),
               "daily": [round(x, 4) for x in daily], "unpriced": unpriced, "estimate": True,
               "last_row_ts": last_ts, "last_ingest_ts": self._last_ingest(),
               # "usd"/"spent"/"pct"/"paused"/"warned" are the box cap, as they always were;
               # "agents" and "paused_by" are #302's additions
               "cap": {"usd": cap, "spent": month["usd"], "pct": (round(month["usd"] * 100 / cap) if cap else 0),
                       "warned": False, "paused": "", "agents": agents, "paused_by": None,
                       "cluster": {"usd": ccap, "spent": cspent, "pct": (round(cspent * 100 / ccap) if ccap else 0),
                                   "members": prow, "not_reporting": silent}}}
        self._refresh_live(out["cap"], now)
        self._totals_key, self._totals = key, out
        return out

    def _refresh_live(self, cap, now):
        """The half of the cap block that changes without a new ledger row:
        which caps are paused right now, and whether the 80% DM went."""
        doc = read_paused(self.paused_json_path())
        cap["paused"] = self.paused_text()
        cap["warned"] = os.path.exists(self._marker("warned", now))
        cap["paused_by"] = doc.get("cluster") or doc.get("box") or None
        for name, a in cap.get("agents", {}).items():
            a["paused"] = (doc.get("agents", {}).get(name) or {}).get("text", "")

    def _file_stat(self, path):
        try:
            st = os.stat(path)
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

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
        """The box cap (MONTHLY_CAP_USD), 0 = none."""
        v = self.conf_value("MONTHLY_CAP_USD")
        return int(v) if v.isdigit() else 0

    def cluster_cap(self):
        """The cluster cap (CLUSTER_CAP_USD), 0 = none — one number on every
        member's card, each member evaluating it on its own (#302 part 2)."""
        v = self.conf_value("CLUSTER_CAP_USD")
        return int(v) if v.isdigit() else 0

    def peers_month_usd(self, now):
        """Every other CURRENT member's month-to-date as this box last saw it
        (cluster.py's last-known notes): an unreachable member counts at its
        last reported figure; a note from another month counts for nothing
        (a box off since August must not count August against September); a
        member whose note carries no figure (an older release, a broken
        ledger) is listed as not reporting — never silently dropped — and a
        note for a box that is no longer a member is ignored.
        Returns (total, [{"id","month_usd","t"}], [ids not reporting])."""
        month = now.strftime("%Y-%m")
        ids = None
        if self.members is not None:
            try:
                ids = set(self.members())
            except Exception:       # noqa: BLE001 — a broken member list must not stop the sum
                ids = None
        rows, silent = [], []
        try:
            names = sorted(n for n in os.listdir(self.cluster_last) if n.endswith(".json"))
        except OSError:
            names = []
        seen = set()
        for n in names:
            mid = n[:-5]
            if ids is not None and mid not in ids:
                continue
            seen.add(mid)
            try:
                with open(os.path.join(self.cluster_last, n)) as f:
                    d = json.load(f)
            except (OSError, ValueError):
                silent.append(mid); continue
            usd = d.get("month_usd") if isinstance(d, dict) else None
            if not isinstance(usd, (int, float)) or isinstance(usd, bool):
                silent.append(mid); continue
            if d.get("month") != month:
                continue
            rows.append({"id": mid, "month_usd": round(float(usd), 4), "t": d.get("t")})
        if ids is not None:
            silent += sorted(ids - seen)        # a member this box has never heard from
        return round(sum(r["month_usd"] for r in rows), 4), rows, sorted(set(silent))

    def agent_caps(self):
        """{job name: cap_usd} for every job in schedule.json that carries one."""
        try:
            with open(self.schedule) as f:
                jobs = json.load(f).get("jobs", [])
        except (OSError, ValueError, AttributeError):
            return {}
        out = {}
        for j in jobs:
            if isinstance(j, dict) and j.get("name") and isinstance(j.get("cap_usd"), int) and not isinstance(j.get("cap_usd"), bool) and j["cap_usd"] > 0:
                out[j["name"]] = j["cap_usd"]
        return out

    def _marker(self, kind, now):
        return os.path.join(self.dir, "%s-%s" % (kind, now.strftime("%Y-%m")))

    def paused_path(self):
        return os.path.join(self.dir, "paused")

    def paused_json_path(self):
        return os.path.join(self.dir, "paused.json")

    def paused_text(self):
        try:
            with open(self.paused_path()) as f:
                return f.read().strip()
        except OSError:
            return ""

    def paused_for(self, name=""):
        """Why job `name` may not run now — the text of the most restrictive
        cap that is reached (cluster, box, then the agent's own) — or ""."""
        return why_paused(name, self.paused_path(), self.paused_json_path())

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
        """Compare this month's spend to every cap. Idempotent: one DM per
        month per threshold per scope (markers); an entry is written into
        paused.json the moment a cap is reached and removed the moment it is
        not; the plain `paused` marker follows the box and cluster entries.
        Every entry carries the text that names its cap."""
        now = _now()
        day = now.strftime("%Y-%m-%d")
        t = self.totals()
        os.makedirs(self.dir, exist_ok=True)
        before = read_paused(self.paused_json_path())
        doc = {"box": None, "cluster": None, "agents": {}}
        cap = self.cap()
        spent = t["month"]["usd"]
        state = {"cap": cap, "spent": spent, "warned": False, "paused": False, "changed": False, "agents_paused": []}
        doc["box"], state["warned"] = self._scope_hit(
            "box", "", cap, spent, day, now, before,
            pause_msg="usage: this Machine reached its monthly cap (USD %.2f of %d) — scheduled jobs are paused; interactive sessions keep working. Raise the cap under Usage to resume." % (spent, cap),
            warn_msg="usage: USD %.2f of this Machine's %d monthly cap (%d%%) — scheduled jobs pause at 100%%" % (spent, cap, spent * 100 / cap if cap else 0))
        c = t["cap"]["cluster"]
        nm = len(c["members"]) + 1
        silent = c.get("not_reporting") or []
        tail = (" — %d Machine(s) not reporting their spend" % len(silent)) if silent else ""
        doc["cluster"], _ = self._scope_hit(
            "cluster", "", c["usd"], c["spent"], day, now, before,
            pause_msg="usage: the cluster reached its monthly cap (USD %.2f of %d across %d Machines%s) — scheduled jobs are paused on every member; interactive sessions keep working. Raise the cluster cap under Usage to resume." % (c["spent"], c["usd"], nm, tail),
            warn_msg="usage: USD %.2f of the cluster's %d monthly cap (%d%%%s) — scheduled jobs on every member pause at 100%%" % (c["spent"], c["usd"], c["pct"], tail))
        state["paused"] = bool(doc["box"] or doc["cluster"])
        for name, acap in self.agent_caps().items():
            aspent = t["month_by_actor"].get("job:" + name, {}).get("usd", 0.0)
            if aspent * 100 / acap >= 100:
                doc["agents"][name] = _entry("agent", name, acap, aspent, day, (before.get("agents", {}).get(name) or {}).get("since"))
                state["agents_paused"].append(name)
                m = self._marker("agent-%s-paused" % name, now)
                if not os.path.exists(m):
                    self.dm("usage: agent %s reached its monthly cap (USD %.2f of %d) — %s is paused; other jobs keep running. pipeos schedule set %s --cap N to raise it." % (name, aspent, acap, name, name))
                    open(m, "w").close()
        state["changed"] = self._write_markers(doc, before)
        state["paused_by"] = doc["cluster"] or doc["box"]
        self._totals_key = None
        return state

    def _scope_hit(self, scope, name, cap, spent, day, now, before, pause_msg, warn_msg):
        """One threshold machine for the box and the cluster: at 100% an
        entry (and one DM per month), at 80% one DM per month. Marker files
        keep their historical names (warned-/paused-YYYY-MM for the box,
        cluster-warned-/cluster-paused- for the cluster). Returns (entry or
        None, warned-now)."""
        if not cap:
            return None, False
        pct = spent * 100 / cap
        prefix = "" if scope == "box" else scope + "-"
        warned = self._marker(prefix + "warned", now)
        paused_m = self._marker(prefix + "paused", now)
        hit = None
        if pct >= 100:
            hit = _entry(scope, name, cap, spent, day, (before.get(scope) or {}).get("since"))
            if not os.path.exists(paused_m):
                self.dm(pause_msg)
                open(paused_m, "w").close()
        warned_now = False
        if pct >= WARN_PCT and not os.path.exists(warned):
            if pct < 100:
                self.dm(warn_msg)
            open(warned, "w").close()
            warned_now = True
        return hit, warned_now

    def _write_markers(self, doc, before):
        """paused.json and the plain marker, both tmp + rename; True when
        what is paused changed. Written once per pause: the entries carry the
        spend at the moment the cap bit, not a number that keeps moving while
        an attached session spends (a reader mid-rewrite must never see an
        empty marker). Both files gone when nothing is paused."""
        changed = (_strip(doc) != _strip(before))
        for scope in ("box", "cluster"):
            if doc[scope] and before.get(scope) and before[scope].get("cap") == doc[scope]["cap"]:
                doc[scope] = _rendered(before[scope])            # same pause (its spend and since), today's sentence
        for name in list(doc["agents"]):
            b = before.get("agents", {}).get(name)
            if b and b.get("cap") == doc["agents"][name]["cap"]:
                doc["agents"][name] = _rendered(b)
        global_entry = doc["cluster"] or doc["box"]
        if doc["box"] or doc["cluster"] or doc["agents"]:
            if changed or doc != before:
                _replace(self.paused_json_path(), json.dumps(doc))
        elif os.path.exists(self.paused_json_path()):
            os.unlink(self.paused_json_path())
        if global_entry:
            if self.paused_text() != global_entry["text"]:
                _replace(self.paused_path(), global_entry["text"] + "\n")
                changed = True
        elif os.path.exists(self.paused_path()):
            os.unlink(self.paused_path())
            changed = True
        return changed


def pause_text(scope, name, cap, spent, day):
    """The one sentence a paused scope shows everywhere, ending with the verb
    that lifts THAT cap (every reader — the 409, the runner's log, the
    banner, selfcheck — shows the sentence as is, so the verb rides with
    it; the plain marker carries only the text). The box text keeps its
    historical prefix — the runner's log, selfcheck and the probes match
    on "monthly cap"."""
    if scope == "agent":
        return ("agent %s: monthly cap USD %d reached %s (spent %.2f; the per-agent cap on %s); %s resumes on the 1st or when its cap is raised (pipeos schedule set %s --cap N|none)"
                % (name, cap, day, spent, name, name, name))
    if scope == "cluster":
        return ("cluster monthly cap USD %d reached %s (spent %.2f across the cluster; CLUSTER_CAP_USD); scheduled jobs on every member resume on the 1st or when the cluster cap is raised under Usage (pipeos usage cap --cluster N|none)"
                % (cap, day, spent))
    return ("monthly cap USD %d reached %s (spent %.2f; this Machine's cap); scheduled jobs resume on the 1st or when the cap is raised under Usage (pipeos usage cap N|none)"
            % (cap, day, spent))


def _rendered(e):
    """A kept entry with its sentence rendered by THIS pause_text: the
    spend and the day stay what they were when the cap bit, the words are
    today's — a deploy that rewords the sentence reaches a box that was
    already paused (else the old words would sit in the marker till the 1st)."""
    e = dict(e)
    e["text"] = pause_text(e.get("scope", "box"), e.get("name", ""), e.get("cap", 0), e.get("spent", 0.0), e.get("since", ""))
    return e


def _entry(scope, name, cap, spent, day, since):
    return {"scope": scope, "name": name, "cap": cap, "spent": round(spent, 4), "since": since or day,
            "text": pause_text(scope, name, cap, spent, since or day)}


def _replace(path, text):
    tmp = path + ".new"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def why_paused(name="", marker=None, doc_path=None):
    """THE reader every gate uses (the runner's shell twin reads the same two
    files): the plain marker's sentence when it is there (the box or the
    cluster: every job stops), else `name`'s own entry in paused.json. ""
    means go. Defaults to this box's ledger dir."""
    marker = marker or os.path.join(LEDGER_DIR, "paused")
    doc_path = doc_path or os.path.join(LEDGER_DIR, "paused.json")
    try:
        with open(marker) as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    return paused_for(read_paused(doc_path), name)


def _strip(doc):
    """The comparable shape of a paused doc: what is paused, not the cents."""
    return (bool(doc.get("box")), bool(doc.get("cluster")), tuple(sorted(doc.get("agents", {}))))


def read_paused(path):
    """paused.json as a dict, an empty doc when absent or unreadable."""
    try:
        with open(path) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError
    except (OSError, ValueError):
        return {"box": None, "cluster": None, "agents": {}}
    d.setdefault("box", None); d.setdefault("cluster", None); d.setdefault("agents", {})
    return d


def paused_for(doc, name=""):
    """The text of the most restrictive reached cap that stops `name`:
    cluster, then the box, then the agent's own. "" when it may run. With
    no name: the global reason only."""
    for scope in ("cluster", "box"):
        if doc.get(scope):
            return doc[scope].get("text", "")
    if name and doc.get("agents", {}).get(name):
        return doc["agents"][name].get("text", "")
    return ""


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
            print("cap: %s" % ("none" if not c["usd"] else "USD %d, spent %.2f (%d%%)%s" % (c["usd"], c["spent"], c["pct"], " — PAUSED: " + c["paused"] if c["paused"] else "")))
            cc = c.get("cluster") or {}
            if cc.get("usd"):
                print("cluster cap: USD %d, spent %.2f across %d Machines (%d%%)%s" % (cc["usd"], cc["spent"], len(cc.get("members", [])) + 1, cc["pct"],
                      (" — not reporting: " + ", ".join(cc["not_reporting"])) if cc.get("not_reporting") else ""))
            for name, a in c.get("agents", {}).items():
                print("  agent %-18s cap USD %d, spent %.2f (%d%%)%s" % (name, a["usd"], a["spent"], a["pct"], " — PAUSED" if a["paused"] else ""))
            return 0
        if argv[0] == "enforce":
            if L.cluster_cap():
                try:            # "enforced now" means with the members' figures of now, not the worker's last minute
                    import cluster  # noqa: PLC0415 — same dir; only when a cluster cap is set
                    cluster.refresh_last(timeout=5)
                except Exception:   # noqa: BLE001 — an unreachable member counts at its last figure
                    pass
            print(json.dumps(L.enforce_cap()))
            return 0
        if argv[0] == "paused":
            # the runner's and the tick's question: may this job run? An
            # empty line is yes; a sentence is the cap that says no.
            name = argv[argv.index("--job") + 1] if "--job" in argv and argv.index("--job") + 1 < len(argv) else ""
            why = L.paused_for(name)
            if why:
                print(why)
            return 0
        print("unknown verb: " + argv[0], file=sys.stderr)
        return 2
    except OSError as e:
        print("ledger: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
