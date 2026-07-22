"""Provider-qualified match state for native Library-v2 entities.

P3 makes the Library-v2 row the only catalogue authority.  Provider chips,
manual matches and clears therefore read/write dedicated Spotify/MusicBrainz
columns plus the provider-keyed ``external_ids`` mapping.  Legacy backrefs may
remain during the rollback window, but never participate in match decisions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.enrichment.match_provenance import load_match_provenance
from core.library2.provider_ids import parse_external_ids
from utils.logging_config import get_logger


logger = get_logger("library2.match_status")


SERVICES: List[tuple] = [
    ("spotify", "Spotify", {"artist": "spotify_artist_id", "album": "spotify_album_id", "track": "spotify_track_id"}),
    ("musicbrainz", "MusicBrainz", {"artist": "musicbrainz_id", "album": "musicbrainz_release_id", "track": "musicbrainz_recording_id"}),
    ("deezer", "Deezer", {"artist": "deezer_id", "album": "deezer_id", "track": "deezer_id"}),
    ("itunes", "iTunes", {"artist": "itunes_artist_id", "album": "itunes_album_id", "track": "itunes_track_id"}),
    ("audiodb", "AudioDB", {"artist": "audiodb_id", "album": "audiodb_id", "track": "audiodb_id"}),
    ("discogs", "Discogs", {"artist": "discogs_id", "album": "discogs_id"}),
    ("lastfm", "Last.fm", {"artist": "lastfm_url", "album": "lastfm_url", "track": "lastfm_url"}),
    ("genius", "Genius", {"artist": "genius_id", "track": "genius_id"}),
    ("tidal", "Tidal", {"artist": "tidal_id", "album": "tidal_id", "track": "tidal_id"}),
    ("qobuz", "Qobuz", {"artist": "qobuz_id", "album": "qobuz_id", "track": "qobuz_id"}),
    ("amazon", "Amazon", {"artist": "amazon_id", "album": "amazon_id", "track": "amazon_id"}),
    ("jiosaavn", "JioSaavn", {"artist": "jiosaavn_id", "album": "jiosaavn_id", "track": "jiosaavn_id"}),
    ("bandcamp", "Bandcamp", {"album": "bandcamp_url", "track": "bandcamp_url"}),
]

_TABLES = {
    "artist": "lib2_artists",
    "album": "lib2_albums",
    "track": "lib2_tracks",
}
_NORMALIZE = {
    "artist": "artist", "artists": "artist",
    "album": "album", "albums": "album",
    "track": "track", "tracks": "track",
}


def _canonical(entity_type: str) -> str:
    value = _NORMALIZE.get(str(entity_type))
    if value is None:
        raise ValueError(f"Unknown entity type: {entity_type}")
    return value


def _available(service: str, available_services: Optional[set]) -> bool:
    return available_services is None or service in available_services


def _source_ids(row: Any) -> Dict[str, str]:
    ids = parse_external_ids(row["external_ids"] if "external_ids" in row.keys() else None)
    if "spotify_id" in row.keys() and row["spotify_id"]:
        ids["spotify"] = str(row["spotify_id"])
    if "musicbrainz_id" in row.keys() and row["musicbrainz_id"]:
        ids["musicbrainz"] = str(row["musicbrainz_id"])
    return ids


def _native_chips(
    conn: Any,
    canonical: str,
    entity_id: int,
    row: Any,
    available_services: Optional[set],
) -> List[Dict[str, Any]]:
    ids = _source_ids(row)
    origins = load_match_provenance(
        conn, f"lib2_{canonical}", [int(entity_id)]
    ).get(str(entity_id), {})
    chips: List[Dict[str, Any]] = []
    for service, label, supported in SERVICES:
        if canonical not in supported:
            continue
        external_id = ids.get(service)
        provenance = origins.get(service) or {}
        provenance_matches = bool(
            external_id
            and str(provenance.get("external_id") or "") == str(external_id)
        )
        chips.append({
            "service": service,
            "label": label,
            "status": "matched" if external_id else "pending",
            "external_id": external_id,
            "last_attempted": provenance.get("matched_at") if provenance_matches else None,
            # Kept only as a response-shape compatibility field. P3 clients
            # always use library_v2_entity_id for mutation.
            "legacy_entity_id": None,
            "library_v2_entity_id": int(entity_id),
            "available": _available(service, available_services),
            "match_origin": provenance.get("origin") if provenance_matches else None,
            "matched_at": provenance.get("matched_at") if provenance_matches else None,
        })
    return chips


def entity_match_status(
    conn: Any,
    entity_type: str,
    entity_id: int,
    *,
    available_services: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Return provider chips from one authoritative native entity row."""

    canonical = _canonical(entity_type)
    row = conn.execute(
        f"SELECT id, spotify_id, musicbrainz_id, external_ids "
        f"FROM {_TABLES[canonical]} WHERE id=?",
        (int(entity_id),),
    ).fetchone()
    if row is None:
        return []
    return _native_chips(conn, canonical, int(entity_id), row, available_services)


def album_match_bundle(
    conn: Any,
    album_id: int,
    *,
    available_services: Optional[set] = None,
) -> Dict[str, Any]:
    """Album chips plus per-track native chips."""

    result: Dict[str, Any] = {
        "album": entity_match_status(
            conn, "album", album_id, available_services=available_services,
        ),
        "tracks": {},
    }
    rows = conn.execute(
        "SELECT id, spotify_id, musicbrainz_id, external_ids "
        "FROM lib2_tracks WHERE album_id=? ORDER BY id",
        (int(album_id),),
    ).fetchall()
    for row in rows:
        track_id = int(row["id"])
        result["tracks"][track_id] = _native_chips(
            conn, "track", track_id, row, available_services,
        )
    return result


def set_library_v2_match(
    conn: Any,
    entity_type: str,
    entity_id: int,
    service: str,
    external_id: Optional[str],
    *,
    actor: str = "admin",
) -> None:
    """Set or clear one explicitly qualified provider identity."""

    canonical = _canonical(entity_type)
    service = str(service or "").strip().lower()
    supported = {
        name for name, _label, entity_types in SERVICES
        if canonical in entity_types
    }
    if service not in supported:
        raise ValueError(f"Provider {service!r} does not support {canonical}")
    table = _TABLES[canonical]
    row = conn.execute(
        f"SELECT id, external_ids FROM {table} WHERE id=?", (int(entity_id),)
    ).fetchone()
    if row is None:
        raise LookupError(f"Library v2 {canonical} {entity_id} not found")

    ids = parse_external_ids(row["external_ids"])
    value = str(external_id).strip() if external_id not in (None, "") else None
    if value:
        ids[service] = value
    else:
        ids.pop(service, None)
    assignments = ["external_ids=?", "updated_at=CURRENT_TIMESTAMP"]
    params: List[Any] = [
        json.dumps(ids, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ]
    if service == "spotify":
        assignments.append("spotify_id=?")
        params.append(value)
    elif service == "musicbrainz":
        assignments.append("musicbrainz_id=?")
        params.append(value)
    params.append(int(entity_id))
    conn.execute(
        f"UPDATE {table} SET {', '.join(assignments)} WHERE id=?", params,
    )

    provenance_type = f"lib2_{canonical}"
    if value:
        try:
            from core.enrichment.match_provenance import record_manual_match
            record_manual_match(
                conn,
                entity_type=provenance_type,
                entity_id=entity_id,
                service=service,
                external_id=value,
                actor=actor,
            )
        except Exception as exc:  # provenance is supplemental to the native id
            logger.debug("could not record Library-v2 match provenance: %s", exc)
    else:
        try:
            conn.execute(
                "DELETE FROM metadata_match_provenance "
                "WHERE entity_type=? AND entity_id=? AND service=?",
                (provenance_type, str(entity_id), service),
            )
        except Exception as exc:  # older databases may not have provenance yet
            logger.debug("could not clear Library-v2 match provenance: %s", exc)


__all__ = [
    "SERVICES", "album_match_bundle", "entity_match_status",
    "set_library_v2_match",
]
