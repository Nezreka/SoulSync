"""Background worker for Beatport chart discovery.

`run_beatport_discovery_worker(url_hash, deps)` is the function the
beatport discovery start-endpoint submits to its executor to match each
Beatport chart track against Spotify (preferred) or iTunes (fallback):

1. Pause enrichment workers (release shared resources).
2. For each Beatport track:
   - Cancellation gate (state['phase'] != 'discovering').
   - Clean Beatport text (artist/title) of common annotations.
   - Discovery cache lookup; cache hit short-circuits the search and
     normalizes cached artists from ['str'] → [{'name': 'str'}].
   - matching_engine search-query generation, with high min_confidence
     (0.9) to avoid bad matches.
   - Strategy 1: scored candidates from initial Spotify/iTunes searches.
   - Strategy 4: extended search with limit=50 if no high-confidence
     match found.
   - On Spotify match: format artists as [{'name': str}] objects, pull
     full album object from raw cache when available.
   - On iTunes match: format with image_url-derived album.images entry.
   - Save matched result to discovery cache when confidence >= 0.75.
   - On miss: Wing It stub stored as 'wing-it' status (success ticked).
3. After all tracks: phase='discovered', activity feed entry, sync
   discovery results back to mirrored playlist via
   `_sync_discovery_results_to_mirrored`.
4. On error: state['phase']='fresh' + status='error'.
5. Finally: resume enrichment workers.

Lifted verbatim from web_server.py. Wide dependency surface (Spotify
and iTunes clients, matching engine, multiple discovery helpers, state
dict, mirrored sync) all injected via `BeatportDiscoveryDeps`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class BeatportDiscoveryDeps:
    """Bundle of cross-cutting deps the Beatport discovery worker needs."""
    beatport_chart_states: dict
    spotify_client: Any
    matching_engine: Any
    pause_enrichment_workers: Callable[[str], dict]
    resume_enrichment_workers: Callable[[dict, str], None]
    get_active_discovery_source: Callable[[], str]
    get_metadata_fallback_client: Callable[[], Any]
    clean_beatport_text: Callable[[str], str]
    get_discovery_cache_key: Callable
    get_database: Callable[[], Any]
    validate_discovery_cache_artist: Callable
    spotify_rate_limited: Callable[[], bool]
    discovery_score_candidates: Callable
    get_metadata_cache: Callable[[], Any]
    build_discovery_wing_it_stub: Callable
    add_activity_item: Callable
    sync_discovery_results_to_mirrored: Callable


def run_beatport_discovery_worker(url_hash, deps: BeatportDiscoveryDeps):
    """Background worker for Beatport discovery process (Spotify preferred, iTunes fallback)"""
    _ew_state = {}
    try:
        _ew_state = deps.pause_enrichment_workers('Beatport discovery')
        state = deps.beatport_chart_states[url_hash]
        chart = state['chart']
        tracks = chart['tracks']

        # Determine which provider to use
        discovery_source = deps.get_active_discovery_source()
        use_spotify = (discovery_source == 'spotify') and deps.spotify_client and deps.spotify_client.is_spotify_authenticated()

        # Initialize fallback client if needed
        itunes_client_instance = None
        if not use_spotify:
            itunes_client_instance = deps.get_metadata_fallback_client()

        logger.info(f"Starting {discovery_source.upper()} discovery for {len(tracks)} Beatport tracks...")

        # Store discovery source in state for frontend
        state['discovery_source'] = discovery_source

        # Process each track for discovery
        for i, track in enumerate(tracks):
            try:
                # Check for cancellation
                if state.get('phase') != 'discovering':
                    logger.warning(f"Beatport discovery cancelled (phase changed to '{state.get('phase')}')")
                    return

                # Update progress
                state['discovery_progress'] = int((i / len(tracks)) * 100)

                # Get track info from Beatport data (frontend sends 'name' and 'artists' fields)
                track_title = deps.clean_beatport_text(track.get('name', 'Unknown Title'))
                track_artists = track.get('artists', ['Unknown Artist'])
                # Handle artists - could be a list or string
                if isinstance(track_artists, list):
                    if len(track_artists) > 0 and isinstance(track_artists[0], str):
                        # Handle case like ["CID,Taylr Renee"] - split on comma and clean
                        track_artist = deps.clean_beatport_text(track_artists[0].split(',')[0].strip())
                    else:
                        track_artist = deps.clean_beatport_text(track_artists[0] if track_artists else 'Unknown Artist')
                else:
                    track_artist = deps.clean_beatport_text(str(track_artists))

                logger.debug(f"Searching {discovery_source.upper()} for: '{track_artist}' - '{track_title}'")

                # Check discovery cache first
                cache_key = deps.get_discovery_cache_key(track_title, track_artist)
                try:
                    cache_db = deps.get_database()
                    cached_match = cache_db.get_discovery_cache_match(cache_key[0], cache_key[1], discovery_source)
                    if cached_match and deps.validate_discovery_cache_artist(track_artist, cached_match):
                        logger.debug(f"CACHE HIT [{i+1}/{len(tracks)}]: {track_artist} - {track_title}")
                        # Convert artists from ['str'] to [{'name': 'str'}] for Beatport frontend format
                        beatport_artists = cached_match.get('artists', [])
                        if beatport_artists and isinstance(beatport_artists[0], str):
                            cached_match['artists'] = [{'name': a} for a in beatport_artists]
                        result_entry = {
                            'index': i,
                            'beatport_track': {
                                'title': track_title,
                                'artist': track_artist
                            },
                            'status': 'found',
                            'status_class': 'found',
                            'discovery_source': discovery_source,
                            'spotify_data': cached_match
                        }
                        state['spotify_matches'] += 1
                        state['discovery_results'].append(result_entry)
                        continue
                except Exception as cache_err:
                    logger.error(f"Cache lookup error: {cache_err}")

                # Use matching engine for track matching
                found_track = None
                best_confidence = 0.0
                best_raw_track = None
                min_confidence = 0.9  # Higher threshold for Beatport to avoid bad matches

                # Generate search queries using matching engine (with fallback)
                try:
                    temp_track = type('TempTrack', (), {
                        'name': track_title,
                        'artists': [track_artist],
                        'album': None
                    })()
                    search_queries = deps.matching_engine.generate_download_queries(temp_track)
                    logger.debug(f"Generated {len(search_queries)} search queries using matching engine")
                except Exception as e:
                    logger.error(f"Matching engine failed for Beatport, falling back to basic queries: {e}")
                    if use_spotify:
                        search_queries = [
                            f"{track_artist} {track_title}",
                            f'artist:"{track_artist}" track:"{track_title}"',
                            f'"{track_artist}" "{track_title}"'
                        ]
                    else:
                        search_queries = [
                            f"{track_artist} {track_title}",
                            f"{track_title} {track_artist}",
                            track_title
                        ]

                for query_idx, search_query in enumerate(search_queries):
                    try:
                        logger.debug(f"Query {query_idx + 1}/{len(search_queries)}: {search_query} ({discovery_source.upper()})")

                        search_results = None

                        if use_spotify and not deps.spotify_rate_limited():
                            search_results = deps.spotify_client.search_tracks(search_query, limit=10)
                        else:
                            search_results = itunes_client_instance.search_tracks(search_query, limit=10)

                        if not search_results:
                            continue

                        # Score all results using the matching engine
                        match, confidence, match_idx = deps.discovery_score_candidates(
                            track_title, track_artist, 0, search_results
                        )

                        if match and confidence > best_confidence and confidence >= min_confidence:
                            best_confidence = confidence
                            found_track = match
                            if use_spotify and match.id:
                                _cache = deps.get_metadata_cache()
                                best_raw_track = _cache.get_entity('spotify', 'track', match.id)
                            else:
                                best_raw_track = None
                            logger.debug(f"New best Beatport match: {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

                        if best_confidence >= 0.9:
                            logger.debug(f"High confidence match found ({best_confidence:.3f}), stopping search")
                            break

                    except Exception as e:
                        logger.debug(f"Error in {discovery_source.upper()} search for query '{search_query}': {e}")
                        continue

                # Strategy 4: Extended search with higher limit (last resort)
                if not found_track:
                    logger.debug("Beatport Strategy 4: Extended search with limit=50")
                    query = f"{track_artist} {track_title}"
                    if use_spotify:
                        extended_results = deps.spotify_client.search_tracks(query, limit=50)
                    else:
                        extended_results = itunes_client_instance.search_tracks(query, limit=50)
                    if extended_results:
                        match, confidence, _ = deps.discovery_score_candidates(
                            track_title, track_artist, 0, extended_results
                        )
                        if match and confidence >= min_confidence:
                            found_track = match
                            best_confidence = confidence
                            logger.debug(f"Strategy 4 Beatport match (extended): {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

                if found_track:
                    logger.info(f"Final Beatport match: {found_track.artists[0]} - {found_track.name} (confidence: {best_confidence:.3f})")
                else:
                    logger.warning(f"No suitable match found (best confidence was {best_confidence:.3f}, required {min_confidence:.3f})")

                # Create result entry
                result_entry = {
                    'index': i,  # Add index for frontend table row identification
                    'beatport_track': {
                        'title': track_title,
                        'artist': track_artist
                    },
                    'status': 'found' if found_track else 'not_found',
                    'status_class': 'found' if found_track else 'not-found',
                    'discovery_source': discovery_source,
                    'confidence': best_confidence
                }

                if found_track:
                    if use_spotify:
                        # SPOTIFY result formatting
                        # Debug: show available attributes
                        logger.debug(f"Spotify track attributes: {dir(found_track)}")

                        # Format artists correctly for frontend compatibility
                        formatted_artists = []
                        if isinstance(found_track.artists, list):
                            # If it's already a list of strings, convert to objects with 'name' property
                            for artist in found_track.artists:
                                if isinstance(artist, str):
                                    formatted_artists.append({'name': artist})
                                else:
                                    # If it's already an object, use as-is
                                    formatted_artists.append(artist)
                        else:
                            # Single artist case
                            formatted_artists = [{'name': str(found_track.artists)}]

                        # Use full album object from raw Spotify data if available
                        album_data = best_raw_track.get('album', {}) if best_raw_track else {}
                        if not album_data:
                            # Fallback to string album name
                            album_data = {'name': found_track.album, 'album_type': 'album', 'release_date': getattr(found_track, 'release_date', '') or '', 'images': []}

                        result_entry['spotify_data'] = {
                            'name': found_track.name,
                            'artists': formatted_artists,  # Now formatted as list of objects with 'name' property
                            'album': album_data,  # Full album object with images
                            'id': found_track.id,
                            'source': 'spotify'
                        }
                    else:
                        # ITUNES result formatting
                        # Note: iTunes Track dataclass has 'artists' (list) and 'image_url', not 'artist' and 'artwork_url'
                        result_artists = found_track.artists if hasattr(found_track, 'artists') else []
                        result_artist = result_artists[0] if result_artists else 'Unknown'
                        result_name = found_track.name if hasattr(found_track, 'name') else 'Unknown'
                        album_name = found_track.album if hasattr(found_track, 'album') else 'Unknown Album'
                        image_url = found_track.image_url if hasattr(found_track, 'image_url') else ''
                        track_id = found_track.id if hasattr(found_track, 'id') else ''

                        # Format artists as list of objects for frontend compatibility
                        formatted_artists = [{'name': result_artist}]

                        # Build album data with artwork
                        album_data = {
                            'name': album_name,
                            'album_type': 'album',
                            'release_date': getattr(found_track, 'release_date', '') or '',
                            'images': [{'url': image_url, 'height': 300, 'width': 300}] if image_url else []
                        }

                        result_entry['spotify_data'] = {  # Use same key for frontend compatibility
                            'name': result_name,
                            'artists': formatted_artists,
                            'album': album_data,
                            'id': track_id,
                            'source': discovery_source
                        }

                    state['spotify_matches'] += 1

                    # Save to discovery cache (normalize artists from [{name:str}] to [str] for canonical format)
                    if best_confidence >= 0.75:
                        try:
                            cache_data = dict(result_entry['spotify_data'])
                            cache_artists = cache_data.get('artists', [])
                            if cache_artists and isinstance(cache_artists[0], dict):
                                cache_data['artists'] = [a.get('name', '') for a in cache_artists]
                            # Extract image URL for discovery pool display
                            if 'image_url' not in cache_data:
                                _bp_album = cache_data.get('album', {})
                                _bp_images = _bp_album.get('images', []) if isinstance(_bp_album, dict) else []
                                cache_data['image_url'] = _bp_images[0].get('url', '') if _bp_images else ''
                            cache_db = deps.get_database()
                            cache_db.save_discovery_cache_match(
                                cache_key[0], cache_key[1], discovery_source, best_confidence,
                                cache_data, track_title, track_artist
                            )
                            logger.debug(f"CACHE SAVED: {track_artist} - {track_title} (confidence: {best_confidence:.3f})")
                        except Exception as cache_err:
                            logger.error(f"Cache save error: {cache_err}")

                # Auto Wing It fallback for unmatched tracks
                if result_entry.get('status_class') == 'not-found':
                    bp_t = result_entry.get('beatport_track', {})
                    stub = deps.build_discovery_wing_it_stub(
                        bp_t.get('title', ''),
                        bp_t.get('artist', ''),
                    )
                    result_entry['status'] = 'found'
                    result_entry['status_class'] = 'wing-it'
                    result_entry['spotify_data'] = stub
                    result_entry['match_data'] = stub
                    result_entry['wing_it_fallback'] = True
                    result_entry['confidence'] = 0
                    state['spotify_matches'] = state.get('spotify_matches', 0) + 1
                    state['wing_it_count'] = state.get('wing_it_count', 0) + 1

                state['discovery_results'].append(result_entry)

                # Small delay to avoid rate limiting
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error processing Beatport track {i}: {e}")
                # Add error result
                state['discovery_results'].append({
                    'index': i,  # Add index for frontend table row identification
                    'beatport_track': {
                        'title': track.get('name', 'Unknown'),  # Changed from 'title' to 'name' to match track structure
                        'artist': track.get('artists', ['Unknown'])[0] if isinstance(track.get('artists'), list) else 'Unknown'
                    },
                    'status': 'error',
                    'status_class': 'error',  # Add status class for CSS styling
                    'error': str(e),
                    'discovery_source': discovery_source
                })

        # Mark discovery as complete
        state['discovery_progress'] = 100
        state['phase'] = 'discovered'
        state['status'] = 'discovered'

        # Add activity for completion
        chart_name = chart.get('name', 'Unknown Chart')
        source_label = discovery_source.upper()
        deps.add_activity_item("", f"Beatport Discovery Complete ({source_label})",
                         f"'{chart_name}' - {state['spotify_matches']}/{len(tracks)} tracks found", "Now")

        logger.info(f"Beatport discovery complete ({source_label}): {state['spotify_matches']}/{len(tracks)} tracks found")

        # Sync discovery results back to mirrored playlist
        deps.sync_discovery_results_to_mirrored('beatport', url_hash, state.get('discovery_results', []), discovery_source, profile_id=state.get('_profile_id', 1))

    except Exception as e:
        logger.error(f"Error in Beatport discovery worker: {e}")
        if url_hash in deps.beatport_chart_states:
            deps.beatport_chart_states[url_hash]['status'] = 'error'
            deps.beatport_chart_states[url_hash]['phase'] = 'fresh'
    finally:
        deps.resume_enrichment_workers(_ew_state, 'Beatport discovery')
