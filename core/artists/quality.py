"""Artist quality enhancement helper.

`enhance_artist_quality(artist_id, track_ids, deps)` is the route-handler
body for the `/api/library/artist/<artist_id>/enhance` endpoint. It walks
the user's selected tracks, finds the best metadata match against the
configured primary source, and queues high-quality re-downloads on the
wishlist with `source_type='enhance'`.

Per-track flow:

1. Resolve the existing track via the artist's full detail map (built up
   front from `database.get_artist_full_detail`).
2. Read current quality tier from the file extension.
3. Build `matched_track_data` for the wishlist entry, in priority order:
   - **Direct lookup using stored source IDs** — for every source the
     user has configured, if the library track has the corresponding
     stored ID (`spotify_track_id` / `deezer_id` / `itunes_track_id` /
     `soul_id`), call `client.get_track_details(stored_id)` and convert
     the result to the wishlist payload. First success wins; the user's
     configured primary source is tried first. Mirrors what Download
     Discography does — stable IDs straight to the source's API, no
     fuzzy text matching.
   - **Multi-source parallel text search fallback** — if no stored ID
     resolved, run the shared `core.metadata.multi_source_search`
     against every configured source in parallel and pick the best
     cross-source match (auto-accept threshold 0.7).
4. Validate the match has non-empty title, album, and artists. Reject
   matches with empty fields — those propagated as
   "unknown artist - unknown album - unknown track" wishlist entries
   pre-fix because the wishlist payload normalizer's truthy-check
   passthrough accepted dicts with empty string fields.
5. Add to wishlist via `wishlist_service.add_spotify_track_to_wishlist`
   with `source_type='enhance'` and a `source_context` carrying the
   original file path, format tier, bitrate, and artist name.
6. Tally `enhanced_count` / `failed_count` / per-track failure reasons.

The flow originally had Spotify-only logic with an iTunes search-only
fallback. Two failure modes drove the rewrite:

- Users with neither Spotify nor Deezer connected got silent failures
  ("unknown artist - unknown album - unknown track" wishlist entries)
  because iTunes's text search returned junk matches with empty fields
  that cleared the 0.7 confidence threshold.
- Library tracks with messy tags ("Title (Live)", featured artists in
  the artist field, etc.) failed fuzzy text search even when a perfect
  stored ID was available — Download Discography had no such problem
  because it resolves albums by stable ID.

Direct-lookup-via-stored-ID matches the Download Discography contract
for every source where we have an ID column. Text search is only the
fallback now.

Returns `(payload_dict, http_status_code)` so the route wrapper can
`jsonify()` and return.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from utils.logging_config import get_logger

logger = get_logger('artists.quality')


@dataclass
class ArtistQualityDeps:
    """Bundle of cross-cutting deps the artist quality enhancement needs."""
    matching_engine: Any
    get_database: Callable[[], Any]
    get_wishlist_service: Callable[[], Any]
    get_current_profile_id: Callable[[], int]
    get_quality_tier_from_extension: Callable
    # Returns ``[(source_name, client), ...]`` for every metadata source
    # the user has configured. Powers both the direct-lookup fast path
    # (resolves stored source IDs straight from each source's API,
    # like Download Discography) and the multi-source parallel text
    # search fallback (shared with Track Redownload via
    # ``core.metadata.multi_source_search``).
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


# Map metadata source name → DB column on the ``tracks`` table that
# stores that source's native track ID. Used to drive the direct-lookup
# fast path: when a library track has a stored ID for source X and the
# user has source X configured, skip fuzzy text search and resolve
# straight from X's API. Mirrors what Download Discography does — stable
# IDs all the way, no fuzzy text matching.
#
# Discogs is release-based and has no per-track ID column; not listed
# here, so direct lookup never tries Discogs (search-fallback still
# runs for Discogs as one of the parallel sources).
_STORED_ID_COLUMNS = {
    'spotify': 'spotify_track_id',
    'deezer': 'deezer_id',
    'itunes': 'itunes_track_id',
    'hydrabase': 'soul_id',
}


def _enhanced_to_wishlist_payload(enhanced: dict,
                                   fallback_title: str,
                                   fallback_artist: str,
                                   fallback_album: str) -> Optional[dict]:
    """Convert a ``get_track_details`` enhanced-shape dict to the
    Spotify-shape wishlist payload.

    Every metadata source's ``get_track_details`` returns the same
    "enhanced" intermediate shape (top-level ``id``, ``name``,
    ``artists`` as a list of strings, ``album.artists`` as strings),
    documented and pinned across spotify_client / itunes_client /
    deezer_client / hydrabase_client. The wishlist downstream expects
    Spotify's native shape (``artists`` as ``[{'name': ...}]``), so
    this helper does the conversion in one place.

    Spotify's ``raw_data`` field is already in wishlist shape (the
    raw Spotify API response), so we return it as-is when detected,
    preserving full ``album.images`` and ``external_urls`` that the
    enhanced top-level fields drop. Other sources' ``raw_data`` is
    in source-native shape and gets ignored.
    """
    if not enhanced:
        return None
    raw = enhanced.get('raw_data')
    if isinstance(raw, dict):
        raw_artists = raw.get('artists')
        if (isinstance(raw_artists, list) and raw_artists
                and isinstance(raw_artists[0], dict)):
            return raw

    artists = enhanced.get('artists') or [fallback_artist]
    album_data = enhanced.get('album') or {}
    album_artists = album_data.get('artists') or artists

    def _to_dict_artists(seq):
        return [a if isinstance(a, dict) else {'name': a} for a in seq]

    image_url = enhanced.get('image_url') or ''
    album_images_field = album_data.get('images')
    if isinstance(album_images_field, list) and album_images_field:
        album_images = album_images_field
    elif image_url:
        album_images = [{'url': image_url, 'height': 600, 'width': 600}]
    else:
        album_images = []

    return {
        'id': str(enhanced.get('id', '')),
        'name': enhanced.get('name') or fallback_title,
        'artists': _to_dict_artists(artists),
        'album': {
            'id': str(album_data.get('id', '')),
            'name': album_data.get('name') or fallback_album,
            'album_type': album_data.get('album_type', 'album'),
            'release_date': album_data.get('release_date', ''),
            'total_tracks': album_data.get('total_tracks', 1),
            'artists': _to_dict_artists(album_artists),
            'images': album_images,
        },
        'duration_ms': enhanced.get('duration_ms', 0),
        'track_number': enhanced.get('track_number', 1),
        'disc_number': enhanced.get('disc_number', 1),
        'popularity': enhanced.get('popularity', 0),
        'preview_url': enhanced.get('preview_url'),
        'external_urls': enhanced.get('external_urls', {}),
    }


def _try_direct_lookup_all_sources(track: dict,
                                    sources: list,
                                    preferred_source: Optional[str],
                                    title: str,
                                    artist_name: str,
                                    album_title: str
                                    ) -> tuple:
    """Try direct ID-based lookup on every source where the library
    track has a stored ID. Returns ``(payload, source_name)`` on first
    success, or ``(None, None)`` if no source has a stored ID with a
    successful lookup.

    Mirrors what Download Discography does — stable IDs straight to the
    source's API, no fuzzy text matching. Avoids the failure mode where
    library text tags don't match the source's canonical title (the
    Discord report case: track tagged "Title (Live)" and source has
    "Title" → fuzzy search misses, but stored ID resolves directly).

    Preferred source attempted first when present in ``sources``,
    typically the user's configured primary metadata source — so a
    Deezer-primary user gets Deezer art / album shape on the wishlist
    entry instead of whichever source happened to have a stored ID
    first in iteration order.
    """
    def _priority(entry):
        name = entry[0]
        return 0 if name == preferred_source else 1
    ordered = sorted(sources, key=_priority)

    for source_name, client in ordered:
        column = _STORED_ID_COLUMNS.get(source_name)
        if not column:
            continue
        stored_id = track.get(column)
        if not stored_id:
            continue
        if not hasattr(client, 'get_track_details'):
            continue
        try:
            enhanced = client.get_track_details(str(stored_id))
        except Exception as exc:
            logger.error(
                f"[Enhance] {source_name} direct lookup failed for "
                f"ID {stored_id}: {exc}"
            )
            continue
        if not enhanced:
            continue
        payload = _enhanced_to_wishlist_payload(
            enhanced, title, artist_name, album_title,
        )
        if _has_complete_metadata(payload):
            logger.info(
                f"[Enhance] Direct lookup matched: {source_name} "
                f"ID {stored_id} → '{payload.get('name')}'"
            )
            return payload, source_name

    return None, None


# Minimum match-score threshold for accepting a search-fallback match
# without user confirmation. Mirrors the legacy threshold the enhance
# flow has always used.
_AUTO_ACCEPT_SCORE_THRESHOLD = 0.7


def enhance_artist_quality(artist_id, track_ids, deps: ArtistQualityDeps):
    """Add selected tracks to wishlist for quality enhancement re-download.

    Per-track flow:

    1. **Direct lookup using stored source IDs** (mirrors what Download
       Discography does — stable IDs straight to the source's API, no
       fuzzy text matching). For each source the user has configured,
       if the library track has the corresponding stored ID
       (``spotify_track_id`` / ``deezer_id`` / ``itunes_track_id`` /
       ``soul_id``), call ``client.get_track_details(stored_id)`` and
       convert to wishlist payload. First success wins; preferred
       source (user's configured primary) tried first.

    2. **Multi-source parallel text search fallback** (via the shared
       ``core.metadata.multi_source_search`` module — same code path
       Track Redownload uses) for tracks with no stored IDs / lookup
       misses.

    3. **Validation**: reject matches with empty title / album / artists
       so the user sees a clear failure instead of an "unknown artist"
       wishlist entry.

    Pre-refactor: only Spotify had a direct-lookup fast path; everything
    else went through fuzzy text search. Discogs / Hydrabase / Deezer-
    primary users got far worse coverage than Download Discography
    despite both flows asking the same question.
    """
    from core.metadata.multi_source_search import TrackQuery, search_all_sources
    from core.metadata.registry import get_primary_source

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

        # Resolve every configured metadata source up front.
        search_sources = deps.get_metadata_search_sources()

        # User's configured primary source — direct-lookup tries this
        # first so Deezer-primary users get Deezer payloads on the
        # wishlist entry (correct cover art / album shape) even when
        # other sources also have stored IDs for the same track.
        try:
            preferred_source = get_primary_source()
        except Exception:
            preferred_source = None

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

            # 1. Direct lookup via every stored source ID — like Download
            # Discography. Stable IDs, no fuzzy text matching.
            if search_sources:
                matched_track_data, chosen_source = _try_direct_lookup_all_sources(
                    track, search_sources, preferred_source,
                    title, artist_name, album_title,
                )

            # 2. Multi-source parallel text search fallback — for tracks
            # with no stored IDs / lookup misses.
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
