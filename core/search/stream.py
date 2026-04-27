"""Single-track stream search — finds the best Soulseek result for a track
play preview.

Builds a small ordered list of search query variants (artist+title,
artist+cleaned title; or title-only when the stream source is Soulseek
itself) and walks them until one returns a usable match through the
matching engine.

Stream source resolution:
- If `download_source.stream_source` is "youtube" (default), use the
  YouTube downloader for previews — instant, no auth pressure on the
  download stack.
- If it's "active", mirror the user's download mode (tidal / qobuz /
  hifi / deezer_dl / lidarr) — but coerce Soulseek to YouTube because
  Soulseek is too slow for streaming previews.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _resolve_effective_stream_mode(config_manager) -> str:
    """Pick the streaming source based on settings."""
    stream_source = config_manager.get('download_source.stream_source', 'youtube')
    download_mode = config_manager.get('download_source.mode', 'hybrid')

    if stream_source == 'youtube':
        return 'youtube'

    hybrid_order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])
    hybrid_first = hybrid_order[0] if hybrid_order else config_manager.get('download_source.hybrid_primary', 'hifi')

    if download_mode == 'soulseek' or (download_mode == 'hybrid' and hybrid_first == 'soulseek'):
        logger.info("Stream source is 'active' but primary is Soulseek — falling back to YouTube")
        return 'youtube'
    if download_mode == 'hybrid':
        return hybrid_first
    return download_mode


def _build_stream_queries(track_name: str, artist_name: str, effective_mode: str) -> list[str]:
    """Build an ordered, deduped list of search queries to try."""
    queries: list[str] = []

    is_streaming_source = effective_mode in ('youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr')

    if is_streaming_source:
        if artist_name and track_name:
            queries.append(f"{artist_name} {track_name}".strip())

        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()
        if cleaned_name and cleaned_name.lower() != track_name.lower():
            queries.append(f"{artist_name} {cleaned_name}".strip())
    else:
        if track_name.strip():
            queries.append(track_name.strip())
        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()
        if cleaned_name and cleaned_name.lower() != track_name.lower():
            queries.append(cleaned_name.strip())

    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        if q and q.lower() not in seen:
            deduped.append(q)
            seen.add(q.lower())
    return deduped


def _result_to_dict(best_result) -> dict:
    return {
        "username": best_result.username,
        "filename": best_result.filename,
        "size": best_result.size,
        "bitrate": best_result.bitrate,
        "duration": best_result.duration,
        "quality": best_result.quality,
        "free_upload_slots": best_result.free_upload_slots,
        "upload_speed": best_result.upload_speed,
        "queue_length": best_result.queue_length,
        "result_type": "track",
    }


def stream_search_track(
    *,
    track_name: str,
    artist_name: str,
    album_name: Optional[str],
    duration_ms: int,
    config_manager,
    soulseek_client,
    matching_engine,
    run_async: Callable,
) -> Optional[dict]:
    """Find the best Soulseek/stream-source result for a single track.

    Returns the matched result dict on success, or `None` if no query
    variant produced a usable match. The route layer turns `None` into a
    404 response.
    """
    temp_track = type('TempTrack', (), {
        'name': track_name,
        'artists': [artist_name],
        'album': album_name if album_name else None,
        'duration_ms': duration_ms,
    })()

    effective_mode = _resolve_effective_stream_mode(config_manager)
    logger.info(f"Stream source effective mode: {effective_mode}")

    queries = _build_stream_queries(track_name, artist_name, effective_mode)

    stream_clients = {
        'youtube': getattr(soulseek_client, 'youtube', None),
        'tidal': getattr(soulseek_client, 'tidal', None),
        'qobuz': getattr(soulseek_client, 'qobuz', None),
        'hifi': getattr(soulseek_client, 'hifi', None),
        'deezer_dl': getattr(soulseek_client, 'deezer_dl', None),
        'lidarr': getattr(soulseek_client, 'lidarr', None),
    }
    stream_client = stream_clients.get(effective_mode)
    use_direct_client = stream_client is not None

    max_peer_queue = config_manager.get('soulseek.max_peer_queue', 0) or 0

    for query_index, query in enumerate(queries):
        logger.info(f"Stream query {query_index + 1}/{len(queries)}: '{query}'")
        try:
            if use_direct_client:
                tracks_result, _ = run_async(stream_client.search(query, timeout=15))
            else:
                tracks_result, _ = run_async(soulseek_client.search(query, timeout=15))

            if not tracks_result:
                logger.info(f"No results for query '{query}', trying next...")
                continue

            best_matches = matching_engine.find_best_slskd_matches_enhanced(
                temp_track, tracks_result, max_peer_queue=max_peer_queue
            )
            if best_matches:
                best = best_matches[0]
                logger.info(f"Stream match for '{query}': {best.filename} ({best.quality})")
                return _result_to_dict(best)

            logger.info(f"No suitable matches for query '{query}', trying next...")
        except Exception as e:
            logger.warning(f"Stream search failed for query '{query}': {e}")
            continue

    logger.warning(f"No stream match found after {len(queries)} queries")
    return None
