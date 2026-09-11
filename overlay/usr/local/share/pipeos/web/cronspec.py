"""cronspec — the five-field cron expression a scheduled job carries (#242).

Shared by schedtick.py (does this minute match?) and webd.py (is this
string a schedule? when does it next fire?), so the dashboard and the
tick can never disagree about what an expression means. Stdlib only.

Accepted: five fields — minute hour day-of-month month day-of-week —
each `*`, a number, a range `a-b`, a list `a,b,c`, a step `*/n` or
`a-b/n`, month and weekday names (jan..dec, sun..sat; 0 and 7 are both
Sunday); and the aliases @hourly @daily @midnight @weekly @monthly
@yearly @annually. Vixie's rule when BOTH day fields are restricted: the
job runs when either matches. Refused: anything else — a shell character,
a sixth field, a value out of range, a zero step, more than 64 characters.
Time base is the box clock, which is UTC on a Machine.
"""

import datetime
import re

MAX_LEN = 64
_CHARS = re.compile(r"^[0-9a-z*,/\- @]+$")
_ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
            "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
_MONTHS = {n: i + 1 for i, n in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_DAYS = {n: i for i, n in enumerate("sun mon tue wed thu fri sat".split())}
_FIELDS = (("minute", 0, 59, {}), ("hour", 0, 23, {}), ("day", 1, 31, {}), ("month", 1, 12, _MONTHS), ("weekday", 0, 7, _DAYS))


class CronError(ValueError):
    pass


class Spec:
    __slots__ = ("text", "minute", "hour", "day", "month", "weekday", "day_star", "weekday_star")

    def __repr__(self):
        return "Spec(%r)" % self.text


def _num(tok, names, label):
    if tok in names:
        return names[tok]
    if not tok.isdigit():
        raise CronError("%s: %r is not a number" % (label, tok))
    return int(tok)


def _field(text, label, lo, hi, names):
    """The set of values one field allows."""
    out = set()
    for part in text.split(","):
        if not part:
            raise CronError("%s: empty list element" % label)
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) == 0:
                raise CronError("%s: step must be a number above zero" % label)
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = _num(x, names, label), _num(y, names, label)
            if a > b:
                raise CronError("%s: range %s runs backwards" % (label, part))
        else:
            a = _num(part, names, label)
            b = hi if step > 1 else a
        if a < lo or b > hi:
            raise CronError("%s: %s is outside %d-%d" % (label, part, lo, hi))
        out.update(range(a, b + 1, step))
    return out


def parse(text):
    if not isinstance(text, str):
        raise CronError("a schedule is a string")
    t = " ".join(text.strip().lower().split())
    if not t or len(t) > MAX_LEN:
        raise CronError("a schedule is 1 to %d characters" % MAX_LEN)
    if not _CHARS.match(t):
        raise CronError("only digits, * , - / and month/day names belong in a schedule")
    t = _ALIASES.get(t, t)
    parts = t.split(" ")
    if len(parts) != 5:
        raise CronError("five fields: minute hour day month weekday (or @daily, @hourly, @weekly, @monthly)")
    s = Spec()
    s.text = t
    for (label, lo, hi, names), tok in zip(_FIELDS, parts):
        vals = _field(tok, label, lo, hi, names)
        if label == "weekday" and 7 in vals:
            vals.discard(7)
            vals.add(0)
        setattr(s, label, vals)
    # Vixie: a day field is "unrestricted" when it STARTS with * — so */2
    # is a star for the either/both rule below, exactly as crond reads it
    s.day_star = parts[2].startswith("*")
    s.weekday_star = parts[4].startswith("*")
    return s


def matches(spec, dt):
    """Does this minute fire? dt is a naive or aware datetime; seconds ignored."""
    if dt.minute not in spec.minute or dt.hour not in spec.hour or dt.month not in spec.month:
        return False
    wd = (dt.weekday() + 1) % 7   # python: Monday=0; cron: Sunday=0
    dom_ok = dt.day in spec.day
    dow_ok = wd in spec.weekday
    return day_ok(spec, dom_ok, dow_ok)


def day_ok(spec, dom_ok, dow_ok):
    """Vixie's rule verbatim: when either day field starts with * the two
    are ANDed (a literal * has every bit set, so it costs nothing; */2
    keeps its bits, so `*/2 * mon` is odd-day Mondays); when both are
    restricted, either matches."""
    if spec.day_star or spec.weekday_star:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def next_run(spec, after, limit_days=366):
    """The first minute strictly after `after` that matches, or None
    within limit_days. Walks days, then the allowed hours and minutes."""
    start = after.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    hours = sorted(spec.hour)
    minutes = sorted(spec.minute)
    day = start.replace(hour=0, minute=0)
    end = start + datetime.timedelta(days=limit_days)
    while day <= end:
        probe = day.replace(hour=hours[0], minute=minutes[0])
        if matches(spec, probe) or (day.date() == start.date()):
            # day-level fields match (or it is today: check finer)
            if day.month in spec.month:
                wd = (day.weekday() + 1) % 7
                dom_ok, dow_ok = day.day in spec.day, wd in spec.weekday
                if day_ok(spec, dom_ok, dow_ok):
                    for h in hours:
                        for m in minutes:
                            cand = day.replace(hour=h, minute=m)
                            if cand >= start:
                                return cand
        day += datetime.timedelta(days=1)
    return None


def describe(spec):
    """A short human line for the dashboard; falls back to the text."""
    t = spec.text
    if t == "0 * * * *":
        return "every hour"
    if t == "0 0 * * *":
        return "every day at 00:00"
    m = re.fullmatch(r"\*/(\d+) \* \* \* \*", t)
    if m:
        return "every %s minutes" % m.group(1)
    m = re.fullmatch(r"(\d+) (\d+) \* \* \*", t)
    if m:
        return "every day at %02d:%02d" % (int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d+) (\d+) \* \* ([a-z0-9,-]+)", t)
    if m:
        return "at %02d:%02d on %s" % (int(m.group(2)), int(m.group(1)), m.group(3))
    return t
