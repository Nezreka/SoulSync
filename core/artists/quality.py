"""Artist quality enhancement helper.

`enhance_artist_quality(artist_id, track_ids, deps)` is the route-handler
body for the `/api/library/artist/<artist_id>/enhance` endpoint. It walks
the user's selected tracks, finds the best Spotify (preferred) or iTunes
(fallback) match for each, and queues high-quality re-downloads on the
wishlist with `source_type='enhance'`.

Per-track flow:

1. Resolve the existing track via the artist's full detail map (built up
   front from `database.get_artist_full_detail`).
2. Read current quality tier from the file extension.
3. Build `matched_track_data` for the wishlist entry, in priority order:
   - Direct Spotify lookup via stored `spotify_track_id` (preferred).
   - Spotify search fallback using matching_engine queries.
   - iTunes/fallback source search.
4. Add to wishlist via `wishlist_service.add_spotify_track_to_wishlist`
   with `source_type='enhance'` and a `source_context` carrying the
   original file path, format tier, bitrate, and artist name.
5. Tally `enhanced_count` / `failed_count` / per-track failure reasons.

Returns `(payload_dict, http_status_code)` so the route wrapper can
`jsonify()` and return.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ArtistQualityDeps:
    """Bundle of cross-cutting deps the artist quality enhancement needs."""
    spotify_client: Any
    matching_engine: Any
    get_database: Callable[[], Any]
    get_wishlist_service: Callable[[], Any]
    get_current_profile_id: Callable[[], int]
    get_quality_tier_from_extension: Callable
    get_metadata_fallback_client: Callable[[], Any]


def enhance_artist_quality(artist_id, track_ids, deps: ArtistQualityDeps):
    """Add selected tracks to wishlist for quality enhancement re-download."""
    try:
        if not track_ids:
            return {"success": False, "error": "No track IDs provided"}, 400

        database = deps.get_database()
        wishlist_service = deps.get_wishlist_service()
        profile_id = deps.get_current_profile_id()

        # Get artist info
        artist_result = database.get_artist_full_detail(artist_id)
        if not artist_result.get('success'):
            return {"success": False, "error": "Artist not found"}, 404

        artist_name = artist_result.get('artist', {}).get('name', 'Unknown Artist')

        # Build lookup of all tracks for this artist
        track_lookup = {}
        for album in artist_result.get('albums', []):
            album_title = album.get('title', '')
            for track in album.get('tracks', []):
                tid = str(track.get('id', ''))
                track['_album_title'] = album_title
                track['_album_id'] = album.get('id')
                track_lookup[tid] = track

        enhanced_count = 0
        failed_count = 0
        failed_tracks = []

        for track_id in track_ids:
            track_id_str = str(track_id)
            track = track_lookup.get(track_id_str)
            if not track:
                failed_count += 1
                failed_tracks.append({'track_id': track_id, 'reason': 'Track not found'})
                continue

            file_path = track.get('file_path')
            if not file_path:
                failed_count += 1
                failed_tracks.append({'track_id': track_id, 'reason': 'No file path'})
                continue

            tier_name, tier_num = deps.get_quality_tier_from_extension(file_path)
            title = track.get('title', '') or ''
            if not title.strip():
                title = os.path.splitext(os.path.basename(file_path))[0]
            spotify_tid = track.get('spotify_track_id')

            # Build Spotify track data for wishlist
            matched_track_data = None

            if spotify_tid and deps.spotify_client:
                # Direct lookup via stored Spotify ID — raw_data has full Spotify API format
                try:
                    track_details = deps.spotify_client.get_track_details(spotify_tid)
                    if track_details and track_details.get('raw_data'):
                        matched_track_data = track_details['raw_data']
                    elif track_details:
                        # Enhanced format — rebuild with images for wishlist compatibility
                        album_data = track_details.get('album', {})
                        album_images = []
                        # Try to get album art from a full album lookup
                        if album_data.get('id'):
                            try:
                                full_album = deps.spotify_client.get_album(album_data['id'])
                                if full_album and full_album.get('images'):
                                    album_images = full_album['images']
                            except Exception:
                                pass
                        matched_track_data = {
                            'id': spotify_tid,
                            'name': track_details.get('name', title),
                            'artists': [{'name': a} for a in track_details.get('artists', [artist_name])],
                            'album': {
                                'id': album_data.get('id', ''),
                                'name': album_data.get('name', track.get('_album_title', '')),
                                'album_type': album_data.get('album_type', 'album'),
                                'release_date': album_data.get('release_date', ''),
                                'total_tracks': album_data.get('total_tracks', 1),
                                'artists': [{'name': a} for a in album_data.get('artists', [artist_name])],
                                'images': album_images,
                            },
                            'duration_ms': track_details.get('duration_ms', track.get('duration', 0)),
                            'track_number': track_details.get('track_number', track.get('track_number', 1)),
                            'disc_number': track_details.get('disc_number', 1),
                            'popularity': 0,
                            'preview_url': None,
                            'external_urls': {},
                        }
                except Exception as e:
                    logger.error(f"[Enhance] Spotify lookup failed for {spotify_tid}: {e}")

            if not matched_track_data and deps.spotify_client:
                # Fallback: Spotify search matching — need full track data for wishlist
                try:
                    temp_track = type('TempTrack', (), {
                        'name': title, 'artists': [artist_name],
                        'album': track.get('_album_title', '')
                    })()
                    search_queries = deps.matching_engine.generate_download_queries(temp_track)
                    best_match = None
                    best_match_raw = None
                    best_confidence = 0.0

                    for search_query in search_queries[:3]:  # Limit queries
                        try:
                            results = deps.spotify_client.search_tracks(search_query, limit=5)
                            if not results:
                                continue
                            for sp_track in results:
                                artist_conf = max(
                                    (deps.matching_engine.similarity_score(
                                        deps.matching_engine.normalize_string(artist_name),
                                        deps.matching_engine.normalize_string(a)
                                    ) for a in (sp_track.artists or [artist_name])),
                                    default=0
                                )
                                title_conf = deps.matching_engine.similarity_score(
                                    deps.matching_engine.normalize_string(title),
                                    deps.matching_engine.normalize_string(sp_track.name)
                                )
                                combined = artist_conf * 0.5 + title_conf * 0.5
                                # Small bonus for album tracks over singles
                                _at = getattr(sp_track, 'album_type', None) or ''
                                if _at == 'album':
                                    combined += 0.02
                                elif _at == 'ep':
                                    combined += 0.01
                                if combined > best_confidence and combined >= 0.7:
                                    best_confidence = combined
                                    best_match = sp_track
                            if best_confidence >= 0.9:
                                break
                        except Exception:
                            continue

                    if best_match:
                        # Fetch full track data from Spotify for proper wishlist format
                        try:
                            full_details = deps.spotify_client.get_track_details(best_match.id)
                            if full_details and full_details.get('raw_data'):
                                matched_track_data = full_details['raw_data']
                            else:
                                raise ValueError("No raw_data from get_track_details")
                        except Exception:
                            # Build from Track dataclass with image
                            album_images = [{'url': best_match.image_url}] if best_match.image_url else []
                            matched_track_data = {
                                'id': best_match.id,
                                'name': best_match.name,
                                'artists': [{'name': a} for a in best_match.artists],
                                'album': {
                                    'name': best_match.album,
                                    'artists': [{'name': a} for a in best_match.artists],
                                    'album_type': 'album',
                                    'release_date': getattr(best_match, 'release_date', '') or '',
                                    'images': album_images,
                                },
                                'duration_ms': best_match.duration_ms,
                                'popularity': best_match.popularity or 0,
                                'preview_url': best_match.preview_url,
                                'external_urls': best_match.external_urls or {},
                            }
                except Exception as e:
                    logger.error(f"[Enhance] Search match failed for {title}: {e}")

            # Fallback source when Spotify unavailable or no match found
            if not matched_track_data:
                try:
                    fallback_client = deps.get_metadata_fallback_client()
                    itunes_best = None
                    itunes_best_conf = 0.0

                    itunes_queries = deps.matching_engine.generate_download_queries(
                        type('TempTrack', (), {
                            'name': title, 'artists': [artist_name],
                            'album': track.get('_album_title', '')
                        })()
                    )

                    for search_query in itunes_queries[:3]:
                        try:
                            itunes_results = fallback_client.search_tracks(search_query, limit=5)
                            if not itunes_results:
                                continue
                            for it_track in itunes_results:
                                artist_conf = max(
                                    (deps.matching_engine.similarity_score(
                                        deps.matching_engine.normalize_string(artist_name),
                                        deps.matching_engine.normalize_string(a)
                                    ) for a in (it_track.artists or [artist_name])),
                                    default=0
                                )
                                title_conf = deps.matching_engine.similarity_score(
                                    deps.matching_engine.normalize_string(title),
                                    deps.matching_engine.normalize_string(it_track.name)
                                )
                                combined = artist_conf * 0.5 + title_conf * 0.5
                                # Small bonus for album tracks over singles
                                _at = getattr(it_track, 'album_type', None) or ''
                                if _at == 'album':
                                    combined += 0.02
                                elif _at == 'ep':
                                    combined += 0.01
                                if combined > itunes_best_conf and combined >= 0.7:
                                    itunes_best_conf = combined
                                    itunes_best = it_track
                            if itunes_best_conf >= 0.9:
                                break
                        except Exception:
                            continue

                    if itunes_best:
                        album_images = [{'url': itunes_best.image_url, 'height': 600, 'width': 600}] if itunes_best.image_url else []
                        matched_track_data = {
                            'id': itunes_best.id,
                            'name': itunes_best.name,
                            'artists': [{'name': a} for a in itunes_best.artists],
                            'album': {
                                'name': itunes_best.album,
                                'artists': [{'name': a} for a in itunes_best.artists],
                                'album_type': 'album',
                                'images': album_images,
                                'release_date': itunes_best.release_date or '',
                                'total_tracks': 1,
                            },
                            'duration_ms': itunes_best.duration_ms,
                            'track_number': itunes_best.track_number or 1,
                            'disc_number': itunes_best.disc_number or 1,
                            'popularity': itunes_best.popularity or 0,
                            'preview_url': itunes_best.preview_url,
                            'external_urls': itunes_best.external_urls or {},
                        }
                        logger.warning(f"[Enhance] Fallback match for {title}: {itunes_best.artists[0]} - {itunes_best.name} (conf: {itunes_best_conf:.3f})")
                except Exception as e:
                    logger.error(f"[Enhance] Fallback source failed for {title}: {e}")

            if not matched_track_data:
                failed_count += 1
                failed_tracks.append({'track_id': track_id, 'title': title, 'reason': 'No Spotify or fallback match'})
                continue

            # Add to wishlist with enhance source
            source_context = {
                'enhance': True,
                'original_file_path': file_path,
                'original_format': tier_name,
                'original_bitrate': track.get('bitrate'),
                'original_tier': tier_num,
                'artist_name': artist_name,
            }

            success = wishlist_service.add_spotify_track_to_wishlist(
                spotify_track_data=matched_track_data,
                failure_reason=f"Quality enhance - upgrading from {tier_name.replace('_', ' ').title()}",
                source_type='enhance',
                source_context=source_context,
                profile_id=profile_id
            )

            if success:
                enhanced_count += 1
                logger.info(f"[Enhance] Queued for upgrade: {artist_name} - {title} ({tier_name})")
            else:
                failed_count += 1
                failed_tracks.append({'track_id': track_id, 'title': title, 'reason': 'Wishlist add failed'})

        return {
            'success': True,
            'enhanced_count': enhanced_count,
            'failed_count': failed_count,
            'failed_tracks': failed_tracks
        }, 200
    except Exception as e:
        logger.error(f"[Enhance] {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}, 500
