"""Match a metadata-source track against the library by stable external IDs.

Discord-reported (CAL): the watchlist scanner re-downloaded a track that
already existed on disk because the library DB had stale album metadata
(track tagged on album "Left Alone" while Spotify reported it as on the
"NPC" single). The matching logic relied on title + artist + album fuzzy
comparison; the album fuzzy correctly said the names didn't match, the
scanner declared the track missing, and the wishlist re-added + re-
downloaded it on every scan.

The track has a stable external identity though — every download embeds
Spotify / iTunes / Deezer / Tidal / Qobuz / MusicBrainz / AudioDB /
Hydrabase / ISRC IDs as both file tags AND DB columns. This module pulls
those IDs off either side and asks: do we already have a row in the
``tracks`` table whose external-ID column matches one of the source
track's IDs? If yes, the track is NOT missing, regardless of how the
album metadata drifted between sources.

Provider-neutral by design — no spotify-only paths.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library.track_identity")


# Maps the conceptual ID name (used in the source-track dict we extract
# below) to the column name on the library ``tracks`` table where that
# ID is persisted. Keep the column names in sync with the schema in
# ``database/music_database.py``.
EXTERNAL_ID_COLUMNS: Dict[str, str] = {
    'spotify_id': 'spotify_track_id',
    'itunes_id': 'itunes_track_id',
    'deezer_id': 'deezer_id',
    'tidal_id': 'tidal_id',
    'qobuz_id': 'qobuz_id',
    'mbid': 'musicbrainz_recording_id',
    'audiodb_id': 'audiodb_id',
    'soul_id': 'soul_id',
    'isrc': 'isrc',
}


def _coerce(value: Any) -> Optional[str]:
    """Return value as a non-empty string, or None for empty / missing."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _get(track: Any, *names: str) -> Optional[str]:
    """Read the first non-empty attribute / dict key from ``names`` off
    ``track``. Accepts both dict-style and dataclass / object tracks."""
    for name in names:
        try:
            value = track[name] if isinstance(track, dict) else getattr(track, name, None)
        except (TypeError, KeyError):
            value = None
        coerced = _coerce(value)
        if coerced is not None:
            return coerced
    return None


def extract_external_ids(track: Any, source_hint: Optional[str] = None) -> Dict[str, str]:
    """Pull every recognized external ID off a metadata-source track.

    Handles the source-source naming drift: Spotify tracks expose ``id``
    as the Spotify track ID; Deezer tracks expose ``id`` as the Deezer
    track ID; iTunes tracks may use ``trackId`` or ``id``. The disamb-
    iguating field is ``provider`` / ``source`` / ``_source``. Tracks
    coming from a SoulSync internal pipeline often carry every known ID
    set to its source-specific value — we just collect whatever's there.

    ``source_hint`` is the caller's known answer to "where did this
    track dict come from?" — used as a fallback when the track itself
    doesn't carry a provider / source / _source field. Spotify and
    iTunes return raw API responses without provider tags, so the
    watchlist scanner passes ``get_primary_source()`` here to make sure
    a Spotify-primary scan isn't silently no-opping just because the
    raw API track has no provider key.

    Returns a dict mapping conceptual ID name → ID value. Keys present
    in ``EXTERNAL_ID_COLUMNS``. Empty dict when no IDs are available.
    """
    if track is None:
        return {}

    ids: Dict[str, str] = {}

    # Provider-neutral fields that carry their own name regardless of
    # source. Most internal SoulSync tracks have these set; external
    # source responses usually only have one of them populated.
    direct_id_fields = {
        'spotify_id': ('spotify_id', 'spotify_track_id', 'SPOTIFY_TRACK_ID'),
        'itunes_id': ('itunes_id', 'itunes_track_id', 'trackId', 'ITUNES_TRACK_ID'),
        'deezer_id': ('deezer_id', 'deezer_track_id', 'DEEZER_TRACK_ID'),
        'tidal_id': ('tidal_id', 'tidal_track_id', 'TIDAL_TRACK_ID'),
        'qobuz_id': ('qobuz_id', 'qobuz_track_id', 'QOBUZ_TRACK_ID'),
        'mbid': ('musicbrainz_recording_id', 'mbid', 'MUSICBRAINZ_RECORDING_ID'),
        'audiodb_id': ('audiodb_id', 'idTrack', 'AUDIODB_TRACK_ID'),
        'soul_id': ('soul_id', 'SOUL_ID'),
        'isrc': ('isrc', 'ISRC'),
    }
    for name, candidates in direct_id_fields.items():
        value = _get(track, *candidates)
        if value:
            ids[name] = value

    # Provider field tells us which native ``id`` belongs to. Without
    # this, a Deezer track's ``id`` field would be silently ignored
    # (we wouldn't know to map it to deezer_id). Convention varies by
    # client: Spotify-shaped tracks usually have no provider field,
    # Deezer / Discogs / Hydrabase clients tag tracks with ``_source``,
    # internal pipeline normalization may use ``source`` or ``provider``.
    # Fall back to the caller's source_hint when the track has no
    # provider field of its own (Spotify / iTunes raw API responses).
    provider = (_get(track, 'provider', 'source', '_source') or source_hint or '').lower()
    native_id = _get(track, 'id')
    if native_id and provider:
        provider_to_key = {
            'spotify': 'spotify_id',
            'itunes': 'itunes_id',
            'deezer': 'deezer_id',
            'tidal': 'tidal_id',
            'qobuz': 'qobuz_id',
            'musicbrainz': 'mbid',
            'audiodb': 'audiodb_id',
            'hydrabase': 'soul_id',
        }
        key = provider_to_key.get(provider)
        if key and key not in ids:
            ids[key] = native_id

    return ids


def find_library_track_by_external_id(
    db: Any,
    *,
    external_ids: Dict[str, str],
    server_source: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a row from the ``tracks`` table whose any external ID
    column matches one of the provided IDs, or None if no match.

    Returns a sqlite3.Row-like dict so callers can read whatever fields
    they want (id, title, file_path, etc.). When ``server_source`` is
    set, restrict matches to tracks scanned from that media server —
    avoids false positives when a user binds the same DB into multiple
    profiles/servers.

    Performance: every external_id column is indexed in the schema, so
    each OR clause hits an index. Limit 1 because we only need to know
    whether a match exists.
    """
    if not external_ids:
        return None

    clauses: List[str] = []
    params: List[Any] = []
    for id_name, id_value in external_ids.items():
        column = EXTERNAL_ID_COLUMNS.get(id_name)
        if not column or not id_value:
            continue
        clauses.append(f"({column} = ? AND {column} IS NOT NULL AND {column} != '')")
        params.append(id_value)

    if not clauses:
        return None

    where_external = " OR ".join(clauses)

    # Optional server_source filter
    if server_source:
        sql = (
            f"SELECT * FROM tracks WHERE ({where_external}) "
            f"AND (server_source = ? OR server_source IS NULL) LIMIT 1"
        )
        params.append(server_source)
    else:
        sql = f"SELECT * FROM tracks WHERE ({where_external}) LIMIT 1"

    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        # sqlite3.Row supports keys() — return as dict for caller stability.
        try:
            return dict(row)
        except (TypeError, ValueError):
            # Fallback for cursors that don't return Row objects.
            cols = [c[0] for c in cursor.description]
            return dict(zip(cols, row, strict=False))
    except Exception as exc:
        logger.debug(f"find_library_track_by_external_id query failed: {exc}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# Maps the conceptual ID name to the column on the ``track_downloads``
# (provenance) table where SoulSync persists the IDs at download time.
# Naming convention differs from ``tracks``: provenance uses the
# explicit ``_track_id`` suffix to match the existing column shape.
PROVENANCE_ID_COLUMNS: Dict[str, str] = {
    'spotify_id': 'spotify_track_id',
    'itunes_id': 'itunes_track_id',
    'deezer_id': 'deezer_track_id',
    'tidal_id': 'tidal_track_id',
    'qobuz_id': 'qobuz_track_id',
    'mbid': 'musicbrainz_recording_id',
    'audiodb_id': 'audiodb_id',
    'soul_id': 'soul_id',
    'isrc': 'isrc',
}


def find_provenance_by_external_id(
    db: Any,
    *,
    external_ids: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Return a row from the ``track_downloads`` table whose any external
    ID column matches one of the provided IDs, or None.

    Used as a second-tier fallback by the watchlist scanner: when the
    primary library tracks-table lookup misses (e.g. the row exists but
    the enrichment worker hasn't backfilled its IDs yet, or the row
    doesn't exist yet because the media-server scan hasn't run since the
    download), this checks whether SoulSync downloaded the file recently
    enough that the IDs are sitting in the provenance table.

    Caller should typically also confirm the ``file_path`` on the
    returned row still exists on disk before treating the track as
    "already in library" — otherwise a deleted file would prevent
    re-download.
    """
    if not external_ids:
        return None

    clauses: List[str] = []
    params: List[Any] = []
    for id_name, id_value in external_ids.items():
        column = PROVENANCE_ID_COLUMNS.get(id_name)
        if not column or not id_value:
            continue
        clauses.append(f"({column} = ? AND {column} IS NOT NULL AND {column} != '')")
        params.append(id_value)

    if not clauses:
        return None

    where_external = " OR ".join(clauses)
    sql = f"SELECT * FROM track_downloads WHERE ({where_external}) ORDER BY id DESC LIMIT 1"

    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        try:
            return dict(row)
        except (TypeError, ValueError):
            cols = [c[0] for c in cursor.description]
            return dict(zip(cols, row, strict=False))
    except Exception as exc:
        logger.debug(f"find_provenance_by_external_id query failed: {exc}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


__all__ = [
    'EXTERNAL_ID_COLUMNS',
    'PROVENANCE_ID_COLUMNS',
    'extract_external_ids',
    'find_library_track_by_external_id',
    'find_provenance_by_external_id',
]
