"""Pure helper for matching raw MusicBrainz-metadata tracks against
Spotify / iTunes.

Used by the PlaylistSource adapters whose ``get_playlist`` returns
tracks with ``needs_discovery=True`` (ListenBrainz, Last.fm radio).
Phase 1b ships Strategy 1 only (matching-engine queries → search →
score → pick best ≥0.9). The richer multi-strategy +
discovery-cache flow stays in
``core.discovery.listenbrainz.run_listenbrainz_discovery_worker``
for the Discover-page state-machine UI; this helper is the slimmer
version used by the auto-refresh pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MBMatchDeps:
    """Bundle of primitives the matcher needs.

    Wired up at bootstrap. Tests pass stub callables / clients."""

    matching_engine: Any
    score_candidates: Callable[..., Any]
    spotify_client_getter: Callable[[], Any]
    itunes_client_getter: Callable[[], Any]
    prefer_spotify_getter: Callable[[], bool]
    min_confidence: float = 0.9


def match_mb_track(
    track: Dict[str, Any], deps: MBMatchDeps
) -> Optional[Dict[str, Any]]:
    """Try to match a single MB-metadata track.

    Input shape:
        ``{'track_name', 'artist_name', 'album_name', 'duration_ms'}``

    Returns the matched_data dict (Spotify/iTunes track projection)
    or ``None`` when no candidate cleared the confidence threshold.
    """
    title = track.get("track_name") or ""
    artist = track.get("artist_name") or ""
    album = track.get("album_name") or ""
    duration_ms = int(track.get("duration_ms") or 0)
    if not title or not artist:
        return None

    spotify_client = deps.spotify_client_getter()
    itunes_client = deps.itunes_client_getter()
    use_spotify = bool(
        deps.prefer_spotify_getter()
        and spotify_client is not None
        and getattr(spotify_client, "is_spotify_authenticated", lambda: False)()
    )
    if not use_spotify and itunes_client is None:
        return None

    # Strategy 1 — matching-engine query generation.
    try:
        temp_track = type("_TempTrack", (), {
            "name": title,
            "artists": [artist],
            "album": album or None,
        })()
        queries = deps.matching_engine.generate_download_queries(temp_track)
    except Exception as exc:
        logger.debug(f"matching_engine query-gen failed: {exc}")
        queries = [f"{artist} {title}", title]

    best_match: Any = None
    best_confidence = 0.0
    for query in queries:
        try:
            if use_spotify:
                results = spotify_client.search_tracks(query, limit=10)
            else:
                results = itunes_client.search_tracks(query, limit=10)
        except Exception as exc:
            logger.debug(f"search failed for query={query!r}: {exc}")
            continue
        if not results:
            continue
        try:
            match, confidence, _ = deps.score_candidates(
                title, artist, duration_ms, results
            )
        except Exception as exc:
            logger.debug(f"score_candidates failed: {exc}")
            continue
        if match and confidence > best_confidence and confidence >= deps.min_confidence:
            best_match = match
            best_confidence = confidence
        if best_confidence >= deps.min_confidence:
            break

    if not best_match:
        return None

    provider = "spotify" if use_spotify else "itunes"
    image_url = getattr(best_match, "image_url", None) or ""
    album_data: Dict[str, Any] = {
        "name": getattr(best_match, "album", "") or "",
    }
    if image_url:
        album_data["images"] = [{"url": image_url}]
    return {
        "id": getattr(best_match, "id", "") or "",
        "name": getattr(best_match, "name", "") or "",
        "artists": list(getattr(best_match, "artists", []) or []),
        "album": album_data,
        "duration_ms": int(getattr(best_match, "duration_ms", 0) or 0),
        "image_url": image_url,
        "source": provider,
        "_provider": provider,
        "_confidence": float(best_confidence),
    }


def match_mb_tracks(
    tracks: List[Dict[str, Any]], deps: MBMatchDeps
) -> List[Optional[Dict[str, Any]]]:
    """Vectorized variant — runs ``match_mb_track`` per track.

    Phase 1b is sequential. If profiling shows it's too slow on big
    LB playlists, this becomes the natural spot to thread-pool the
    per-track searches."""
    return [match_mb_track(t, deps) for t in tracks]
