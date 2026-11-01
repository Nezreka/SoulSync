"""The Songs tab's search index (upstream #1906 performance, on the catalogue).

``list_songs`` matches a search against every track's title, per-track artist
and album artist, each folded by ``queries._fold_sql``. On a 310k library that
is a pass over the whole catalogue per request, several seconds each. dev kept
it on indexes with stored ``*_norm`` columns; this is the catalogue's version:

- ``lib2_song_search`` holds the three folded fields per track in an FTS5
  trigram table, so a ``LIKE '%q%'`` reads the matches, not the library. Each
  field is folded exactly as ``_fold_sql`` folds it (LOWER for printable
  ASCII, ``normalize_for_comparison`` otherwise) and kept in its own column, so
  the index answers the same rows the query did.
- SQL-only triggers mark a track dirty when its title, credit, album or
  album artist changes. A search answers dirty tracks live through
  ``_fold_sql``, so it is exact before the index catches up and never writes.
- The index is filled once in the background and caught up after searches.
  Until it is filled, or for a query it cannot serve exactly (under three
  characters, or holding a LIKE wildcard), ``matching_ids`` returns None and
  the caller runs its original query.

``song_count_version`` is a counter the file triggers bump, so the unfiltered
song count can be cached exactly.
"""

from __future__ import annotations

import threading
from typing import Any, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.song_search")

TABLE = "lib2_song_search"
_COLUMNS = ("title", "track_artist", "artist")
_DIRTY = "lib2_song_search_dirty"
_STATE = "lib2_song_search_state"
_BATCH = 2000

_DIRTY_TRIGGERS = (
    f"""CREATE TRIGGER IF NOT EXISTS trg_lib2_song_search_track_insert
        AFTER INSERT ON lib2_tracks
        BEGIN INSERT OR IGNORE INTO {_DIRTY}(track_id) VALUES (NEW.id); END""",
    f"""CREATE TRIGGER IF NOT EXISTS trg_lib2_song_search_track_update
        AFTER UPDATE OF title, track_artist, album_id ON lib2_tracks
        BEGIN INSERT OR IGNORE INTO {_DIRTY}(track_id) VALUES (NEW.id); END""",
    f"""CREATE TRIGGER IF NOT EXISTS trg_lib2_song_search_track_delete
        AFTER DELETE ON lib2_tracks
        BEGIN INSERT OR IGNORE INTO {_DIRTY}(track_id) VALUES (OLD.id); END""",
    f"""CREATE TRIGGER IF NOT EXISTS trg_lib2_song_search_album_artist
        AFTER UPDATE OF primary_artist_id ON lib2_albums
        BEGIN INSERT OR IGNORE INTO {_DIRTY}(track_id)
              SELECT id FROM lib2_tracks WHERE album_id = NEW.id; END""",
    f"""CREATE TRIGGER IF NOT EXISTS trg_lib2_song_search_artist_name
        AFTER UPDATE OF name ON lib2_artists
        BEGIN INSERT OR IGNORE INTO {_DIRTY}(track_id)
              SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
               WHERE al.primary_artist_id = NEW.id; END""",
)

_COUNT_TRIGGERS = tuple(
    f"""CREATE TRIGGER IF NOT EXISTS trg_lib2_song_count_{name}
        AFTER {event} ON lib2_track_files
        BEGIN UPDATE {_STATE} SET files_version = files_version + 1 WHERE id = 1; END"""
    for name, event in (("insert", "INSERT"), ("update", "UPDATE OF path, file_state, owner_profile_id, track_id"),
                        ("delete", "DELETE"))
)


def ensure_schema(cursor: Any) -> None:
    """Tables, triggers and the A-Z index. Bounded DDL, safe at every start;
    the index fill itself is never done here. Never raises: without FTS5
    trigram support the Songs tab keeps its original query."""
    from database.music_database import _library_sort_key_sql

    try:
        # the A-Z page walks this and stops after one page (11s -> 0.02s on 310k)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lib2_tracks_alpha "
                       f"ON lib2_tracks({_library_sort_key_sql('title')}, id)")
    except Exception as exc:  # noqa: BLE001
        logger.error("songs A-Z index failed (will retry next start): %s", exc)
    try:
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {_STATE} ("
                       "id INTEGER PRIMARY KEY CHECK (id = 1), built INTEGER NOT NULL DEFAULT 0, "
                       "files_version INTEGER NOT NULL DEFAULT 0)")
        cursor.execute(f"INSERT OR IGNORE INTO {_STATE}(id) VALUES (1)")
        for sql in _COUNT_TRIGGERS:
            cursor.execute(sql)
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {_DIRTY} (track_id INTEGER PRIMARY KEY)")
        # derived data: a table of another shape (an older layout) is dropped
        # and rebuilt rather than failing every build
        columns = [r[1] for r in cursor.execute(f"PRAGMA table_info({TABLE})")]
        if columns and columns != list(_COLUMNS):
            cursor.execute(f"DROP TABLE {TABLE}")
            cursor.execute(f"UPDATE {_STATE} SET built = 0 WHERE id = 1")
        cursor.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING fts5("
                       f"{', '.join(_COLUMNS)}, tokenize='trigram', detail='none')")
        for sql in _DIRTY_TRIGGERS:
            cursor.execute(sql)
    except Exception as exc:  # noqa: BLE001
        logger.warning("songs search index unavailable, searches scan the catalogue: %s", exc)


def fold(value: Optional[str]) -> str:
    """``queries._fold_sql`` in Python: SQLite's LOWER (ASCII only) for a value
    of printable ASCII, ``normalize_for_comparison`` for anything else."""
    if not value:
        return ""
    if all(" " <= ch <= "~" for ch in value):
        return value.lower()
    from core.text.normalize import normalize_for_comparison
    return normalize_for_comparison(value) or ""


def _ready(conn) -> bool:
    try:
        row = conn.execute(f"SELECT built FROM {_STATE} WHERE id = 1").fetchone()
    except Exception:  # noqa: BLE001 - no index table: not ready
        return False
    return bool(row and row[0])


def song_count_version(conn) -> Optional[int]:
    """A counter every live-file change bumps; None when it is unavailable."""
    try:
        row = conn.execute(f"SELECT files_version FROM {_STATE} WHERE id = 1").fetchone()
    except Exception:  # noqa: BLE001
        return None
    return int(row[0]) if row else None


def matching_ids(conn, folded_query: str) -> Optional[List[int]]:
    """Track ids whose folded title, credit or album artist contains the
    folded query, or None when the caller should run its original query."""
    if len(folded_query) < 3 or "%" in folded_query or "_" in folded_query:
        return None
    if not _ready(conn):
        return None
    from core.library2.queries import _fold_sql
    from core.text.normalize import normalize_for_comparison

    # the dirty-track branch folds live through _fold_sql's norm_text
    conn.create_function("norm_text", 1, lambda x: normalize_for_comparison(x) if x else "",
                         deterministic=True)
    like = f"%{folded_query}%"
    try:
        rows = conn.execute(
            f"""SELECT rowid FROM {TABLE}
                 WHERE (title LIKE :like OR track_artist LIKE :like OR artist LIKE :like)
                   AND rowid NOT IN (SELECT track_id FROM {_DIRTY})
                UNION
                SELECT t.id FROM {_DIRTY} d
                  JOIN lib2_tracks t ON t.id = d.track_id
                  JOIN lib2_albums al ON al.id = t.album_id
                  LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                 WHERE {_fold_sql('t.title')} LIKE :like
                    OR {_fold_sql('t.track_artist')} LIKE :like
                    OR {_fold_sql('ar.name')} LIKE :like""",
            {"like": like}).fetchall()
    except Exception as exc:  # noqa: BLE001 - fall back, never fail a search
        logger.debug("songs search index read failed: %s", exc)
        return None
    return [int(r[0]) for r in rows]


def _rows_for(conn, track_ids: Optional[List[int]]):
    sql = ("SELECT t.id, t.title, t.track_artist, ar.name FROM lib2_tracks t "
           "JOIN lib2_albums al ON al.id = t.album_id "
           "LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id")
    if track_ids is None:
        return conn.execute(sql).fetchall()
    import json
    return conn.execute(sql + " WHERE t.id IN (SELECT value FROM json_each(?))",
                        (json.dumps(track_ids),)).fetchall()


def _write(conn, rows) -> None:
    conn.executemany(
        f"INSERT INTO {TABLE}(rowid, title, track_artist, artist) VALUES (?,?,?,?)",
        [(int(r[0]), fold(r[1]), fold(r[2]), fold(r[3])) for r in rows])


def rebuild(database) -> bool:
    """Fill the index from the whole catalogue. Changes made while it runs
    stay dirty and are answered live, then caught up by ``flush``."""
    conn = database._get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"DELETE FROM {_DIRTY}")
        conn.execute(f"DELETE FROM {TABLE}")
        _write(conn, _rows_for(conn, None))
        conn.execute(f"UPDATE {_STATE} SET built = 1 WHERE id = 1")
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        if conn.in_transaction:
            conn.rollback()
        logger.warning("songs search index build failed: %s", exc)
        return False
    finally:
        conn.close()


def flush(database) -> int:
    """Catch the index up with every dirty track, a batch per transaction.
    Each batch reads, rewrites and clears its tracks under one write lock,
    so a change landing meanwhile is never cleared unseen."""
    done = 0
    while True:
        conn = database._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ids = [int(r[0]) for r in conn.execute(
                f"SELECT track_id FROM {_DIRTY} LIMIT {_BATCH}")]
            if not ids:
                conn.rollback()
                return done
            import json
            marks = json.dumps(ids)
            conn.execute(f"DELETE FROM {TABLE} WHERE rowid IN (SELECT value FROM json_each(?))", (marks,))
            _write(conn, _rows_for(conn, ids))
            conn.execute(f"DELETE FROM {_DIRTY} WHERE track_id IN (SELECT value FROM json_each(?))", (marks,))
            conn.commit()
            done += len(ids)
        except Exception as exc:  # noqa: BLE001
            if conn.in_transaction:
                conn.rollback()
            logger.debug("songs search index catch-up failed: %s", exc)
            return done
        finally:
            conn.close()


_worker_lock = threading.Lock()
_worker_running = False
#: the test suite turns this off: a thread outliving its test's database would
#: write into the next one. tests build and flush the index directly.
BACKGROUND = True


def refresh_in_background(database) -> None:
    """Build the index if it was never built, else catch it up. One worker at
    a time; a search never waits for it."""
    global _worker_running
    if not BACKGROUND:
        return
    with _worker_lock:
        if _worker_running:
            return
        _worker_running = True

    def _run():
        global _worker_running
        try:
            conn = database._get_connection()
            try:
                ready = _ready(conn)
                dirty = conn.execute(f"SELECT 1 FROM {_DIRTY} LIMIT 1").fetchone() is not None
            finally:
                conn.close()
            if not ready:
                if rebuild(database):
                    logger.info("songs search index built")
            elif dirty:
                flush(database)
        except Exception as exc:  # noqa: BLE001
            logger.debug("songs search index refresh failed: %s", exc)
        finally:
            with _worker_lock:
                _worker_running = False

    threading.Thread(target=_run, name="lib2-song-search", daemon=True).start()
