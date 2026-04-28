"""Background worker for YouTube playlist discovery.

`run_youtube_discovery_worker(url_hash, deps)` is the function
`youtube_discovery_executor.submit(...)` invokes to match each YouTube
playlist track against Spotify (preferred) or iTunes (fallback):

1. Pause enrichment workers (release shared resources).
2. For each YouTube track:
   - Check discovery cache; cache hit short-circuits the search.
   - Strategy 1: matching_engine search queries with confidence scoring.
   - Strategy 2: swapped artist/title query.
   - Strategy 3: raw (untokenized) query.
   - Strategy 4: extended search with limit=50.
   - On match → save to discovery cache.
   - On miss → build a Wing It stub from raw source data.
3. After all tracks: mark phase 'discovered', sort results by index, and
   for mirrored playlists write extra_data back to the DB.
4. Activity feed entry with match summary.
5. On error → state['status'] = 'error', phase reset to 'fresh'.
6. Finally: resume enrichment workers.

Lifted verbatim from web_server.py. Wide dependency surface (Spotify and
iTunes clients, matching engine, multiple metadata helpers, state dicts,
database access) all injected via `YoutubeDiscoveryDeps`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class YoutubeDiscoveryDeps:
    """Bundle of cross-cutting deps the YouTube discovery worker needs."""
    youtube_playlist_states: dict
    spotify_client: Any
    matching_engine: Any
    pause_enrichment_workers: Callable[[str], dict]
    resume_enrichment_workers: Callable[[dict, str], None]
    get_active_discovery_source: Callable[[], str]
    get_metadata_fallback_client: Callable[[], Any]
    get_discovery_cache_key: Callable
    validate_discovery_cache_artist: Callable
    extract_artist_name: Callable
    spotify_rate_limited: Callable[[], bool]
    discovery_score_candidates: Callable
    get_metadata_cache: Callable[[], Any]
    build_discovery_wing_it_stub: Callable
    get_database: Callable[[], Any]
    add_activity_item: Callable


def run_youtube_discovery_worker(url_hash, deps: YoutubeDiscoveryDeps):
    """Background worker for YouTube music discovery process (Spotify preferred, iTunes fallback)"""
    _ew_state = {}
    try:
        _ew_state = deps.pause_enrichment_workers('YouTube discovery')
        state = deps.youtube_playlist_states[url_hash]
        playlist = state['playlist']
        tracks = playlist['tracks']

        # Determine which provider to use (Spotify preferred, iTunes fallback)
        discovery_source = deps.get_active_discovery_source()
        use_spotify = (discovery_source == 'spotify') and deps.spotify_client and deps.spotify_client.is_spotify_authenticated()

        # Get fallback client
        itunes_client = deps.get_metadata_fallback_client()

        logger.info(f"Starting {discovery_source} discovery for {len(tracks)} YouTube tracks...")

        # Store the discovery source in state
        state['discovery_source'] = discovery_source
        
        # Process each track for discovery
        for i, track in enumerate(tracks):
            try:
                # Check for cancellation (phase changed by reset/delete/close)
                if state.get('phase') != 'discovering':
                    logger.warning(f"Discovery cancelled for {url_hash} (phase changed to '{state.get('phase')}')")
                    return

                # Update progress
                state['discovery_progress'] = int((i / len(tracks)) * 100)

                # Skip tracks flagged by retry (already found)
                if track.get('skip_discovery'):
                    continue

                # Search for track using active provider
                cleaned_title = track['name']
                cleaned_artist = track['artists'][0] if track['artists'] else 'Unknown Artist'

                logger.info(f"Searching {discovery_source} for: '{cleaned_artist}' - '{cleaned_title}'")

                # Check discovery cache first
                cache_key = deps.get_discovery_cache_key(cleaned_title, cleaned_artist)
                try:
                    cache_db = deps.get_database()
                    cached_match = cache_db.get_discovery_cache_match(cache_key[0], cache_key[1], discovery_source)
                    if cached_match and deps.validate_discovery_cache_artist(cleaned_artist, cached_match):
                        logger.debug(f"CACHE HIT [{i+1}/{len(tracks)}]: {cleaned_artist} - {cleaned_title}")
                        result = {
                            'index': i,
                            'yt_track': cleaned_title,
                            'yt_artist': cleaned_artist,
                            'status': 'Found',
                            'status_class': 'found',
                            'spotify_track': cached_match.get('name', ''),
                            'spotify_artist': deps.extract_artist_name(cached_match.get('artists', [''])[0]) if cached_match.get('artists') else '',
                            'spotify_album': cached_match.get('album', {}).get('name', '') if isinstance(cached_match.get('album'), dict) else cached_match.get('album', ''),
                            'duration': f"{track['duration_ms'] // 60000}:{(track['duration_ms'] % 60000) // 1000:02d}" if track['duration_ms'] else '0:00',
                            'discovery_source': discovery_source,
                            'matched_data': cached_match,
                            'spotify_data': cached_match
                        }
                        state['spotify_matches'] += 1
                        state['discovery_results'].append(result)
                        continue
                except Exception as cache_err:
                    logger.error(f"Cache lookup error: {cache_err}")

                # Try multiple search strategies using matching engine
                matched_track = None
                best_confidence = 0.0
                best_raw_track = None
                min_confidence = 0.9
                source_duration = track.get('duration_ms', 0) or 0

                # Strategy 1: Use matching_engine search queries
                try:
                    temp_track = type('TempTrack', (), {
                        'name': cleaned_title,
                        'artists': [cleaned_artist],
                        'album': None
                    })()
                    search_queries = deps.matching_engine.generate_download_queries(temp_track)
                    logger.info(f"Generated {len(search_queries)} search queries for YouTube track")
                except Exception as e:
                    logger.error(f"Matching engine failed for YouTube, falling back to basic query: {e}")
                    search_queries = [f"{cleaned_artist} {cleaned_title}", cleaned_title]

                for query_idx, search_query in enumerate(search_queries):
                    try:
                        logger.debug(f"YouTube query {query_idx + 1}/{len(search_queries)}: {search_query}")

                        search_results = None

                        if use_spotify and not deps.spotify_rate_limited():
                            search_results = deps.spotify_client.search_tracks(search_query, limit=10)
                        else:
                            search_results = itunes_client.search_tracks(search_query, limit=10)

                        if not search_results:
                            continue

                        # Score all results using the matching engine
                        match, confidence, match_idx = deps.discovery_score_candidates(
                            cleaned_title, cleaned_artist, source_duration, search_results
                        )

                        if match and confidence > best_confidence and confidence >= min_confidence:
                            best_confidence = confidence
                            matched_track = match
                            if use_spotify and match.id:
                                _cache = deps.get_metadata_cache()
                                best_raw_track = _cache.get_entity('spotify', 'track', match.id)
                            else:
                                best_raw_track = None
                            logger.info(f"New best YouTube match: {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

                        if best_confidence >= 0.9:
                            logger.info(f"High confidence YouTube match found ({best_confidence:.3f}), stopping search")
                            break

                    except Exception as e:
                        logger.debug(f"Error in YouTube search for query '{search_query}': {e}")
                        continue

                if matched_track:
                    logger.info(f"Strategy 1 YouTube match: {matched_track.artists[0]} - {matched_track.name} (confidence: {best_confidence:.3f})")

                # Strategy 2: Swapped search (if first failed) - score results properly
                if not matched_track:
                    logger.info("YouTube Strategy 2: Trying swapped search (artist/title reversed)")
                    if use_spotify:
                        query = f"artist:{cleaned_title} track:{cleaned_artist}"
                        fallback_results = deps.spotify_client.search_tracks(query, limit=5)
                    else:
                        query = f"{cleaned_title} {cleaned_artist}"
                        fallback_results = itunes_client.search_tracks(query, limit=5)
                    if fallback_results:
                        match, confidence, _ = deps.discovery_score_candidates(
                            cleaned_title, cleaned_artist, source_duration, fallback_results
                        )
                        if match and confidence >= min_confidence:
                            matched_track = match
                            best_confidence = confidence
                            logger.info(f"Strategy 2 YouTube match (swapped): {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

                # Strategy 3: Raw data search (if still failed) - score results properly
                if not matched_track:
                    raw_title = track.get('raw_title', cleaned_title)
                    raw_artist = track.get('raw_artist', cleaned_artist)
                    logger.info(f"YouTube Strategy 3: Trying raw data search: '{raw_artist} {raw_title}'")
                    query = f"{raw_artist} {raw_title}"
                    if use_spotify:
                        fallback_results = deps.spotify_client.search_tracks(query, limit=5)
                    else:
                        fallback_results = itunes_client.search_tracks(query, limit=5)
                    if fallback_results:
                        match, confidence, _ = deps.discovery_score_candidates(
                            cleaned_title, cleaned_artist, source_duration, fallback_results
                        )
                        if match and confidence >= min_confidence:
                            matched_track = match
                            best_confidence = confidence
                            logger.info(f"Strategy 3 YouTube match (raw): {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

                # Strategy 4: Extended search with higher limit (last resort)
                if not matched_track:
                    logger.info("YouTube Strategy 4: Extended search with limit=50")
                    query = f"{cleaned_artist} {cleaned_title}"
                    if use_spotify:
                        extended_results = deps.spotify_client.search_tracks(query, limit=50)
                    else:
                        extended_results = itunes_client.search_tracks(query, limit=50)
                    if extended_results:
                        match, confidence, _ = deps.discovery_score_candidates(
                            cleaned_title, cleaned_artist, source_duration, extended_results
                        )
                        if match and confidence >= min_confidence:
                            matched_track = match
                            best_confidence = confidence
                            logger.info(f"Strategy 4 YouTube match (extended): {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

                # Create result entry
                result = {
                    'index': i,
                    'yt_track': cleaned_title,
                    'yt_artist': cleaned_artist,
                    'status': 'Found' if matched_track else 'Not Found',
                    'status_class': 'found' if matched_track else 'not-found',
                    'spotify_track': matched_track.name if matched_track else '',
                    'spotify_artist': deps.extract_artist_name(matched_track.artists[0]) if matched_track else '',
                    'spotify_album': matched_track.album if matched_track else '',
                    'duration': f"{track['duration_ms'] // 60000}:{(track['duration_ms'] % 60000) // 1000:02d}" if track['duration_ms'] else '0:00',
                    'discovery_source': discovery_source,
                    'confidence': best_confidence
                }

                if matched_track:
                    state['spotify_matches'] += 1

                    # Build album data based on provider
                    if use_spotify and best_raw_track:
                        album_data = best_raw_track.get('album', {})
                    else:
                        album_data = {
                            'name': matched_track.album,
                            'album_type': 'album',
                            'release_date': getattr(matched_track, 'release_date', '') or '',
                            'images': [{'url': matched_track.image_url}] if hasattr(matched_track, 'image_url') and matched_track.image_url else []
                        }

                    # Extract image URL for discovery pool display
                    _yt_album_images = album_data.get('images', [])
                    _yt_image_url = _yt_album_images[0].get('url', '') if _yt_album_images else (getattr(matched_track, 'image_url', '') or '')

                    result['matched_data'] = {
                        'id': matched_track.id,
                        'name': matched_track.name,
                        'artists': matched_track.artists,
                        'album': album_data,
                        'duration_ms': matched_track.duration_ms,
                        'image_url': _yt_image_url,
                        'source': discovery_source
                    }
                    result['spotify_data'] = result['matched_data']

                    # Save to discovery cache (only high-confidence matches)
                    if best_confidence >= 0.7:
                        try:
                            cache_db = deps.get_database()
                            cache_db.save_discovery_cache_match(
                                cache_key[0], cache_key[1], discovery_source, best_confidence,
                                result['matched_data'], cleaned_title, cleaned_artist
                            )
                            logger.info(f"CACHE SAVED: {cleaned_artist} - {cleaned_title} (confidence: {best_confidence:.3f})")
                        except Exception as cache_err:
                            logger.error(f"Cache save error: {cache_err}")

                else:
                    # Auto Wing It fallback — build stub from raw source data
                    stub = deps.build_discovery_wing_it_stub(cleaned_title, cleaned_artist, track.get('duration_ms', 0))
                    result['status'] = 'Wing It'
                    result['status_class'] = 'wing-it'
                    result['spotify_track'] = cleaned_title
                    result['spotify_artist'] = cleaned_artist
                    result['spotify_album'] = ''
                    result['matched_data'] = stub
                    result['spotify_data'] = stub
                    result['wing_it_fallback'] = True
                    state['wing_it_count'] = state.get('wing_it_count', 0) + 1

                state['discovery_results'].append(result)

                logger.info(f"  {'' if matched_track else ''} Track {i+1}/{len(tracks)}: {result['status']}")

            except Exception as e:
                logger.error(f"Error processing track {i}: {e}")
                result = {
                    'index': i,
                    'yt_track': track['name'],
                    'yt_artist': track['artists'][0] if track['artists'] else 'Unknown',
                    'status': 'Error',
                    'status_class': 'error',
                    'spotify_track': '',
                    'spotify_artist': '',
                    'spotify_album': '',
                    'duration': '0:00'
                }
                state['discovery_results'].append(result)

        # Complete discovery
        state['phase'] = 'discovered'
        state['status'] = 'complete'
        state['discovery_progress'] = 100

        # Sort results by index so array position matches result['index'].
        # Critical after retry where found results are kept at the front
        # and newly-discovered results are appended out of order.
        state['discovery_results'].sort(key=lambda r: r.get('index', 0))

        # Write back discovery results to DB for mirrored playlists
        if url_hash.startswith('mirrored_'):
            try:
                db = deps.get_database()
                for result in state['discovery_results']:
                    idx = result.get('index', -1)
                    if idx < 0 or idx >= len(tracks):
                        continue
                    db_track_id = tracks[idx].get('db_track_id')
                    if not db_track_id:
                        continue
                    if result.get('status_class') in ('found', 'wing-it') and result.get('matched_data'):
                        extra_data = {
                            'discovered': True,
                            'provider': result.get('discovery_source', discovery_source),
                            'confidence': result.get('confidence', 0),
                            'matched_data': result['matched_data'],
                        }
                        if result.get('manual_match'):
                            extra_data['manual_match'] = True
                        if result.get('wing_it_fallback'):
                            extra_data['wing_it_fallback'] = True
                            extra_data['provider'] = 'wing_it_fallback'
                        db.update_mirrored_track_extra_data(db_track_id, extra_data)
                    else:
                        extra_data = {
                            'discovered': False,
                            'discovery_attempted': True,
                            'provider': discovery_source,
                        }
                        db.update_mirrored_track_extra_data(db_track_id, extra_data)
                logger.info(f"Wrote discovery results to DB for {url_hash}")
            except Exception as wb_err:
                logger.error(f"Error writing discovery results to DB: {wb_err}")

        playlist_name = playlist['name']
        source_label = discovery_source.upper()
        wing_it_count = state.get('wing_it_count', 0)
        activity_msg = f"'{playlist_name}' - {state['spotify_matches']}/{len(tracks)} tracks found"
        if wing_it_count:
            activity_msg += f", {wing_it_count} wing it"
        deps.add_activity_item("", f"YouTube Discovery Complete ({source_label})", activity_msg, "Now")

        logger.info(f"YouTube discovery complete ({discovery_source}): {state['spotify_matches']}/{len(tracks)} tracks matched, {wing_it_count} wing it")

    except Exception as e:
        logger.error(f"Error in YouTube discovery worker: {e}")
        state['status'] = 'error'
        state['phase'] = 'fresh'
    finally:
        deps.resume_enrichment_workers(_ew_state, 'YouTube discovery')
