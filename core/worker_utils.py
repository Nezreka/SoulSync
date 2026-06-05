"""Shared helpers for background workers."""

import logging
import re
import threading
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

# Artist-match acceptance gate. Stricter than the 0.80 each worker uses for
# album/track titles: artist names are short, so 0.80 lets distinct artists
# slip through ("ODESZA"/"odessa", "Blance"/"Blanke", "Lady A"/"Lady Gaga" all
# score 0.80-0.83). 0.85 rejects those while still tolerating real variation
# that survives normalization.
ARTIST_NAME_MATCH_THRESHOLD = 0.85

# Whitelist of artist source-id columns we'll interpolate into SQL — guards the
# conflict query against any unexpected column name.
_ARTIST_ID_COLUMNS = frozenset({
    'deezer_id', 'spotify_artist_id', 'itunes_artist_id', 'musicbrainz_id',
    'discogs_id', 'audiodb_id', 'qobuz_id', 'tidal_id', 'amazon_id', 'soul_id',
})


def normalize_artist_name(name: str) -> str:
    """Lowercase, drop ' - ...' suffixes / parentheticals / punctuation, and
    collapse whitespace — the same normalization the per-worker matchers use."""
    name = (name or '').lower().strip()
    name = re.sub(r'\s+[-–—]\s+.*$', '', name)
    name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def artist_name_matches(query: str, result: str,
                        threshold: float = ARTIST_NAME_MATCH_THRESHOLD) -> bool:
    """True if two artist names match at/above ``threshold`` after normalization."""
    nq, nr = normalize_artist_name(query), normalize_artist_name(result)
    if not nq or not nr:
        return False
    return SequenceMatcher(None, nq, nr).ratio() >= threshold


def _names_equivalent(a: str, b: str) -> bool:
    return normalize_artist_name(a) == normalize_artist_name(b)


def source_id_conflict(database, id_column: str, source_id, artist_id,
                       artist_name: str) -> Optional[str]:
    """Return the name of a DIFFERENTLY-named library artist that already holds
    ``source_id`` in ``id_column``, or None.

    A same-named holder (the same artist indexed on two media servers) is NOT a
    conflict — both legitimately share the id. Only a different artist holding
    the id signals the kind of corruption where one source id gets smeared
    across unrelated artists.
    """
    if source_id in (None, ''):
        return None
    if id_column not in _ARTIST_ID_COLUMNS:
        logger.debug(f"source_id_conflict: refusing unknown column {id_column!r}")
        return None
    try:
        with database._get_connection() as conn:
            rows = conn.execute(
                f"SELECT name FROM artists WHERE {id_column} = ? AND id != ?",
                (str(source_id), artist_id),
            ).fetchall()
    except Exception as e:
        logger.debug(f"source_id_conflict check failed for {id_column}={source_id}: {e}")
        return None
    for (other_name,) in rows:
        if not _names_equivalent(artist_name, other_name):
            return other_name
    return None


def accept_artist_match(database, id_column: str, source_id, artist_id,
                        query_name: str, result_name: str,
                        threshold: float = ARTIST_NAME_MATCH_THRESHOLD) -> tuple:
    """Decide whether to store ``source_id`` on an artist.

    Returns ``(ok: bool, reason: str)``. Accepts only when the result's name
    matches the library artist at/above ``threshold`` AND the id isn't already
    claimed by a differently-named artist. ``reason`` explains a rejection (for
    debug logging). This is the single gate every worker's artist match should
    pass through, so the 'one id smeared across many artists' bug can't recur.
    """
    if not artist_name_matches(query_name, result_name, threshold):
        return False, (
            f"name mismatch '{query_name}' vs '{result_name}' (< {threshold})"
        )
    conflict = source_id_conflict(database, id_column, source_id, artist_id, query_name)
    if conflict:
        return False, (
            f"{id_column}={source_id} already claimed by '{conflict}' — "
            f"skipping to avoid a shared/duplicate id"
        )
    return True, ""


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
