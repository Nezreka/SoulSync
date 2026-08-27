"""Normalize and deduplicate playback-queue acquisition requests.

The playback queue accepts rows from several metadata providers, while the
download worker expects the Spotify-like ``name``/``artists``/``album`` shape.
Keep that translation small and deterministic so the queue can reuse the
existing verified download/import pipeline without creating a second matcher.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable


MAX_PREFETCH_TRACKS = 250
PLAYBACK_PREFETCH_MAX_CONCURRENT = 1


def _text(value: Any) -> str:
    return str(value or "").strip()


def _artist_names(track: dict[str, Any]) -> list[str]:
    raw = track.get("artists")
    values = raw if isinstance(raw, list) else []
    names: list[str] = []
    for value in values:
        if isinstance(value, dict):
            name = _text(value.get("name"))
        else:
            name = _text(value)
        if name:
            names.append(name)
    fallback = _text(track.get("artist") or track.get("artist_name"))
    if not names and fallback:
        names.append(fallback)
    return names


def _album_name(track: dict[str, Any]) -> str:
    raw = track.get("album")
    if isinstance(raw, dict):
        return _text(raw.get("name") or raw.get("title"))
    return _text(raw or track.get("album_title"))


def track_identity(track: dict[str, Any]) -> str:
    """Return a stable, non-secret key for one musical recording request."""
    source = _text(track.get("source") or track.get("metadata_source")).casefold()
    source_id = _text(
        track.get("source_track_id")
        or track.get("spotify_track_id")
        or track.get("tidal_track_id")
        or track.get("deezer_id")
        or track.get("itunes_track_id")
        or track.get("musicbrainz_recording_id")
        or track.get("track_id")
    )
    if source and source_id:
        raw = f"source:{source}:{source_id}"
    else:
        title = _text(track.get("title") or track.get("name")).casefold()
        artists = "|".join(name.casefold() for name in _artist_names(track))
        album = _album_name(track).casefold()
        raw = f"metadata:{title}|{artists}|{album}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_prefetch_track(track: dict[str, Any]) -> dict[str, Any]:
    """Translate a queue row into the shape consumed by the download worker."""
    if not isinstance(track, dict):
        raise ValueError("Each playback queue track must be an object")
    title = _text(track.get("title") or track.get("name"))
    artists = _artist_names(track)
    if not title or not artists:
        raise ValueError("Each missing queue track needs a title and artist")

    raw_duration_ms = track.get("duration_ms")
    if raw_duration_ms in (None, ""):
        raw_duration = track.get("duration") or 0
        try:
            duration_number = float(raw_duration)
        except (TypeError, ValueError):
            duration_number = 0
        # Metadata APIs use milliseconds; library/player rows use seconds.
        raw_duration_ms = duration_number if duration_number > 10_000 else duration_number * 1000
    try:
        duration_ms = max(0, int(float(raw_duration_ms or 0)))
    except (TypeError, ValueError):
        duration_ms = 0

    identity = track_identity(track)
    source_id = _text(
        track.get("source_track_id")
        or track.get("spotify_track_id")
        or track.get("tidal_track_id")
        or track.get("deezer_id")
        or track.get("itunes_track_id")
        or track.get("musicbrainz_recording_id")
        or track.get("track_id")
        or track.get("id")
    )
    normalized = dict(track)
    normalized.update(
        {
            "id": source_id or identity,
            "name": title,
            "title": title,
            "artists": [{"name": name} for name in artists],
            "artist": artists[0],
            "album": _album_name(track),
            "duration_ms": duration_ms,
            "_playback_queue_key": identity,
            "_queue_request_id": _text(track.get("_queue_request_id")) or identity,
        }
    )
    return normalized


def deduplicate_prefetch_tracks(
    tracks: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Normalize requests and collapse duplicate recordings in queue order.

    Returns the unique worker payloads plus every client request id associated
    with each identity so repeated playlist entries can share one download.
    """
    unique: list[dict[str, Any]] = []
    request_ids: dict[str, list[str]] = {}
    seen: set[str] = set()
    for raw in list(tracks or [])[:MAX_PREFETCH_TRACKS]:
        normalized = normalize_prefetch_track(raw)
        identity = normalized["_playback_queue_key"]
        request_id = normalized["_queue_request_id"]
        if request_id not in request_ids.setdefault(identity, []):
            request_ids[identity].append(request_id)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(normalized)
    return unique, request_ids


__all__ = [
    "MAX_PREFETCH_TRACKS",
    "PLAYBACK_PREFETCH_MAX_CONCURRENT",
    "deduplicate_prefetch_tracks",
    "normalize_prefetch_track",
    "track_identity",
]
