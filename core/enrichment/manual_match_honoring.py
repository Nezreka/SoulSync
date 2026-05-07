"""Honor manually-matched source IDs in per-source enrichment workers.

GitHub issue #501 (@Tacobell444): every per-source enrichment worker's
``_process_*_individual`` method ran a fuzzy text search on the album /
track name and overwrote the stored source ID with whatever the search
returned. If the user had manually matched an album to a specific source
ID (e.g. set ``albums.spotify_album_id = 'ABC'`` via the match-chip UI),
the next "Enrich" click would search by name → pick a different result
→ overwrite the manual match with the wrong ID, OR fail to match
anything and revert the status to ``not_found``.

This module lifts the "honor stored ID" fast path into one shared
helper. Each per-source worker (Spotify / iTunes / Deezer / Discogs /
MusicBrainz / AudioDB / Tidal / Qobuz) calls it before falling back
to its existing search-by-name flow. Same fix in 8 workers gets
exactly one implementation; per-worker variability (column name,
client fetch method, response shape) plugs in via callbacks.

Lift what's truly shared. Caller knows its own column + client
method + update logic; the helper just orchestrates.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("enrichment.manual_match_honoring")


def _read_id_column(db, entity_table: str, entity_id, id_column: str) -> Optional[str]:
    """Read the stored source ID for one entity. Returns None when the
    column is empty / unset."""
    if entity_table not in ('albums', 'tracks', 'artists'):
        # Defensive: we only operate on these three. Avoids SQL injection
        # via a bad table name (id_column is also restricted to known
        # column names by callers but defense in depth never hurts).
        return None
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {id_column} FROM {entity_table} WHERE id = ?",
            (entity_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    value = row[0] if not hasattr(row, 'keys') else row[id_column]
    return str(value).strip() if value else None


def honor_stored_match(
    *,
    db,
    entity_table: str,
    entity_id,
    id_column: str,
    client_fetch_fn: Callable[[str], Any],
    on_match_fn: Callable[[Any, str, Any], None],
    log_prefix: str = '',
) -> bool:
    """Fast-path enrichment via a stored source ID — preserves manual
    matches.

    Args:
        db: ``MusicDatabase`` instance (for the column read).
        entity_table: ``'albums'``, ``'tracks'``, or ``'artists'``.
        entity_id: Library DB ID of the entity to enrich.
        id_column: Column on ``entity_table`` that stores the source-
            specific ID (``spotify_album_id`` / ``itunes_album_id`` /
            ``deezer_id`` / etc).
        client_fetch_fn: Callable taking the stored ID and returning
            the source's raw response (Album dataclass, dict, or
            whatever the client returns). Typically
            ``self.client.get_album`` or ``self.client.get_track``.
        on_match_fn: Worker callback invoked with
            ``(entity_id, stored_id, api_response)`` to apply the
            metadata refresh. Worker knows the response shape; helper
            doesn't.
        log_prefix: Display name for log lines (``'Spotify'`` /
            ``'iTunes'`` / etc).

    Returns:
        True if a stored ID was found AND the fetch returned data AND
        the on-match callback ran. Caller skips its search-by-name
        flow and counts a match.

        False if no stored ID is set, the fetch failed, or the fetch
        returned empty. Caller falls through to its existing search-
        by-name flow (the legacy behavior for un-matched entities).

    Notes:
        - Exceptions in ``client_fetch_fn`` are caught and logged at
          warning level — caller falls through to search.
        - Exceptions in ``on_match_fn`` propagate (those are real
          DB errors the worker should know about).
    """
    stored_id = _read_id_column(db, entity_table, entity_id, id_column)
    if not stored_id:
        return False

    try:
        api_data = client_fetch_fn(stored_id)
    except Exception as exc:
        logger.warning(
            f"[{log_prefix}] Stored-ID fetch failed for "
            f"{entity_table[:-1]} #{entity_id} (id={stored_id}): {exc}"
        )
        return False

    if not api_data:
        logger.debug(
            f"[{log_prefix}] Stored ID {stored_id} for "
            f"{entity_table[:-1]} #{entity_id} returned empty data — "
            f"falling through to search-by-name"
        )
        return False

    on_match_fn(entity_id, stored_id, api_data)
    logger.info(
        f"[{log_prefix}] Honored manual match: "
        f"{entity_table[:-1]} #{entity_id} → {id_column}={stored_id}"
    )
    return True
