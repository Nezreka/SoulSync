"""Cached per-artist counts, used ONLY as a sort key for the artist list.

Why this exists
---------------
``GET /api/library/v2/artists?sort=albums`` (and ``sort=tracks``) has to order
every artist in the library before it can take a page of 75. The counts it
orders by are aggregates over ``lib2_album_artists`` / ``lib2_track_artists``
folded through the artist-alias graph, and there is no way to express that as an
indexed ``ORDER BY``.

Three shapes were measured on a 12,000-artist / 288,000-track catalogue:

===============================================  =========
correlated scalar subquery, per artist row        11,469 ms
one aggregate CTE + ``LEFT JOIN`` (no index)       4,906 ms
aggregate into an indexed table, then join             3 ms
===============================================  =========

The middle row is the interesting one: hoisting the aggregate out of the
``ORDER BY`` removes the per-row re-execution, but SQLite will not build an
automatic index on the right-hand side of a ``LEFT JOIN`` against a CTE, so it
falls back to scanning that 12,000-row CTE once per artist -- 1.4 x 10^8
comparisons. The aggregate itself only costs ~155 ms; essentially all of the
remaining time was that join.

So the aggregate is persisted in a real table with a real primary key.

What this is NOT
----------------
It is not a second source of truth for the counts. The numbers rendered in the
artist table still come from ``list_artists``' live per-page CTEs, which are
exact and cheap because they are scoped to the 75 artists on screen. This table
is consulted only to decide the ORDER, where being a few minutes behind moves an
artist by a row or two and costs nothing. ``list_artists`` refreshes it when it
is missing or older than :data:`STALE_AFTER_SECONDS`, so no caller has to
remember to.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from utils.logging_config import get_logger

logger = get_logger("library2.artist_rollup")

#: A roll-up older than this is rebuilt on the next count-sorted request.
STALE_AFTER_SECONDS = 900

#: One rebuild at a time per process. Two concurrent count-sorted requests would
#: otherwise both decide the roll-up is stale and both start writing, so the
#: second would sit on `busy_timeout` behind the first for no benefit -- on a
#: READ path. The loser re-checks freshness after the winner commits and finds
#: there is nothing to do.
_refresh_lock = threading.Lock()

LIB2_ARTIST_ROLLUP_DDL = """
CREATE TABLE IF NOT EXISTS lib2_artist_rollup (
    artist_id   INTEGER PRIMARY KEY
                REFERENCES lib2_artists(id) ON DELETE CASCADE,
    album_count INTEGER NOT NULL DEFAULT 0,
    track_count INTEGER NOT NULL DEFAULT 0,
    computed_at REAL    NOT NULL DEFAULT 0
)
"""

# The eligibility rule has to match `list_artists`' album_stats CTE exactly, or
# the sort order would disagree with the numbers rendered next to it.
_ELIGIBLE_ALBUM = "al.album_type <> 'single' AND (al.origin='library' OR al.monitored=1)"

_ALBUM_COUNTS = f"""
    SELECT artist_id, COUNT(DISTINCT album_id) AS n FROM (
        SELECT COALESCE(m.canonical_artist_id, m.id) AS artist_id,
               aa.album_id AS album_id
          FROM lib2_album_artists aa
          JOIN lib2_artists m ON m.id = aa.artist_id
          JOIN lib2_albums al ON al.id = aa.album_id
         WHERE {_ELIGIBLE_ALBUM}
        UNION ALL
        SELECT COALESCE(m.canonical_artist_id, m.id), t.album_id
          FROM lib2_track_artists ta
          JOIN lib2_artists m ON m.id = ta.artist_id
          JOIN lib2_tracks t ON t.id = ta.track_id
          JOIN lib2_albums al ON al.id = t.album_id
         WHERE {_ELIGIBLE_ALBUM}
    ) x GROUP BY artist_id
"""

_TRACK_COUNTS = """
    SELECT COALESCE(m.canonical_artist_id, m.id) AS artist_id,
           COUNT(DISTINCT t.id) AS n
      FROM lib2_track_artists ta
      JOIN lib2_artists m ON m.id = ta.artist_id
      JOIN lib2_tracks t ON t.id = ta.track_id
      LEFT JOIN lib2_wanted_tracks w ON w.track_id = t.id AND w.profile_id = 1
     WHERE COALESCE(w.wanted, t.monitored) = 1 OR EXISTS (
               SELECT 1 FROM lib2_track_files tf
                WHERE tf.track_id = t.id
                  AND COALESCE(tf.file_state, 'active')
                      NOT IN ('missing_confirmed', 'deleted'))
     GROUP BY 1
"""


def ensure_artist_rollup_schema(cursor: Any) -> None:
    cursor.execute(LIB2_ARTIST_ROLLUP_DDL)
    # Ordering is by count DESC; the artist_id tiebreak keeps the index useful
    # for the paged scan rather than only for equality.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_artist_rollup_albums "
        "ON lib2_artist_rollup(album_count DESC, artist_id)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_artist_rollup_tracks "
        "ON lib2_artist_rollup(track_count DESC, artist_id)")


def refresh_artist_rollup(conn: Any) -> int:
    """Rebuild the whole table. Returns the number of artists written.

    A full rebuild rather than an incremental one on purpose: it is a single
    ~150 ms aggregate at 288k tracks, and every incremental scheme would need
    to hook the alias graph, the album origin/monitored flags and the wanted
    projection -- three places that already have enough invariants.

    Commits, because the caller is a read path that must not hold a write
    transaction open across the page query.
    """
    started = time.time()
    ensure_artist_rollup_schema(conn)
    conn.execute("DELETE FROM lib2_artist_rollup")
    # Seed the rows, then merge each aggregate in through an INDEXED staging
    # table. Two shapes were tried and rejected, both for the same reason this
    # table exists at all -- SQLite will not index a subquery:
    #
    #   one SELECT with two LEFT JOINed sub-aggregates  -> 10 s  (scans the
    #       12,000-row aggregate once per artist)
    #   UPDATE ... SET c = (SELECT ... FROM (aggregate)) -> worse still: the
    #       correlated subquery re-runs the WHOLE aggregate per row.
    conn.execute(
        """INSERT INTO lib2_artist_rollup(artist_id, album_count, track_count, computed_at)
           SELECT id, 0, 0, :now FROM lib2_artists WHERE canonical_artist_id IS NULL""",
        {"now": started},
    )
    for column, aggregate in (("album_count", _ALBUM_COUNTS),
                              ("track_count", _TRACK_COUNTS)):
        conn.execute("DROP TABLE IF EXISTS temp.lib2_rollup_stage")
        conn.execute(
            "CREATE TEMP TABLE lib2_rollup_stage("
            "  artist_id INTEGER PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0)")
        conn.execute(
            "INSERT OR REPLACE INTO temp.lib2_rollup_stage(artist_id, n) "
            f"SELECT CAST(artist_id AS INTEGER), n FROM ({aggregate}) agg "
            "WHERE artist_id IS NOT NULL")
        conn.execute(
            f"""UPDATE lib2_artist_rollup SET {column} = stage.n
                  FROM temp.lib2_rollup_stage stage
                 WHERE stage.artist_id = lib2_artist_rollup.artist_id""")
    conn.execute("DROP TABLE IF EXISTS temp.lib2_rollup_stage")
    written = conn.execute("SELECT COUNT(*) FROM lib2_artist_rollup").fetchone()[0]
    conn.commit()
    logger.debug("artist roll-up rebuilt: %d artists in %.0f ms",
                 written, (time.time() - started) * 1000)
    return int(written)


def ensure_fresh_artist_rollup(conn: Any) -> bool:
    """Rebuild if the roll-up is missing, empty or stale. True if it rebuilt.

    Never raises: an ordering key is not worth failing a page render for. A
    caller that gets ``False`` after an error simply falls back to the live
    aggregate, which is slow but correct.
    """
    try:
        ensure_artist_rollup_schema(conn)
        if not _is_stale(conn):
            return False
        with _refresh_lock:
            # Re-check: the thread that held the lock may have just rebuilt it.
            if not _is_stale(conn):
                return False
            refresh_artist_rollup(conn)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("artist roll-up refresh skipped: %s", e)
        return False


def _is_stale(conn: Any) -> bool:
    """Whether the roll-up needs rebuilding. Cheap: two counts."""
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(MAX(computed_at), 0) AS at "
        "FROM lib2_artist_rollup"
    ).fetchone()
    artists = conn.execute(
        "SELECT COUNT(*) FROM lib2_artists WHERE canonical_artist_id IS NULL"
    ).fetchone()[0]
    rows = int(row["n"] if hasattr(row, "keys") else row[0])
    computed_at = float(row["at"] if hasattr(row, "keys") else row[1])
    # Row-count drift is checked as well as age. An artist that was just
    # added or removed is the one case a user notices immediately -- they
    # sort by album count right after an import and the new artist is
    # missing from the ordering -- and it is free to detect, because the
    # count is already in hand. Everything else (an album gained, a file
    # deleted) only nudges an existing artist's position, which is what the
    # age window is for.
    fresh = (
        rows > 0
        and rows == artists
        and (time.time() - computed_at) < STALE_AFTER_SECONDS
    )
    return not (fresh or artists == 0)


__all__ = [
    "LIB2_ARTIST_ROLLUP_DDL",
    "STALE_AFTER_SECONDS",
    "ensure_artist_rollup_schema",
    "ensure_fresh_artist_rollup",
    "refresh_artist_rollup",
]
