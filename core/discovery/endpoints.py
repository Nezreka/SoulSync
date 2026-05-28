"""Generic, source-agnostic helpers for the playlist-discovery route layer.

The discovery/sync endpoints in ``web_server.py`` were copy-pasted once per
source (Tidal, Deezer, Qobuz, Spotify-public, iTunes-link, YouTube,
ListenBrainz, Beatport). The per-source copies differ only by a source label
string and which ``<source>_discovery_states`` global they read. This module
lifts the source-agnostic pieces into importable, unit-testable helpers so the
route functions become thin wrappers — exactly preserving behavior (1:1).

Each helper is lifted verbatim from its web_server.py counterpart; any
per-source quirk that genuinely differs (e.g. Beatport's distinct result
shape) is intentionally NOT routed through here and stays in its own function.
"""

from __future__ import annotations

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("discovery.endpoints")


def convert_results_to_spotify_tracks(
    discovery_results: List[Dict[str, Any]],
    source_label: str,
) -> List[Dict[str, Any]]:
    """Convert a source's discovery results into the Spotify-track dicts the
    sync pipeline expects.

    Lifted verbatim from the per-source ``convert_<source>_results_to_spotify_tracks``
    functions (and the already-generic ``_convert_link_results_to_spotify_tracks``),
    which were byte-identical apart from the ``source_label`` used in the log
    line. Two input shapes are supported, matching the originals exactly:

    - ``spotify_data`` (manual-fix shape): copied through, preserving optional
      ``track_number`` / ``disc_number``.
    - ``spotify_track`` + ``status_class == 'found'`` (auto-discovery shape):
      rebuilt from the flat ``spotify_*`` fields.

    Any result matching neither shape is skipped, identical to the originals.

    NOTE: Beatport deliberately does NOT use this — its converter coerces
    artist objects to strings and emits a different track shape (``source``
    field, album dict), so it keeps its own implementation.
    """
    spotify_tracks: List[Dict[str, Any]] = []

    for result in discovery_results:
        # Support both data formats: spotify_data (manual fixes) and individual
        # fields (automatic discovery).
        if result.get('spotify_data'):
            spotify_data = result['spotify_data']
            track = {
                'id': spotify_data['id'],
                'name': spotify_data['name'],
                'artists': spotify_data['artists'],
                'album': spotify_data['album'],
                'duration_ms': spotify_data.get('duration_ms', 0),
            }
            if spotify_data.get('track_number'):
                track['track_number'] = spotify_data['track_number']
            if spotify_data.get('disc_number'):
                track['disc_number'] = spotify_data['disc_number']
            spotify_tracks.append(track)
        elif result.get('spotify_track') and result.get('status_class') == 'found':
            spotify_tracks.append({
                'id': result.get('spotify_id', 'unknown'),
                'name': result.get('spotify_track', 'Unknown Track'),
                'artists': [result.get('spotify_artist', 'Unknown Artist')] if result.get('spotify_artist') else ['Unknown Artist'],
                'album': result.get('spotify_album', 'Unknown Album'),
                'duration_ms': 0,
            })

    logger.info(f"Converted {len(spotify_tracks)} {source_label} matches to Spotify tracks for sync")
    return spotify_tracks
