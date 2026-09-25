"""The shared reads an enrichment worker makes around a provider call.

Companion to :mod:`core.library2.worker_queue` (what to look at next) and
:mod:`core.library2.provider_writes` (where the answer goes). This module holds
the three things in between, lifted off legacy for docs §32.3.1 stage 2:

* the artist-match acceptance gate, and the owned-catalog titles it leans on;
* the expected-track-count cache the Album Completeness repair job reads;
* the stored-id fast path that stops a manual match from being searched over.

They were in ``core/worker_utils.py`` and
``core/enrichment/manual_match_honoring.py``, reached by twelve workers, so the
legacy SQL here is the shared half of twelve conversions.

The gate is the part with teeth. It exists because one provider id smeared across
unrelated artists is a bug that actually happened, and lib2 keeps provider ids in
two places — a promoted column for Spotify and MusicBrainz, ``external_ids`` for
the rest — so a check that read only one of them would let the smear through
exactly where it hurts most. Both are searched, and only within the one service:
numeric ids repeat across catalogues, and treating Deezer 12345 as Discogs 12345
would reject good matches for a collision that is not one.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from core.library2.provider_ids import (
    external_id_sql, normalize_provider_name, parse_external_ids,
)
from core.worker_utils import (
    ARTIST_NAME_MATCH_THRESHOLD, artist_name_matches, normalize_artist_name,
)
from utils.logging_config import get_logger

logger = get_logger("library2.worker_support")

_TABLES = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}
_NORMALIZE = {
    "artist": "artist", "artists": "artist",
    "album": "album", "albums": "album",
    "track": "track", "tracks": "track",
}
# Services lib2 promotes to a real column as well as external_ids.
_PROMOTED = {"spotify": "spotify_id", "musicbrainz": "musicbrainz_id"}


def _entity(entity_type: Any) -> Optional[str]:
    return _NORMALIZE.get(str(entity_type or "").strip().lower())


def _ids(row: Any, service: str) -> Optional[str]:
    """The id this row carries for one service, column or JSON."""
    keys = row.keys()
    column = _PROMOTED.get(service)
    if column and column in keys and row[column]:
        return str(row[column]).strip()
    if "external_ids" in keys:
        value = parse_external_ids(row["external_ids"]).get(service)
        if value:
            return str(value).strip()
    return None


def provider_id_conflict(conn, service: str, provider_id: Any, artist_id: Any,
                         artist_name: str) -> Optional[str]:
    """Name of a differently-named lib2 artist already holding ``provider_id``.

    A same-named holder is not a conflict: the same artist reached through two
    provider identities legitimately shares an id, and rejecting that would block
    ordinary re-matching. Only a different artist holding it is the corruption
    this guards against.

    Narrowed in SQL, and indexed. Twelve workers call this on every artist
    match; reading the whole table to filter in Python made each match cost a
    scan plus a materialized ``external_ids`` per artist. The predicate is a
    superset of what ``_ids`` accepts — a promoted column and the JSON both
    count as a hit — so ``_ids`` still decides, over the handful of rows the
    two indexes return.
    """
    service = normalize_provider_name(service)
    if not service:
        return None
    wanted = str(provider_id or "").strip()
    if not wanted:
        return None
    try:
        json_id = external_id_sql("external_ids", service)
    except ValueError:
        return None
    column = _PROMOTED.get(service)
    holds = f"{json_id} = ?"
    params: List[Any] = [artist_id, wanted]
    if column:
        # Both halves are indexed (`idx_lib2_artists_<service>` and the matching
        # `idx_lib2_artists_ext_<service>`), so SQLite resolves this as a
        # MULTI-INDEX OR rather than scanning.
        holds = f"{column} = ? OR {holds}"
        params.insert(1, wanted)
    try:
        rows = conn.execute(
            "SELECT id, name, spotify_id, musicbrainz_id, external_ids "
            f"FROM lib2_artists WHERE id <> ? AND ({holds})", params).fetchall()
    except Exception as exc:
        logger.debug("provider_id_conflict(%s=%s) failed: %s", service, wanted, exc)
        return None
    for row in rows:
        if _ids(row, service) != wanted:
            continue
        other = row["name"]
        if normalize_artist_name(artist_name) != normalize_artist_name(other):
            return other
    return None


def accept_artist_match(conn, service: str, provider_id: Any, artist_id: Any,
                        query_name: str, result_name: str,
                        threshold: float = ARTIST_NAME_MATCH_THRESHOLD) -> tuple:
    """Whether to store ``provider_id`` on this artist — ``(ok, reason)``.

    The single gate every worker's artist match passes through. Accepts only when
    the provider's name matches the library artist at or above ``threshold`` and
    the id is not already claimed by a differently-named artist. ``reason``
    explains a rejection, for the worker's debug log.
    """
    if not artist_name_matches(query_name, result_name, threshold):
        return False, (
            f"name mismatch '{query_name}' vs '{result_name}' (< {threshold})")
    conflict = provider_id_conflict(conn, service, provider_id, artist_id, query_name)
    if conflict:
        return False, (
            f"{service} id {provider_id} already claimed by '{conflict}' — "
            f"skipping to avoid a shared/duplicate id")
    return True, ""


def owned_album_titles(conn, artist_id: Any) -> List[str]:
    """Album titles the library actually has for this artist.

    The ground truth that separates two artists sharing a name. Two differences
    from the legacy read it replaces, both forced by lib2 holding more than legacy
    did: provider-only discography rows are excluded, because an album the user has
    no files for is not evidence of anything; and featured credits count, because
    lib2 records a compilation appearance through ``lib2_album_artists`` rather
    than ``primary_artist_id``.
    """
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT al.title
              FROM lib2_albums al
              LEFT JOIN lib2_album_artists aa ON aa.album_id = al.id
             WHERE (al.primary_artist_id = ? OR aa.artist_id = ?)
               AND EXISTS (SELECT 1 FROM lib2_tracks t
                            JOIN lib2_track_files f ON f.track_id=t.id
                           WHERE t.album_id=al.id AND f.path IS NOT NULL
                             AND TRIM(f.path)<>''
                             AND COALESCE(f.file_state,'active')='active')
            """,
            (artist_id, artist_id),
        ).fetchall()
    except Exception as exc:
        logger.debug("owned_album_titles(%s) failed: %s", artist_id, exc)
        return []
    return [row[0] for row in rows if row and row[0]]


def set_expected_track_count(conn, album_id: Any, count: Any) -> None:
    """Cache a metadata source's total for an album.

    lib2's ``expected_track_count`` is what legacy called ``api_track_count``: the
    total a provider reports, as opposed to the count already indexed. The Album
    Completeness repair job reads it, so populating it during enrichment saves a
    second round of API calls during the scan.

    Non-positive and non-numeric counts are skipped rather than written, so a
    source carrying no track info cannot blank a good value another source gave.
    Last write wins otherwise, as on legacy: any provider count beats the observed
    fallback, and keeping the maximum would leave a deluxe edition's total making a
    standard album look permanently incomplete.

    The caller owns the transaction; this does not commit.
    """
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        return
    if count <= 0:
        return
    try:
        conn.execute(
            "UPDATE lib2_albums SET expected_track_count=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?", (count, album_id))
    except Exception as exc:
        # A worker batches several album writes into one transaction; a failure
        # here must not roll back the provider data it just fetched.
        logger.warning("Failed to cache expected_track_count for album %s: %s",
                       album_id, exc)


def stored_provider_id(conn, entity_type: str, entity_id: Any,
                       service: str) -> Optional[str]:
    """The id already stored for this entity and service, or None."""
    entity = _entity(entity_type)
    key = normalize_provider_name(service)
    if not entity or not key:
        return None
    try:
        row = conn.execute(
            f"SELECT * FROM {_TABLES[entity]} WHERE id=?", (entity_id,)).fetchone()
    except Exception as exc:
        logger.debug("stored_provider_id(%s #%s) failed: %s", entity, entity_id, exc)
        return None
    return _ids(row, key) if row is not None else None


MATCHED = "matched"
UNAVAILABLE = "error"
NO_STORED_ID = ""


def _record_unavailable(db, entity_type: str, entity_id: Any, service: str,
                        detail: str) -> None:
    """Persist a failed stored-id refresh in the attempt ledger.

    Without it the failure leaves no trace at all, and ``next_pending()`` hands
    the same entity straight back on the following tick — a tight provider loop
    with no backoff. ``record_attempt`` increments the consecutive-failure count
    for a non-settled status, which is exactly the backoff this needs.
    """
    try:
        from core.library2 import provider_attempts

        conn = db._get_connection()
        try:
            if not provider_attempts._table_exists(conn, "lib2_provider_attempts"):
                return
            provider_attempts.record_attempt(
                conn, entity_type=entity_type, entity_id=entity_id,
                service=service, status="error", detail=detail[:500])
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not break the run
        logger.debug("could not record failed stored-id refresh for %s #%s: %s",
                     entity_type, entity_id, exc)


def honor_stored_match(db, *, entity_type: str, entity_id: Any, service: str,
                       fetch: Callable[[str], Any],
                       on_match: Callable[[Any, str, Any], None],
                       log_prefix: str = "") -> str:
    """Refresh from an id already stored, instead of searching by name.

    Issue #501: without this, every enrichment pass ran a fuzzy name search and
    overwrote the stored id with whatever came back — so a manual match got
    replaced by a wrong result, or reverted to ``not_found``.

    Takes the database rather than a connection, unlike its neighbours here, and
    that is deliberate: the id read is closed before ``fetch`` runs. Holding a
    connection open across a provider call is how this project's worst production
    bug worked, and ``on_match`` opens its own connection to write.

    Returns one of three states (L2-005):

    ``MATCHED``     — an id was stored, the fetch returned data and ``on_match``
        ran. The caller skips its search and counts a match.
    ``UNAVAILABLE`` — an id IS stored but the provider could not confirm it. The
        caller must NOT search by name: a transient provider failure is not
        evidence that the id is wrong, and searching would overwrite a
        deliberately chosen id with whatever a fuzzy name match returns. The
        failure is written to the attempt ledger so the retry gets a backoff
        instead of coming straight back round. An id is released only by an
        explicit re-match, never by a timeout.
    ``NO_STORED_ID`` (falsy) — nothing stored; the caller searches as before.

    A fetch error is caught (transient rate limits are normal); an ``on_match``
    error is not — a failed write is something the worker has to hear about
    rather than report as a match that never landed.
    """
    conn = db._get_connection()
    try:
        stored = stored_provider_id(conn, entity_type, entity_id, service)
    finally:
        conn.close()
    if not stored:
        return NO_STORED_ID

    entity = _entity(entity_type) or str(entity_type)

    def _unavailable(reason: str) -> str:
        _record_unavailable(db, entity_type, entity_id, service, reason)
        logger.warning(
            "[%s] Stored id %s for %s #%s could not be confirmed (%s) — keeping "
            "it rather than searching by name",
            log_prefix or service, stored, entity, entity_id, reason)
        return UNAVAILABLE

    try:
        data = fetch(stored)
    except Exception as exc:
        return _unavailable(str(exc))

    if not data:
        return _unavailable("the provider returned nothing")

    on_match(entity_id, stored, data)
    logger.info("[%s] Honored stored match: %s #%s → %s=%s",
                log_prefix or service, entity, entity_id, service, stored)
    return MATCHED


__all__ = [
    "MATCHED",
    "NO_STORED_ID",
    "UNAVAILABLE",
    "accept_artist_match",
    "honor_stored_match",
    "owned_album_titles",
    "provider_id_conflict",
    "set_expected_track_count",
    "stored_provider_id",
]
