"""Artist quality enhancement helper.

`enhance_artist_quality(artist_id, track_ids, deps)` is the route-handler
body for the `/api/library/artist/<artist_id>/enhance` endpoint. It walks
the user's selected tracks, finds the best metadata match against the
configured primary source, and queues high-quality re-downloads on the
wishlist with `source_type='enhance'`.

Per-track flow (source-agnostic):

1. Resolve the existing track via the artist's full detail map (built up
   front from `database.get_artist_full_detail`).
2. Read current quality tier from the file extension.
3. Build `matched_track_data` for the wishlist entry, in priority order:
   - Direct Spotify lookup via stored `spotify_track_id` (only when
     Spotify is the active primary source — Spotify exposes
     `get_track_details(id)` returning rich raw data; other sources
     don't have an equivalent stored-ID-to-track API today).
   - Search match against the primary metadata source (Spotify /
     iTunes / Deezer / Discogs / Hydrabase — whichever the user has
     configured as their primary). Confidence threshold is 0.7.
4. Validate the match has non-empty title, album, and artists. Reject
   matches with empty fields — those propagated as
   "unknown artist - unknown album - unknown track" wishlist entries
   pre-fix because the wishlist payload normalizer's truthy-check
   passthrough accepted dicts with empty string fields.
5. Add to wishlist via `wishlist_service.add_spotify_track_to_wishlist`
   with `source_type='enhance'` and a `source_context` carrying the
   original file path, format tier, bitrate, and artist name.
6. Tally `enhanced_count` / `failed_count` / per-track failure reasons.

The flow originally had Spotify-only logic for steps 1 and 2 with iTunes
hardcoded as the only fallback. That broke for users with neither
Spotify nor Deezer connected — iTunes returned sparse / no matches and
the failure mode was silent. The Track Redownload modal had been doing
parallel multi-source search across every configured source the whole
time; enhance now uses the same shared module
(``core.metadata.multi_source_search``) so both endpoints dispatch
identically. Spotify keeps its direct-lookup fast-path optimization
(only Spotify exposes ``get_track_details(id)`` returning a rich
wishlist-shaped payload); when that doesn't fire, the multi-source
parallel search picks the cross-source best match.

Returns `(payload_dict, http_status_code)` so the route wrapper can
`jsonify()` and return.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

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
    # Returns ``[(source_name, client), ...]`` for every metadata source
    # the user has configured (not just the active primary). The Track
    # Redownload modal already searches multi-source in parallel —
    # Enhance Quality now matches that contract via the shared
    # ``core.metadata.multi_source_search`` module.
    get_metadata_search_sources: Callable[[], list]


def _has_complete_metadata(payload: Optional[dict]) -> bool:
    """Reject matches with empty / missing core fields. Pre-fix, iTunes
    returned matches that cleared the 0.7 confidence threshold while
    having empty artist / album / title — those propagated as junk
    wishlist entries displayed as 'unknown artist - unknown album -
    unknown track'."""
    if not payload:
        return False
    if not (payload.get('name') or '').strip():
        return False
    artists = payload.get('artists') or []
    has_artist = any(
        (a.get('name') or '').strip() if isinstance(a, dict) else (a or '').strip()
        for a in artists
    )
    if not has_artist:
        return False
    album = payload.get('album') or {}
    if isinstance(album, dict):
        if not (album.get('name') or '').strip():
            return False
    elif not (album or '').strip():
        return False
    return True


def _build_payload_from_track(track_obj) -> dict:
    """Build a Spotify-shaped wishlist payload from any metadata source's
    Track-shaped object (Spotify Track, iTunes Track, Deezer Track,
    Discogs Track — they all have the same .id / .name / .artists /
    .album / .duration_ms / etc shape because each client mimics
    Spotify's surface).

    The wishlist's downstream pipeline expects Spotify shape; this helper
    is the single place that knows how to produce it. Replaces the
    duplicated payload construction that used to live in the Spotify
    search path AND the iTunes fallback path.

    Does NOT substitute defaults for missing artists / album / title —
    ``_has_complete_metadata`` rejects empty matches downstream so the
    user sees a clear failure instead of a junk wishlist entry with
    fabricated values.
    """
    image_url = getattr(track_obj, 'image_url', '') or ''
    album_images = (
        [{'url': image_url, 'height': 600, 'width': 600}]
        if image_url else []
    )
    artist_names = list(getattr(track_obj, 'artists', None) or [])
    return {
        'id': getattr(track_obj, 'id', ''),
        'name': getattr(track_obj, 'name', '') or '',
        'artists': [{'name': a} for a in artist_names],
        'album': {
            'name': getattr(track_obj, 'album', '') or '',
            'artists': [{'name': a} for a in artist_names],
            'album_type': getattr(track_obj, 'album_type', None) or 'album',
            'images': album_images,
            'release_date': getattr(track_obj, 'release_date', '') or '',
            'total_tracks': 1,
        },
        'duration_ms': getattr(track_obj, 'duration_ms', 0) or 0,
        'track_number': getattr(track_obj, 'track_number', None) or 1,
        'disc_number': getattr(track_obj, 'disc_number', None) or 1,
        'popularity': getattr(track_obj, 'popularity', None) or 0,
        'preview_url': getattr(track_obj, 'preview_url', None),
        'external_urls': getattr(track_obj, 'external_urls', None) or {},
    }


def _spotify_direct_lookup(spotify_client, spotify_tid: str,
                           fallback_artist_name: str,
                           fallback_album_name: str,
                           fallback_title: str) -> Optional[dict]:
    """Spotify-only direct-lookup optimization. Spotify's
    ``get_track_details(id)`` returns rich `raw_data` already in the
    wishlist payload shape; other sources don't have an equivalent
    stored-ID-to-track API today, so we fall through to search for
    them.
    """
    try:
        track_details = spotify_client.get_track_details(spotify_tid)
        if not track_details:
            return None
        if track_details.get('raw_data'):
            return track_details['raw_data']
        # Enhanced format — rebuild with images
        album_data = track_details.get('album', {})
        album_images = []
        if album_data.get('id'):
            try:
                full_album = spotify_client.get_album(album_data['id'])
                if full_album and full_album.get('images'):
                    album_images = full_album['images']
            except Exception:
                pass
        return {
            'id': spotify_tid,
            'name': track_details.get('name', fallback_title),
            'artists': [
                {'name': a}
                for a in track_details.get('artists', [fallback_artist_name])
            ],
            'album': {
                'id': album_data.get('id', ''),
                'name': album_data.get('name', fallback_album_name),
                'album_type': album_data.get('album_type', 'album'),
                'release_date': album_data.get('release_date', ''),
                'total_tracks': album_data.get('total_tracks', 1),
                'artists': [
                    {'name': a}
                    for a in album_data.get('artists', [fallback_artist_name])
                ],
                'images': album_images,
            },
            'duration_ms': track_details.get('duration_ms', 0),
            'track_number': track_details.get('track_number', 1),
            'disc_number': track_details.get('disc_number', 1),
            'popularity': 0,
            'preview_url': None,
            'external_urls': {},
        }
    except Exception as exc:
        logger.error(f"[Enhance] Spotify direct lookup failed for {spotify_tid}: {exc}")
        return None


# Minimum match-score threshold for accepting a match without user
# confirmation. Mirrors the legacy single-source threshold the enhance
# flow has always used.
_AUTO_ACCEPT_SCORE_THRESHOLD = 0.7


def _try_upgrade_to_rich_payload(client: Any, track_id: str) -> Optional[dict]:
    """Spotify-only optimization — ``get_track_details(id)`` returns
    rich raw_data already in the wishlist payload shape. Other sources
    don't expose this; caller falls back to ``_build_payload_from_track``.
    """
    if not hasattr(client, 'get_track_details'):
        return None
    try:
        details = client.get_track_details(track_id)
        if details and details.get('raw_data'):
            return details['raw_data']
    except Exception:
        pass
    return None


def enhance_artist_quality(artist_id, track_ids, deps: ArtistQualityDeps):
    """Add selected tracks to wishlist for quality enhancement re-download.

    Per-track flow now mirrors the Track Redownload modal: searches every
    configured metadata source in parallel via the shared
    ``core.metadata.multi_source_search`` module, picks the best cross-source
    match if it clears the auto-accept threshold, validates the match
    has non-empty fields, and queues the wishlist payload.

    Pre-refactor this was Spotify-only with an iTunes fallback —
    Discogs / Hydrabase / Deezer-primary users got far worse coverage
    than redownload despite both flows asking the same question
    ("find me the metadata for this library track").
    """
    from core.metadata.multi_source_search import TrackQuery, search_all_sources

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

        # Resolve every configured metadata source up front — the same
        # parallel multi-source search the Track Redownload modal uses.
        search_sources = deps.get_metadata_search_sources()

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
            album_title = track.get('_album_title', '')

            matched_track_data = None
            chosen_source = None

            # 1. Spotify-only direct-lookup optimization. If Spotify is
            # configured AND we have a stored Spotify ID, fast path
            # straight to the rich raw payload (no search required).
            if deps.spotify_client:
                spotify_tid = track.get('spotify_track_id')
                if spotify_tid:
                    matched_track_data = _spotify_direct_lookup(
                        deps.spotify_client, spotify_tid,
                        artist_name, album_title, title,
                    )
                    if matched_track_data:
                        chosen_source = 'spotify'

            # 2. Multi-source parallel search across every configured
            # metadata source. Picks the highest-scoring match across
            # all sources (Spotify / iTunes / Deezer / Discogs /
            # Hydrabase — whichever the user has configured).
            if not matched_track_data and search_sources:
                try:
                    track_query = TrackQuery(
                        title=title,
                        artist=artist_name,
                        album=album_title,
                        duration_ms=track.get('duration', 0) or 0,
                        spotify_track_id=track.get('spotify_track_id'),
                        deezer_id=track.get('deezer_id'),
                    )
                    multi_result = search_all_sources(track_query, search_sources)
                    if multi_result.best_match and multi_result.best_match['score'] >= _AUTO_ACCEPT_SCORE_THRESHOLD:
                        chosen_source = multi_result.best_match['source']
                        best_track_obj = multi_result.best_track()
                        if best_track_obj:
                            # Try Spotify's rich-payload upgrade first; fall
                            # back to building from the source-native Track.
                            client = next(
                                (c for n, c in search_sources if n == chosen_source),
                                None,
                            )
                            if client:
                                matched_track_data = _try_upgrade_to_rich_payload(
                                    client, str(best_track_obj.id),
                                )
                            if not matched_track_data:
                                matched_track_data = _build_payload_from_track(best_track_obj)
                except Exception as exc:
                    logger.error(f"[Enhance] Multi-source search failed for {title}: {exc}")

            # 3. Reject matches with empty / missing core fields.
            if not _has_complete_metadata(matched_track_data):
                if matched_track_data:
                    logger.warning(
                        f"[Enhance] {chosen_source} match for '{title}' rejected — "
                        f"empty title / album / artists (would render as 'unknown')"
                    )
                matched_track_data = None

            if not matched_track_data:
                failed_count += 1
                source_list = ', '.join(name for name, _ in (search_sources or []))
                if not source_list:
                    reason = (
                        'No metadata source configured — connect Spotify / '
                        'iTunes / Deezer / Discogs / Hydrabase to enable enhance'
                    )
                else:
                    reason = (
                        f'No usable match across {source_list} — '
                        f'try connecting an additional metadata source'
                    )
                failed_tracks.append({
                    'track_id': track_id,
                    'title': title,
                    'reason': reason,
                })
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
