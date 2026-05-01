"""Background worker for public Spotify-link playlist discovery.

`run_spotify_public_discovery_worker(url_hash, deps)` is the function the
spotify-public discovery start-endpoint submits to its executor to match
each public Spotify playlist track against Spotify (preferred) or iTunes
(fallback):

1. Pause enrichment workers (release shared resources).
2. For each track:
   - Cancellation gate (state['cancelled']).
   - Discovery cache lookup; cache hit short-circuits the search and
     populates display fields from the cached match.
   - SimpleNamespace duck-type → `_search_spotify_for_tidal_track`
     (shared search helper, returns tuple for Spotify or dict for iTunes).
   - On Spotify match: build `match_data` preserving track_number /
     disc_number from raw API data, image extracted from album images
     or track object fallback, release_date filled from track.release_date
     when album dict is missing it.
   - On iTunes match: dict result populated as `match_data`, source set
     to discovery_source, image extracted from album images.
   - Save matched result to discovery cache.
   - On miss: Wing It stub stored as 'wing-it' status.
3. After all tracks: phase='discovered', activity feed entry.
4. On error: state['phase']='error' + status with error string.
5. Finally: resume enrichment workers.

Lifted verbatim from web_server.py. Wide dependency surface (Spotify and
iTunes clients, multiple metadata helpers, state dict, shared tidal
search helper) all injected via `SpotifyPublicDiscoveryDeps`.
"""

from __future__ import annotations

import logging
import time
import types
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class SpotifyPublicDiscoveryDeps:
    """Bundle of cross-cutting deps the Spotify Public discovery worker needs."""
    spotify_public_discovery_states: dict
    spotify_client: Any
    pause_enrichment_workers: Callable[[str], dict]
    resume_enrichment_workers: Callable[[dict, str], None]
    get_active_discovery_source: Callable[[], str]
    get_metadata_fallback_client: Callable[[], Any]
    get_discovery_cache_key: Callable
    get_database: Callable[[], Any]
    validate_discovery_cache_artist: Callable
    search_spotify_for_tidal_track: Callable
    build_discovery_wing_it_stub: Callable
    add_activity_item: Callable


def run_spotify_public_discovery_worker(url_hash, deps: SpotifyPublicDiscoveryDeps):
    """Background worker for Spotify Public discovery process (Spotify preferred, iTunes fallback)"""
    _ew_state = {}
    try:
        _ew_state = deps.pause_enrichment_workers('Spotify Public discovery')
        state = deps.spotify_public_discovery_states[url_hash]
        playlist = state['playlist']

        # Determine which provider to use — respect user's configured primary source
        discovery_source = deps.get_active_discovery_source()
        use_spotify = (discovery_source == 'spotify') and deps.spotify_client and deps.spotify_client.is_spotify_authenticated()

        # Initialize fallback client if needed
        itunes_client_instance = None
        if not use_spotify:
            itunes_client_instance = deps.get_metadata_fallback_client()

        logger.info(f"Starting Spotify Public discovery for: {playlist['name']} (using {discovery_source.upper()})")

        # Store discovery source in state for frontend
        state['discovery_source'] = discovery_source

        successful_discoveries = 0
        tracks = playlist['tracks']

        for i, sp_track in enumerate(tracks):
            if state.get('cancelled', False):
                break

            try:
                track_name = sp_track['name']
                track_artists_raw = sp_track.get('artists', [])
                # Normalize artists to list of strings
                track_artists = []
                for a in track_artists_raw:
                    if isinstance(a, dict):
                        track_artists.append(a.get('name', ''))
                    else:
                        track_artists.append(str(a))
                track_id = sp_track.get('id', '')
                track_album = sp_track.get('album', '')
                if isinstance(track_album, dict):
                    track_album_name = track_album.get('name', '')
                else:
                    track_album_name = track_album or ''
                track_duration_ms = sp_track.get('duration_ms', 0)

                logger.info(f"[{i+1}/{len(tracks)}] Searching {discovery_source.upper()}: {track_name} by {', '.join(track_artists)}")

                # Check discovery cache first
                cache_key = deps.get_discovery_cache_key(track_name, track_artists[0] if track_artists else '')
                try:
                    cache_db = deps.get_database()
                    cached_match = cache_db.get_discovery_cache_match(cache_key[0], cache_key[1], discovery_source)
                    if cached_match and deps.validate_discovery_cache_artist(track_artists[0] if track_artists else '', cached_match):
                        logger.debug(f"CACHE HIT [{i+1}/{len(tracks)}]: {track_name} by {', '.join(track_artists)}")
                        # Extract display-friendly artist string from cached match
                        cached_artists = cached_match.get('artists', [])
                        if cached_artists:
                            cached_artist_str = ', '.join(
                                a if isinstance(a, str) else a.get('name', '') for a in cached_artists
                            )
                        else:
                            cached_artist_str = ''
                        cached_album = cached_match.get('album', '')
                        if isinstance(cached_album, dict):
                            cached_album = cached_album.get('name', '')

                        result = {
                            'spotify_public_track': {
                                'id': track_id,
                                'name': track_name,
                                'artists': track_artists or [],
                                'album': track_album_name,
                                'duration_ms': track_duration_ms,
                            },
                            'spotify_data': cached_match,
                            'match_data': cached_match,
                            'status': 'Found',
                            'status_class': 'found',
                            'spotify_track': cached_match.get('name', ''),
                            'spotify_artist': cached_artist_str,
                            'spotify_album': cached_album,
                            'spotify_id': cached_match.get('id', ''),
                            'discovery_source': discovery_source,
                            'index': i
                        }
                        successful_discoveries += 1
                        state['spotify_matches'] = successful_discoveries
                        state['discovery_results'].append(result)
                        state['discovery_progress'] = int(((i + 1) / len(tracks)) * 100)
                        continue
                except Exception as cache_err:
                    logger.error(f"Cache lookup error: {cache_err}")

                # Create a SimpleNamespace duck-type object for _search_spotify_for_tidal_track
                track_ns = types.SimpleNamespace(
                    id=track_id,
                    name=track_name,
                    artists=track_artists,
                    album=track_album_name,
                    duration_ms=track_duration_ms
                )

                # Use the search function with appropriate provider
                track_result = deps.search_spotify_for_tidal_track(
                    track_ns,
                    use_spotify=use_spotify,
                    itunes_client=itunes_client_instance
                )

                # Create result entry
                result = {
                    'spotify_public_track': {
                        'id': track_id,
                        'name': track_name,
                        'artists': track_artists or [],
                        'album': track_album_name,
                        'duration_ms': track_duration_ms,
                    },
                    'spotify_data': None,
                    'match_data': None,
                    'status': 'Not Found',
                    'status_class': 'not-found',
                    'spotify_track': '',
                    'spotify_artist': '',
                    'spotify_album': '',
                    'discovery_source': discovery_source
                }

                match_confidence = 0.0

                if use_spotify and isinstance(track_result, tuple):
                    # Spotify: Function returns (Track, raw_data, confidence)
                    track_obj, raw_track_data, match_confidence = track_result
                    album_obj = raw_track_data.get('album', {}) if raw_track_data else {}
                    # Ensure album has a name — fall back to track_obj.album if raw_data was missing
                    if isinstance(album_obj, dict) and not album_obj.get('name') and track_obj.album:
                        album_obj['name'] = track_obj.album
                    elif not album_obj and track_obj.album:
                        album_obj = {'name': track_obj.album}
                    # Ensure release_date is present (raw Spotify data has it, but fallback may not)
                    if isinstance(album_obj, dict) and not album_obj.get('release_date'):
                        album_obj['release_date'] = getattr(track_obj, 'release_date', '') or ''
                    # Extract image URL from album data or track object
                    _album_images = album_obj.get('images', []) if isinstance(album_obj, dict) else []
                    _image_url = _album_images[0].get('url', '') if _album_images else (getattr(track_obj, 'image_url', '') or '')

                    match_data = {
                        'id': track_obj.id,
                        'name': track_obj.name,
                        'artists': track_obj.artists,
                        'album': album_obj,
                        'duration_ms': track_obj.duration_ms,
                        'external_urls': track_obj.external_urls,
                        'image_url': _image_url,
                        'source': 'spotify'
                    }
                    # Preserve track_number/disc_number from raw Spotify API data
                    if raw_track_data and raw_track_data.get('track_number'):
                        match_data['track_number'] = raw_track_data['track_number']
                    if raw_track_data and raw_track_data.get('disc_number'):
                        match_data['disc_number'] = raw_track_data['disc_number']
                    result['spotify_data'] = match_data
                    result['match_data'] = match_data
                    result['status'] = 'Found'
                    result['status_class'] = 'found'
                    result['spotify_track'] = track_obj.name
                    result['spotify_artist'] = ', '.join(track_obj.artists) if isinstance(track_obj.artists, list) else str(track_obj.artists)
                    result['spotify_album'] = album_obj.get('name', '') if isinstance(album_obj, dict) else str(album_obj)
                    result['spotify_id'] = track_obj.id
                    result['confidence'] = match_confidence
                    successful_discoveries += 1
                    state['spotify_matches'] = successful_discoveries

                elif not use_spotify and track_result and isinstance(track_result, dict):
                    # Fallback: Function returns a dict with track data (includes 'confidence' key)
                    match_confidence = track_result.pop('confidence', 0.80)
                    match_data = track_result
                    match_data['source'] = discovery_source
                    # Extract image URL from album images
                    _fb_album = match_data.get('album', {})
                    _fb_images = _fb_album.get('images', []) if isinstance(_fb_album, dict) else []
                    if _fb_images and 'image_url' not in match_data:
                        match_data['image_url'] = _fb_images[0].get('url', '')
                    result['spotify_data'] = match_data
                    result['match_data'] = match_data
                    result['status'] = 'Found'
                    result['status_class'] = 'found'
                    result['spotify_track'] = match_data.get('name', '')
                    itunes_artists = match_data.get('artists', [])
                    result['spotify_artist'] = ', '.join(a if isinstance(a, str) else a.get('name', '') for a in itunes_artists) if itunes_artists else ''
                    result['spotify_album'] = match_data.get('album', {}).get('name', '') if isinstance(match_data.get('album'), dict) else match_data.get('album', '')
                    result['spotify_id'] = match_data.get('id', '')
                    result['confidence'] = match_confidence
                    successful_discoveries += 1
                    state['spotify_matches'] = successful_discoveries

                # Save to discovery cache if match found
                if result['status_class'] == 'found' and result.get('match_data'):
                    try:
                        cache_db = deps.get_database()
                        cache_db.save_discovery_cache_match(
                            cache_key[0], cache_key[1], discovery_source, match_confidence,
                            result['match_data'], track_name,
                            track_artists[0] if track_artists else ''
                        )
                        logger.info(f"CACHE SAVED: {track_name} (confidence: {match_confidence:.3f})")
                    except Exception as cache_err:
                        logger.error(f"Cache save error: {cache_err}")

                # Auto Wing It fallback for unmatched tracks
                if result['status_class'] == 'not-found':
                    sp_t = result.get('spotify_public_track', {})
                    stub = deps.build_discovery_wing_it_stub(
                        sp_t.get('name', ''),
                        ', '.join(sp_t.get('artists', [])),
                        sp_t.get('duration_ms', 0)
                    )
                    result['status'] = 'Wing It'
                    result['status_class'] = 'wing-it'
                    result['spotify_data'] = stub
                    result['match_data'] = stub
                    result['spotify_track'] = sp_t.get('name', '')
                    result['spotify_artist'] = ', '.join(sp_t.get('artists', []))
                    result['wing_it_fallback'] = True
                    result['confidence'] = 0
                    successful_discoveries += 1
                    state['spotify_matches'] = successful_discoveries
                    state['wing_it_count'] = state.get('wing_it_count', 0) + 1

                result['index'] = i
                state['discovery_results'].append(result)
                state['discovery_progress'] = int(((i + 1) / len(tracks)) * 100)

                # Add delay between requests
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error processing track {i+1}: {e}")
                # Add error result
                result = {
                    'spotify_public_track': {
                        'name': sp_track.get('name', 'Unknown'),
                        'artists': sp_track.get('artists', []),
                    },
                    'spotify_data': None,
                    'match_data': None,
                    'status': 'Error',
                    'status_class': 'error',
                    'spotify_track': '',
                    'spotify_artist': '',
                    'spotify_album': '',
                    'error': str(e),
                    'discovery_source': discovery_source,
                    'index': i
                }
                state['discovery_results'].append(result)
                state['discovery_progress'] = int(((i + 1) / len(tracks)) * 100)

        # Mark as complete
        state['phase'] = 'discovered'
        state['status'] = 'discovered'
        state['discovery_progress'] = 100

        # Add activity for discovery completion
        source_label = discovery_source.upper()
        deps.add_activity_item("", f"Spotify Link Discovery Complete ({source_label})", f"'{playlist['name']}' - {successful_discoveries}/{len(tracks)} tracks found", "Now")

        logger.info(f"Spotify Public discovery complete ({source_label}): {successful_discoveries}/{len(tracks)} tracks found")

    except Exception as e:
        logger.error(f"Error in Spotify Public discovery worker: {e}")
        if url_hash in deps.spotify_public_discovery_states:
            deps.spotify_public_discovery_states[url_hash]['phase'] = 'error'
            deps.spotify_public_discovery_states[url_hash]['status'] = f'error: {str(e)}'
    finally:
        deps.resume_enrichment_workers(_ew_state, 'Spotify Public discovery')
