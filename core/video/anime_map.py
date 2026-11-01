"""Which library titles are anime, and which AniList entries they are.

TMDB and TVDB stay the identity of every show and movie. An anime is linked to
AniList through the community mapping list (Fribb's anime-lists, built on
AniDB's anime-list-master: the list HAMA, Kometa and Overseerr use), which
pairs AniList/MAL/AniDB ids with TVDB and TMDB ids and says which season and
episode offset each AniList entry covers: AniList splits a show into one entry
per season or cour, TVDB and TMDB don't.

Matching by id is exact, so it runs for every show and only ever touches the
ones the list knows are anime. A title search stays as the fallback for an
animated show the list doesn't have.

The list (~6 MB, ~40k entries, ~21k with an AniList id) is fetched from
GitHub at most once a week and kept in ``anime_id_map``.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.anime_map")

MAP_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-mini.json"
REFRESH_SECONDS = 7 * 86400
_SYNCED_KEY = "anime_map_synced_at"


def _int(v: Any) -> Optional[int]:
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_entries(raw: Any) -> List[Dict[str, Any]]:
    """the list's entries that carry an AniList id, as anime_id_map rows. pure."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        anilist = _int(e.get("anilist_id"))
        if not anilist:
            continue
        tmdb = e.get("themoviedb_id") if isinstance(e.get("themoviedb_id"), dict) else {}
        season = e.get("season") if isinstance(e.get("season"), dict) else {}
        offset = e.get("episode_offset") if isinstance(e.get("episode_offset"), dict) else {}
        imdb = e.get("imdb_id")
        if isinstance(imdb, list):
            imdb = imdb[0] if imdb else None

        def _num(d, k):
            try:
                return int(d.get(k)) if d.get(k) is not None else None
            except (TypeError, ValueError):
                return None

        out.append({
            "anilist_id": anilist,
            "mal_id": _int(e.get("mal_id")),
            "anidb_id": _int(e.get("anidb_id")),
            "format": str(e.get("type") or "").upper() or None,
            "tvdb_id": _int(e.get("tvdb_id")),
            "tvdb_season": _num(season, "tvdb"),
            "tvdb_offset": _num(offset, "tvdb") or 0,
            "tmdb_tv": _int(tmdb.get("tv")),
            "tmdb_season": _num(season, "tmdb"),
            "tmdb_offset": _num(offset, "tmdb") or 0,
            "tmdb_movie": _int(tmdb.get("movie")),
            "imdb_id": str(imdb) if imdb else None,
        })
    return out


def _default_fetch(url: str) -> Any:
    import requests
    r = requests.get(url, timeout=60, headers={"User-Agent": "SoulSync (anime id map)"})
    r.raise_for_status()
    return r.json()


def refresh(db: Any, *, fetch: Optional[Callable[[str], Any]] = None,
            now: Optional[float] = None, force: bool = False) -> int:
    """fetch the list again when it's a week old (or never fetched). returns
    how many entries were stored, 0 when it was fresh or the fetch failed (the
    old copy stays). never raises."""
    now = now or time.time()
    try:
        last = float(db.get_setting(_SYNCED_KEY) or 0)
    except (TypeError, ValueError):
        last = 0.0
    if not force and last and now - last < REFRESH_SECONDS and db.anime_map_count():
        return 0
    try:
        rows = parse_entries((fetch or _default_fetch)(MAP_URL))
    except Exception as e:   # noqa: BLE001 - no network: keep what we had
        logger.info("anime id map: could not fetch the list (%s), keeping the old one", e)
        return 0
    if len(rows) < 1000:
        # a truncated or empty answer must never wipe a good map
        logger.warning("anime id map: the list came back with %d entries, keeping the old one", len(rows))
        return 0
    db.replace_anime_map(rows)
    db.set_setting(_SYNCED_KEY, str(int(now)))
    logger.info("anime id map: %d AniList entries", len(rows))
    return len(rows)


__all__ = ["MAP_URL", "parse_entries", "refresh"]
