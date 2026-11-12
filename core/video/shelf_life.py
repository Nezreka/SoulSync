"""Shelf Life — how much a title has earned its space, as a score from 0 to 100.

Pure: give it one title's facts (what the library already knows about it) and
the run's settings, get back a score, the gate that protects it if any, and the
reasons in plain words. No I/O; the Shelf Life job gathers the facts and stores
the result. Higher means keep; a title below the threshold is a candidate.

Gates come first. A gated title gets no score at all and is never a candidate:
someone is playing it right now or is part-way through it, it's a show someone
has watched lately, it's marked Keep, its requester hasn't watched it yet
(within the grace window), it's a followed show still airing, or it only just
arrived.

Five signals, each 0-100 (higher = keep), mixed by the preset's weights:

- watch: how many people have watched it, how recently, how often. each
  person adds half of what's left (one 50, two 75, three 88), so the score
  doesn't sink as the server gains accounts. never watched decays toward 0
  the longer it sits.
- replaceability: on how few streaming services it is in the region (none is
  the hardest to get back), and how hard it was to grab.
- cultural: blended critic and audience ratings, weighted by how many votes
  stand behind them, plus awards and age.
- size: gigabytes per hour of runtime against the library's median for that
  kind; a bloated copy scores low.
- requests: asked for and watched by the asker scores up; asked for and never
  watched by them scores down; never requested is neutral.

A signal with no data counts as neutral (50) and says so in the reasons; it
never drags a title down on its own.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional

NEUTRAL = 50.0
DEFAULT_THRESHOLD = 30

SIGNALS = ("watch", "replaceability", "cultural", "size", "requests")

# weights sum to 100 in every preset
PRESETS: Dict[str, Dict[str, int]] = {
    "balanced": {"watch": 40, "replaceability": 20, "cultural": 20, "size": 10, "requests": 10},
    "space_first": {"watch": 35, "replaceability": 15, "cultural": 15, "size": 25, "requests": 10},
    "collector": {"watch": 30, "replaceability": 30, "cultural": 30, "size": 5, "requests": 5},
}
DEFAULT_PRESET = "balanced"

DEFAULT_SETTINGS = {
    "preset": DEFAULT_PRESET,
    "threshold": DEFAULT_THRESHOLD,
    "request_grace_days": 90,
    "new_arrival_days": 30,
}

RECENCY_HALF_LIFE_DAYS = 180       # a watch half a year ago counts half
UNWATCHED_HALF_LIFE_DAYS = 365     # never watched: 50 at arrival, 25 after a year
PLAYS_FOR_FULL = 8                 # the eighth play is as good as it gets
CLASSIC_YEARS = 25

# gate keys, in the order they're checked; the first that holds is the one shown
GATES = ("playing", "in_progress", "watching", "keep", "requested", "following", "new_arrival")
GATE_TEXT = {
    "playing": "someone is playing it right now",
    "in_progress": "someone is part-way through it",
    "watching": "someone has watched an episode lately",
    "keep": "marked Keep",
    "requested": "requested and not yet watched by the requester",
    "following": "a followed show that is still airing",
    "new_arrival": "added recently",
}


def _days(stamp: Any, now: datetime) -> Optional[float]:
    """days from an ISO-ish DB stamp to now; None when it can't be read."""
    if not stamp:
        return None
    raw = str(stamp)[:19].replace("T", " ")
    for text, fmt in ((raw, "%Y-%m-%d %H:%M:%S"), (raw[:10], "%Y-%m-%d")):
        try:
            return max(0.0, (now - datetime.strptime(text, fmt)).total_seconds() / 86400)
        except ValueError:
            continue
    return None


def _num(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def weights_for(preset: Any) -> Dict[str, int]:
    """the preset's weights; an unknown name falls back to Balanced."""
    return dict(PRESETS.get(str(preset or ""), PRESETS[DEFAULT_PRESET]))


def gate(facts: Dict[str, Any], settings: Dict[str, Any], now: datetime) -> Optional[str]:
    """the first gate that protects this title, or None."""
    if facts.get("playing"):
        return "playing"
    if facts.get("in_progress"):
        return "in_progress"
    if facts.get("kind") == "show" and facts.get("watching"):
        return "watching"
    if facts.get("keep"):
        return "keep"
    req = facts.get("request") or {}
    if req.get("approved") and not req.get("requester_watched"):
        arrived = _days(req.get("arrived_at"), now)
        grace = _num(settings.get("request_grace_days"))
        grace = DEFAULT_SETTINGS["request_grace_days"] if grace is None else grace
        if arrived is None or arrived < grace:
            return "requested"
    if facts.get("kind") == "show" and facts.get("followed") and facts.get("airing"):
        return "following"
    added = _days(facts.get("added_at"), now)
    fresh = _num(settings.get("new_arrival_days"))
    fresh = DEFAULT_SETTINGS["new_arrival_days"] if fresh is None else fresh
    if added is not None and added < fresh:
        return "new_arrival"
    return None


# ── the five signals: each returns (value 0-100 or None, reason text) ────────

def watch_signal(facts: Dict[str, Any], now: datetime):
    """how many people watched it, how recently, how often. ``watch`` holds
    {active, watched, people, last_viewed_at, plays, history_since}: ``active``
    is how many people watched anything lately (none means watching can't
    tell), ``watched`` how many watched this title, ``people`` the same count
    weighted by how much of it each one watched (a show is the share of its
    episodes; defaults to ``watched``). ``history_since`` is where the
    server's play history starts, when it was read: before it, nobody's
    plays but the ones the library stores itself are known."""
    w = facts.get("watch") or {}
    active = int(w.get("active") or 0)
    if active <= 0:
        return None, "no one has watched anything lately, so watching can't tell"
    watched = int(w.get("watched") or 0)
    people = _num(w.get("people"))
    if people is None:
        people = float(watched)
    people = max(0.0, people)
    plays = max(0, int(w.get("plays") or 0))
    since = _days(w.get("last_viewed_at"), now)
    if watched <= 0:
        tenure = _days(facts.get("added_at"), now)
        known = _days(w.get("history_since"), now)
        if tenure is not None and known is not None and known < tenure:
            # plays before the history starts aren't known: count the time
            # unwatched from there, never from further back
            value = NEUTRAL * 0.5 ** (known / UNWATCHED_HALF_LIFE_DAYS)
            return value, "no plays on record since %s" % _month(w.get("history_since"))
        if tenure is None:
            return 0.0, "never watched"
        value = NEUTRAL * 0.5 ** (tenure / UNWATCHED_HALF_LIFE_DAYS)
        if facts.get("added_from") == "first_seen":
            # the server's add date wasn't trusted: count from SoulSync's first sight
            return value, "never watched since SoulSync first saw it %d days ago" % int(tenure)
        return value, "never watched in %d days" % int(tenure)
    # each person adds half of what's left: one 50, two 75, three 88
    audience = 1.0 - 0.5 ** people
    # a watch the server gave no date for still counts as watched; it just
    # earns nothing for recency (never guess a date)
    recency = 0.0 if since is None else 0.5 ** (since / RECENCY_HALF_LIFE_DAYS)
    repeat = min(1.0, math.log2(1 + plays) / math.log2(1 + PLAYS_FOR_FULL))
    value = 100.0 * (0.5 * audience + 0.35 * recency + 0.15 * repeat)
    who = "watched by %d %s" % (watched, "person" if watched == 1 else "people")
    when = "date unknown" if since is None else "last %d days ago" % int(since)
    return _clamp(value), "%s, %s" % (who, when)


def _month(stamp: Any) -> str:
    """'Mar 2025' from a DB stamp; the raw text when it can't be read."""
    raw = str(stamp or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%b %Y")
    except ValueError:
        return raw or "the start of the server's history"


_RARITY = {0: 100.0, 1: 60.0, 2: 40.0, 3: 25.0}


def replaceability_signal(facts: Dict[str, Any]):
    """streaming services in the region (``streaming_status`` ok/not_found plus
    ``streaming_count``) and how hard the grab was (``failed_grabs``: grabs of
    it that failed or failed to import, from the download history; None when
    it never went through a grab here). either half alone answers."""
    status = facts.get("streaming_status")
    rarity = None
    if status == "not_found":
        rarity, count = _RARITY[0], 0
    elif status == "ok":
        count = facts.get("streaming_count")
        count = 1 if count is None else max(0, int(count))
        rarity = _RARITY.get(count, 15.0)
    failed = facts.get("failed_grabs")
    difficulty = None
    if failed is not None:
        failed = max(0, int(failed))
        # first try: 0; one failure: 30; three: 60; seven: 90
        difficulty = _clamp(30.0 * math.log2(1 + failed))
    if rarity is None and difficulty is None:
        return None, "no streaming or grab data"
    parts = []
    if rarity is not None:
        parts.append("not streaming anywhere here" if count == 0 else
                     "streams on %d service%s" % (count, "" if count == 1 else "s"))
    if failed:
        parts.append("%d failed grab%s before it landed" % (failed, "" if failed == 1 else "s"))
    if rarity is None:
        value = difficulty
    elif difficulty is None:
        value = rarity
    else:
        value = 0.6 * rarity + 0.4 * difficulty
    return value, ", ".join(parts) or "easy to get again"


def cultural_signal(facts: Dict[str, Any], now: datetime):
    """ratings on a 0-100 scale (IMDb/TMDB x10, RT, Metacritic, AniList), the
    Trakt vote count as confidence, awards ('oscar' > 'winner'), age."""
    r = facts.get("ratings") or {}
    scaled = []
    for key, mult in (("imdb", 10), ("tmdb", 10), ("rt", 1), ("metacritic", 1), ("anilist", 1)):
        v = _num(r.get(key))
        if v is not None and v > 0:
            scaled.append(_clamp(v * mult))
    awards = facts.get("awards")
    if not scaled and awards not in ("oscar", "winner"):
        return None, "no rating data"
    base = sum(scaled) / len(scaled) if scaled else NEUTRAL
    votes = _num(r.get("trakt_votes"))
    if votes is not None and votes >= 0:
        # few votes pull a rating toward neutral; ~10k votes stand on their own
        conf = min(1.0, math.log10(1 + votes) / 4)
        base = NEUTRAL + (base - NEUTRAL) * (0.5 + 0.5 * conf)
    bonus, notes = 0.0, []
    if awards == "oscar":
        bonus, notes = 20.0, ["Oscar winner"]
    elif awards == "winner":
        bonus, notes = 10.0, ["award winner"]
    year = facts.get("year")
    try:
        age = now.year - int(year)
    except (TypeError, ValueError):
        age = None
    if age is not None and age >= CLASSIC_YEARS and base >= 60:
        bonus += 10.0
        notes.append("a %d-year-old title still well rated" % age)
    rated = "rated %d/100" % round(base) if scaled else "no ratings"
    return _clamp(base + bonus), ", ".join([rated] + notes)


def size_signal(facts: Dict[str, Any]):
    """GB per runtime hour against the median for its kind: at the median 50,
    twice it 33, half it 67."""
    size = _num(facts.get("size_bytes"))
    minutes = _num(facts.get("runtime_minutes"))
    median = _num(facts.get("median_gb_per_hour"))
    if not size or not minutes or minutes <= 0 or not median or median <= 0:
        return None, "no size or runtime data"
    gbh = (size / 1024 ** 3) / (minutes / 60.0)
    ratio = gbh / median
    return _clamp(100.0 / (1.0 + ratio)), "%.1f GB per hour (library median %.1f)" % (gbh, median)


def requests_signal(facts: Dict[str, Any], settings: Dict[str, Any], now: datetime):
    """asked-for and watched by the asker: 80. asked-for and never watched past
    the grace window: 35, halving each year after. never requested: neutral."""
    req = facts.get("request")
    if not req or not req.get("approved"):
        return NEUTRAL, "not requested"
    if req.get("requester_watched"):
        return 80.0, "requested and watched by the requester"
    if req.get("requester_watched") is None and "requester_watched" in req:
        # a requester with no watch state of their own (no linked account):
        # what they watched isn't known, so it can't count against them
        return NEUTRAL, "requested; the requester's watching isn't known"
    arrived = _days(req.get("arrived_at"), now)
    grace = _num(settings.get("request_grace_days"))
    grace = DEFAULT_SETTINGS["request_grace_days"] if grace is None else grace
    if arrived is None:
        return NEUTRAL, "requested, arrival date unknown"
    value = 35.0 * 0.5 ** (max(0.0, arrived - grace) / 365.0)
    return value, "requested %d days ago, never watched by the requester" % int(arrived)


def score(facts: Dict[str, Any], settings: Optional[Dict[str, Any]] = None,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """one title's Shelf Life. returns {score, gate, candidate, signals, reasons}:
    ``score`` is None when gated; ``signals`` maps each signal to its value (None
    = no data, counted as neutral); ``reasons`` are the signals furthest from
    neutral by their weight, most telling first, as {signal, text}."""
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    now = now or datetime.now()
    g = gate(facts, settings, now)
    if g:
        return {"score": None, "gate": g, "candidate": False, "signals": {},
                "reasons": [{"signal": "gate", "text": GATE_TEXT[g]}]}
    weights = weights_for(settings.get("preset"))
    results = {
        "watch": watch_signal(facts, now),
        "replaceability": replaceability_signal(facts),
        "cultural": cultural_signal(facts, now),
        "size": size_signal(facts),
        "requests": requests_signal(facts, settings, now),
    }
    total = 0.0
    pull: List[tuple] = []
    for name in SIGNALS:
        value, text = results[name]
        used = NEUTRAL if value is None else value
        total += weights[name] * used
        pull.append((abs(used - NEUTRAL) * weights[name], name, text, value is None))
    final = int(round(total / 100.0))
    threshold = _num(settings.get("threshold"))
    threshold = DEFAULT_THRESHOLD if threshold is None else threshold
    # the most telling signals first (a neutral one tells nothing and is left
    # out); every no-data signal follows, as a note
    told = sorted((t for t in pull if not t[3] and t[0] > 0), key=lambda t: -t[0])
    reasons = [{"signal": n, "text": t} for _p, n, t, _nd in told[:3]]
    reasons += [{"signal": n, "text": t} for _p, n, t, nodata in pull if nodata]
    return {"score": final, "gate": None, "candidate": final < threshold,
            "signals": {n: (None if results[n][0] is None else round(results[n][0], 1)) for n in SIGNALS},
            "reasons": reasons}


def median_gb_per_hour(rows: List[Dict[str, Any]]) -> Optional[float]:
    """the median GB per runtime hour over rows of {size_bytes, runtime_minutes};
    None when no row has both."""
    vals = []
    for r in rows:
        size, minutes = _num(r.get("size_bytes")), _num(r.get("runtime_minutes"))
        if size and minutes and minutes > 0:
            vals.append((size / 1024 ** 3) / (minutes / 60.0))
    if not vals:
        return None
    vals.sort()
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
