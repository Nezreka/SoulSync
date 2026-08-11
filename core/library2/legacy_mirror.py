"""Legacy → lib2 mirror for enrichment writes (iss32-E01).

Nezreka's requirement is not negotiable: *"i don't want to lose any enrichment
functionality or data in the move to v2. v2 should get filled the same way v1
does now. every artist and album, artwork, genres, bios, provider ids, all
twelve workers."*

Today the twelve metadata workers write the LEGACY row and nothing carries the
result into ``lib2_*``. ``resync_entity_from_legacy`` exists for exactly this
and has no production caller.

**Why triggers and not calls.** The obvious fix — call the resync after
``_run_single_enrichment`` — only covers the manual single-entity Enrich from
the UI. The bulk is written by the workers' own loops: **137
``UPDATE artists/albums/tracks`` statements across 14 files**, and only about
half of them even touch ``updated_at``, so neither a per-call-site hook nor an
``updated_at`` sweep is complete. A hook also has to be remembered by whoever
adds the fifteenth worker.

An ``AFTER UPDATE`` trigger cannot be forgotten and does not care which file
the statement lives in. Its ``WHEN`` clause names exactly the columns the
resync copies, so the ordinary traffic that does not change enrichment data
(play counts, scan timestamps, ``*_last_attempted`` bookkeeping) enqueues
nothing. The queue row is tiny and collapses per entity, so a full
media-server scan cannot make it grow without bound.

**This is transitional by construction.** It exists because legacy is still
the write side. When the producers move to lib2 (docs §32.3.1 Stufe 2), the
triggers and this module are deleted together — that is the point of keeping
the bridge in one file instead of spreading it through the workers.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.legacy_mirror")

LIB2_LEGACY_DIRTY_DDL = """
CREATE TABLE IF NOT EXISTS lib2_legacy_dirty (
    entity_type TEXT NOT NULL,                 -- 'artist' | 'album' | 'track'
    legacy_id INTEGER NOT NULL,
    queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_type, legacy_id)
)
"""

# The columns each resync_*_from_legacy actually reads. A trigger that fired on
# any column change would enqueue the whole library on every media-server scan;
# these are the only ones whose change can make lib2 disagree with legacy.
_WATCHED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "artists": (
        "thumb_url", "genres", "summary", "style", "mood", "label", "banner_url",
        "aliases",
        # provider identity (mirrored into lib2_artists.external_ids)
        "spotify_artist_id", "musicbrainz_id", "deezer_id", "itunes_artist_id",
        "audiodb_id", "discogs_id", "tidal_id", "qobuz_id", "amazon_id",
        "jiosaavn_id", "genius_id", "lastfm_url",
        # provider bios/stats (mirrored into lib2_artists.enrichment). Kept in
        # step with `enrich._ARTIST_ENRICHMENT_COLUMNS` by
        # tests/library2/test_mirror_declaration.py — a column watched here but
        # not copied there churns the queue, and one copied there but not watched
        # here only ever moves on a manual Enrich.
        "lastfm_bio", "lastfm_listeners", "lastfm_playcount", "lastfm_tags",
        "lastfm_similar",
        "genius_description", "genius_alt_names", "genius_url",
        "discogs_bio", "discogs_members", "discogs_urls",
    ),
    "albums": (
        "thumb_url", "genres", "label", "explicit", "upc", "style", "mood",
        "release_date",
        "spotify_album_id", "musicbrainz_release_id", "deezer_id",
        "itunes_album_id", "audiodb_id", "discogs_id", "tidal_id", "qobuz_id",
        "amazon_id", "jiosaavn_id", "bandcamp_url", "lastfm_url",
        # provider payload (mirrored into lib2_albums.enrichment)
        "lastfm_listeners", "lastfm_playcount", "lastfm_tags", "lastfm_wiki",
        "discogs_genres", "discogs_styles", "discogs_label", "discogs_catno",
        "discogs_country", "discogs_rating", "discogs_rating_count",
        "bandcamp_tags", "bandcamp_label",
    ),
    "tracks": (
        "bpm", "explicit", "genius_lyrics", "copyright", "style", "mood", "isrc",
        "disc_number",
        "spotify_track_id", "musicbrainz_recording_id", "deezer_id",
        "itunes_track_id", "audiodb_id", "tidal_id", "qobuz_id", "amazon_id",
        "jiosaavn_id", "genius_id", "bandcamp_url", "lastfm_url",
        # provider payload (mirrored into lib2_tracks.enrichment)
        "lastfm_listeners", "lastfm_playcount", "lastfm_tags",
        "genius_description", "genius_url", "bandcamp_tags", "bandcamp_label",
    ),
}

_ENTITY_FOR_TABLE = {"artists": "artist", "albums": "album", "tracks": "track"}

_LIB2_TABLE = {
    "artist": ("lib2_artists", "legacy_artist_id"),
    "album": ("lib2_albums", "legacy_album_id"),
    "track": ("lib2_tracks", "legacy_track_id"),
}


def _existing_columns(cursor: Any, table: str) -> set:
    try:
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}
    except Exception:  # noqa: BLE001 - table may not exist in a test harness
        return set()


def _trigger_sql(table: str, columns: Tuple[str, ...]) -> str:
    entity = _ENTITY_FOR_TABLE[table]
    # `IS NOT` rather than `<>` so a NULL → value transition counts as a change;
    # `<>` is NULL (falsy) whenever either side is NULL, which is precisely the
    # case an enrichment worker creates when it fills an empty column.
    when = " OR ".join(f"NEW.{col} IS NOT OLD.{col}" for col in columns)
    return f"""
CREATE TRIGGER IF NOT EXISTS trg_lib2_mirror_{table}
AFTER UPDATE ON {table}
WHEN {when}
BEGIN
    INSERT OR IGNORE INTO lib2_legacy_dirty(entity_type, legacy_id)
    VALUES('{entity}', NEW.id);
END
"""


def ensure_legacy_mirror_schema(cursor: Any) -> None:
    """Create the dirty queue and (re)install the triggers. Idempotent.

    Triggers are dropped and recreated rather than ``IF NOT EXISTS``-ed alone,
    because the watched column set grows as lib2 learns to mirror more fields
    and a stale trigger would silently stop enqueueing the new ones.
    """
    cursor.execute(LIB2_LEGACY_DIRTY_DDL)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_legacy_dirty_queued "
        "ON lib2_legacy_dirty(queued_at)")
    for table, columns in _WATCHED_COLUMNS.items():
        present = _existing_columns(cursor, table)
        if "id" not in present:
            continue  # legacy table absent (sqlite-only harness)
        watched = tuple(col for col in columns if col in present)
        if not watched:
            continue
        try:
            cursor.execute(f"DROP TRIGGER IF EXISTS trg_lib2_mirror_{table}")
            cursor.execute(_trigger_sql(table, watched))
        except Exception as exc:  # noqa: BLE001
            logger.error("legacy mirror trigger for %s failed: %s", table, exc)


def enqueue(cursor: Any, entity_type: str, legacy_id: Any) -> None:
    """Queue one entity by hand — used by the manual Enrich path so the user
    sees the result immediately instead of at the next drain tick."""
    if entity_type not in _LIB2_TABLE or legacy_id in (None, ""):
        return
    cursor.execute(
        "INSERT OR IGNORE INTO lib2_legacy_dirty(entity_type, legacy_id) VALUES(?,?)",
        (entity_type, legacy_id))


def pending_count(connection: Any) -> int:
    try:
        return int(connection.execute(
            "SELECT COUNT(*) FROM lib2_legacy_dirty").fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def drain(database: Any, *, limit: int = 5000, batch_size: int = 200,
          entity_type: Optional[str] = None,
          legacy_id: Optional[Any] = None) -> Dict[str, int]:
    """Apply queued legacy changes to their lib2 rows.

    Scoping to one ``(entity_type, legacy_id)`` serves the manual Enrich: the
    user clicked, so that one entity is applied synchronously and the rest
    waits for the background tick.

    A queued id with no lib2 counterpart is dropped, not retried — an artist
    that only exists in legacy has nothing to mirror into, and leaving the row
    would make the queue grow forever on installs with unmigrated rows.
    """
    from core.library2.enrich import resync_entity_from_legacy

    stats = {"applied": 0, "unmapped": 0, "failed": 0}
    conn = database._get_connection()
    try:
        if entity_type and legacy_id is not None:
            rows: List[Any] = [(entity_type, legacy_id)]
        else:
            rows = [
                (r[0], r[1]) for r in conn.execute(
                    "SELECT entity_type, legacy_id FROM lib2_legacy_dirty "
                    "ORDER BY queued_at LIMIT ?", (int(limit),))
            ]
        if not rows:
            return stats

        for start in range(0, len(rows), batch_size):
            for kind, lid in rows[start:start + batch_size]:
                spec = _LIB2_TABLE.get(kind)
                if spec is None:
                    stats["unmapped"] += 1
                else:
                    table, column = spec
                    match = conn.execute(
                        f"SELECT id FROM {table} WHERE {column}=?", (lid,)).fetchone()
                    if match is None:
                        stats["unmapped"] += 1
                    else:
                        try:
                            resync_entity_from_legacy(conn, kind, int(match[0]), lid)
                            stats["applied"] += 1
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("mirror of %s %s failed: %s", kind, lid, exc)
                            stats["failed"] += 1
                            continue
                conn.execute(
                    "DELETE FROM lib2_legacy_dirty WHERE entity_type=? AND legacy_id=?",
                    (kind, lid))
            conn.commit()
    finally:
        conn.close()
    if stats["applied"] or stats["failed"]:
        logger.info("Legacy → lib2 mirror: %s", stats)
    return stats


def reconcile_divergent(database: Any, *, scan_limit: int = 2000,
                        enqueue_limit: int = 500,
                        after: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Queue mirrored rows that already disagree with legacy (iss32-T01a).

    The triggers only see legacy writes that happen after they are installed.
    Everything the twelve workers wrote before then is divergent and enqueues
    nothing — on a real library that was 156 of 170 mirrored rows. Without this
    sweep, promise 2's expected value of 0 would be unreachable and the figure
    would measure only how long the mirror has existed.

    The sweep reuses the audit's own walk and the existing queue, so it adds a
    producer, not a second repair path: the ordinary drain does the writing,
    at the pace and with the migration deference it already has. Bounded and
    resumable through ``cursor`` for the same reason every other lib2 backfill
    is (iss32-M05): no unbounded work inside one transaction.
    """
    from core.library2.enrich import iter_mirror_divergences

    stats: Dict[str, Any] = {
        "scanned": 0, "enqueued": 0, "dangling": 0, "exhausted": True,
        "cursor": {},
    }
    cursor: Dict[str, Any] = dict(after or {})
    conn = database._get_connection()
    try:
        for item in iter_mirror_divergences(conn, after=cursor):
            stats["scanned"] += 1
            cursor[item.entity_type] = item.lib2_id
            if item.status == "divergent":
                enqueue(conn, item.entity_type, item.legacy_id)
                stats["enqueued"] += 1
            elif item.status == "dangling":
                # The legacy row is gone; queueing it would make ``drain``
                # count it as unmapped and the next sweep find it again.
                stats["dangling"] += 1
            if (stats["scanned"] >= int(scan_limit)
                    or stats["enqueued"] >= int(enqueue_limit)):
                stats["exhausted"] = False
                stats["cursor"] = cursor
                break
        conn.commit()
    finally:
        conn.close()
    if stats["enqueued"]:
        logger.info("Legacy → lib2 reconcile queued %s divergent row(s) "
                    "(scanned %s)", stats["enqueued"], stats["scanned"])
    return stats


class MirrorDrainer:
    """Apply the queue on a timer, out of everyone else's way.

    Skips its tick entirely while a migration holds the writer — the mirror is
    never urgent enough to compete with it (iss32-M02), and the queue is
    durable, so nothing is lost by waiting.
    """

    def __init__(self, database: Any, *, interval: float = 30.0,
                 limit: int = 5000, sweep_scan_limit: int = 2000) -> None:
        self._database = database
        self._interval = max(float(interval), 1.0)
        self._limit = int(limit)
        self._sweep_scan_limit = int(sweep_scan_limit)
        self._sweep_cursor: Dict[str, Any] = {}
        self._seeded_provider_attempts = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="Lib2LegacyMirrorDrainer", daemon=True)

    def start(self) -> "MirrorDrainer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def tick(self) -> Dict[str, int]:
        from core.library2.bootstrap import bootstrap_is_active
        from core.library2.migration_gate import local_migration_active

        if local_migration_active() or bootstrap_is_active(self._database):
            return {"applied": 0, "unmapped": 0, "failed": 0, "deferred": 1}
        stats = drain(self._database, limit=self._limit)
        if stats["applied"] or stats["failed"]:
            # The queue is the urgent work and the sweep is catch-up; running
            # both in one tick would let a large backlog scan delay ordinary
            # mirroring. An idle tick has the time.
            return stats
        if not self._seeded_provider_attempts:
            # One-off: carry the legacy per-provider history into the lib2 ledger
            # so the first worker to move does not re-ask every provider about
            # the whole library. Idempotent, but pointless to repeat, and it
            # belongs off the startup path (iss32-M03).
            self._seeded_provider_attempts = True
            stats.update(self._seed_provider_attempts())
        sweep = reconcile_divergent(
            self._database, scan_limit=self._sweep_scan_limit,
            after=self._sweep_cursor)
        self._sweep_cursor = sweep["cursor"]
        stats.update(sweep)
        return stats

    def _seed_provider_attempts(self) -> Dict[str, int]:
        from core.library2.provider_attempts import backfill_from_legacy

        conn = self._database._get_connection()
        try:
            seeded = backfill_from_legacy(conn)
            conn.commit()
            return seeded
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider-attempt seeding failed: %s", exc)
            return {"seeded": 0, "scanned": 0}
        finally:
            conn.close()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                logger.debug("legacy mirror drain tick failed: %s", exc)


__all__ = [
    "LIB2_LEGACY_DIRTY_DDL",
    "MirrorDrainer",
    "drain",
    "enqueue",
    "ensure_legacy_mirror_schema",
    "pending_count",
]
