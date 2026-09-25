"""Library-v2 materialization at confirmed Search/Download boundaries.

The metadata search UI may inspect releases and source candidates without
creating catalog rows. Once the user presses ``Begin Analysis``, the selected
tracks are no longer speculative: Artist, Release and Track must exist before
search, quality checks or quarantine can fail. This adapter keeps that boundary
small and reuses :func:`core.library2.materialize.materialize_track_intent`.

Provider IDs are persisted under the payload's explicit provider namespace for
artist, release and track. Only Spotify IDs enter the dedicated scalar columns.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .materialize import materialize_track_intent
from .monitor_rules import PROVENANCE_USER


SEARCH_INTENT_PREFIXES = ("enhanced_search_", "gsearch_")


def is_confirmed_search_process(playlist_id: Any) -> bool:
    value = str(playlist_id or "")
    return value.startswith(SEARCH_INTENT_PREFIXES)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _first_artist(track: Mapping[str, Any]) -> Mapping[str, Any]:
    artists = track.get("artists")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, Mapping):
            return first
        if _text(first):
            return {"name": _text(first)}
    artist = track.get("artist") or track.get("artist_name")
    if isinstance(artist, Mapping):
        return artist
    return {"name": _text(artist)} if _text(artist) else {}


def _source_info(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _intent_fields(
    track: Mapping[str, Any],
    *,
    album_context: Optional[Mapping[str, Any]],
    artist_context: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    album_context = _mapping(album_context)
    artist_context = _mapping(artist_context)
    artist = _first_artist(track)
    artist_name = _text(artist.get("name") or artist_context.get("name"))
    track_title = _text(track.get("name") or track.get("title"))

    raw_album = track.get("album")
    album = _mapping(raw_album)
    album_title = _text(
        album.get("name")
        or album.get("title")
        or (raw_album if isinstance(raw_album, str) else None)
        or album_context.get("name")
        or album_context.get("title")
    )
    if not artist_name or not track_title or not album_title:
        return None

    source = _text(
        track.get("source")
        or track.get("provider")
        or album.get("source")
        or album_context.get("source")
        or artist_context.get("source")
    ).lower()
    total_tracks = album.get("total_tracks") or album_context.get("total_tracks")
    album_type = _text(
        album.get("album_type") or album_context.get("album_type")
    ).lower() or ("single" if total_tracks in (1, "1") else "album")

    return {
        "artist_name": artist_name,
        "track_title": track_title,
        "album_title": album_title,
        "album_type": album_type,
        "artist_provider_id": _text(
            artist.get("id") or artist_context.get("id")
        ) or None,
        "album_provider_id": _text(
            album.get("id") or album_context.get("id")
        ) or None,
        "track_provider_id": _text(track.get("id")) or None,
        "track_number": _int_or_none(track.get("track_number")),
        "disc_number": _int_or_none(track.get("disc_number")),
        "source": source or None,
    }


def materialize_confirmed_search_tracks(
    conn: Any,
    tracks: Iterable[Mapping[str, Any]],
    *,
    album_context: Optional[Mapping[str, Any]] = None,
    artist_context: Optional[Mapping[str, Any]] = None,
    explicit_profile_id: int,
    profile_id: int = 1,
    correlation_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], ...]:
    """Materialize every confirmed track and return correlation-enriched copies.

    The caller owns the transaction. Invalid/missing metadata raises before a
    batch is started so an enabled Library-v2 installation cannot create an
    uncorrelated search attempt. Repeating the same intent remains idempotent.
    """
    profile_id_value = _int_or_none(explicit_profile_id)
    if profile_id_value is None:
        raise ValueError("quality_profile_id must be a positive integer")
    exists = conn.execute(
        "SELECT 1 FROM quality_profiles WHERE id=?", (profile_id_value,)
    ).fetchone()
    if exists is None:
        raise ValueError("unknown quality_profile_id")

    output = []
    for index, raw_track in enumerate(tracks):
        if not isinstance(raw_track, Mapping):
            raise ValueError(f"track {index + 1} must be an object")
        fields = _intent_fields(
            raw_track,
            album_context=album_context,
            artist_context=artist_context,
        )
        if fields is None:
            raise ValueError(
                f"track {index + 1} requires artist, album and track metadata"
            )
        result = materialize_track_intent(
            conn,
            **fields,
            explicit_profile_id=profile_id_value,
            provenance=PROVENANCE_USER,
            profile_id=profile_id,
        )
        effective = result["quality_profile"]
        source_info = _source_info(raw_track.get("source_info"))
        source_info.update({
            "lib2_artist_id": result["artist_id"],
            "lib2_album_id": result["album_id"],
            "lib2_track_id": result["track_id"],
            "quality_profile_id": effective["id"],
            "quality_profile_source": effective["source"],
            "quality_profile_source_id": effective["source_id"],
        })
        if correlation_id:
            source_info["intent_correlation_id"] = str(correlation_id)
        enriched = dict(raw_track)
        enriched.update({
            "lib2_artist_id": result["artist_id"],
            "lib2_album_id": result["album_id"],
            "lib2_track_id": result["track_id"],
            "quality_profile_id": effective["id"],
            "quality_profile_source": effective["source"],
            "source_info": source_info,
        })
        output.append(enriched)
    return tuple(output)


__all__ = [
    "SEARCH_INTENT_PREFIXES",
    "is_confirmed_search_process",
    "materialize_confirmed_search_tracks",
]
