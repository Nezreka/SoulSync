"""Background worker for mirrored playlist track discovery.

`run_playlist_discovery_worker(playlists, automation_id, deps)` is the
function the automation engine schedules to enrich undiscovered mirrored
playlist tracks with Spotify (preferred) or iTunes (fallback) metadata:

1. Pause enrichment workers and pre-compute total track count for the
   automation progress card.
2. For each playlist:
   - Fast pre-scan separates already-discovered tracks (skipped, unless
     incomplete metadata or a Wing It stub) from undiscovered ones.
   - For each undiscovered track:
     - Cancellation gate.
     - Discovery cache lookup (with artist validation).
     - matching_engine search-query generation, then Spotify/iTunes
       search + scoring across queries.
     - Extended search fallback (limit=50) if no high-confidence match.
     - On match → enrich album from metadata cache, build matched_data,
       store in track.extra_data, save discovery cache entry.
     - On miss → Wing It stub stored as 'wing_it_fallback' provider.
3. After all playlists: emit `discovery_completed` event when at least
   one new track was discovered, mark automation progress 'finished'.
4. On error → automation progress 'error', traceback printed.
5. Finally: resume enrichment workers.

Lifted verbatim from web_server.py. Wide dependency surface (Spotify
and iTunes clients, matching engine, discovery helpers, DB access,
automation engine, cancellation set) all injected via
`PlaylistDiscoveryDeps`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PlaylistDiscoveryDeps:
    """Bundle of cross-cutting deps the playlist discovery worker needs."""
    spotify_client: Any
    matching_engine: Any
    automation_engine: Any
    playlist_discovery_cancelled: set
    pause_enrichment_workers: Callable[[str], dict]
    resume_enrichment_workers: Callable[[dict, str], None]
    get_active_discovery_source: Callable[[], str]
    get_metadata_fallback_client: Callable[[], Any]
    get_metadata_fallback_source: Callable[[], str]
    update_automation_progress: Callable
    get_database: Callable[[], Any]
    get_discovery_cache_key: Callable
    validate_discovery_cache_artist: Callable
    discovery_score_candidates: Callable
    get_metadata_cache: Callable[[], Any]
    build_discovery_wing_it_stub: Callable


def run_playlist_discovery_worker(playlists, automation_id=None, deps: PlaylistDiscoveryDeps = None):
    """Background worker that discovers Spotify/iTunes metadata for undiscovered
    mirrored playlist tracks. Stores results in extra_data for use by sync."""
    _ew_state = {}
    try:
        _ew_state = deps.pause_enrichment_workers('mirrored playlist discovery')
        discovery_source = deps.get_active_discovery_source()
        use_spotify = (discovery_source == 'spotify') and deps.spotify_client and deps.spotify_client.is_spotify_authenticated()

        itunes_client_instance = None
        if not use_spotify:
            try:
                itunes_client_instance = deps.get_metadata_fallback_client()
            except Exception:
                logger.warning(f"Neither Spotify nor {deps.get_metadata_fallback_source()} available for discovery")
                deps.update_automation_progress(automation_id, status='error', progress=100,
                                            phase='Error', log_line=f'Neither Spotify nor {deps.get_metadata_fallback_source()} available',
                                            log_type='error')
                return

        total_discovered = 0
        total_failed = 0
        total_skipped = 0
        total_tracks = 0
        last_playlist_name = ''

        # Pre-compute grand total for progress tracking
        grand_total = 0
        db_init = deps.get_database()
        for pl in playlists:
            t = db_init.get_mirrored_playlist_tracks(pl['id'])
            if t:
                grand_total += len(t)
        deps.update_automation_progress(automation_id, total=grand_total)

        for pl in playlists:
            pl_id = pl['id']
            pl_name = pl.get('name', '')
            last_playlist_name = pl_name
            source = pl.get('source', '')

            db = deps.get_database()
            tracks = db.get_mirrored_playlist_tracks(pl_id)
            if not tracks:
                continue

            logger.info(f"Starting discovery for playlist '{pl_name}' ({len(tracks)} tracks, using {discovery_source.upper()})")
            deps.update_automation_progress(automation_id, phase=f'Discovering: "{pl_name}"',
                                         log_line=f'Playlist "{pl_name}" — {len(tracks)} tracks ({discovery_source.upper()})', log_type='info')

            # Fast pre-scan: separate already-discovered from undiscovered
            undiscovered_tracks = []
            pl_skipped = 0
            for track in tracks:
                existing_extra = {}
                if track.get('extra_data'):
                    try:
                        existing_extra = json.loads(track['extra_data']) if isinstance(track['extra_data'], str) else track['extra_data']
                    except (json.JSONDecodeError, TypeError):
                        pass
                if existing_extra.get('discovered'):
                    if existing_extra.get('wing_it_fallback'):
                        # Wing It stub — always re-attempt to find a real match
                        undiscovered_tracks.append(track)
                    else:
                        # Check if matched_data is complete — old discoveries may be missing
                        # track_number/release_date due to the Track dataclass stripping them.
                        # Re-discover these so the enriched pipeline fills in the gaps.
                        md = existing_extra.get('matched_data', {})
                        album = md.get('album', {})
                        has_track_num = md.get('track_number')
                        has_release = album.get('release_date') if isinstance(album, dict) else None
                        has_album_id = album.get('id') if isinstance(album, dict) else None
                        if has_track_num and (has_release or has_album_id):
                            pl_skipped += 1
                            total_skipped += 1
                        else:
                            # Incomplete discovery — re-discover to get full metadata
                            undiscovered_tracks.append(track)
                elif existing_extra.get('unmatched_by_user'):
                    # User explicitly removed this match — respect their choice
                    pl_skipped += 1
                    total_skipped += 1
                else:
                    undiscovered_tracks.append(track)

            if pl_skipped > 0:
                deps.update_automation_progress(automation_id,
                    log_line=f'{pl_skipped} tracks already discovered — skipped', log_type='skip')

            if not undiscovered_tracks:
                deps.update_automation_progress(automation_id,
                    progress=((total_skipped + total_discovered + total_failed) / max(1, grand_total)) * 100,
                    log_line=f'All {len(tracks)} tracks already discovered', log_type='skip')
                continue

            deps.update_automation_progress(automation_id,
                log_line=f'{len(undiscovered_tracks)} tracks to discover', log_type='info')

            for i, track in enumerate(undiscovered_tracks):
                # Check for cancellation
                if automation_id and automation_id in deps.playlist_discovery_cancelled:
                    deps.playlist_discovery_cancelled.discard(automation_id)
                    logger.warning(f"Playlist discovery cancelled (automation {automation_id})")
                    deps.update_automation_progress(automation_id, status='finished', progress=100,
                                                 phase='Discovery cancelled',
                                                 log_line=f'Cancelled: {total_discovered} discovered, {total_failed} failed',
                                                 log_type='info')
                    return

                total_tracks += 1
                track_id = track['id']
                track_name = track.get('track_name', '')
                artist_name = track.get('artist_name', '')
                duration_ms = track.get('duration_ms', 0)

                # Step 1: Check discovery cache
                cache_key = deps.get_discovery_cache_key(track_name, artist_name)
                try:
                    cached_match = db.get_discovery_cache_match(cache_key[0], cache_key[1], discovery_source)
                    if cached_match and deps.validate_discovery_cache_artist(artist_name, cached_match):
                        extra_data = {
                            'discovered': True,
                            'provider': discovery_source,
                            'confidence': cached_match.get('confidence', 0.85),
                            'matched_data': cached_match,
                        }
                        db.update_mirrored_track_extra_data(track_id, extra_data)
                        total_discovered += 1
                        logger.info(f"CACHE [{i+1}/{len(undiscovered_tracks)}]: {track_name} → {cached_match.get('name', '?')}")
                        deps.update_automation_progress(automation_id,
                            progress=((total_skipped + total_discovered + total_failed) / max(1, grand_total)) * 100,
                            current_item=track_name,
                            log_line=f'{track_name} → {cached_match.get("name", "?")} (cache)', log_type='success')
                        continue
                except Exception as e:
                    logger.debug("discovery cache lookup failed: %s", e)

                # Step 2: Generate search queries
                try:
                    temp_track = type('TempTrack', (), {
                        'name': track_name,
                        'artists': [artist_name],
                        'album': None
                    })()
                    search_queries = deps.matching_engine.generate_download_queries(temp_track)
                except Exception:
                    search_queries = [f"{artist_name} {track_name}", track_name]

                # Step 3: Search and score
                best_match = None
                best_confidence = 0.0
                min_confidence = 0.7

                for search_query in search_queries:
                    try:
                        if use_spotify:
                            results = deps.spotify_client.search_tracks(search_query, limit=10)
                        else:
                            results = itunes_client_instance.search_tracks(search_query, limit=10)
                        if not results:
                            continue

                        match, confidence, _ = deps.discovery_score_candidates(
                            track_name, artist_name, duration_ms, results
                        )

                        if match and confidence > best_confidence:
                            best_confidence = confidence
                            best_match = match

                        if best_confidence >= 0.9:
                            break
                    except Exception:
                        continue

                # Extended search fallback
                if not best_match or best_confidence < min_confidence:
                    try:
                        query = f"{artist_name} {track_name}"
                        if use_spotify:
                            extended = deps.spotify_client.search_tracks(query, limit=50)
                        else:
                            extended = itunes_client_instance.search_tracks(query, limit=50)
                        if extended:
                            match, confidence, _ = deps.discovery_score_candidates(
                                track_name, artist_name, duration_ms, extended
                            )
                            if match and confidence > best_confidence:
                                best_confidence = confidence
                                best_match = match
                    except Exception as e:
                        logger.debug("extended discovery search failed: %s", e)

                # Step 4: Store results
                if best_match and best_confidence >= min_confidence:
                    match_artists = best_match.artists if hasattr(best_match, 'artists') else []
                    match_image = getattr(best_match, 'image_url', None)
                    album_name = best_match.album if hasattr(best_match, 'album') else ''
                    album_obj = {'name': album_name, 'release_date': getattr(best_match, 'release_date', '') or ''}
                    if match_image:
                        album_obj['images'] = [{'url': match_image, 'height': 600, 'width': 600}]

                    # Enrich album data from metadata cache — search_tracks() caches the
                    # raw API response which has full album info (id, images, total_tracks)
                    # that the Track dataclass strips to just a name string
                    track_number = None
                    disc_number = None
                    if hasattr(best_match, 'id') and best_match.id:
                        try:
                            cache = deps.get_metadata_cache()
                            _raw = cache.get_entity(discovery_source if not use_spotify else 'spotify', 'track', best_match.id)
                            if _raw and isinstance(_raw.get('album'), dict):
                                _raw_album = _raw['album']
                                if _raw_album.get('id'):
                                    album_obj['id'] = _raw_album['id']
                                if _raw_album.get('images') and not album_obj.get('images'):
                                    album_obj['images'] = _raw_album['images']
                                if _raw_album.get('total_tracks'):
                                    album_obj['total_tracks'] = _raw_album['total_tracks']
                                if _raw_album.get('album_type'):
                                    album_obj['album_type'] = _raw_album['album_type']
                                if _raw_album.get('release_date') and not album_obj.get('release_date'):
                                    album_obj['release_date'] = _raw_album['release_date']
                                if _raw_album.get('artists'):
                                    album_obj['artists'] = _raw_album['artists']
                            if _raw:
                                track_number = _raw.get('track_number')
                                disc_number = _raw.get('disc_number')
                        except Exception as e:
                            logger.debug("metadata cache lookup for album enrichment failed: %s", e)

                    matched_data = {
                        'id': best_match.id if hasattr(best_match, 'id') else '',
                        'name': best_match.name if hasattr(best_match, 'name') else '',
                        'artists': [{'name': a} if isinstance(a, str) else a for a in match_artists],
                        'album': album_obj,
                        'duration_ms': best_match.duration_ms if hasattr(best_match, 'duration_ms') else 0,
                        'image_url': match_image,
                        'source': discovery_source,
                    }
                    if track_number:
                        matched_data['track_number'] = track_number
                    if disc_number:
                        matched_data['disc_number'] = disc_number

                    extra_data = {
                        'discovered': True,
                        'provider': discovery_source,
                        'confidence': best_confidence,
                        'matched_data': matched_data,
                    }
                    db.update_mirrored_track_extra_data(track_id, extra_data)
                    total_discovered += 1

                    # Save to discovery cache
                    try:
                        db.save_discovery_cache_match(
                            cache_key[0], cache_key[1], discovery_source,
                            best_confidence, matched_data,
                            track_name, artist_name
                        )
                    except Exception as e:
                        logger.debug("save discovery cache match failed: %s", e)

                    logger.info(f"[{i+1}/{len(undiscovered_tracks)}] {track_name} → {matched_data['name']} ({best_confidence:.2f})")
                    deps.update_automation_progress(automation_id,
                        progress=((total_skipped + total_discovered + total_failed) / max(1, grand_total)) * 100,
                        processed=total_discovered + total_failed,
                        current_item=f'{track_name} - {artist_name}',
                        log_line=f'{track_name} → {matched_data["name"]} ({best_confidence:.2f})', log_type='success')
                else:
                    # Auto Wing It fallback — mark as discovered with stub metadata
                    stub = deps.build_discovery_wing_it_stub(track_name, artist_name, duration_ms)
                    extra_data = {
                        'discovered': True,
                        'provider': 'wing_it_fallback',
                        'confidence': 0,
                        'wing_it_fallback': True,
                        'matched_data': stub,
                    }
                    db.update_mirrored_track_extra_data(track_id, extra_data)
                    total_discovered += 1
                    logger.info(f"[{i+1}/{len(undiscovered_tracks)}] Wing It: {track_name} by {artist_name}")
                    deps.update_automation_progress(automation_id,
                        progress=((total_skipped + total_discovered + total_failed) / max(1, grand_total)) * 100,
                        processed=total_discovered + total_failed,
                        current_item=f'{track_name} - {artist_name}',
                        log_line=f'{track_name} by {artist_name} → wing it (no API match)', log_type='info')

                time.sleep(0.15)

        # Emit completion event only if new tracks were actually discovered
        # (no point triggering downstream sync if nothing changed)
        try:
            if deps.automation_engine and total_discovered > 0:
                _disc_pl_id = str(playlists[0]['id']) if len(playlists) == 1 else ''
                deps.automation_engine.emit('discovery_completed', {
                    'playlist_name': last_playlist_name if len(playlists) == 1 else f'{len(playlists)} playlists',
                    'playlist_id': _disc_pl_id,
                    'total_tracks': str(total_tracks),
                    'discovered_count': str(total_discovered),
                    'failed_count': str(total_failed),
                    'skipped_count': str(total_skipped),
                })
        except Exception as e:
            logger.debug("discovery_completed emit failed: %s", e)

        logger.error(f"Playlist discovery complete: {total_discovered} discovered, {total_failed} failed, {total_skipped} skipped")
        deps.update_automation_progress(automation_id, status='finished', progress=100,
                                     phase='Discovery complete',
                                     log_line=f'Done: {total_discovered} discovered, {total_failed} failed, {total_skipped} skipped',
                                     log_type='success')

    except Exception as e:
        logger.error(f"Error in playlist discovery worker: {e}")
        import traceback
        traceback.print_exc()
        deps.update_automation_progress(automation_id, status='error', progress=100,
                                     phase='Error',
                                     log_line=f'Error: {str(e)}', log_type='error')
    finally:
        deps.resume_enrichment_workers(_ew_state, 'mirrored playlist discovery')
