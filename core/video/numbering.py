"""an episode's SCENE numbering, when the scene numbers it differently.

the wishlist numbers episodes the TMDB way. the scene names releases the TVDB
way (that's what sonarr uses), and for about 40% of boulder's wanted episodes
the two disagree: 60 Minutes S59E25 is TVDB S58E48, AFV S37E02 is S37E01, One
Piece S23E1177 is S23E21. every release for such an episode read as "Wrong
season" or "Wrong episode" and the row never filled.

an air date is the one thing both catalogues agree on, so the episode is
mapped by it: the TVDB episode that aired the same day is its scene number.
TVDB seasons are cached a week (video.db tvdb_episode_cache); without a TVDB
key, or without a match, nothing changes.

usually the mapping just ADDS a numbering a release may carry. the exception
is when TVDB gives the TMDB number to a DIFFERENT episode (House Hunters'
TMDB S279E10 aired 10-04, TVDB's S279E10 aired 09-27): then a release named
S279E10 is the other episode, and only the scene number counts ("exclusive").
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("video.numbering")

CACHE_DAYS = 7

Numbering = Tuple[int, int]


def _day(value: Any) -> str:
    text = str(value or "")[:10]
    return text if len(text) == 10 else ""


def scene_numbering(want: Numbering, air_date: Any,
                    tvdb_seasons: Dict[int, Sequence[Tuple[int, Any]]],
                    same_day_wanted: Optional[Iterable[Numbering]] = None) -> Optional[Numbering]:
    """the TVDB (season, episode) for a TMDB episode, by its air date, or None
    when there's no unambiguous match or it's the same number anyway.

    ``tvdb_seasons`` is {season: [(episode, air_date), ...]} for the seasons
    worth looking in. two TVDB episodes on one day (a double bill) map by
    order, but only when the TMDB side has the same count of episodes that day
    (``same_day_wanted``); otherwise it's a guess and the answer is None."""
    day = _day(air_date)
    if not day:
        return None
    hits = sorted((int(s), int(e)) for s, eps in (tvdb_seasons or {}).items()
                  for e, d in eps or [] if _day(d) == day)
    if not hits:
        return None
    want = (int(want[0]), int(want[1]))
    if len(hits) == 1:
        found = hits[0]
    else:
        tmdb_side = sorted({(int(s), int(e)) for s, e in (same_day_wanted or [])} | {want})
        if len(tmdb_side) != len(hits):
            return None
        found = hits[tmdb_side.index(want)]
    return None if found == want else found


def number_belongs_elsewhere(want: Numbering, air_date: Any,
                             tvdb_seasons: Dict[int, Sequence[Tuple[int, Any]]]) -> bool:
    """True when TVDB has this exact (season, episode) on a different day: the
    TMDB number names another episode in the scene's numbering."""
    day = _day(air_date)
    eps = (tvdb_seasons or {}).get(int(want[0])) or []
    for e, d in eps:
        if int(e) == int(want[1]):
            return bool(day and _day(d) and _day(d) != day)
    return False


def _fresh(fetched_at: Any, now: _dt.datetime, days: int = CACHE_DAYS) -> bool:
    try:
        stamp = _dt.datetime.fromisoformat(str(fetched_at).replace("Z", "")[:19])
    except ValueError:
        return False
    return now - stamp < _dt.timedelta(days=days)


def tvdb_season(db: Any, client: Any, tvdb_id: int, season: int,
                now: Optional[_dt.datetime] = None) -> List[Tuple[int, str]]:
    """a TVDB season's [(episode, air_date)], from the week-old cache or TVDB.
    [] when there's no client or TVDB has nothing; never raises."""
    now = now or _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    try:
        cached = db.tvdb_season_cached(tvdb_id, season)
    except Exception:   # noqa: BLE001 - a cache miss, not a failure
        cached = None
    if cached and _fresh(cached[1], now):
        return list(cached[0])
    if client is None or not getattr(client, "enabled", False):
        return list(cached[0]) if cached else []
    try:
        eps = [(int(e["episode_number"]), e.get("air_date") or "")
               for e in (client.season_episodes(tvdb_id, season) or [])
               if e.get("episode_number") is not None]
    except Exception:   # noqa: BLE001 - TVDB down: keep whatever we had
        logger.debug("tvdb season fetch failed for %s S%s", tvdb_id, season, exc_info=True)
        return list(cached[0]) if cached else []
    try:
        try:
            db.tvdb_season_store(tvdb_id, season, eps, fetched_at=now)
        except TypeError:
            db.tvdb_season_store(tvdb_id, season, eps)
    except Exception:   # noqa: BLE001
        logger.debug("tvdb season cache write failed", exc_info=True)
    return eps


def _default_client(db: Any):
    try:
        from core.video.enrichment.clients import TVDBClient
        return TVDBClient(db.get_setting("tvdb_api_key"))
    except Exception:   # noqa: BLE001
        return None


def alt_numbering(item: Dict[str, Any], db: Any = None, client: Any = None,
                  same_day_wanted: Optional[Iterable[Numbering]] = None,
                  now: Optional[_dt.datetime] = None,
                  client_factory: Optional[Callable[[Any], Any]] = None) -> Optional[Numbering]:
    info = scene_info(item, db=db, client=client, same_day_wanted=same_day_wanted,
                      now=now, client_factory=client_factory)
    return info[0] if info else None


def scene_info(item: Dict[str, Any], db: Any = None, client: Any = None,
               same_day_wanted: Optional[Iterable[Numbering]] = None,
               now: Optional[_dt.datetime] = None,
               client_factory: Optional[Callable[[Any], Any]] = None):
    """the scene (TVDB) numbering for a wished episode row, or None.

    looks in the episode's own season and the ones either side: a season
    boundary is exactly where the two catalogues drift (60 Minutes' season
    turnover, Law & Order S25E22 = S26E01)."""
    tvdb_id = item.get("tvdb_id")
    season, episode = item.get("season_number"), item.get("episode_number")
    if not tvdb_id or season is None or episode is None or not _day(item.get("air_date")):
        return None
    if str(item.get("series_type") or "").lower() == "daily":
        return None   # dailies already match by air date
    if db is None:
        try:
            from api.video import get_video_db
            db = get_video_db()
        except Exception:   # noqa: BLE001
            return None
    if client is None:
        client = (client_factory or _default_client)(db)
    if same_day_wanted is None:
        try:
            same_day_wanted = db.wished_episodes_on(item.get("show_tmdb_id"), item.get("air_date"))
        except Exception:   # noqa: BLE001
            same_day_wanted = []
    try:
        s = int(season)
        seasons = {n: tvdb_season(db, client, int(tvdb_id), n, now=now)
                   for n in (s - 1, s, s + 1) if n >= 0}
        want = (s, int(episode))
        alt = scene_numbering(want, item.get("air_date"), seasons, same_day_wanted)
        if not alt:
            return None
        return alt, number_belongs_elsewhere(want, item.get("air_date"), seasons)
    except Exception:   # noqa: BLE001 - a numbering assist must never break a grab
        logger.debug("scene numbering failed for %s", item.get("show_title"), exc_info=True)
        return None


def annotate(items: List[Dict[str, Any]], db: Any = None, client: Any = None) -> List[Dict[str, Any]]:
    """set alt_season / alt_episode on every wished episode whose scene
    numbering differs. one TVDB client for the batch; seasons come from the
    week-old cache, so a run costs at most one TVDB call per new season."""
    if not items:
        return items
    if db is None:
        try:
            from api.video import get_video_db
            db = get_video_db()
        except Exception:   # noqa: BLE001
            return items
    if client is None:
        client = _default_client(db)
    for it in items:
        if it.get("episode_number") is None:
            continue
        info = scene_info(it, db=db, client=client)
        if info:
            (it["alt_season"], it["alt_episode"]), exclusive = info
            if exclusive:
                it["alt_exclusive"] = True
    return items


def ctx_alt(ctx: Dict[str, Any]):
    """the scene numbering a search context carries: (season, episode), or
    (season, episode, True) when it's exclusive (the TMDB number is another
    episode in the scene's numbering)."""
    try:
        if ctx.get("alt_season") is not None and ctx.get("alt_episode") is not None:
            alt = (int(ctx["alt_season"]), int(ctx["alt_episode"]))
            return alt + (True,) if ctx.get("alt_exclusive") else alt
    except (TypeError, ValueError, AttributeError):
        pass
    return None
