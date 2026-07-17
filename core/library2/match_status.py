"""Per-provider metadata match-status for Library v2 entities.

The legacy Enhanced View shows a row of colored provider chips per
artist/album/track (Spotify/MusicBrainz/Deezer/…), each reflecting whether that
provider matched the entity and clickable to manually (re-)match. lib2 rows keep
a back-reference to the legacy source row (``legacy_artist_id`` /
``legacy_album_id`` / ``legacy_track_id``); the legacy ``artists``/``albums``/
``tracks`` tables carry the ``{service}_id`` and (optionally) ``{service}_match_status``
/ ``{service}_last_attempted`` columns. So the exact legacy match data is read
straight through the back-reference — no migration, no re-import.

The returned ``legacy_entity_id`` lets the UI reuse the app-wide
``PUT /api/library/manual-match`` endpoint (which keys on the legacy id) for the
manual re-match action.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.enrichment.match_provenance import load_match_provenance
from utils.logging_config import get_logger

logger = get_logger("library2.match_status")

# Service → per-entity legacy id column. Mirrors web_server._SERVICE_ID_COLUMNS
# (config data, intentionally duplicated to keep core free of the Flask module).
SERVICES: List[tuple] = [
    ("spotify", "Spotify",
     {"artist": "spotify_artist_id", "album": "spotify_album_id", "track": "spotify_track_id"}),
    ("musicbrainz", "MusicBrainz",
     {"artist": "musicbrainz_id", "album": "musicbrainz_release_id", "track": "musicbrainz_recording_id"}),
    ("deezer", "Deezer",
     {"artist": "deezer_id", "album": "deezer_id", "track": "deezer_id"}),
    ("itunes", "iTunes",
     {"artist": "itunes_artist_id", "album": "itunes_album_id", "track": "itunes_track_id"}),
    ("audiodb", "AudioDB",
     {"artist": "audiodb_id", "album": "audiodb_id", "track": "audiodb_id"}),
    ("discogs", "Discogs",
     {"artist": "discogs_id", "album": "discogs_id"}),
    ("lastfm", "Last.fm",
     {"artist": "lastfm_url", "album": "lastfm_url", "track": "lastfm_url"}),
    ("genius", "Genius",
     {"artist": "genius_id", "track": "genius_id"}),
    ("tidal", "Tidal",
     {"artist": "tidal_id", "album": "tidal_id", "track": "tidal_id"}),
    ("qobuz", "Qobuz",
     {"artist": "qobuz_id", "album": "qobuz_id", "track": "qobuz_id"}),
    ("amazon", "Amazon",
     {"artist": "amazon_id", "album": "amazon_id", "track": "amazon_id"}),
    ("jiosaavn", "JioSaavn",
     {"artist": "jiosaavn_id", "album": "jiosaavn_id", "track": "jiosaavn_id"}),
    ("bandcamp", "Bandcamp",
     {"album": "bandcamp_url", "track": "bandcamp_url"}),
]

# lib2 entity_type → (lib2 table, legacy back-ref column, legacy table).
_LIB2 = {
    "artist": ("lib2_artists", "legacy_artist_id", "artists"),
    "album": ("lib2_albums", "legacy_album_id", "albums"),
    "track": ("lib2_tracks", "legacy_track_id", "tracks"),
}

_NORMALIZE = {
    "artist": "artist", "artists": "artist",
    "album": "album", "albums": "album",
    "track": "track", "tracks": "track",
}


def _table_columns(conn, table: str) -> set:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:  # noqa: BLE001
        return set()


def _is_available(service: str, available_services: Optional[set]) -> bool:
    """True unless the caller passed an explicit configured-services set that
    excludes this one (A8: hide chips for providers nobody configured —
    default behavior when the caller omits the set is 'assume available' so
    existing callers/tests are unaffected)."""
    return available_services is None or service in available_services


def _chips_for_row(
    canonical: str,
    legacy_row,
    columns: set,
    legacy_id: int,
    available_services: Optional[set] = None,
    provenance: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build the per-service chip list from one already-fetched legacy row."""
    row_keys = set(legacy_row.keys())
    out: List[Dict[str, Any]] = []
    for service, label, id_cols in SERVICES:
        id_col = id_cols.get(canonical)
        if not id_col:
            continue  # provider has no id column for this entity type
        external_id: Optional[str] = legacy_row[id_col] if id_col in row_keys else None
        status_col = f"{service}_match_status"
        attempted_col = f"{service}_last_attempted"
        if status_col in columns and legacy_row[status_col]:
            status = legacy_row[status_col]
        elif external_id:
            status = "matched"
        else:
            status = "pending"
        provenance_row = (provenance or {}).get(service)
        provenance_matches = bool(
            status == "matched"
            and provenance_row
            and str(provenance_row.get("external_id") or "") == str(external_id or "")
        )
        out.append({
            "service": service,
            "label": label,
            "status": status,
            "external_id": external_id,
            "last_attempted": legacy_row[attempted_col] if attempted_col in columns else None,
            "legacy_entity_id": legacy_id,
            "available": _is_available(service, available_services),
            "match_origin": provenance_row.get("origin") if provenance_matches else None,
            "matched_at": provenance_row.get("matched_at") if provenance_matches else None,
        })
    return out


def entity_match_status(conn, entity_type: str, entity_id: int,
                        *, available_services: Optional[set] = None) -> List[Dict[str, Any]]:
    """Per-provider match status for one lib2 entity, read from its legacy row.

    If the entity has no legacy source row (e.g. a discography-only release,
    or a new direct-import), synthesizes chips from its own columns.
    Each entry is ``{service, label, status, external_id, last_attempted,
    legacy_entity_id, available}`` where ``status`` is ``matched`` /
    ``not_found`` / ``pending``. ``available_services``, when given, is the
    set of service ids actually configured on this instance (A8) — chips for
    everything else still come back (so a manual re-match action stays
    reachable) but flagged ``available: False`` for the UI to grey out/hide.
    """
    canonical = _NORMALIZE.get(str(entity_type))
    if canonical is None:
        raise ValueError(f"Unknown entity type: {entity_type}")
    lib2_table, legacy_col, legacy_table = _LIB2[canonical]

    row = conn.execute(
        f"SELECT * FROM {lib2_table} WHERE id=?", (entity_id,)
    ).fetchone()
    if row is None:
        return []

    legacy_id = row[legacy_col]
    columns = _table_columns(conn, legacy_table)

    if legacy_id is not None and columns:
        legacy_row = conn.execute(
            f"SELECT * FROM {legacy_table} WHERE id=?", (legacy_id,)
        ).fetchone()
        if legacy_row is not None:
            origins = load_match_provenance(conn, canonical, [int(legacy_id)])
            return _chips_for_row(
                canonical,
                legacy_row,
                columns,
                legacy_id,
                available_services,
                origins.get(int(legacy_id), {}),
            )

    # Fallback: synthesize chips from lib2 row columns
    import json
    row_keys = set(row.keys())
    out: List[Dict[str, Any]] = []

    ext_ids = {}
    if "external_ids" in row_keys and row["external_ids"]:
        try:
            ext_ids = json.loads(row["external_ids"])
        except (json.JSONDecodeError, TypeError):
            ext_ids = {}

    for service, label, id_cols in SERVICES:
        id_col = id_cols.get(canonical)
        if not id_col:
            continue

        external_id = None
        if service == "spotify":
            external_id = row["spotify_id"] if "spotify_id" in row_keys else None
        elif service == "musicbrainz":
            external_id = row["musicbrainz_id"] if "musicbrainz_id" in row_keys else None
        else:
            external_id = ext_ids.get(service)

        status = "matched" if external_id else "pending"
        out.append({
            "service": service,
            "label": label,
            "status": status,
            "external_id": external_id,
            "last_attempted": None,
            "legacy_entity_id": None,
            "available": _is_available(service, available_services),
            "match_origin": None,
            "matched_at": None,
        })
    return out


def album_match_bundle(conn, album_id: int,
                       *, available_services: Optional[set] = None) -> Dict[str, Any]:
    """Album-level chips plus a per-track chip map, in one batched pass.

    Returns ``{"album": [...chips], "tracks": {lib2_track_id: [...chips]}}``.
    Computes the legacy column sets once for the whole album (cheap for a
    detail view). Tracks/albums without a legacy back-reference synthesize
    chips based on their own columns. ``available_services`` — see
    ``entity_match_status``.
    """
    result: Dict[str, Any] = {
        "album": entity_match_status(conn, "album", album_id, available_services=available_services),
        "tracks": {},
    }
    track_columns = _table_columns(conn, "tracks")

    rows = conn.execute(
        "SELECT id, legacy_track_id, spotify_id, musicbrainz_id FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchall()

    legacy_ids = {int(r["legacy_track_id"]) for r in rows if r["legacy_track_id"] is not None}
    legacy_rows = {}
    provenance = load_match_provenance(conn, "track", legacy_ids)
    if legacy_ids and track_columns:
        marks = ",".join("?" for _ in legacy_ids)
        legacy_rows = {
            int(r["id"]): r
            for r in conn.execute(
                f"SELECT * FROM tracks WHERE id IN ({marks})", tuple(legacy_ids)
            )
        }

    for row in rows:
        lid = row["legacy_track_id"]
        legacy_row = legacy_rows.get(int(lid)) if lid is not None else None
        if legacy_row is not None:
            result["tracks"][int(row["id"])] = _chips_for_row(
                "track",
                legacy_row,
                track_columns,
                int(lid),
                available_services,
                provenance.get(int(lid), {}),
            )
        else:
            # Synthetic chips for track without legacy row
            chips = []
            for service, label, id_cols in SERVICES:
                id_col = id_cols.get("track")
                if not id_col:
                    continue
                external_id = None
                if service == "spotify":
                    external_id = row["spotify_id"]
                elif service == "musicbrainz":
                    external_id = row["musicbrainz_id"]

                status = "matched" if external_id else "pending"
                chips.append({
                    "service": service,
                    "label": label,
                    "status": status,
                    "external_id": external_id,
                    "last_attempted": None,
                    "legacy_entity_id": None,
                    "available": _is_available(service, available_services),
                    "match_origin": None,
                    "matched_at": None,
                })
            result["tracks"][int(row["id"])] = chips
    return result


__all__ = ["entity_match_status", "album_match_bundle", "SERVICES"]
