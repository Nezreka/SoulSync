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


# how far past a lumped TMDB season to walk TVDB's seasons (Dragon Ball's one
# TMDB season of 153 is five on TVDB)
REGROUP_MAX_SEASONS = 40


def _shift(day: str, days: int) -> str:
    try:
        return (_dt.date.fromisoformat(day) + _dt.timedelta(days=days)).isoformat()
    except ValueError:
        return ""


def tvdb_regroup(season: int, episodes: Sequence[Dict[str, Any]],
                 tvdb_seasons: Dict[int, Sequence[Tuple[int, Any]]]):
    """split one TMDB season's episodes by the season TVDB files them under.

    TMDB lumps some anime into one long season (Jujutsu Kaisen S01E01-59) where
    TVDB, the servers, the scene and the subtitle sites all split it (24/23/12).
    an episode TVDB's same season doesn't have is moved to the TVDB episode
    that aired the same day, in another season. everything else stays put.

    late-night anime slips a day between the two: TVDB dates a 00:26 airing by
    the broadcast day before, TMDB by the calendar. five of Jujutsu Kaisen's
    stayed behind as phantom S1E32-35 and S1E46 (Thibs). a leftover moves to a
    TVDB episode one day either side, but only when that's the only candidate,
    nobody else claimed it, and no TMDB episode has that TVDB day exactly, so a
    show airing every day never shuffles onto its neighbours.

    returns ({season: [episode dicts]}, {old episode number: (season, episode)})."""
    season = int(season)
    groups: Dict[int, List[Dict[str, Any]]] = {season: []}
    moved: Dict[int, Numbering] = {}
    have = {int(e) for e, _ in (tvdb_seasons or {}).get(season) or []}
    by_day: Dict[str, List[Numbering]] = {}
    for e in episodes or []:
        if e.get("episode_number") is not None and _day(e.get("air_date")):
            by_day.setdefault(_day(e["air_date"]), []).append((season, int(e["episode_number"])))
    claimed = set()
    targets: Dict[int, Numbering] = {}
    leftovers = []
    for idx, e in enumerate(episodes or []):
        en = e.get("episode_number")
        if not (season > 0 and have and en is not None and int(en) not in have):
            continue
        alt = scene_numbering((season, int(en)), e.get("air_date"), tvdb_seasons,
                              by_day.get(_day(e.get("air_date"))))
        if alt and alt[0] > 0 and alt[0] != season and alt not in claimed:
            claimed.add(alt)
            targets[idx] = alt
        else:
            leftovers.append(idx)

    if leftovers:
        others = [(int(s), int(n), _day(d)) for s, eps in (tvdb_seasons or {}).items()
                  if int(s) > 0 and int(s) != season for n, d in eps or [] if _day(d)]
        tmdb_days = set(by_day)
        for idx in leftovers:
            e = episodes[idx]
            day = _day(e.get("air_date"))
            if not day:
                continue
            near = {_shift(day, -1), _shift(day, 1)}
            hits = [(s, n) for s, n, d in others
                    if d in near and d not in tmdb_days and (s, n) not in claimed]
            if len(hits) == 1:
                claimed.add(hits[0])
                targets[idx] = hits[0]

    for idx, e in enumerate(episodes or []):
        target = targets.get(idx)
        if target:
            moved[int(e["episode_number"])] = target
            groups.setdefault(target[0], []).append(dict(e, episode_number=target[1]))
        else:
            groups[season].append(e)
    return groups, moved


def regroup_season(db: Any, tvdb_id: Any, season: int, episodes: Sequence[Dict[str, Any]],
                   client: Any = None, now: Optional[_dt.datetime] = None):
    """tvdb_regroup with TVDB's seasons fetched (week-old cache). TVDB is only
    asked past the TMDB season when that season runs longer than TVDB's.
    without a TVDB key or id the season comes back as it was. never raises."""
    unchanged = ({int(season): list(episodes or [])}, {})
    if not tvdb_id or int(season) <= 0:
        return unchanged
    try:
        if client is None:
            client = _default_client(db)
        s = int(season)
        have = tvdb_season(db, client, int(tvdb_id), s, now=now)
        nums = {int(e) for e, _ in have}
        if not nums or all(int(e["episode_number"]) in nums for e in episodes or []
                           if e.get("episode_number") is not None):
            return unchanged
        seasons = {s: have}
        for n in range(s + 1, s + 1 + REGROUP_MAX_SEASONS):
            eps = tvdb_season(db, client, int(tvdb_id), n, now=now)
            if not eps:
                break
            seasons[n] = eps
        return tvdb_regroup(s, episodes, seasons)
    except Exception:   # noqa: BLE001 - a numbering assist must never break a sync
        logger.debug("tvdb regroup failed for %s S%s", tvdb_id, season, exc_info=True)
        return unchanged


# AniList entries that are a run of regular episodes (not a movie, OVA or special)
_RUN_FORMATS = ("", "TV", "TV_SHORT", "ONA")


def anilist_regroup(season: int, episodes: Sequence[Dict[str, Any]],
                    entries: Sequence[Dict[str, Any]]):
    """split one lumped TMDB season by the AniList entries it holds.

    AniList says how many episodes each entry (each season, each cour) has, so
    walking the entries in order and adding the counts up gives where each one
    starts inside the TMDB season. the anime id map says the same thing its own
    way (Jujutsu Kaisen's third season starts after TMDB S1E47) and names the
    TVDB season each entry is. TVDB does the same split by air date, but only
    with a TVDB key; this needs neither.

    the two are checked against each other, because each is wrong sometimes:

    - the map lags behind TMDB (it files JJK's and Dandadan's second seasons
      under a TMDB season 2 that TMDB folded into season 1) and is off by one
      here and there (Tokyo Revengers, Re:Zero). the counts are believed when
      something bears them out: a map entry further on that starts exactly
      where the counts say, or the counts ending exactly on the season's last
      episode.
    - an entry that starts past the season's last episode hasn't reached TMDB
      yet (The Apothecary Diaries' next cour). it is left out, and no longer
      keeps the seasons before it from splitting.
    - where the counts are missing or nothing bears them out, the map's own
      offsets are used, but only when the map places every entry inside this
      season.

    anything else leaves the season as it is.

    returns ({season: [episode dicts]}, {old episode number: (season, episode)})."""
    season = int(season)
    unchanged = ({season: list(episodes or [])}, {})

    def _n(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    numbers = [n for n in (_n(e.get("episode_number")) for e in episodes or []) if n is not None]
    total = max(numbers) if numbers else 0
    rows = [e for e in entries or []
            if (_n(e.get("tvdb_season")) or 0) > 0
            and str(e.get("format") or "").upper() in _RUN_FORMATS]
    rows.sort(key=lambda e: (_n(e["tvdb_season"]), _n(e.get("tvdb_offset")) or 0))
    here = [i for i, e in enumerate(rows) if _n(e.get("tmdb_season")) == season]
    if season <= 0 or total <= 0 or not here:
        return unchanged
    chain = rows[here[0]:]
    first = chain[0]
    if (_n(first.get("tmdb_offset")) or 0) != 0 or _n(first["tvdb_season"]) != season:
        return unchanged

    def _mapped(row):
        """where the map starts this entry inside this season, if it says"""
        return (_n(row.get("tmdb_offset")) or 0) if _n(row.get("tmdb_season")) == season else None

    # by the counts: (start, row). start = the TMDB episode number before its first
    counted: List[Tuple[int, Dict[str, Any]]] = [(0, first)]
    borne_out = False       # something agreed with the counts since the last disagreement
    usable = True
    for prev, row in zip(chain, chain[1:], strict=False):
        count = _n(prev.get("episodes"))
        mapped = _mapped(row)
        if count:
            start = counted[-1][0] + count
            if mapped is not None and mapped < total:
                if mapped == start:
                    borne_out = True
                else:
                    borne_out = False
        elif mapped is not None:
            # no count: the map's word for where this one starts. a start
            # worked out from counts just before it can't be checked against
            # anything now (how long was the entry in between?), so that
            # leaves the season alone
            if len(counted) > 1 and not borne_out:
                usable = False
                break
            start = mapped
        else:
            usable = False
            break
        if start >= total:
            break                               # not on TMDB yet
        if start <= counted[-1][0]:
            usable = False
            break
        counted.append((start, row))
    if usable:
        last_count = _n(counted[-1][1].get("episodes"))
        if last_count and counted[-1][0] + last_count == total:
            borne_out = True
    runs = counted if (usable and borne_out and len(counted) > 1) else None

    if runs is None:
        # the map alone, when it places every entry in this season itself
        placed = [(_mapped(r), r) for r in chain]
        placed = [(start, r) for start, r in placed if start is None or start < total]
        if (len(placed) < 2 or any(start is None for start, _ in placed)
                or any(b[0] <= a[0] for a, b in zip(placed, placed[1:], strict=False))):
            return unchanged
        runs = placed

    groups: Dict[int, List[Dict[str, Any]]] = {season: []}
    moved: Dict[int, Numbering] = {}
    for e in episodes or []:
        en = _n(e.get("episode_number"))
        target = None
        if en is not None:
            for start, row in runs:
                if en > start:
                    target = (_n(row["tvdb_season"]), en - start + (_n(row.get("tvdb_offset")) or 0))
        if target and target[0] != season:
            moved[en] = target
            groups.setdefault(target[0], []).append(dict(e, episode_number=target[1]))
        else:
            groups[season].append(e)
    return (groups, moved) if moved else unchanged


def split_preview_seasons(seasons: Sequence[Dict[str, Any]],
                          entries: Sequence[Dict[str, Any]]):
    """the same AniList split for a show that isn't in the library yet.

    a search preview lists TMDB's seasons as they are, so Frieren showed one
    season of 38 and JJK one of 59 while the library split them (Thibs). this
    splits the season LIST the same way anilist_regroup splits episodes.
    skipped for any season whose split would land on a season number TMDB
    already uses, so a preview never shows two different season 2s.

    returns (seasons, {season shown: TMDB season it comes from}), the map
    holding every season that came out of a split, sources included."""
    def _n(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    have = {_n(s.get("season_number")) for s in seasons or []}
    out: List[Dict[str, Any]] = []
    source: Dict[int, int] = {}
    for s in seasons or []:
        n = _n(s.get("season_number"))
        total = _n(s.get("episode_total")) or 0
        if not n or n <= 0 or total <= 0:
            out.append(s)
            continue
        groups, moved = anilist_regroup(
            n, [{"episode_number": i} for i in range(1, total + 1)], entries)
        extra = sorted(k for k in groups if k != n)
        if not moved or any(k in have for k in extra):
            out.append(s)
            continue
        out.append(dict(s, episode_total=len(groups[n])))
        source[n] = n
        for k in extra:
            # "title" is the key a season card reads: the split season came
            # out with no name on it (Thibs' second Frieren card)
            out.append({"season_number": k, "name": "Season %d" % k, "title": "Season %d" % k,
                        "episode_total": len(groups[k]), "episode_owned": 0, "episodes": [],
                        "poster_url": s.get("poster_url"), "has_poster": s.get("has_poster"),
                        "air_date": None})
            source[k] = n
    out.sort(key=lambda s: _n(s.get("season_number")) if _n(s.get("season_number")) is not None else -1)
    return out, source


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
        kept = kept_season_number(it, db)
        if kept:
            it["alt_season"], it["alt_episode"] = kept
            continue
        info = scene_info(it, db=db, client=client)
        if info:
            (it["alt_season"], it["alt_episode"]), exclusive = info
            if exclusive:
                it["alt_exclusive"] = True
            continue
        alt = season_relative(it, db)
        if alt:
            it["alt_season"], it["alt_episode"] = alt
    return items


def kept_season_number(item: Dict[str, Any], db: Any) -> Optional[Numbering]:
    """the split number of an episode in a season kept the way the server
    numbers it, or None.

    a server that numbers Jujutsu Kaisen as TMDB does (one season of 59) keeps
    that season here too, so its files stay where the server has them. the
    episode sync wrote down where each one sits in the split everything else
    uses, and S01E48 goes out as S03E01 as well. TVDB's air-date match only
    looks one season either side and needs a key; this needs neither."""
    try:
        return db.scene_number(item.get("show_tmdb_id"), item.get("season_number"),
                               item.get("episode_number"))
    except Exception:   # noqa: BLE001 - a numbering assist must never break a search
        return None


def season_relative(item: Dict[str, Any], db: Any) -> Optional[Numbering]:
    """the per-season number of an episode TMDB numbers on from the season
    before, or None.

    TMDB runs Naruto Shippuden's season 19 from E394 to E413, so the search
    went out as S19E397 and found nothing: a release is named S19E04, or by
    the absolute number (Thibs). TVDB gives the per-season number when there is
    a key; without one it is simply the episode's place in its season. only
    for a number past the season's own length, which can't be a per-season
    number already, so a normal season is never touched. it goes out as one
    more thing to search for and accept, never instead of TMDB's number."""
    try:
        season, episode = int(item.get("season_number")), int(item.get("episode_number"))
        if season < 1 or not item.get("show_tmdb_id"):
            return None
        place = db.episode_season_position(item["show_tmdb_id"], season, episode)
    except Exception:   # noqa: BLE001 - a numbering assist must never break a search
        return None
    if not place:
        return None
    position, size = place
    if episode > size and 0 < position != episode:
        return (season, position)
    return None


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
