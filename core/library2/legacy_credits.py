"""3.6.1's Spotify and Deezer artist credits, carried into Library v2 once.

3.6.1 kept every artist a source credits on a track or album in
``track_artist_credits`` / ``album_artist_credits`` (#6ec2ece64: collab albums
on every artist's page). the upgrade copied neither, and it records every
3.6.1 match as done, so the workers never fetch them again: "watch the
throne" left kanye's page, every featured artist only a source named lost the
track. this copies them into ``lib2_provider_credits`` by legacy id and
links the artists the library has, the way a fresh match would.

runs once, in batches with a commit each, from the deferred convergence pass.
a release the v2 workers matched since keeps its own snapshot.
"""

from __future__ import annotations

from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("library2.legacy_credits")

_MARKER = "_lib2_legacy_credits_carried"
_BATCH = 5000


def _has_table(conn: Any, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _carry(conn: Any, entity: str, legacy_table: str, legacy_key: str,
           lib2_table: str, lib2_legacy_col: str) -> int:
    """copy one entity type's multi-artist credits, batch by batch."""
    if not _has_table(conn, legacy_table):
        return 0
    # a lone credit is the artist it is filed under: nothing to link (the v2
    # writer skips those too). worked out once, not per batch
    conn.execute("DROP TABLE IF EXISTS temp._legacy_multi_credit")
    conn.execute(
        f"CREATE TEMP TABLE _legacy_multi_credit AS "
        f"SELECT {legacy_key} AS legacy_id, source FROM {legacy_table} "
        f"WHERE source IN ('spotify','deezer') "
        f"GROUP BY {legacy_key}, source HAVING COUNT(*) > 1")
    conn.execute("CREATE INDEX temp._legacy_multi_credit_key "
                 "ON _legacy_multi_credit(legacy_id, source)")
    # a release the v2 workers matched since has its own snapshot: keep it.
    # taken before copying, so this run's own rows never count as one
    conn.execute("DROP TABLE IF EXISTS temp._v2_has_credits")
    conn.execute("CREATE TEMP TABLE _v2_has_credits AS SELECT DISTINCT entity_id, source "
                 "FROM lib2_provider_credits WHERE entity_type = ?", (entity,))
    conn.execute("CREATE INDEX temp._v2_has_credits_key ON _v2_has_credits(entity_id, source)")
    # the 3.6.1 tables are WITHOUT ROWID: batch over the temp table's keys and
    # reach each key's credits through the (id, source, position) primary key
    last = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM _legacy_multi_credit").fetchone()[0]
    copied = 0
    start = 0
    while start <= last:
        cur = conn.execute(
            f"""INSERT OR IGNORE INTO lib2_provider_credits
                    (entity_type, entity_id, source, position, name, source_artist_id)
                SELECT ?, e.id, c.source, c.position, COALESCE(c.name, ''), c.source_artist_id
                  FROM _legacy_multi_credit m
                  JOIN {legacy_table} c
                    ON c.{legacy_key} = m.legacy_id AND c.source = m.source
                  JOIN {lib2_table} e ON e.{lib2_legacy_col} = m.legacy_id
                 WHERE m.rowid > ? AND m.rowid <= ?
                   AND NOT EXISTS (
                       SELECT 1 FROM _v2_has_credits h
                        WHERE h.entity_id = e.id AND h.source = m.source)""",
            (entity, start, start + _BATCH))
        copied += max(int(cur.rowcount or 0), 0)
        conn.commit()
        start += _BATCH
    conn.execute("DROP TABLE IF EXISTS temp._legacy_multi_credit")
    conn.execute("DROP TABLE IF EXISTS temp._v2_has_credits")
    return copied


def carry_legacy_artist_credits(conn: Any) -> Dict[str, int]:
    """once: 3.6.1 credits into the snapshot, then linked. never raises."""
    stats = {"track_credits": 0, "album_credits": 0, "links": 0}
    try:
        if _has_table(conn, _MARKER) or not _has_table(conn, "lib2_provider_credits"):
            return stats
        stats["track_credits"] = _carry(conn, "track", "track_artist_credits", "track_id",
                                        "lib2_tracks", "legacy_track_id")
        stats["album_credits"] = _carry(conn, "album", "album_artist_credits", "album_id",
                                        "lib2_albums", "legacy_album_id")
        if stats["track_credits"] or stats["album_credits"]:
            from core.library2.provider_credits import materialize_artist_credits
            from core.library2.provider_ids import external_id_sql
            artist_ids = [row[0] for row in conn.execute(
                f"""SELECT id FROM lib2_artists
                     WHERE (spotify_id IS NOT NULL AND spotify_id <> '')
                        OR {external_id_sql('external_ids', 'deezer')} IS NOT NULL""").fetchall()]
            for index, artist_id in enumerate(artist_ids, 1):
                stats["links"] += materialize_artist_credits(conn, int(artist_id))
                if index % 50 == 0:
                    conn.commit()
        conn.execute(f"CREATE TABLE {_MARKER} (applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
        if any(stats.values()):
            logger.info("3.6.1 artist credits carried into Library v2: %s", stats)
    except Exception as exc:  # noqa: BLE001 - a credit is decoration, never a blocker
        logger.warning("3.6.1 artist credits not carried: %s", exc)
        try:
            conn.rollback()
        except Exception as rollback_exc:  # noqa: BLE001
            logger.debug("legacy credits rollback also failed: %s", rollback_exc)
    return stats
