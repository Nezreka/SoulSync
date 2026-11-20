"""what you said you love, as taste seeds next to what you play.

three signals, per profile:

- Liked Songs: the profile's own liked playlist (core.playlists.user_playlists)
- server favourites: stars and high ratings the media servers reported for
  this profile's account (curation_signals, read by core.library.curation_sync)
- the Watchlist: artists the profile follows, a recent add counting more

each gives an artist some units. a unit is sized to the profile's own play
seeds (their median weight), so a liked artist lands among your regular
artists rather than above your favourites or lost under them, and a profile
with no plays yet still gets seeds from its likes. capped per artist, so a
hundred liked songs by one band can't drown everything else.

the seed builders (recommendations, daily mixes, stations, the taste draw)
pass these to build_recency_weighted_seeds(favorites=...).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from utils.logging_config import get_logger

logger = get_logger("discovery.favorite_seeds")

LIKE_UNITS = 1.0          # per liked song
STAR_UNITS = 1.0          # per starred or highly rated track on the server
LIKE_CAP = 4.0            # most units liked songs give one artist
STAR_CAP = 4.0            # most units server favourites give one artist
WATCHLIST_UNITS = 1.0     # following an artist
RECENT_FOLLOW_UNITS = 1.0  # more when the follow is recent
RECENT_FOLLOW_DAYS = 90
ARTIST_CAP = 6.0          # most units all favourites give one artist
# a high rating, on the 0-5 scale the servers' readers use
HIGH_RATING = 4.0

# a play seed's weight is lifetime plays plus recent plays; a unit is the
# median of the top ones, never below this so a light listener's likes count
UNIT_FLOOR = 5.0
UNIT_SAMPLE = 20

_CACHE_TTL = 600.0
_cache: Dict[Tuple[Any, int], Tuple[float, Dict[str, float]]] = {}
_cache_lock = threading.Lock()


def _norm(name: Any) -> str:
    return " ".join(str(name or "").split())


def _add(out: Dict[str, Tuple[str, float]], name: Any, units: float, cap: float) -> None:
    key = _norm(name).casefold()
    if not key:
        return
    shown, have = out.get(key, (_norm(name), 0.0))
    out[key] = (shown, min(cap, have + units))


def liked_song_artists(conn, profile_id: int) -> Dict[str, Tuple[str, float]]:
    """{artist key: (name, units)} from the profile's Liked Songs"""
    out: Dict[str, Tuple[str, float]] = {}
    try:
        rows = conn.execute("""
            SELECT t.artist_name FROM mirrored_playlist_tracks t
            JOIN mirrored_playlists p ON p.id = t.playlist_id
            WHERE p.source = 'soulsync' AND p.source_playlist_id = 'liked' AND p.profile_id = ?
        """, (int(profile_id),)).fetchall()
    except Exception as e:  # noqa: BLE001 - no playlist tables: nothing liked
        logger.debug("liked songs read failed: %s", e)
        return out
    for (artist,) in rows:
        _add(out, artist, LIKE_UNITS, LIKE_CAP)
    return out


def watchlist_artists(conn, profile_id: int, now: Optional[datetime] = None
                      ) -> Dict[str, Tuple[str, float]]:
    """{artist key: (name, units)} from the profile's Watchlist"""
    out: Dict[str, Tuple[str, float]] = {}
    try:
        rows = conn.execute(
            "SELECT artist_name, date_added FROM watchlist_artists WHERE profile_id = ?",
            (int(profile_id),)).fetchall()
    except Exception as e:  # noqa: BLE001 - no watchlist table
        logger.debug("watchlist read failed: %s", e)
        return out
    now = now or datetime.now(timezone.utc)
    recent_since = (now - timedelta(days=RECENT_FOLLOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    for name, added in rows:
        units = WATCHLIST_UNITS
        if added and str(added).replace("T", " ")[:19] >= recent_since:
            units += RECENT_FOLLOW_UNITS
        _add(out, name, units, WATCHLIST_UNITS + RECENT_FOLLOW_UNITS)
    return out


def _admin_navidrome_user() -> str:
    try:
        from core.settings import config_manager
        return str(config_manager.get("navidrome.username", "") or "").strip().casefold()
    except Exception:  # noqa: BLE001 - no config: no configured account to match
        return ""


def server_user_keys(conn, profile_id: int, *, admin_navidrome: Optional[str] = None
                     ) -> Set[Tuple[str, str]]:
    """the (server, user key) pairs whose curation signals are this profile's.

    plex reads one account, the server owner's, so it is the admin's.
    navidrome keys by username: a profile's own navidrome login, and the
    configured account for the admin. jellyfin keys by user name or id: a
    profile whose linked jellyfin user id is the key."""
    pid = int(profile_id)
    keys: Set[Tuple[str, str]] = set()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    row = None
    wanted = [c for c in ("navidrome_username", "jellyfin_user_id") if c in cols]
    if wanted:
        row = conn.execute(f"SELECT {', '.join(wanted)} FROM profiles WHERE id = ?",
                           (pid,)).fetchone()
    values = dict(zip(wanted, row, strict=True)) if row else {}
    if values.get("navidrome_username"):
        keys.add(("navidrome", str(values["navidrome_username"]).strip().casefold()))
    if values.get("jellyfin_user_id"):
        keys.add(("jellyfin", str(values["jellyfin_user_id"]).strip()))
    if pid == 1:
        admin = admin_navidrome if admin_navidrome is not None else _admin_navidrome_user()
        if admin:
            keys.add(("navidrome", admin))
        for (user_key,) in conn.execute(
                "SELECT DISTINCT user_key FROM curation_signals WHERE server = 'plex'").fetchall():
            keys.add(("plex", str(user_key)))
    return keys


def server_favorite_artists(conn, profile_id: int, *, admin_navidrome: Optional[str] = None
                            ) -> Dict[str, Tuple[str, float]]:
    """{artist key: (name, units)} from the server stars and high ratings the
    curation sweep stored for this profile's accounts"""
    out: Dict[str, Tuple[str, float]] = {}
    try:
        keys = server_user_keys(conn, profile_id, admin_navidrome=admin_navidrome)
    except Exception as e:  # noqa: BLE001 - no curation table: nothing starred
        logger.debug("curation accounts read failed: %s", e)
        return out
    if not keys:
        return out
    wanted: Dict[str, int] = {}
    for server, user_key in keys:
        for track_key, favorite, rating in conn.execute(
                "SELECT track_key, favorite, rating FROM curation_signals "
                "WHERE server = ? AND user_key = ?", (server, user_key)).fetchall():
            try:
                high = rating is not None and float(rating) >= HIGH_RATING
            except (TypeError, ValueError):
                high = False
            if favorite or high:
                wanted[str(track_key)] = wanted.get(str(track_key), 0) + 1
    if not wanted:
        return out
    from core.library.expired_cleanup import path_suffix_key
    # the stored key is the path's last two parts; the library's files are
    # matched on the same key, the filename prefiltered in sql
    names = sorted({k.rsplit("/", 1)[-1] for k in wanted})
    rows: List[Tuple[Any, ...]] = []
    for i in range(0, len(names), 200):
        chunk = names[i:i + 200]
        rows += conn.execute(
            "SELECT f.path, ar.name FROM lib2_track_files f "
            "JOIN lib2_tracks t ON t.id = f.track_id "
            "JOIN lib2_albums al ON al.id = t.album_id "
            "JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
            "WHERE f.path IS NOT NULL AND ("
            + " OR ".join(["f.path LIKE ?"] * len(chunk)) + ")",
            ["%" + "".join(ch if ord(ch) < 128 else "_" for ch in n) for n in chunk]).fetchall()
    seen: Set[str] = set()
    for path, artist in rows:
        key = path_suffix_key(path)
        if key in wanted and key not in seen:
            seen.add(key)
            _add(out, artist, STAR_UNITS * wanted[key], STAR_CAP)
    return out


def favorite_artist_units(database, profile_id: Any, *, admin_navidrome: Optional[str] = None,
                          now: Optional[datetime] = None) -> Dict[str, float]:
    """{artist name: units} from every favourite signal of this profile.
    cached a few minutes per profile; {} on any failure."""
    try:
        pid = int(profile_id) if profile_id is not None else 1
    except (TypeError, ValueError):
        pid = 1
    cache_key = (getattr(database, "database_path", None) or id(database), pid)
    stamp = time.time()
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and hit[0] > stamp:
            return dict(hit[1])
    merged: Dict[str, Tuple[str, float]] = {}
    conn = None
    try:
        conn = database._get_connection()
        for part in (liked_song_artists(conn, pid),
                     server_favorite_artists(conn, pid, admin_navidrome=admin_navidrome),
                     watchlist_artists(conn, pid, now=now)):
            for key, (name, units) in part.items():
                shown, have = merged.get(key, (name, 0.0))
                merged[key] = (shown, min(ARTIST_CAP, have + units))
    except Exception as e:  # noqa: BLE001 - favourites only ever add to taste
        logger.debug("favourite seeds failed for profile %s: %s", pid, e)
        return {}
    finally:
        if conn is not None:
            conn.close()
    out = {name: units for name, units in merged.values() if units > 0}
    with _cache_lock:
        _cache[cache_key] = (stamp + _CACHE_TTL, out)
    return dict(out)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def seed_unit(weights: Iterable[float]) -> float:
    """how much one favourite unit weighs next to these play seeds: the median
    of the top ones, never below UNIT_FLOOR"""
    top = sorted((float(w) for w in weights if w and w > 0), reverse=True)[:UNIT_SAMPLE]
    if not top:
        return UNIT_FLOOR
    mid = len(top) // 2
    median = top[mid] if len(top) % 2 else (top[mid - 1] + top[mid]) / 2
    return max(UNIT_FLOOR, median)


__all__ = [
    "favorite_artist_units", "liked_song_artists", "seed_unit", "server_favorite_artists",
    "server_user_keys", "watchlist_artists", "clear_cache",
]
