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


MATCHED = 'matched'
UNAVAILABLE = 'error'
NO_STORED_ID = ''


def honor_stored_match(
    *,
    db,
    entity_table: str,
    entity_id,
    id_column: str,
    client_fetch_fn: Callable[[str], Any],
    on_match_fn: Callable[[Any, str, Any], None],
    mark_status_fn: Optional[Callable[[str, Any, str], None]] = None,
    status_column: Optional[str] = None,
    log_prefix: str = '',
) -> str:
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

        mark_status_fn: Optional ``fn(entity_kind, entity_id, status)``
            — the worker's own ``_mark_status``. Used to persist an
            ``error`` (with its ``*_last_attempted`` retry timestamp)
            when a stored ID exists but the source could not confirm
            it. Without it the failure leaves no trace and the entity
            is picked again on the very next cycle, with no backoff.
        status_column: The worker's ``<service>_match_status`` column.
            An entity that already reads ``matched`` is NOT downgraded
            to ``error`` by a transient failure — its chip would turn
            red for a match that is still perfectly good, and it is
            not the row a retry loop would pick anyway.

    Returns one of three states (L2-005):
        ``MATCHED``     — the stored ID was refreshed. Caller counts a
            match and skips search-by-name.
        ``UNAVAILABLE`` — a stored ID IS set but the source could not
            confirm it right now (fetch raised, or returned nothing).
            Caller must NOT search by name: a transient provider
            failure is not evidence that the stored ID is wrong, and
            searching would overwrite a deliberately chosen ID with
            whatever a fuzzy name match happens to return. The ID is
            released only by an explicit re-match, never by a timeout.
        ``NO_STORED_ID`` (falsy) — nothing is stored, so the caller
            falls through to its existing search-by-name flow.

    Notes:
        - Exceptions in ``client_fetch_fn`` are caught and logged.
        - Exceptions in ``on_match_fn`` propagate (those are real
          DB errors the worker should know about).
    """
    stored_id = _read_id_column(db, entity_table, entity_id, id_column)
    if not stored_id:
        return NO_STORED_ID

    entity_kind = entity_table[:-1]

    def _unavailable(reason: str) -> str:
        already_matched = False
        if status_column is not None:
            try:
                already_matched = _read_id_column(
                    db, entity_table, entity_id, status_column) == 'matched'
            except Exception as read_exc:  # noqa: BLE001 - older schema, no column
                logger.debug(
                    f"[{log_prefix}] could not read {status_column}: {read_exc}"
                )
        if mark_status_fn is not None and not already_matched:
            try:
                mark_status_fn(entity_kind, entity_id, 'error')
            except Exception as mark_exc:  # noqa: BLE001 - bookkeeping only
                logger.debug(
                    f"[{log_prefix}] could not record the failed stored-ID "
                    f"refresh for {entity_kind} #{entity_id}: {mark_exc}"
                )
        logger.warning(
            f"[{log_prefix}] Stored ID {stored_id} for {entity_kind} "
            f"#{entity_id} could not be confirmed ({reason}) — keeping it "
            f"rather than searching by name"
        )
        return UNAVAILABLE

    try:
        api_data = client_fetch_fn(stored_id)
    except Exception as exc:
        return _unavailable(str(exc))

    if not api_data:
        return _unavailable("the source returned no data")

    on_match_fn(entity_id, stored_id, api_data)
    logger.info(
        f"[{log_prefix}] Honored manual match: "
        f"{entity_kind} #{entity_id} → {id_column}={stored_id}"
    )
    return MATCHED
