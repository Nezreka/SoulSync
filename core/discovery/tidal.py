"""Background worker for Tidal playlist discovery.

`run_tidal_discovery_worker(playlist_id, deps)` is the function the tidal
discovery start-endpoint submits to its executor to match each Tidal
playlist track against Spotify (preferred) or iTunes (fallback). Same
shape as the other source-specific discovery workers in this package.

1. Pause enrichment workers (release shared resources).
2. For each Tidal track:
   - Cancellation gate (state['cancelled']).
   - Discovery cache lookup; cache hit short-circuits the search.
   - `_search_spotify_for_tidal_track` (shared helper that the deezer +
     spotify_public workers also use; returns tuple for Spotify or dict
     for iTunes).
   - On Spotify match: build `match_data` preserving track_number /
     disc_number from raw API data; image extracted from album images
     or track object fallback; release_date filled from
     track.release_date when album dict is missing it.
   - On iTunes match: dict result populated as `match_data` with source
     set to discovery_source; image extracted from album images.
   - Save matched result to discovery cache.
   - On miss: Wing It stub stored as 'wing-it' status (success ticked).
3. After all tracks: phase='discovered', activity feed entry, sync
   discovery results back to mirrored playlist via
   `_sync_discovery_results_to_mirrored` with 'tidal' tag.
4. On error: state['phase']='error' + status with error string.
5. Finally: resume enrichment workers.

Lifted verbatim from web_server.py. Wide dependency surface (Spotify
and iTunes clients, multiple metadata helpers, state dict, mirrored
sync, shared tidal search helper) all injected via `TidalDiscoveryDeps`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class TidalDiscoveryDeps:
    """Bundle of cross-cutting deps the Tidal discovery worker needs."""
    tidal_discovery_states: dict
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
    sync_discovery_results_to_mirrored: Callable


def run_tidal_discovery_worker(playlist_id, deps: TidalDiscoveryDeps):
    """Background worker for Tidal discovery process (Spotify preferred, iTunes fallback)"""
    _ew_state = {}
    try:
        _ew_state = deps.pause_enrichment_workers('Tidal discovery')
        state = deps.tidal_discovery_states[playlist_id]
        playlist = state['playlist']

        # Determine which provider to use — respect user's configured primary source
        discovery_source = deps.get_active_discovery_source()
        use_spotify = (discovery_source == 'spotify') and deps.spotify_client and deps.spotify_client.is_spotify_authenticated()

        # Initialize fallback client if needed
        itunes_client_instance = None
        if not use_spotify:
            itunes_client_instance = deps.get_metadata_fallback_client()

        logger.info(f"Starting Tidal discovery for: {playlist.name} (using {discovery_source.upper()})")

        # Store discovery source in state for frontend
        state['discovery_source'] = discovery_source

        successful_discoveries = 0

        for i, tidal_track in enumerate(playlist.tracks):
            if state.get('cancelled', False):
                break

            try:
                logger.info(f"[{i+1}/{len(playlist.tracks)}] Searching {discovery_source.upper()}: {tidal_track.name} by {', '.join(tidal_track.artists)}")

                # Check discovery cache first
                cache_key = deps.get_discovery_cache_key(tidal_track.name, tidal_track.artists[0] if tidal_track.artists else '')
                try:
                    cache_db = deps.get_database()
                    cached_match = cache_db.get_discovery_cache_match(cache_key[0], cache_key[1], discovery_source)
                    if cached_match and deps.validate_discovery_cache_artist(tidal_track.artists[0] if tidal_track.artists else '', cached_match):
                        logger.debug(f"CACHE HIT [{i+1}/{len(playlist.tracks)}]: {tidal_track.name} by {', '.join(tidal_track.artists)}")
                        result = {
                            'tidal_track': {
                                'id': tidal_track.id,
                                'name': tidal_track.name,
                                'artists': tidal_track.artists or [],
                                'album': getattr(tidal_track, 'album', 'Unknown Album'),
                                'duration_ms': getattr(tidal_track, 'duration_ms', 0),
                            },
                            'spotify_data': cached_match,
                            'match_data': cached_match,
                            'status': 'found',
                            'discovery_source': discovery_source
                        }
                        successful_discoveries += 1
                        state['spotify_matches'] = successful_discoveries
                        state['discovery_results'].append(result)
                        state['discovery_progress'] = int(((i + 1) / len(playlist.tracks)) * 100)
                        continue
                except Exception as cache_err:
                    logger.error(f"Cache lookup error: {cache_err}")

                # Use the search function with appropriate provider
                track_result = deps.search_spotify_for_tidal_track(
                    tidal_track,
                    use_spotify=use_spotify,
                    itunes_client=itunes_client_instance
                )

                # Create result entry - use 'match_data' as generic key for both providers
                result = {
                    'tidal_track': {
                        'id': tidal_track.id,
                        'name': tidal_track.name,
                        'artists': tidal_track.artists or [],
                        'album': getattr(tidal_track, 'album', 'Unknown Album'),
                        'duration_ms': getattr(tidal_track, 'duration_ms', 0),
                    },
                    'spotify_data': None,  # Keep for backwards compatibility
                    'match_data': None,    # Generic field for any provider
                    'status': 'not_found',
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
                    result['status'] = 'found'
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
                    result['status'] = 'found'
                    result['confidence'] = match_confidence
                    successful_discoveries += 1
                    state['spotify_matches'] = successful_discoveries

                # Save to discovery cache if match found
                if result['status'] == 'found' and result.get('match_data'):
                    try:
                        cache_db = deps.get_database()
                        cache_db.save_discovery_cache_match(
                            cache_key[0], cache_key[1], discovery_source, match_confidence,
                            result['match_data'], tidal_track.name,
                            tidal_track.artists[0] if tidal_track.artists else ''
                        )
                        logger.info(f"CACHE SAVED: {tidal_track.name} (confidence: {match_confidence:.3f})")
                    except Exception as cache_err:
                        logger.error(f"Cache save error: {cache_err}")

                # Auto Wing It fallback for unmatched tracks
                if result['status'] != 'found':
                    tidal_t = result.get('tidal_track', {})
                    stub = deps.build_discovery_wing_it_stub(
                        tidal_t.get('name', ''),
                        ', '.join(tidal_t.get('artists', [])),
                        tidal_t.get('duration_ms', 0)
                    )
                    result['status'] = 'found'
                    result['status_class'] = 'wing-it'
                    result['spotify_data'] = stub
                    result['match_data'] = stub
                    result['wing_it_fallback'] = True
                    result['confidence'] = 0
                    successful_discoveries += 1
                    state['spotify_matches'] = successful_discoveries
                    state['wing_it_count'] = state.get('wing_it_count', 0) + 1

                state['discovery_results'].append(result)
                state['discovery_progress'] = int(((i + 1) / len(playlist.tracks)) * 100)

                # Add delay between requests
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error processing track {i+1}: {e}")
                # Add error result
                result = {
                    'tidal_track': {
                        'name': tidal_track.name,
                        'artists': tidal_track.artists or [],
                    },
                    'spotify_data': None,
                    'match_data': None,
                    'status': 'error',
                    'error': str(e),
                    'discovery_source': discovery_source
                }
                state['discovery_results'].append(result)
                state['discovery_progress'] = int(((i + 1) / len(playlist.tracks)) * 100)

        # Mark as complete
        state['phase'] = 'discovered'
        state['status'] = 'discovered'
        state['discovery_progress'] = 100

        # Add activity for discovery completion
        source_label = discovery_source.upper()
        deps.add_activity_item("", f"Tidal Discovery Complete ({source_label})", f"'{playlist.name}' - {successful_discoveries}/{len(playlist.tracks)} tracks found", "Now")

        logger.info(f"Tidal discovery complete ({source_label}): {successful_discoveries}/{len(playlist.tracks)} tracks found")

        # Sync discovery results back to mirrored playlist
        deps.sync_discovery_results_to_mirrored('tidal', playlist_id, state.get('discovery_results', []), discovery_source, profile_id=state.get('_profile_id', 1))

    except Exception as e:
        logger.error(f"Error in Tidal discovery worker: {e}")
        state['phase'] = 'error'
        state['status'] = f'error: {str(e)}'
    finally:
        deps.resume_enrichment_workers(_ew_state, 'Tidal discovery')
