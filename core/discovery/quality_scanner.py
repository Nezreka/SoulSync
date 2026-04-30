"""Background worker for the library quality scanner.

`run_quality_scanner(scope, profile_id, deps)` is the function the
quality-scanner endpoint kicks off in a thread to scan the library
for low-quality tracks (below the user's configured quality profile)
and add provider matches to the wishlist:

1. Reset scanner state, load quality profile + minimum acceptable tier.
2. Load tracks from DB based on scope:
   - 'watchlist' → tracks for watchlisted artists only.
   - other → all library tracks.
3. For each track:
   - Stop-request gate (state['status'] != 'running').
   - Quality-tier check via _get_quality_tier_from_extension(file_path).
   - Skip tracks meeting standards (tier_num <= min_acceptable_tier).
   - For low-quality tracks: matching_engine search query gen, score
     candidates against the configured metadata source priority
     (artist + title similarity, album-type bonus), pick best match >=
     0.7 confidence.
   - On match: add normalized track data to wishlist via
     `wishlist_service.add_track_to_wishlist` with
     source_type='quality_scanner' and a source_context that captures
     original file_path, format tier, bitrate, and match confidence.
4. After all tracks: status='finished', progress=100, activity feed
   entry, emit `quality_scan_completed` event for automation engine.
5. On critical exception: status='error', error message captured.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from core.metadata.registry import get_client_for_source, get_primary_source, get_source_priority
from core.wishlist.payloads import ensure_wishlist_track_format

logger = logging.getLogger(__name__)


@dataclass
class QualityScannerDeps:
    """Bundle of cross-cutting deps the quality scanner needs."""
    quality_scanner_state: dict
    quality_scanner_lock: Any  # threading.Lock
    QUALITY_TIERS: dict
    matching_engine: Any
    automation_engine: Any
    get_quality_tier_from_extension: Callable
    add_activity_item: Callable


def _extract_lookup_value(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        return value

    for name in names:
        if isinstance(value, dict):
            if name in value and value[name] is not None:
                return value[name]
        else:
            candidate = getattr(value, name, None)
            if candidate is not None:
                return candidate
    return default


def _normalize_track_artists(track_item: Any) -> list[dict]:
    artists = _extract_lookup_value(track_item, 'artists', default=[]) or []
    if isinstance(artists, (str, bytes)):
        artists = [artists]
    elif isinstance(artists, dict):
        artists = [artists]
    else:
        try:
            artists = list(artists)
        except TypeError:
            artists = [artists]

    normalized = []
    for artist in artists:
        artist_name = _extract_lookup_value(artist, 'name', 'artist_name', 'title')
        if not artist_name and isinstance(artist, (str, bytes)):
            artist_name = artist
        if artist_name:
            normalized.append({'name': str(artist_name)})

    if not normalized:
        normalized.append({'name': 'Unknown Artist'})

    return normalized


def _normalize_track_album(track_item: Any) -> dict:
    album = _extract_lookup_value(track_item, 'album', default={})
    if isinstance(album, dict):
        album_data = dict(album)
    else:
        album_data = {
            'name': _extract_lookup_value(album, 'name', 'title', default=str(album) if album else '') or '',
            'images': _extract_lookup_value(album, 'images', default=[]) or [],
            'album_type': _extract_lookup_value(album, 'album_type', default='album') or 'album',
            'total_tracks': _extract_lookup_value(album, 'total_tracks', 'track_count', default=0) or 0,
            'release_date': _extract_lookup_value(album, 'release_date', default='') or '',
        }

    album_data.setdefault('name', _extract_lookup_value(track_item, 'album_name', default='Unknown Album') or 'Unknown Album')
    album_data.setdefault('album_type', _extract_lookup_value(track_item, 'album_type', default='album') or 'album')
    album_data.setdefault('total_tracks', _extract_lookup_value(track_item, 'total_tracks', 'track_count', default=0) or 0)
    album_data.setdefault('release_date', _extract_lookup_value(track_item, 'release_date', default='') or '')
    if isinstance(album, dict):
        album_data.setdefault('images', album.get('images', []) or [])
    else:
        album_data.setdefault('images', [])
    album_data.setdefault('artists', _normalize_track_artists(track_item))
    return album_data


def _normalize_track_match(track_item: Any, provider: str) -> dict:
    track_data = {
        'id': _extract_lookup_value(track_item, 'id', 'track_id', default='') or '',
        'name': _extract_lookup_value(track_item, 'name', 'title', default='Unknown Track') or 'Unknown Track',
        'artists': _normalize_track_artists(track_item),
        'album': _normalize_track_album(track_item),
        'duration_ms': _extract_lookup_value(track_item, 'duration_ms', default=0) or 0,
        'track_number': _extract_lookup_value(track_item, 'track_number', default=1) or 1,
        'disc_number': _extract_lookup_value(track_item, 'disc_number', default=1) or 1,
        'preview_url': _extract_lookup_value(track_item, 'preview_url', default=None),
        'external_urls': _extract_lookup_value(track_item, 'external_urls', default={}) or {},
        'popularity': _extract_lookup_value(track_item, 'popularity', default=0) or 0,
        'provider': provider,
        'source': provider,
    }
    return ensure_wishlist_track_format(track_data)


def _track_name(track_item: Any) -> str:
    return str(_extract_lookup_value(track_item, 'name', 'title', default='Unknown Track') or 'Unknown Track')


def _track_artist_names(track_item: Any) -> list[str]:
    artists = _extract_lookup_value(track_item, 'artists', default=[]) or []
    if isinstance(artists, (str, bytes)):
        artists = [artists]
    elif isinstance(artists, dict):
        artists = [artists]
    else:
        try:
            artists = list(artists)
        except TypeError:
            artists = [artists]

    normalized = []
    for artist in artists:
        artist_name = _extract_lookup_value(artist, 'name', 'artist_name', 'title')
        if not artist_name and isinstance(artist, (str, bytes)):
            artist_name = artist
        if artist_name:
            normalized.append(str(artist_name))
    return normalized


def _search_tracks_for_source(source: str, query: str, limit: int = 5, client: Any = None):
    if client is None:
        client = get_client_for_source(source)
    if not client or not hasattr(client, 'search_tracks'):
        return []

    try:
        if source == 'spotify':
            return client.search_tracks(query, limit=limit, allow_fallback=False) or []
        return client.search_tracks(query, limit=limit) or []
    except TypeError:
        try:
            return client.search_tracks(query, limit=limit) or []
        except Exception as exc:
            logger.debug("Could not search %s for %s: %s", source, query, exc)
            return []
    except Exception as exc:
        logger.debug("Could not search %s for %s: %s", source, query, exc)
        return []


def run_quality_scanner(scope='watchlist', profile_id=1, deps: QualityScannerDeps = None):
    """Main quality scanner worker function"""
    from core.wishlist_service import get_wishlist_service
    from database.music_database import MusicDatabase

    try:
        with deps.quality_scanner_lock:
            deps.quality_scanner_state["status"] = "running"
            deps.quality_scanner_state["phase"] = "Initializing scan..."
            deps.quality_scanner_state["progress"] = 0
            deps.quality_scanner_state["processed"] = 0
            deps.quality_scanner_state["total"] = 0
            deps.quality_scanner_state["quality_met"] = 0
            deps.quality_scanner_state["low_quality"] = 0
            deps.quality_scanner_state["matched"] = 0
            deps.quality_scanner_state["results"] = []
            deps.quality_scanner_state["error_message"] = ""

        logger.info(f"[Quality Scanner] Starting scan with scope: {scope}")

        # Get database instance
        db = MusicDatabase()

        # Get quality profile to determine preferred quality
        quality_profile = db.get_quality_profile()
        preferred_qualities = quality_profile.get('qualities', {})

        # Determine minimum acceptable tier based on enabled qualities
        min_acceptable_tier = 999
        for quality_name, quality_config in preferred_qualities.items():
            if quality_config.get('enabled', False):
                # Map quality profile names to tier names
                tier_map = {
                    'flac': 'lossless',
                    'mp3_320': 'low_lossy',
                    'mp3_256': 'low_lossy',
                    'mp3_192': 'low_lossy'
                }
                tier_name = tier_map.get(quality_name)
                if tier_name:
                    tier_num = deps.QUALITY_TIERS[tier_name]['tier']
                    min_acceptable_tier = min(min_acceptable_tier, tier_num)

        logger.info(f"[Quality Scanner] Minimum acceptable tier: {min_acceptable_tier}")

        # Get tracks to scan based on scope
        with deps.quality_scanner_lock:
            deps.quality_scanner_state["phase"] = "Loading tracks from database..."

        if scope == 'watchlist':
            # Get watchlist artists
            watchlist_artists = db.get_watchlist_artists(profile_id=profile_id)
            if not watchlist_artists:
                with deps.quality_scanner_lock:
                    deps.quality_scanner_state["status"] = "finished"
                    deps.quality_scanner_state["phase"] = "No watchlist artists found"
                    deps.quality_scanner_state["error_message"] = "Please add artists to watchlist first"
                logger.warning("[Quality Scanner] No watchlist artists found")
                return

            # Get artist names from watchlist
            artist_names = [artist.artist_name for artist in watchlist_artists]
            logger.info(f"[Quality Scanner] Scanning {len(artist_names)} watchlist artists")

            # Get all tracks for these artists by name
            conn = db._get_connection()
            placeholders = ','.join(['?' for _ in artist_names])
            tracks_to_scan = conn.execute(
                f"SELECT t.id, t.title, t.artist_id, t.album_id, t.file_path, t.bitrate, a.name as artist_name, al.title as album_title "
                f"FROM tracks t "
                f"JOIN artists a ON t.artist_id = a.id "
                f"JOIN albums al ON t.album_id = al.id "
                f"WHERE a.name IN ({placeholders}) AND t.file_path IS NOT NULL",
                artist_names
            ).fetchall()
            conn.close()
        else:
            # Scan all library tracks
            with deps.quality_scanner_lock:
                deps.quality_scanner_state["phase"] = "Loading all library tracks..."

            conn = db._get_connection()
            tracks_to_scan = conn.execute(
                "SELECT t.id, t.title, t.artist_id, t.album_id, t.file_path, t.bitrate, a.name as artist_name, al.title as album_title "
                "FROM tracks t "
                "JOIN artists a ON t.artist_id = a.id "
                "JOIN albums al ON t.album_id = al.id "
                "WHERE t.file_path IS NOT NULL"
            ).fetchall()
            conn.close()

        total_tracks = len(tracks_to_scan)
        logger.info(f"[Quality Scanner] Found {total_tracks} tracks to scan")

        with deps.quality_scanner_lock:
            deps.quality_scanner_state["total"] = total_tracks
            deps.quality_scanner_state["phase"] = f"Scanning {total_tracks} tracks..."

        source_priority = get_source_priority(get_primary_source())
        if not source_priority:
            with deps.quality_scanner_lock:
                deps.quality_scanner_state["status"] = "error"
                deps.quality_scanner_state["phase"] = "No metadata provider available"
                deps.quality_scanner_state["error_message"] = "No metadata provider is available for quality scanning"
            logger.info("[Quality Scanner] No metadata provider available")
            return

        logger.info("[Quality Scanner] Using metadata source priority: %s", source_priority)

        wishlist_service = get_wishlist_service()
        add_to_wishlist = getattr(wishlist_service, 'add_track_to_wishlist', None)
        if add_to_wishlist is None:
            add_to_wishlist = getattr(wishlist_service, 'add_spotify_track_to_wishlist', None)
        if add_to_wishlist is None:
            raise AttributeError("Wishlist service does not expose an add-to-wishlist method")

        # Scan each track
        for idx, track_row in enumerate(tracks_to_scan, 1):
            # Check for stop request
            if deps.quality_scanner_state.get('status') != 'running':
                logger.info(f"[Quality Scanner] Stop requested, halting at track {idx}/{total_tracks}")
                break

            try:
                track_id, title, artist_id, album_id, file_path, bitrate, artist_name, album_title = track_row

                # Check quality tier
                tier_name, tier_num = deps.get_quality_tier_from_extension(file_path)

                # Update progress
                with deps.quality_scanner_lock:
                    deps.quality_scanner_state["processed"] = idx
                    deps.quality_scanner_state["progress"] = (idx / total_tracks) * 100
                    deps.quality_scanner_state["phase"] = f"Scanning: {artist_name} - {title}"

                # Check if meets quality standards
                if tier_num <= min_acceptable_tier:
                    # Quality met
                    with deps.quality_scanner_lock:
                        deps.quality_scanner_state["quality_met"] += 1
                    continue

                # Low quality track found
                with deps.quality_scanner_lock:
                    deps.quality_scanner_state["low_quality"] += 1

                logger.info(f"[Quality Scanner] Low quality: {artist_name} - {title} ({tier_name}, {file_path})")

                # Attempt to match using the active metadata provider
                matched = False
                matched_track_data = None
                best_source = None
                attempted_any_provider = False

                try:
                    # Generate search queries using matching engine
                    temp_track = type('TempTrack', (), {
                        'name': title,
                        'artists': [artist_name],
                        'album': album_title
                    })()

                    search_queries = deps.matching_engine.generate_download_queries(temp_track)
                    logger.info(f"[Quality Scanner] Generated {len(search_queries)} search queries for {artist_name} - {title}")

                    # Find best match using confidence scoring
                    best_match = None
                    best_confidence = 0.0
                    min_confidence = 0.7  # Match existing standard

                    for _query_idx, search_query in enumerate(search_queries):
                        try:
                            for source in source_priority:
                                client = get_client_for_source(source)
                                if not client or not hasattr(client, 'search_tracks'):
                                    continue

                                attempted_any_provider = True
                                provider_matches = _search_tracks_for_source(source, search_query, limit=5, client=client)
                                time.sleep(0.5)  # Rate limit metadata API calls

                                if not provider_matches:
                                    continue

                                # Score each result using matching engine
                                for provider_track in provider_matches:
                                    try:
                                        # Calculate artist confidence
                                        artist_confidence = 0.0
                                        provider_artists = _track_artist_names(provider_track)
                                        if provider_artists:
                                            for result_artist in provider_artists:
                                                artist_sim = deps.matching_engine.similarity_score(
                                                    deps.matching_engine.normalize_string(artist_name),
                                                    deps.matching_engine.normalize_string(result_artist)
                                                )
                                                artist_confidence = max(artist_confidence, artist_sim)

                                        # Calculate title confidence
                                        title_confidence = deps.matching_engine.similarity_score(
                                            deps.matching_engine.normalize_string(title),
                                            deps.matching_engine.normalize_string(_track_name(provider_track))
                                        )

                                        # Combined confidence (50% artist + 50% title)
                                        combined_confidence = (artist_confidence * 0.5 + title_confidence * 0.5)

                                        # Small bonus for album tracks over singles
                                        _at = _extract_lookup_value(provider_track, 'album_type', default='') or ''
                                        if _at == 'album':
                                            combined_confidence += 0.02
                                        elif _at == 'ep':
                                            combined_confidence += 0.01

                                        candidate_artist = provider_artists[0] if provider_artists else 'Unknown Artist'
                                        candidate_name = _track_name(provider_track)
                                        logger.info(
                                            f"[Quality Scanner] Candidate ({source}): '{candidate_artist}' - "
                                            f"'{candidate_name}' (confidence: {combined_confidence:.3f})"
                                        )

                                        # Update best match if this is better
                                        if combined_confidence > best_confidence and combined_confidence >= min_confidence:
                                            best_confidence = combined_confidence
                                            best_match = provider_track
                                            best_source = source
                                            logger.info(
                                                f"[Quality Scanner] New best match ({source}): {candidate_artist} - "
                                                f"{candidate_name} (confidence: {combined_confidence:.3f})"
                                            )

                                    except Exception as e:
                                        logger.error(f"[Quality Scanner] Error scoring result: {e}")
                                        continue

                                # If we found a very high confidence match, stop searching this query
                                if best_confidence >= 0.9:
                                    logger.info(f"[Quality Scanner] High confidence match found ({best_confidence:.3f}), stopping search")
                                    break

                        except Exception as e:
                            logger.debug(f"[Quality Scanner] Error searching with query '{search_query}': {e}")
                            continue

                    if not attempted_any_provider:
                        with deps.quality_scanner_lock:
                            deps.quality_scanner_state["status"] = "error"
                            deps.quality_scanner_state["phase"] = "No metadata provider available"
                            deps.quality_scanner_state["error_message"] = "No metadata provider is available for quality scanning"
                        logger.info("[Quality Scanner] No metadata provider available")
                        return

                    # Process best match
                    if best_match:
                        matched = True
                        final_artist = _track_artist_names(best_match)[0] if _track_artist_names(best_match) else 'Unknown Artist'
                        final_name = _track_name(best_match)
                        final_source = best_source or 'metadata'
                        logger.info(
                            f"[Quality Scanner] Final match ({final_source}): {final_artist} - "
                            f"{final_name} (confidence: {best_confidence:.3f})"
                        )

                        # Build normalized track data for wishlist
                        matched_track_data = _normalize_track_match(best_match, final_source)

                        # Add to wishlist
                        source_context = {
                            'quality_scanner': True,
                            'original_file_path': file_path,
                            'original_format': tier_name,
                            'original_bitrate': bitrate,
                            'match_confidence': best_confidence,
                            'scan_date': datetime.now().isoformat()
                        }

                        success = add_to_wishlist(
                            track_data=matched_track_data,
                            failure_reason=f"Low quality - {tier_name.replace('_', ' ').title()} format",
                            source_type='quality_scanner',
                            source_context=source_context,
                            profile_id=profile_id
                        )

                        if success:
                            with deps.quality_scanner_lock:
                                deps.quality_scanner_state["matched"] += 1
                            logger.info(f"[Quality Scanner] Matched and added to wishlist: {artist_name} - {title}")
                        else:
                            logger.error(f"[Quality Scanner] Failed to add to wishlist: {artist_name} - {title}")
                    else:
                        logger.warning(
                            f"[Quality Scanner] No suitable metadata match found "
                            f"(best confidence: {best_confidence:.3f}, required: {min_confidence:.3f})"
                        )

                except Exception as matching_error:
                    logger.error(f"[Quality Scanner] Matching error for {artist_name} - {title}: {matching_error}")

                    # Store result
                    result_entry = {
                        'track_id': track_id,
                        'title': title,
                        'artist': artist_name,
                        'album': album_title,
                        'file_path': file_path,
                        'current_format': tier_name,
                        'bitrate': bitrate,
                        'matched': matched,
                        'match_id': matched_track_data['id'] if matched_track_data else None,
                        'provider': best_source if matched else None,
                        'spotify_id': matched_track_data['id'] if matched_track_data else None,
                    }

                    with deps.quality_scanner_lock:
                        deps.quality_scanner_state["results"].append(result_entry)

                    if not matched:
                        logger.warning(f"[Quality Scanner] No metadata match found for: {artist_name} - {title}")

            except Exception as track_error:
                logger.error(f"[Quality Scanner] Error processing track: {track_error}")
                continue

        # Scan complete (don't overwrite if already stopped by user)
        with deps.quality_scanner_lock:
            was_stopped = deps.quality_scanner_state["status"] != "running"
            deps.quality_scanner_state["status"] = "finished"
            deps.quality_scanner_state["progress"] = 100
            if not was_stopped:
                deps.quality_scanner_state["phase"] = "Scan complete"

        logger.info(f"[Quality Scanner] Scan {'stopped' if was_stopped else 'complete'}: {deps.quality_scanner_state['processed']} processed, "
              f"{deps.quality_scanner_state['low_quality']} low quality, {deps.quality_scanner_state['matched']} matched to metadata providers")

        # Add activity
        deps.add_activity_item("", "Quality Scan Complete",
                         f"{deps.quality_scanner_state['matched']} tracks added to wishlist", "Now")

        try:
            if deps.automation_engine:
                deps.automation_engine.emit('quality_scan_completed', {
                    'quality_met': str(deps.quality_scanner_state.get('quality_met', 0)),
                    'low_quality': str(deps.quality_scanner_state.get('low_quality', 0)),
                    'total_scanned': str(deps.quality_scanner_state.get('processed', 0)),
                })
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[Quality Scanner] Critical error: {e}")
        import traceback
        traceback.print_exc()

        with deps.quality_scanner_lock:
            deps.quality_scanner_state["status"] = "error"
            deps.quality_scanner_state["error_message"] = str(e)
            deps.quality_scanner_state["phase"] = f"Error: {str(e)}"
