"""Per-indexer seed goals, the way Sonarr and Radarr do it.

One global goal is not enough. A private tracker wants a ratio of 2 or a week
of seed time or it bans you, a public one wants nothing at all. arr puts Seed
Ratio / Seed Time on the INDEXER, so each tracker gets its own tail, and
Sonarr adds a longer Season-Pack Seed Time because packs are what trackers
actually want kept alive.

This is the pure math. Overrides are stored as a json map keyed by prowlarr
indexer id:

    {"7": {"ratio": 2.0, "hours": 336, "pack_hours": 504}}

Any field left blank falls back to the global goal, so an override can set
just a ratio and inherit the time. A rule of 0 is a real value, it means "no
goal on this criterion for this indexer", which is how you exempt one tracker
from a global goal. That is why blank and 0 have to stay different things.

No i/o, no db, and it lives here rather than in core.torrent_clients because
importing anything from that package runs its __init__, which pulls all four
client adapters and the config manager. core.video.download_config is supposed
to import json and typing only.

Video uses this today. Music can't yet, its torrent_seed_grabs table has no
indexer id to key on.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

# nobody has this many indexers; the cap just stops a bad client stuffing the
# settings row with an unbounded blob.
MAX_RULES = 200


def _num(value: Any, cast, lo, hi) -> Optional[Any]:
    """A clamped number, or None when the field is blank/junk (= inherit).

    A NEGATIVE is junk, not a value. Clamping it up to the floor would land it
    on 0, and 0 means "exempt this tracker" — so a typo would silently switch
    a goal off. Never clamp into a sentinel."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n < lo:                 # NaN or below the floor → treat as unset
        return None
    return max(lo, min(hi, cast(n)))


def normalize_rule(raw: Any) -> Dict[str, Any]:
    """One indexer's rule, keys present only when actually set."""
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    ratio = _num(raw.get("ratio"), float, 0.0, 100.0)
    hours = _num(raw.get("hours"), int, 0, 24 * 365)
    pack_hours = _num(raw.get("pack_hours"), int, 0, 24 * 365)
    if ratio is not None:
        out["ratio"] = ratio
    if hours is not None:
        out["hours"] = hours
    if pack_hours is not None:
        out["pack_hours"] = pack_hours
    return out


def normalize_overrides(raw: Any) -> Dict[str, Dict[str, Any]]:
    """The stored map, cleaned. Accepts a json string (as stored) or a dict
    (as posted). Indexer ids are kept as strings so json round-trips clean.
    Empty rules are dropped, an indexer with nothing set is not an override."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, rule in list(raw.items())[:MAX_RULES]:
        try:
            idx = str(int(key))
        except (TypeError, ValueError):
            continue
        clean = normalize_rule(rule)
        if clean:
            out[idx] = clean
    return out


def dumps(overrides: Any) -> str:
    return json.dumps(normalize_overrides(overrides), sort_keys=True)


def effective_goal(cfg: Dict[str, Any], indexer_id: Any = None, *,
                   is_pack: bool = False) -> Tuple[float, int]:
    """The (ratio, hours) goal that applies to one grab.

    Resolution order per criterion: this indexer's rule, then the global goal.
    A season pack uses pack_hours when the indexer sets one, else the
    indexer's own hours, else the global hours. (0, 0) means seed forever,
    which is what happens when nothing is configured.
    """
    cfg = cfg or {}
    try:
        g_ratio = max(0.0, float(cfg.get("seed_ratio_goal") or 0))
    except (TypeError, ValueError):
        g_ratio = 0.0
    try:
        g_hours = max(0, int(float(cfg.get("seed_time_goal_hours") or 0)))
    except (TypeError, ValueError):
        g_hours = 0

    rule: Dict[str, Any] = {}
    if indexer_id is not None and indexer_id != "":
        try:
            rule = normalize_overrides(cfg.get("seed_overrides")).get(str(int(indexer_id)), {})
        except (TypeError, ValueError):
            rule = {}

    ratio = rule["ratio"] if "ratio" in rule else g_ratio
    if is_pack and "pack_hours" in rule:
        hours = rule["pack_hours"]
    elif "hours" in rule:
        hours = rule["hours"]
    else:
        hours = g_hours
    return float(ratio), int(hours)


def any_goal_set(cfg: Dict[str, Any]) -> bool:
    """True when ANY goal exists, global or per indexer. The sweep short
    circuits on this. Checking only the globals meant a user who set rules on
    one tracker and left the globals blank got a sweep that never ran."""
    cfg = cfg or {}
    # coerce, don't trust truthiness: the settings table stores these as TEXT
    # and bool("0.0") is True.
    if _num(cfg.get("seed_ratio_goal"), float, 0.0, 100.0) or \
            _num(cfg.get("seed_time_goal_hours"), int, 0, 24 * 365):
        return True
    for rule in normalize_overrides(cfg.get("seed_overrides")).values():
        if rule.get("ratio") or rule.get("hours") or rule.get("pack_hours"):
            return True
    return False


__all__ = ["normalize_rule", "normalize_overrides", "dumps", "effective_goal", "any_goal_set"]
