"""Shared helpers for background workers."""

import logging
import threading

logger = logging.getLogger(__name__)


def interruptible_sleep(stop_event: threading.Event, seconds: float, step: float = 0.5) -> bool:
    """Sleep in chunks so shutdown can interrupt long waits."""
    if seconds <= 0:
        return stop_event.is_set()

    remaining = float(seconds)
    while remaining > 0 and not stop_event.is_set():
        wait_for = min(step, remaining)
        if stop_event.wait(wait_for):
            break
        remaining -= wait_for
    return stop_event.is_set()


def set_album_api_track_count(cursor, album_id, count):
    """Cache an album's authoritative track count from a metadata source.

    Called by enrichment workers (Spotify / iTunes / Deezer / Discogs) after
    they fetch album metadata. The count is the EXPECTED total tracks
    according to that source — distinct from `albums.track_count`, which
    server syncs (Plex `leafCount`, SoulSync standalone `len(tracks)`)
    populate with the OBSERVED count SoulSync already has indexed. The
    Album Completeness repair job reads `albums.api_track_count` as the
    expected total; populating it here during enrichment avoids a second
    round of API calls during the repair scan.

    Skips the write when the source didn't supply a positive numeric count
    (None, 0, negative, or non-numeric) — that way a source lacking track
    info doesn't overwrite a good value another source already wrote. If
    multiple sources report different counts (rare, usually deluxe vs.
    standard edition), last-write-wins across enrichment cycles; that's
    fine since any metadata-source count is strictly better than the
    observed-count fallback that the repair job used before this column
    existed.

    Caller owns the cursor (and its connection / transaction) — this
    helper does not commit. Integrates with each worker's existing
    `_update_album` method, which already batches several UPDATEs into
    one transaction.
    """
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        return
    if count <= 0:
        return
    # Swallow SQL errors — each worker batches several album UPDATEs into
    # one transaction, and we don't want a failure here (e.g., the
    # migration somehow hasn't run yet and the column is missing) to
    # rollback the worker's other writes (spotify_album_id, thumb_url,
    # etc.). The repair job's fallback path will eventually populate the
    # column via its own save path once the column exists.
    try:
        cursor.execute(
            "UPDATE albums SET api_track_count = ? WHERE id = ?",
            (count, album_id),
        )
    except Exception as e:
        if "api_track_count" in str(e) and "no such column" in str(e).lower():
            try:
                cursor.execute("ALTER TABLE albums ADD COLUMN api_track_count INTEGER DEFAULT NULL")
                cursor.execute(
                    "UPDATE albums SET api_track_count = ? WHERE id = ?",
                    (count, album_id),
                )
                logger.info("Repaired missing api_track_count column while caching album track count")
                return
            except Exception as repair_error:
                e = repair_error
        logger.warning(
            "Failed to cache api_track_count for album %s: %s", album_id, e
        )


# --- Enrichment "process this group first" override -----------------------
# Each enrichment worker normally processes artist -> album -> track. A user
# can pin one entity type to run first via the Manage Enrichment Workers modal;
# the choice is stored in config as "<service>_enrichment_priority" and read
# at the top of each worker's _get_next_item so it takes effect live. When the
# pinned group is exhausted (or unset), the worker falls back to its normal
# chain — so the default path is unchanged.

PRIORITY_ENTITIES = ('artist', 'album', 'track')


def read_enrichment_priority(service: str) -> str:
    """Return the pinned entity ('artist'|'album'|'track') for a worker, or ''.

    Read every loop so the override applies without restarting the worker.
    Any error / unset / invalid value yields '' (no override)."""
    try:
        from config.settings import config_manager
        val = (config_manager.get(f'{service}_enrichment_priority', '') or '')
        val = str(val).strip().lower()
        return val if val in PRIORITY_ENTITIES else ''
    except Exception:
        return ''


def priority_pending_item(cursor, service, entity, type_overrides=None):
    """Return one pending (NULL match_status) item of `entity`, or None.

    `service` is the column prefix (e.g. 'spotify' -> spotify_match_status) and
    MUST be a trusted worker-supplied literal (it is interpolated into SQL).
    `type_overrides` maps the canonical entity to the worker's dispatch 'type'
    string — Spotify/iTunes process individual items as 'album_individual' /
    'track_individual', the other workers use 'album' / 'track'. The returned
    dict matches the shape those workers already return from _get_next_item."""
    if not str(service).isalpha() or entity not in PRIORITY_ENTITIES:
        return None
    type_overrides = type_overrides or {}
    ms = f"{service}_match_status"

    if entity == 'artist':
        cursor.execute(
            f"SELECT id, name FROM artists WHERE {ms} IS NULL AND id IS NOT NULL "
            f"ORDER BY id ASC LIMIT 1"
        )
        r = cursor.fetchone()
        return {'type': type_overrides.get('artist', 'artist'), 'id': r[0], 'name': r[1]} if r else None

    if entity == 'album':
        cursor.execute(
            f"SELECT a.id, a.title, ar.name FROM albums a JOIN artists ar ON a.artist_id = ar.id "
            f"WHERE a.{ms} IS NULL AND a.id IS NOT NULL ORDER BY a.id ASC LIMIT 1"
        )
        r = cursor.fetchone()
        return {'type': type_overrides.get('album', 'album'), 'id': r[0], 'name': r[1], 'artist': r[2]} if r else None

    # track
    cursor.execute(
        f"SELECT t.id, t.title, ar.name FROM tracks t JOIN artists ar ON t.artist_id = ar.id "
        f"WHERE t.{ms} IS NULL AND t.id IS NOT NULL ORDER BY t.id ASC LIMIT 1"
    )
    r = cursor.fetchone()
    return {'type': type_overrides.get('track', 'track'), 'id': r[0], 'name': r[1], 'artist': r[2]} if r else None
