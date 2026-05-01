"""Discovery scoring + tidal-track search — lifted from web_server.py.

Both function bodies are byte-identical to the originals. The
``spotify_client`` proxy and ``_get_metadata_fallback_source`` shim
let the bodies resolve their original names without modification.
``matching_engine`` is injected via init() because it is constructed
in web_server.py and referenced by name throughout the bodies.
"""
import logging

from core.metadata.cache import get_metadata_cache
from core.metadata.registry import get_primary_source, get_spotify_client
from core.spotify_client import _is_globally_rate_limited as _spotify_rate_limited

logger = logging.getLogger(__name__)


def _get_metadata_fallback_source():
    """Mirror of web_server._get_metadata_fallback_source — delegates to registry."""
    return get_primary_source()


class _SpotifyClientProxy:
    """Resolves the global Spotify client lazily through core.metadata.registry."""

    def __getattr__(self, name):
        client = get_spotify_client()
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __bool__(self):
        return get_spotify_client() is not None


spotify_client = _SpotifyClientProxy()


# Injected at runtime via init().
matching_engine = None


def init(matching_engine_obj):
    """Bind the shared matching engine instance from web_server."""
    global matching_engine
    matching_engine = matching_engine_obj


def _discovery_score_candidates(source_title, source_artist, source_duration_ms, search_results):
    """Score search results against a source track using the matching engine.

    Both artist AND title must independently pass minimum similarity floors.
    This prevents weighted scoring from allowing a perfect artist to carry a
    garbage title (or vice versa). If either dimension doesn't match, the
    candidate is rejected — no match is better than a wrong match.

    Args:
        source_title: The source track title (already cleaned for YouTube, raw for others)
        source_artist: The source track primary artist
        source_duration_ms: The source track duration in ms (0 if unknown)
        search_results: List of Track objects (Spotify or iTunes) from search

    Returns:
        (best_match, best_confidence, best_index) or (None, 0.0, -1) if no results
    """
    best_match = None
    best_confidence = 0.0
    best_index = -1
    min_artist_similarity = 0.5
    min_title_similarity = 0.5

    source_artist_cleaned = matching_engine.clean_artist(source_artist)
    source_title_cleaned = matching_engine.clean_title(source_title)
    source_core_title = matching_engine.get_core_string(source_title)

    for idx, result in enumerate(search_results):
        try:
            result_artists = result.artists if hasattr(result, 'artists') and result.artists else []
            result_name = result.name if hasattr(result, 'name') else ''
            result_duration = result.duration_ms if hasattr(result, 'duration_ms') else 0

            # Artist floor — both must match, not just the weighted score
            best_artist_sim = 0.0
            for cand_artist in result_artists:
                if not cand_artist:
                    continue
                cand_cleaned = matching_engine.clean_artist(cand_artist)
                cand_normalized = matching_engine.normalize_string(cand_artist)
                if source_artist_cleaned and source_artist_cleaned in cand_normalized:
                    best_artist_sim = 1.0
                    break
                sim = matching_engine.similarity_score(source_artist_cleaned, cand_cleaned)
                if sim > best_artist_sim:
                    best_artist_sim = sim

            if best_artist_sim < min_artist_similarity:
                continue

            # Title floor — both must match, not just the weighted score
            cand_title_cleaned = matching_engine.clean_title(result_name)
            cand_core_title = matching_engine.get_core_string(result_name)

            # Core title exact match bypasses the floor (e.g., "edamame" == "edamame")
            title_passes = False
            if source_core_title and cand_core_title and source_core_title == cand_core_title:
                title_passes = True
            else:
                title_sim = matching_engine.similarity_score(source_title_cleaned, cand_title_cleaned)
                if title_sim >= min_title_similarity:
                    title_passes = True

            if not title_passes:
                continue

            # Both floors passed — now do full scoring
            confidence, match_type = matching_engine.score_track_match(
                source_title=source_title,
                source_artists=[source_artist],
                source_duration_ms=source_duration_ms,
                candidate_title=result_name,
                candidate_artists=result_artists,
                candidate_duration_ms=result_duration
            )

            if confidence > best_confidence:
                best_confidence = confidence
                best_match = result
                best_index = idx

        except Exception as e:
            logger.error(f"Error scoring candidate {idx}: {e}")
            continue

    return best_match, best_confidence, best_index


def _search_spotify_for_tidal_track(tidal_track, use_spotify=True, itunes_client=None):
    """Search Spotify/fallback for a Tidal track using matching_engine for better accuracy

    Args:
        tidal_track: The Tidal track to search for
        use_spotify: If True, use Spotify; if False, use fallback source
        itunes_client: Fallback client instance (required when use_spotify=False)

    Returns:
        For Spotify: (Track, raw_data, confidence) tuple or None
        For fallback: dict with track data (includes 'confidence' key) or None
    """
    if use_spotify:
        if not spotify_client or not spotify_client.is_authenticated():
            return None
    else:
        if not itunes_client:
            return None

    try:
        # Get track info
        track_name = tidal_track.name
        artists = tidal_track.artists or []

        if not artists:
            return None

        artist_name = artists[0]  # Use primary artist
        source_duration = getattr(tidal_track, 'duration_ms', 0) or 0
        source_name = "Spotify" if use_spotify else _get_metadata_fallback_source().capitalize()

        logger.info(f"Tidal track: '{artist_name}' - '{track_name}' (searching {source_name})")

        # Use matching engine to generate search queries (with fallback)
        try:
            temp_track = type('TempTrack', (), {
                'name': track_name,
                'artists': [artist_name],
                'album': None
            })()
            search_queries = matching_engine.generate_download_queries(temp_track)
            logger.info(f"Generated {len(search_queries)} search queries for Tidal track")
        except Exception as e:
            logger.error(f"Matching engine failed for Tidal, falling back to basic queries: {e}")
            if use_spotify:
                search_queries = [
                    f'track:"{track_name}" artist:"{artist_name}"',
                    f'"{track_name}" "{artist_name}"',
                    f'{track_name} {artist_name}'
                ]
            else:
                search_queries = [
                    f'{artist_name} {track_name}',
                    f'{track_name} {artist_name}',
                    track_name
                ]

        best_match = None
        best_match_raw = None
        best_confidence = 0.0
        min_confidence = 0.9

        for query_idx, search_query in enumerate(search_queries):
            try:
                logger.debug(f"Tidal query {query_idx + 1}/{len(search_queries)}: {search_query} ({source_name})")

                if use_spotify and not _spotify_rate_limited():
                    results = spotify_client.search_tracks(search_query, limit=10)
                    if not results:
                        continue
                else:
                    results = itunes_client.search_tracks(search_query, limit=10)
                    if not results:
                        continue

                # Score all results using the matching engine
                match, confidence, match_idx = _discovery_score_candidates(
                    track_name, artist_name, source_duration, results
                )

                if match and confidence > best_confidence and confidence >= min_confidence:
                    best_confidence = confidence
                    best_match = match
                    if use_spotify and match.id:
                        _cache = get_metadata_cache()
                        best_match_raw = _cache.get_entity('spotify', 'track', match.id)
                    else:
                        best_match_raw = None
                    logger.info(f"New best Tidal match: {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

                if best_confidence >= 0.9:
                    logger.info(f"High confidence Tidal match found ({best_confidence:.3f}), stopping search")
                    break

            except Exception as e:
                logger.debug(f"Error in Tidal {source_name} search for query '{search_query}': {e}")
                continue

        # Strategy 4: Extended search with higher limit (last resort)
        if not best_match:
            logger.info("Tidal Strategy 4: Extended search with limit=50")
            query = f"{artist_name} {track_name}"
            if use_spotify:
                extended_results = spotify_client.search_tracks(query, limit=50)
            else:
                extended_results = itunes_client.search_tracks(query, limit=50)
            if extended_results:
                match, confidence, match_idx = _discovery_score_candidates(
                    track_name, artist_name, source_duration, extended_results
                )
                if match and confidence >= min_confidence:
                    best_match = match
                    best_confidence = confidence
                    logger.info(f"Strategy 4 Tidal match (extended): {match.artists[0]} - {match.name} (confidence: {confidence:.3f})")

        if best_match:
            if use_spotify:
                logger.info(f"Final Tidal Spotify match: {best_match.artists[0]} - {best_match.name} (confidence: {best_confidence:.3f})")
                return (best_match, best_match_raw, best_confidence)
            else:
                result_artists = best_match.artists if hasattr(best_match, 'artists') else []
                result_artist = result_artists[0] if result_artists else 'Unknown'
                result_name = best_match.name if hasattr(best_match, 'name') else 'Unknown'
                logger.info(f"Final Tidal {source_name} match: {result_artist} - {result_name} (confidence: {best_confidence:.3f})")

                album_name = best_match.album if hasattr(best_match, 'album') else 'Unknown Album'
                image_url = best_match.image_url if hasattr(best_match, 'image_url') else ''
                track_id = best_match.id if hasattr(best_match, 'id') else ''
                duration_ms = best_match.duration_ms if hasattr(best_match, 'duration_ms') else 0

                # Fetch full track details to get album ID, track_number, etc.
                # The Track dataclass strips this data — the API has it
                album_obj = {
                    'name': album_name,
                    'album_type': 'album',
                    'release_date': getattr(best_match, 'release_date', '') or '',
                    'images': [{'url': image_url, 'height': 300, 'width': 300}] if image_url else []
                }
                track_number = None
                disc_number = None
                if track_id:
                    try:
                        detailed = itunes_client.get_track_details(track_id)
                        if detailed and isinstance(detailed.get('album'), dict):
                            dt_album = detailed['album']
                            if dt_album.get('id'):
                                album_obj['id'] = dt_album['id']
                            if dt_album.get('total_tracks'):
                                album_obj['total_tracks'] = dt_album['total_tracks']
                            if dt_album.get('release_date') and not album_obj.get('release_date'):
                                album_obj['release_date'] = dt_album['release_date']
                            if dt_album.get('album_type'):
                                album_obj['album_type'] = dt_album['album_type']
                            if dt_album.get('images') and not album_obj.get('images'):
                                album_obj['images'] = dt_album['images']
                            if dt_album.get('artists'):
                                album_obj['artists'] = dt_album['artists']
                        if detailed:
                            track_number = detailed.get('track_number')
                            disc_number = detailed.get('disc_number')
                            logger.info(f"[Discovery Enrich] {result_name}: track_number={track_number}, disc={disc_number}")
                        else:
                            logger.info(f"[Discovery Enrich] get_track_details returned None for ID {track_id} ({result_name})")
                    except Exception as _enrich_err:
                        logger.error(f"[Discovery Enrich] Failed for {result_name} (ID {track_id}): {_enrich_err}")

                result_data = {
                    'id': track_id,
                    'name': result_name,
                    'artists': [result_artist],
                    'album': album_obj,
                    'duration_ms': duration_ms,
                    'source': _get_metadata_fallback_source(),
                    'confidence': best_confidence
                }
                if track_number:
                    result_data['track_number'] = track_number
                if disc_number:
                    result_data['disc_number'] = disc_number
                return result_data
        else:
            logger.warning(f"No suitable Tidal match found (best confidence was {best_confidence:.3f}, required {min_confidence:.3f})")
            return None

    except Exception as e:
        logger.error(f"Error searching Spotify for Tidal track: {e}")
        return None
