"""on-the-fly virtual tracks for Subsonic music apps (#2341).

when enabled, songs in mirrored/discover playlists or search results that
aren't in the local library yet are exposed with synthetic ``otf-<id>`` ids.
streaming one downloads a temporary audio file into a dedicated cache folder
(never touching the user's real music library). unappreciated temporary files
are pruned after the configured TTL; replaying, starring, or adding one to a
playlist marks it promoted and hands it to the wishlist so the main source can
grab a permanent high-quality copy.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("subsonic.on_the_fly")

DEFAULT_TTL_DAYS = 7
MIN_TTL_DAYS = 1
MAX_TTL_DAYS = 90
_PRUNE_INTERVAL_S = 3600.0

_track_locks: Dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_last_prune_ts = 0.0


def clamp_ttl_days(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TTL_DAYS
    return max(MIN_TTL_DAYS, min(MAX_TTL_DAYS, n))


def enabled(cfg_get: Callable[..., Any]) -> bool:
    return bool(cfg_get("music_apps.on_the_fly_enabled", False))


def ttl_days(cfg_get: Callable[..., Any]) -> int:
    return clamp_ttl_days(cfg_get("music_apps.on_the_fly_ttl_days", DEFAULT_TTL_DAYS))


def promote_wishlist(cfg_get: Callable[..., Any]) -> bool:
    return bool(cfg_get("music_apps.on_the_fly_promote_wishlist", True))


def ensure_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subsonic_otf_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0,
            spotify_id TEXT,
            track_number INTEGER,
            disc_number INTEGER,
            release_year INTEGER,
            cover_url TEXT,
            file_path TEXT,
            file_size INTEGER DEFAULT 0,
            bitrate INTEGER DEFAULT 0,
            suffix TEXT DEFAULT 'mp3',
            play_count INTEGER NOT NULL DEFAULT 0,
            promoted INTEGER NOT NULL DEFAULT 0,
            last_played_at REAL,
            created_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_subsonic_otf_promoted_time "
        "ON subsonic_otf_tracks (promoted, last_played_at, created_at)"
    )


def _conn(db):
    return db._get_connection()


def _lock_for(otf_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _track_locks.get(otf_id)
        if lock is None:
            lock = threading.Lock()
            _track_locks[otf_id] = lock
        return lock


def _make_key(title: str, artist: str, album: str = "", spotify_id: Optional[str] = None) -> str:
    if spotify_id and str(spotify_id).strip():
        return "sp:" + str(spotify_id).strip()
    norm = f"{artist.strip().lower()}|{title.strip().lower()}|{album.strip().lower()}"
    return "meta:" + hashlib.sha1(norm.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


def register_track(
    db,
    *,
    title: str,
    artist: str,
    album: str = "",
    duration_ms: int = 0,
    spotify_id: Optional[str] = None,
    track_number: Optional[int] = None,
    disc_number: Optional[int] = None,
    release_year: Optional[int] = None,
    cover_url: Optional[str] = None,
) -> Dict[str, Any]:
    """return the existing or newly created virtual track row."""
    clean_title = str(title or "").strip() or "Unknown Title"
    clean_artist = str(artist or "").strip() or "Unknown Artist"
    clean_album = str(album or "").strip()
    key = _make_key(clean_title, clean_artist, clean_album, spotify_id)
    now = time.time()
    with _conn(db) as conn:
        ensure_table(conn.cursor())
        existing = conn.execute(
            "SELECT * FROM subsonic_otf_tracks WHERE track_key = ?", (key,)
        ).fetchone()
        if existing:
            return dict(existing)
        cur = conn.execute(
            """
            INSERT INTO subsonic_otf_tracks
                (track_key, title, artist, album, duration_ms, spotify_id,
                 track_number, disc_number, release_year, cover_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                clean_title,
                clean_artist,
                clean_album,
                int(duration_ms or 0),
                str(spotify_id).strip() if spotify_id else None,
                int(track_number) if track_number is not None else None,
                int(disc_number) if disc_number is not None else None,
                int(release_year) if release_year is not None else None,
                str(cover_url).strip() if cover_url else None,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM subsonic_otf_tracks WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def get_track(db, otf_id: int) -> Optional[Dict[str, Any]]:
    with _conn(db) as conn:
        ensure_table(conn.cursor())
        row = conn.execute(
            "SELECT * FROM subsonic_otf_tracks WHERE id = ?", (int(otf_id),)
        ).fetchone()
        return dict(row) if row else None


def to_subsonic_song(row: Dict[str, Any]) -> Dict[str, Any]:
    """format a virtual track row in the standard Subsonic song shape."""
    from core.subsonic import library as L

    suffix = (row.get("suffix") or "mp3").lower().lstrip(".")
    mime = L.AUDIO_MIME.get(suffix, "audio/mpeg")
    duration_s = max(0, int(round(int(row.get("duration_ms") or 0) / 1000.0)))
    out: Dict[str, Any] = {
        "id": L.sid(L.OTF, row["id"]),
        "isDir": False,
        "title": row.get("title") or "Unknown Title",
        "artist": row.get("artist") or "Unknown Artist",
        "album": row.get("album") or "",
        "duration": duration_s,
        "suffix": suffix,
        "contentType": mime,
        "type": "music",
        "isVideo": False,
        "size": int(row.get("file_size") or 0),
        "bitRate": int(row.get("bitrate") or 192),
        "coverArt": None,
    }
    if row.get("track_number"):
        out["track"] = int(row["track_number"])
    if row.get("disc_number"):
        out["discNumber"] = int(row["disc_number"])
    if row.get("release_year"):
        out["year"] = int(row["release_year"])
    if row.get("play_count"):
        out["playCount"] = int(row["play_count"])
    return out


def default_cache_dir(db) -> str:
    """dedicated temp cache folder next to the DB so real music folders stay clean."""
    db_path = getattr(db, "db_path", None)
    base = Path(db_path).resolve().parent if db_path else Path("data").resolve()
    cache = base / "subsonic_otf_cache"
    cache.mkdir(parents=True, exist_ok=True)
    return str(cache)


def _default_downloader(row: Dict[str, Any], cache_dir: str) -> Optional[str]:
    """fast single-track download via YouTubeClient into the temp cache dir."""
    from core.youtube_client import YouTubeClient
    from database.music_database import Track as SpotifyTrack

    client = YouTubeClient(download_path=cache_dir)
    if not client.is_available():
        return None
    track = SpotifyTrack(
        id=row.get("spotify_id") or f"otf-{row['id']}",
        name=row.get("title") or "",
        artists=[row.get("artist") or ""],
        album=row.get("album") or "",
        duration_ms=int(row.get("duration_ms") or 0),
        popularity=0,
    )
    return client.search_and_download_best(track, min_confidence=0.52)


def fetch_audio(
    db,
    row: Dict[str, Any],
    *,
    cache_dir: Optional[str] = None,
    downloader: Optional[Callable[[Dict[str, Any], str], Optional[str]]] = None,
) -> Optional[str]:
    """return a local audio file for ``row``, downloading it on the fly if needed."""
    otf_id = int(row["id"])
    with _lock_for(otf_id):
        fresh = get_track(db, otf_id) or row
        existing = fresh.get("file_path")
        if existing and os.path.isfile(existing):
            return existing

        target_dir = cache_dir or default_cache_dir(db)
        os.makedirs(target_dir, exist_ok=True)
        dl = downloader or _default_downloader
        try:
            path = dl(fresh, target_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("on-the-fly download failed for otf-%s: %s", otf_id, exc)
            return None

        if not path or not os.path.isfile(path):
            return None

        size = os.path.getsize(path)
        suffix = os.path.splitext(path)[1].lstrip(".").lower() or "mp3"
        with _conn(db) as conn:
            conn.execute(
                "UPDATE subsonic_otf_tracks SET file_path = ?, file_size = ?, suffix = ? WHERE id = ?",
                (path, int(size), suffix, otf_id),
            )
            conn.commit()
        return path


def record_play(db, otf_id: int) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _conn(db) as conn:
        ensure_table(conn.cursor())
        conn.execute(
            "UPDATE subsonic_otf_tracks SET play_count = play_count + 1, last_played_at = ? WHERE id = ?",
            (now, int(otf_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM subsonic_otf_tracks WHERE id = ?", (int(otf_id),)
        ).fetchone()
        return dict(row) if row else None


def mark_promoted(db, otf_id: int) -> bool:
    """mark a virtual track as appreciated/promoted. returns True if newly promoted."""
    with _conn(db) as conn:
        ensure_table(conn.cursor())
        cur = conn.execute(
            "UPDATE subsonic_otf_tracks SET promoted = 1 WHERE id = ? AND promoted = 0",
            (int(otf_id),),
        )
        conn.commit()
        return cur.rowcount > 0


def prune_expired(db, ttl: int = DEFAULT_TTL_DAYS, *, now: Optional[float] = None) -> int:
    """delete temporary audio files for expired unpromoted virtual tracks."""
    cutoff = (now if now is not None else time.time()) - clamp_ttl_days(ttl) * 86400.0
    removed = 0
    with _conn(db) as conn:
        ensure_table(conn.cursor())
        rows = conn.execute(
            """
            SELECT id, file_path FROM subsonic_otf_tracks
            WHERE promoted = 0
              AND file_path IS NOT NULL
              AND file_path != ''
              AND COALESCE(last_played_at, created_at) < ?
            """,
            (cutoff,),
        ).fetchall()
        for r in rows:
            fp = r["file_path"]
            if fp and os.path.isfile(fp):
                try:
                    os.remove(fp)
                except OSError as exc:
                    logger.debug("could not remove expired otf file %s: %s", fp, exc)
            conn.execute(
                "UPDATE subsonic_otf_tracks SET file_path = NULL, file_size = 0 WHERE id = ?",
                (r["id"],),
            )
            removed += 1
        if removed:
            conn.commit()
    return removed


def maybe_prune(db, cfg_get: Callable[..., Any]) -> None:
    """lazily prune expired temp files at most once an hour."""
    global _last_prune_ts
    now = time.time()
    if now - _last_prune_ts < _PRUNE_INTERVAL_S:
        return
    _last_prune_ts = now
    try:
        prune_expired(db, ttl_days(cfg_get), now=now)
    except Exception as exc:  # noqa: BLE001
        logger.debug("lazy otf prune failed: %s", exc)


def search_unowned(
    conn,
    db,
    query: str,
    limit: int = 10,
    *,
    owned_pairs: Optional[Set[tuple]] = None,
    external_search: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """find unowned tracks from mirrored playlists (and optional external search)
    matching ``query``, register them as virtual tracks, and return Subsonic song dicts."""
    q = str(query or "").strip()
    if not q or limit <= 0:
        return []
    seen: Set[tuple] = set(owned_pairs or ())
    results: List[Dict[str, Any]] = []
    like = f"%{q}%"

    # 1. unowned rows sitting in mirrored/discover playlists
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT track_name, artist_name, album_name, duration_ms, source_track_id
            FROM mirrored_playlist_tracks
            WHERE (track_name LIKE ? OR artist_name LIKE ? OR album_name LIKE ?)
            LIMIT ?
            """,
            (like, like, like, limit * 3),
        ).fetchall()
    except Exception:  # noqa: BLE001
        rows = []

    for r in rows:
        title = (r["track_name"] or "").strip()
        artist = (r["artist_name"] or "").strip()
        if not title:
            continue
        pair = (artist.lower(), title.lower())
        if pair in seen:
            continue
        seen.add(pair)
        otf = register_track(
            db,
            title=title,
            artist=artist,
            album=r["album_name"] or "",
            duration_ms=int(r["duration_ms"] or 0),
            spotify_id=r["source_track_id"],
        )
        results.append(to_subsonic_song(otf))
        if len(results) >= limit:
            return results

    # 2. optional external quick search hook (e.g. YouTube/metadata search)
    if external_search and len(results) < limit:
        try:
            ext = external_search(q, limit - len(results)) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("external otf search failed: %s", exc)
            ext = []
        for item in ext:
            title = str(item.get("title") or item.get("name") or "").strip()
            artist = str(item.get("artist") or "").strip()
            if not title:
                continue
            pair = (artist.lower(), title.lower())
            if pair in seen:
                continue
            seen.add(pair)
            otf = register_track(
                db,
                title=title,
                artist=artist,
                album=str(item.get("album") or ""),
                duration_ms=int(item.get("duration_ms") or 0),
                spotify_id=item.get("spotify_id"),
            )
            results.append(to_subsonic_song(otf))
            if len(results) >= limit:
                break

    return results
