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
        logger.warning(
            "Failed to cache api_track_count for album %s: %s", album_id, e
        )
