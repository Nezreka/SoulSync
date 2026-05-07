"""Persistent MusicBrainz release-MBID cache for albums.

The original in-memory `mb_release_cache` in `core/metadata/source.py`
maps `(normalized_album_name, artist_name) -> release_mbid` so per-track
enrichment of the same album hits the cache and writes the same
``MUSICBRAINZ_ALBUMID`` to every track's tags. That cache is a bounded
``OrderedDict`` (4096 entries) — bounded means it can evict entries
between tracks of the same album when other albums are processed in
between. Server restart drops it entirely. Either case can produce
inconsistent album MBIDs across tracks of the same album, which causes
Navidrome (and other media servers that group by album MBID) to split
the album into multiple entries.

This module is the persistent layer behind that cache. Same key shape,
backed by a tiny SQLite table so a successful lookup remembered ONCE
applies to every future track of the same album for the lifetime of
the install — not just the bounded in-memory window.

Strict additive design: every public function is wrapped in try/except
and degrades to a None / no-op return on any database error. The
existing in-memory cache + MusicBrainz lookup stays behind it as the
authoritative fallback. If this module breaks, downloads continue
exactly as they would today — just without the persistent benefit.
"""

from __future__ import annotations

import threading
from typing import Optional

from utils.logging_config import get_logger


logger = get_logger("metadata.album_mbid_cache")


# Lazy DB accessor — the cache module shouldn't trigger MusicDatabase
# import at module-load time (circular-import risk when source.py is
# imported during database initialization).
_db_factory_lock = threading.Lock()
_db_factory = None


def _get_database():
    """Resolve the MusicDatabase singleton lazily.

    Returns None if anything goes wrong — callers MUST handle a None
    return as "cache unavailable, fall through to MB lookup."
    """
    global _db_factory
    with _db_factory_lock:
        if _db_factory is None:
            try:
                from database.music_database import get_database
                _db_factory = get_database
            except Exception as exc:
                logger.warning(f"Persistent MBID cache: could not load database module: {exc}")
                return None
    try:
        return _db_factory()
    except Exception as exc:
        logger.warning(f"Persistent MBID cache: database accessor failed: {exc}")
        return None


def lookup(normalized_album_key: str, artist_key: str) -> Optional[str]:
    """Read a cached release MBID for the given (album, artist) pair.

    Returns the stored MBID string if found, otherwise None. Never
    raises — DB errors degrade silently to "cache miss" so the caller
    falls through to MusicBrainz like it does today.

    Args:
        normalized_album_key: Output of ``normalize_album_cache_key`` —
            already lowercased and stripped of edition parentheticals.
        artist_key: Lowercased artist name (caller's responsibility to
            pass a normalized key — keeps the schema uniform).
    """
    if not normalized_album_key or not artist_key:
        return None

    db = _get_database()
    if db is None:
        return None

    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT release_mbid FROM mb_album_release_cache "
            "WHERE normalized_album_key = ? AND artist_key = ? LIMIT 1",
            (normalized_album_key, artist_key),
        )
        row = cursor.fetchone()
        if row:
            mbid = row[0] if not hasattr(row, 'keys') else row['release_mbid']
            return mbid or None
    except Exception as exc:
        logger.debug(f"Persistent MBID cache lookup failed: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                pass

    return None


def record(normalized_album_key: str, artist_key: str, release_mbid: str) -> bool:
    """Persist a (album, artist) -> release_mbid mapping.

    Idempotent — uses INSERT OR REPLACE so re-recording the same key
    just refreshes the timestamp. Returns True on success, False on
    any failure. Failure is logged at debug level and never propagated
    so a flaky DB write can't break the enrichment path.
    """
    if not normalized_album_key or not artist_key or not release_mbid:
        return False

    db = _get_database()
    if db is None:
        return False

    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO mb_album_release_cache "
            "(normalized_album_key, artist_key, release_mbid, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (normalized_album_key, artist_key, release_mbid),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.debug(f"Persistent MBID cache record failed: {exc}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                pass


def clear_all() -> bool:
    """Wipe the persistent cache. Used by tests and by the maintenance
    endpoint when a user wants to force a fresh MusicBrainz re-lookup
    (e.g. after fixing widespread MBID inconsistencies)."""
    db = _get_database()
    if db is None:
        return False

    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM mb_album_release_cache")
        conn.commit()
        return True
    except Exception as exc:
        logger.warning(f"Persistent MBID cache clear failed: {exc}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                pass


__all__ = ["lookup", "record", "clear_all"]
