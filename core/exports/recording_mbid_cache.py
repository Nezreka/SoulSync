"""Persistent (artist,title) -> MusicBrainz recording-MBID cache for playlist export.

The export waterfall (``core.exports.mbid_resolver``) ends in a live MusicBrainz lookup
that's rate-limited to ~1 req/s — the slow tail of exporting a big playlist. Remembering a
resolved recording MBID ONCE means the same song never costs a second lookup, across every
future export and every playlist it appears in.

Mirrors ``core.metadata.album_mbid_cache`` exactly: a tiny SQLite table, lazy DB accessor,
every function wrapped so any DB error degrades to a cache miss / no-op. If this module
breaks, exports still work — they just re-resolve via the live waterfall like a cold cache.
Key is the normalized ``track_key`` from ``mbid_resolver.normalize_key(artist, title)``.
"""

from __future__ import annotations

import threading
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("exports.recording_mbid_cache")

_db_factory_lock = threading.Lock()
_db_factory = None


def _get_database():
    """Resolve the MusicDatabase singleton lazily; None on any failure (treated as miss)."""
    global _db_factory
    with _db_factory_lock:
        if _db_factory is None:
            try:
                from database.music_database import get_database
                _db_factory = get_database
            except Exception as exc:
                logger.warning(f"Recording-MBID cache: could not load database module: {exc}")
                return None
    try:
        return _db_factory()
    except Exception as exc:
        logger.warning(f"Recording-MBID cache: database accessor failed: {exc}")
        return None


def lookup(track_key: str) -> Optional[str]:
    """Read a cached recording MBID for ``track_key``; None on miss or any DB error."""
    if not track_key:
        return None
    db = _get_database()
    if db is None:
        return None
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT recording_mbid FROM mb_recording_cache WHERE track_key = ? LIMIT 1",
            (track_key,),
        )
        row = cursor.fetchone()
        if row:
            return (row[0] if not hasattr(row, "keys") else row["recording_mbid"]) or None
    except Exception as exc:
        logger.debug(f"Recording-MBID cache lookup failed: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally cleanup
                pass
    return None


def record(track_key: str, recording_mbid: str) -> bool:
    """Persist ``track_key`` -> ``recording_mbid`` (idempotent). False on any failure."""
    if not track_key or not recording_mbid:
        return False
    db = _get_database()
    if db is None:
        return False
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO mb_recording_cache "
            "(track_key, recording_mbid, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (track_key, recording_mbid),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.debug(f"Recording-MBID cache record failed: {exc}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally cleanup
                pass


def clear_all() -> bool:
    """Wipe the cache (tests / forced re-resolve)."""
    db = _get_database()
    if db is None:
        return False
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM mb_recording_cache")
        conn.commit()
        return True
    except Exception as exc:
        logger.warning(f"Recording-MBID cache clear failed: {exc}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally cleanup
                pass


__all__ = ["lookup", "record", "clear_all"]
