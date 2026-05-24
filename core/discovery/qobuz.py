"""Background worker for Qobuz playlist discovery.

`run_qobuz_discovery_worker(playlist_id, deps)` is the function the
Qobuz discovery start-endpoint submits to its executor to match each
Qobuz playlist track against Spotify (preferred) or the configured
fallback metadata source (iTunes / Deezer / Discogs / MusicBrainz).

Mirrors `core/discovery/deezer.py` exactly — Qobuz playlists arrive as
dicts (not dataclasses) from `core/qobuz_client.py:get_playlist`, so
this worker uses dict-style access on track data and wraps each entry
in a SimpleNamespace before handing it to the shared
`_search_spotify_for_tidal_track` helper.
"""

from __future__ import annotations

import logging
import time
import types
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class QobuzDiscoveryDeps:
    """Bundle of cross-cutting deps the Qobuz discovery worker needs."""
    qobuz_discovery_states: dict
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


def run_qobuz_discovery_worker(playlist_id, deps: QobuzDiscoveryDeps):
    """Background worker for Qobuz discovery process (Spotify preferred, fallback metadata source)."""
    _ew_state = {}
    try:
        _ew_state = deps.pause_enrichment_workers('Qobuz discovery')
        state = deps.qobuz_discovery_states[playlist_id]
        playlist = state['playlist']

        # Determine which provider to use
        discovery_source = deps.get_active_discovery_source()
        use_spotify = (discovery_source == 'spotify') and deps.spotify_client and deps.spotify_client.is_spotify_authenticated()

        # Initialize fallback client if needed
        itunes_client_instance = None
        if not use_spotify:
            itunes_client_instance = deps.get_metadata_fallback_client()

        logger.info(f"Starting Qobuz discovery for: {playlist['name']} (using {discovery_source.upper()})")

        # Store discovery source in state for frontend
        state['discovery_source'] = discovery_source

        successful_discoveries = 0
        tracks = playlist['tracks']

        for i, qobuz_track in enumerate(tracks):
            if state.get('cancelled', False):
                break

            try:
                track_name = qobuz_track['name']
                track_artists = qobuz_track['artists']
                track_id = qobuz_track['id']
                track_album = qobuz_track.get('album', '')
                track_duration_ms = qobuz_track.get('duration_ms', 0)

                logger.info(f"[{i+1}/{len(tracks)}] Searching {discovery_source.upper()}: {track_name} by {', '.join(track_artists)}")

                # Check discovery cache first
                cache_key = deps.get_discovery_cache_key(track_name, track_artists[0] if track_artists else '')
                try:
                    cache_db = deps.get_database()
                    cached_match = cache_db.get_discovery_cache_match(cache_key[0], cache_key[1], discovery_source)
                    if cached_match and deps.validate_discovery_cache_artist(track_artists[0] if track_artists else '', cached_match):
                        logger.debug(f"CACHE HIT [{i+1}/{len(tracks)}]: {track_name} by {', '.join(track_artists)}")
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
                            'qobuz_track': {
                                'id': track_id,
                                'name': track_name,
                                'artists': track_artists or [],
                                'album': track_album,
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

                # SimpleNamespace duck-type for _search_spotify_for_tidal_track
                track_ns = types.SimpleNamespace(
                    id=track_id,
                    name=track_name,
                    artists=track_artists,
                    album=track_album,
                    duration_ms=track_duration_ms
                )

                track_result = deps.search_spotify_for_tidal_track(
                    track_ns,
                    use_spotify=use_spotify,
                    itunes_client=itunes_client_instance
                )

                result = {
                    'qobuz_track': {
                        'id': track_id,
                        'name': track_name,
                        'artists': track_artists or [],
                        'album': track_album,
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
                    track_obj, raw_track_data, match_confidence = track_result
                    album_obj = raw_track_data.get('album', {}) if raw_track_data else {}
                    if isinstance(album_obj, dict) and not album_obj.get('name') and track_obj.album:
                        album_obj['name'] = track_obj.album
                    elif not album_obj and track_obj.album:
                        album_obj = {'name': track_obj.album}
                    if isinstance(album_obj, dict) and not album_obj.get('release_date'):
                        album_obj['release_date'] = getattr(track_obj, 'release_date', '') or ''
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
                    match_confidence = track_result.pop('confidence', 0.80)
                    match_data = track_result
                    match_data['source'] = discovery_source
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
                    qobuz_t = result.get('qobuz_track', {})
                    stub = deps.build_discovery_wing_it_stub(
                        qobuz_t.get('name', ''),
                        ', '.join(qobuz_t.get('artists', [])),
                        qobuz_t.get('duration_ms', 0)
                    )
                    result['status'] = 'Wing It'
                    result['status_class'] = 'wing-it'
                    result['spotify_data'] = stub
                    result['match_data'] = stub
                    result['spotify_track'] = qobuz_t.get('name', '')
                    result['spotify_artist'] = ', '.join(qobuz_t.get('artists', []))
                    result['wing_it_fallback'] = True
                    result['confidence'] = 0
                    successful_discoveries += 1
                    state['spotify_matches'] = successful_discoveries
                    state['wing_it_count'] = state.get('wing_it_count', 0) + 1

                result['index'] = i
                state['discovery_results'].append(result)
                state['discovery_progress'] = int(((i + 1) / len(tracks)) * 100)

                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error processing track {i+1}: {e}")
                result = {
                    'qobuz_track': {
                        'name': qobuz_track.get('name', 'Unknown'),
                        'artists': qobuz_track.get('artists', []),
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

        source_label = discovery_source.upper()
        deps.add_activity_item("", f"Qobuz Discovery Complete ({source_label})", f"'{playlist['name']}' - {successful_discoveries}/{len(tracks)} tracks found", "Now")

        logger.info(f"Qobuz discovery complete ({source_label}): {successful_discoveries}/{len(tracks)} tracks found")

        deps.sync_discovery_results_to_mirrored('qobuz', playlist_id, state.get('discovery_results', []), discovery_source, profile_id=state.get('_profile_id', 1))

    except Exception as e:
        logger.error(f"Error in Qobuz discovery worker: {e}")
        if playlist_id in deps.qobuz_discovery_states:
            deps.qobuz_discovery_states[playlist_id]['phase'] = 'error'
            deps.qobuz_discovery_states[playlist_id]['status'] = f'error: {str(e)}'
    finally:
        deps.resume_enrichment_workers(_ew_state, 'Qobuz discovery')
