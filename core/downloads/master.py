"""Master worker for the missing-tracks download workflow.

`run_full_missing_tracks_process(batch_id, playlist_id, tracks_json, deps)` is
the single 580-line worker that orchestrates the entire pipeline:

  1. PHASE 1 — Analysis: per-track DB ownership check, with album fast path
     (lookup album by name+artist, match tracks within it) plus a
     MusicBrainz release-cache preflight so per-track post-processing all
     uses the same release MBID (prevents Navidrome album splits).
  2. Wishlist removal for tracks already in the library.
  3. Explicit-content filter.
  4. PHASE 2 transition — if nothing missing, mark batch complete, update
     per-source playlist phases, kick auto-wishlist completion handler.
  5. Soulseek album pre-flight — search for a complete album folder before
     falling back to track-by-track search, cache the source for reuse.
  6. Wishlist album grouping — derive per-album disc counts and resolve
     ONE artist context per album so collab albums don't fold-split.
  7. Task creation with explicit album/artist context injection.
  8. Hand off to download monitor + start_next_batch_of_downloads.

Lifted verbatim from web_server.py. Wide dependency surface (config, MB
caches, Soulseek client, source-page state dicts, multiple helper funcs)
all injected via `MasterDeps`.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from core.downloads import album_bundle_dispatch as _album_bundle_dispatch
from core.runtime_state import download_batches, download_tasks, tasks_lock

logger = logging.getLogger(__name__)


_ALBUM_PREFLIGHT_MIN_SCORE = 0.62
_EDITION_WORDS = {
    'deluxe', 'expanded', 'anniversary', 'special', 'platinum', 'bonus',
    'remaster', 'remastered', 'edition', 'version',
}
_VARIANT_WORDS = {
    'remix', 'rmx', 'acapella', 'a cappella', 'instrumental', 'karaoke',
    'live', 'demo', 'extended',
}
_ALBUM_BUNDLE_SOURCES = frozenset(('torrent', 'usenet', 'soulseek'))


def _norm_text(value: Any) -> str:
    text = str(value or '').lower()
    text = re.sub(r'[_./\\|()[\]{}:;,+]', ' ', text)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _similarity(left: Any, right: Any) -> float:
    a = _norm_text(left)
    b = _norm_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def _track_title_from_candidate(candidate: Any) -> str:
    title = getattr(candidate, 'title', None)
    if title:
        return str(title)
    filename = getattr(candidate, 'filename', '') or ''
    stem = Path(filename.replace('\\', '/')).stem
    stem = re.sub(r'^\s*(?:disc\s*)?\d+[-_.\s]+', '', stem, flags=re.IGNORECASE)
    return stem


def _track_number_from_track(track_data: dict) -> int:
    value = track_data.get('track_number') or track_data.get('trackNumber') or 0
    try:
        return int(str(value).split('/')[0])
    except (TypeError, ValueError):
        return 0


def _track_number_from_candidate(candidate: Any) -> int:
    value = getattr(candidate, 'track_number', None) or 0
    try:
        return int(str(value).split('/')[0])
    except (TypeError, ValueError):
        return 0


def _folder_variant_penalty(expected_album_name: str, folder_text: str) -> float:
    expected = _norm_text(expected_album_name)
    folder = _norm_text(folder_text)
    if not folder:
        return 0.0

    penalty = 0.0
    for word in _VARIANT_WORDS:
        if word in folder and word not in expected:
            penalty += 0.12
    for word in _EDITION_WORDS:
        if word in folder and word not in expected:
            penalty += 0.06
    return min(penalty, 0.30)


def _source_quality_score(source: Any) -> float:
    score = getattr(source, 'quality_score', None)
    if callable(score):
        try:
            return float(score())
        except Exception:
            return 0.0
    try:
        return float(score or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _album_context_richness(album_ctx: dict) -> int:
    if not isinstance(album_ctx, dict):
        return 0
    fields = ('id', 'name', 'release_date', 'total_tracks', 'album_type')
    score = sum(1 for field in fields if album_ctx.get(field))
    images = album_ctx.get('images')
    if images:
        score += 1
    artists = album_ctx.get('artists')
    if artists:
        score += 1
    return score


def _score_album_folder(album_result: Any, album_context: dict, artist_context: dict,
                        tracks_json: list[dict], filtered_track_count: int) -> float:
    """Score one slskd folder as a whole release, not as isolated tracks."""
    expected_album = str((album_context or {}).get('name') or '')
    expected_artist = str((artist_context or {}).get('name') or '')
    expected_count = int((album_context or {}).get('total_tracks') or len(tracks_json) or 0)
    expected_year = str((album_context or {}).get('release_date') or '')[:4]

    folder_text = ' '.join(
        str(getattr(album_result, attr, '') or '')
        for attr in ('album_title', 'album_path')
    )
    album_score = max(
        _similarity(expected_album, getattr(album_result, 'album_title', '')),
        _similarity(expected_album, getattr(album_result, 'album_path', '')),
    )
    artist_score = max(
        _similarity(expected_artist, getattr(album_result, 'artist', '')),
        _similarity(expected_artist, getattr(album_result, 'album_path', '')),
    )

    actual_count = int(getattr(album_result, 'track_count', 0) or len(getattr(album_result, 'tracks', []) or []))
    if expected_count > 0 and actual_count > 0:
        diff = abs(actual_count - expected_count)
        if diff == 0:
            count_score = 1.0
        elif diff <= 2:
            count_score = 0.75
        elif diff <= 5:
            count_score = 0.35
        else:
            count_score = 0.0
    else:
        count_score = 0.4

    candidate_tracks = list(getattr(album_result, 'tracks', []) or [])
    matched = 0
    expected_tracks = [
        (track_data, _norm_text(track_data.get('name', '')))
        for track_data in tracks_json
        if track_data.get('name')
    ]
    for track_data, expected_title in expected_tracks:
        expected_number = _track_number_from_track(track_data)
        best = 0.0
        for candidate in candidate_tracks:
            cand_title = _norm_text(_track_title_from_candidate(candidate))
            title_sim = _similarity(expected_title, cand_title)
            cand_number = _track_number_from_candidate(candidate)
            if expected_number and cand_number and expected_number == cand_number:
                title_sim = min(1.0, title_sim + 0.12)
            best = max(best, title_sim)
        if best >= 0.72:
            matched += 1
    coverage_score = matched / max(1, len(expected_tracks))

    year_score = 0.5
    folder_year = str(getattr(album_result, 'year', '') or '')
    if expected_year and folder_year:
        year_score = 1.0 if expected_year == folder_year else 0.2
    elif expected_year and expected_year in _norm_text(folder_text):
        year_score = 1.0

    quality_count_score = min(1.0, filtered_track_count / max(1, expected_count or actual_count or 1))
    peer_score = _source_quality_score(album_result)
    penalty = _folder_variant_penalty(expected_album, folder_text)

    score = (
        album_score * 0.24
        + artist_score * 0.16
        + count_score * 0.16
        + coverage_score * 0.28
        + year_score * 0.06
        + quality_count_score * 0.06
        + peer_score * 0.04
        - penalty
    )
    return max(0.0, min(score, 1.0))


def _resolve_soulseek_client(download_orchestrator: Any) -> Any:
    if hasattr(download_orchestrator, 'client'):
        try:
            client = download_orchestrator.client('soulseek')
            if client:
                return client
        except Exception as exc:
            logger.debug("Soulseek client lookup through orchestrator failed: %s", exc)
    return getattr(download_orchestrator, 'soulseek', download_orchestrator)


def _soulseek_album_preflight_enabled(config_manager: Any) -> bool:
    mode = config_manager.get('download_source.mode', 'hybrid')
    if mode == 'soulseek':
        return True
    if mode != 'hybrid':
        return False
    order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])
    if order:
        return order[0] == 'soulseek'
    primary = config_manager.get('download_source.hybrid_primary', '')
    return primary == 'soulseek'


def _resolve_album_bundle_source(config_manager: Any) -> str:
    """Return the album-bundle source for this batch.

    In single-source mode, the active source may own the whole album if
    it supports album bundles. In hybrid mode, only the first source in
    the configured order may claim the whole album; later sources remain
    per-track fallback.
    """
    mode = (config_manager.get('download_source.mode', 'soulseek') or 'soulseek').lower()
    if mode in _ALBUM_BUNDLE_SOURCES:
        return mode
    if mode != 'hybrid':
        return ''

    order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])
    first = ''
    if order:
        first = str(order[0] or '').lower()
    else:
        first = str(config_manager.get('download_source.hybrid_primary', '') or '').lower()
    return first if first in _ALBUM_BUNDLE_SOURCES else ''


@dataclass
class MasterDeps:
    """Bundle of cross-cutting deps the master worker needs."""
    config_manager: Any
    download_orchestrator: Any
    run_async: Callable[..., Any]
    mb_worker: Any
    mb_release_cache: dict
    mb_release_cache_lock: Any
    mb_release_detail_cache: dict
    mb_release_detail_cache_lock: Any
    normalize_album_cache_key: Callable[[str], str]
    check_and_remove_track_from_wishlist_by_metadata: Callable
    is_explicit_blocked: Callable
    youtube_playlist_states: dict
    tidal_discovery_states: dict
    deezer_discovery_states: dict
    spotify_public_discovery_states: dict
    missing_download_executor: Any
    process_failed_tracks_to_wishlist_exact_with_auto_completion: Callable
    source_reuse_logger: Any
    download_monitor: Any
    start_next_batch_of_downloads: Callable[[str], None]
    reset_wishlist_auto_processing: Callable[[], None]


class _BatchStateAccessImpl:
    """Concrete ``BatchStateAccess`` for the runtime ``download_batches``
    dict — wraps the lock + the existing-batch check so the album-
    bundle dispatcher stays decoupled from runtime_state."""

    def update_fields(self, batch_id: str, fields: dict) -> None:
        with tasks_lock:
            row = download_batches.get(batch_id)
            if row is not None:
                row.update(fields)

    def mark_failed(self, batch_id: str, error: str) -> None:
        with tasks_lock:
            row = download_batches.get(batch_id)
            if row is not None:
                row['phase'] = 'failed'
                row['error'] = error
                row['album_bundle_state'] = 'failed'


# Task states that mean a batch still has work in flight. While ANY of a batch's
# tasks is in one of these, a serialized album-pool worker keeps its slot.
_NON_TERMINAL_TASK_STATUSES = ('pending', 'queued', 'searching', 'downloading', 'post_processing')


def _wait_for_batch_drain(batch_id: str, poll_seconds: float = 1.5,
                          max_wait_seconds: float = 3600.0) -> None:
    """Block until every task in ``batch_id`` reaches a terminal state (the batch
    is fully drained), the batch is removed, shutdown is requested, or a safety
    cap elapses.

    Used to make the dedicated album-bundle pool actually SERIALIZE albums: the
    worker holds its pool slot for the album's whole lifetime instead of
    returning the instant downloads are started. That stops every album from
    dumping its tracks into the shared download pool at once (Sokhi: "searching
    for way too many tracks at once"). It's a PASSIVE wait — the downloads are
    driven by the monitor + completion callbacks on other threads, so this never
    drives the work and can't deadlock; worst case the cap releases the slot and
    the downloads simply finish in the background."""
    from core.downloads import monitor as _monitor
    start = time.time()
    while True:
        if getattr(_monitor, 'IS_SHUTTING_DOWN', False):
            return
        with tasks_lock:
            batch = download_batches.get(batch_id)
            if not batch:
                return
            queue = list(batch.get('queue', ()) or ())
            still_working = any(
                download_tasks.get(t, {}).get('status') in _NON_TERMINAL_TASK_STATUSES
                for t in queue
            )
        if not still_working:
            return
        if time.time() - start > max_wait_seconds:
            logger.warning(
                "[Album Serialize] batch %s not drained after %.0fs — releasing the "
                "album-pool slot (its downloads continue in the background)",
                batch_id, max_wait_seconds)
            return
        time.sleep(poll_seconds)


def run_full_missing_tracks_process(batch_id, playlist_id, tracks_json, deps: MasterDeps,
                                    serialize: bool = False):
    """
    A master worker that handles the entire missing tracks process:
    1. Runs the analysis.
    2. If missing tracks are found, it automatically queues them for download.
    """
    try:
        # PHASE 1: ANALYSIS
        with tasks_lock:
            if batch_id in download_batches:
                download_batches[batch_id]['phase'] = 'analysis'
                download_batches[batch_id]['analysis_total'] = len(tracks_json)
                download_batches[batch_id]['analysis_processed'] = 0

        from database.music_database import MusicDatabase
        from core.library import manual_library_match as _mlm
        db = MusicDatabase()
        active_server = deps.config_manager.get_active_media_server()
        analysis_results = []

        # Get force download flag and album context from batch
        force_download_all = False
        ignore_manual_matches = False
        batch_album_context = None
        batch_artist_context = None
        batch_is_album = False
        batch_profile_id = 1
        batch_source = 'spotify'
        batch_playlist_folder_mode = False
        batch_playlist_name = 'Unknown Playlist'
        batch_playlist_id = playlist_id
        batch_source_playlist_ref = ''
        # Issue #797 — per-request "Skip AcoustID verification" toggle from
        # the album-download modal. When set, every track in this batch
        # bypasses the AcoustID quarantine gate (the user has chosen to
        # trust the metadata over fingerprint disagreement — useful for
        # non-English artists whose native-script metadata AcoustID can't
        # reconcile with the romanized request).
        batch_skip_acoustid = False
        with tasks_lock:
            if batch_id in download_batches:
                force_download_all = download_batches[batch_id].get('force_download_all', False)
                ignore_manual_matches = download_batches[batch_id].get('ignore_manual_matches', False)
                batch_is_album = download_batches[batch_id].get('is_album_download', False)
                batch_album_context = download_batches[batch_id].get('album_context')
                batch_artist_context = download_batches[batch_id].get('artist_context')
                batch_profile_id = download_batches[batch_id].get('profile_id', 1) or 1
                batch_source = download_batches[batch_id].get('batch_source', 'spotify') or 'spotify'
                batch_playlist_folder_mode = download_batches[batch_id].get('playlist_folder_mode', False)
                batch_playlist_name = download_batches[batch_id].get('playlist_name', 'Unknown Playlist')
                batch_playlist_id = download_batches[batch_id].get('playlist_id', playlist_id)
                batch_source_playlist_ref = (
                    download_batches[batch_id].get('source_playlist_ref') or ''
                ).strip()
                batch_skip_acoustid = bool(download_batches[batch_id].get('skip_acoustid', False))

        from core.downloads.playlist_folder import (
            resolve_playlist_folder_mode_for_batch,
            track_exists_in_playlist_folder_from_track_data,
        )
        effective_playlist_folder_mode, effective_playlist_name = resolve_playlist_folder_mode_for_batch(
            db,
            playlist_id=str(batch_playlist_id),
            playlist_name=batch_playlist_name,
            batch_playlist_folder_mode=batch_playlist_folder_mode,
            profile_id=batch_profile_id,
            source=batch_source,
        )
        if effective_playlist_folder_mode and not batch_playlist_folder_mode:
            with tasks_lock:
                if batch_id in download_batches:
                    download_batches[batch_id]['playlist_folder_mode'] = True
                    download_batches[batch_id]['playlist_name'] = effective_playlist_name

        if force_download_all:
            logger.warning(f"[Force Download] Force download mode enabled for batch {batch_id} - treating all tracks as missing")

        # Allow duplicate tracks across albums — when enabled, only skip tracks already
        # owned in THIS album, not tracks owned in other albums
        allow_duplicates = deps.config_manager.get('wishlist.allow_duplicate_tracks', True)
        if allow_duplicates and batch_is_album:
            logger.info("[Duplicates] Allow duplicate tracks enabled — only checking ownership within target album")

        # PREFLIGHT: Pre-populate MusicBrainz release cache for album downloads.
        # This ensures ALL tracks in the album use the same release MBID during
        # per-track post-processing, preventing Navidrome album splits.
        if batch_is_album and batch_album_context and batch_artist_context:
            try:
                album_name_pf = batch_album_context.get('name', '')
                artist_name_pf = batch_artist_context.get('name', '')
                if album_name_pf and artist_name_pf:
                    mb_svc = deps.mb_worker.mb_service if deps.mb_worker else None
                    if mb_svc:
                        from core.album_consistency import _find_best_release
                        release = _find_best_release(album_name_pf, artist_name_pf, len(tracks_json), mb_svc)
                        if release and release.get('id'):
                            release_mbid = release['id']
                            _artist_key = artist_name_pf.lower().strip()
                            _rc_key_norm = (deps.normalize_album_cache_key(album_name_pf), _artist_key)
                            _rc_key_exact = (album_name_pf.lower().strip(), _artist_key)
                            with deps.mb_release_cache_lock:
                                deps.mb_release_cache[_rc_key_norm] = release_mbid
                                deps.mb_release_cache[_rc_key_exact] = release_mbid
                            # Also cache the full release detail for tag extraction
                            with deps.mb_release_detail_cache_lock:
                                deps.mb_release_detail_cache[release_mbid] = release
                            logger.info(f"[Preflight] Pre-cached MB release for '{album_name_pf}': "
                                  f"'{release.get('title', '')}' ({release_mbid[:8]}...)")
                        else:
                            logger.warning(f"[Preflight] No MB release found for '{album_name_pf}' — per-track lookup will be used")
            except Exception as pf_err:
                logger.error(f"[Preflight] MB release preflight failed: {pf_err}")

        # ALBUM FAST PATH: If this is an album download, try to find the album in the DB first
        # and match tracks within it — faster and more accurate than N global searches
        album_tracks_map = {}  # Maps normalized title -> DatabaseTrack for album-scoped matching
        if batch_is_album and batch_album_context and batch_artist_context and not force_download_all:
            album_name = batch_album_context.get('name', '')
            artist_name = batch_artist_context.get('name', '')
            total_tracks = batch_album_context.get('total_tracks', 0)
            if album_name and artist_name:
                try:
                    db_album, album_confidence = db.check_album_exists_with_editions(
                        title=album_name, artist=artist_name,
                        confidence_threshold=0.7,
                        expected_track_count=total_tracks if total_tracks > 0 else None,
                        server_source=active_server
                    )
                    if db_album and album_confidence >= 0.7:
                        db_album_tracks = db.get_tracks_by_album(db_album.id)
                        for t in db_album_tracks:
                            album_tracks_map[t.title.lower().strip()] = t
                        logger.info(f"[Album Analysis] Found album '{db_album.title}' in DB with {len(db_album_tracks)} tracks (confidence: {album_confidence:.2f})")
                    else:
                        logger.warning(f"[Album Analysis] Album '{album_name}' not found in DB — falling back to per-track search")
                except Exception as album_err:
                    logger.error(f"[Album Analysis] Album lookup error: {album_err} — falling back to per-track search")

        for i, track_data in enumerate(tracks_json):
            # Use original table index if provided (for partial track selection),
            # otherwise fall back to enumeration index
            track_index = track_data.get('_original_index', i)
            track_name = track_data.get('name', '')
            artists = track_data.get('artists', [])
            found, confidence = False, 0.0

            # Manual library matches are authoritative unless the user explicitly
            # requested a force re-download from the normal download modal.
            _stid = track_data.get('spotify_track_id') or track_data.get('source_track_id') or track_data.get('id', '')
            if not ignore_manual_matches and _stid and _mlm.get_match_for_track(
                db, batch_profile_id, track_data, default_source=batch_source
            ):
                logger.info(f"[Manual Match] '{track_name}' already matched in library — skipping download")
                try:
                    deps.check_and_remove_track_from_wishlist_by_metadata(track_data)
                except Exception as _wl_err:
                    logger.debug(f"[Manual Match] Wishlist removal attempt failed: {_wl_err}")
                analysis_results.append({
                    'track_index': track_index,
                    'track': track_data,
                    'found': True,
                    'confidence': 1.0,
                    'match_reason': 'manual_library_match',
                })
                continue

            if effective_playlist_folder_mode and not force_download_all:
                if track_exists_in_playlist_folder_from_track_data(
                    effective_playlist_name,
                    track_data,
                ):
                    logger.info(
                        f"[Playlist Folder] '{track_name}' already on disk in playlist folder — skipping download"
                    )
                    try:
                        deps.check_and_remove_track_from_wishlist_by_metadata(track_data)
                    except Exception as _wl_err:
                        logger.debug(f"[Playlist Folder] Wishlist removal attempt failed: {_wl_err}")
                    analysis_results.append({
                        'track_index': track_index,
                        'track': track_data,
                        'found': True,
                        'confidence': 1.0,
                        'match_reason': 'playlist_folder_file',
                    })
                    continue

            # Skip database check if force download is enabled
            if force_download_all:
                logger.warning(f"[Force Download] Skipping database check for '{track_name}' - treating as missing")
                found, confidence = False, 0.0
            elif album_tracks_map:
                # Album-scoped matching: check against known album tracks first
                track_name_lower = track_name.lower().strip()
                # Issue #589 — strip suffixes that just repeat the album
                # context (e.g. "Shy Away (MTV Unplugged Live)" on a
                # "MTV Unplugged" album → "Shy Away") so album-owned
                # tracks don't false-miss when the local DB stored the
                # base title. Only fires inside the album-confirmed
                # scope; global matching elsewhere is unchanged.
                from core.matching.album_context_title import strip_redundant_album_suffix
                _album_name_for_strip = (batch_album_context or {}).get('name', '')
                _normalized_source_title = strip_redundant_album_suffix(
                    track_name, _album_name_for_strip
                ).lower().strip()
                # Direct title match (try both raw and normalized)
                if track_name_lower in album_tracks_map:
                    found, confidence = True, 1.0
                elif _normalized_source_title and _normalized_source_title in album_tracks_map:
                    found, confidence = True, 1.0
                else:
                    # Fuzzy match against album tracks using string similarity.
                    # Compare BOTH the raw and normalized source titles —
                    # whichever scores higher wins. Preserves strict
                    # matching when the album doesn't imply version
                    # context (helper returns the input unchanged).
                    best_sim = 0.0
                    for db_title_lower, _db_track in album_tracks_map.items():
                        sim_raw = db._string_similarity(track_name_lower, db_title_lower)
                        sim_norm = db._string_similarity(_normalized_source_title, db_title_lower) if _normalized_source_title else 0.0
                        sim = max(sim_raw, sim_norm)
                        if sim > best_sim:
                            best_sim = sim
                    if best_sim >= 0.7:
                        found, confidence = True, best_sim
                    else:
                        # Fall back to global per-track search for this track
                        # When allow_duplicates is on for album downloads, skip global
                        # search — the track isn't in THIS album so treat as missing
                        if allow_duplicates and batch_is_album:
                            found, confidence = False, 0.0
                        else:
                            _fallback_album = batch_album_context.get('name') if batch_album_context else None
                            for artist in artists:
                                if isinstance(artist, str):
                                    artist_name = artist
                                elif isinstance(artist, dict) and 'name' in artist:
                                    artist_name = artist['name']
                                else:
                                    artist_name = str(artist)
                                db_track, track_confidence = db.check_track_exists(
                                    track_name, artist_name, confidence_threshold=0.7, server_source=active_server, album=_fallback_album
                                )
                                if db_track and track_confidence >= 0.7:
                                    found, confidence = True, track_confidence
                                    break
            elif allow_duplicates and batch_is_album:
                # Allow duplicates + album download + album not in DB yet → treat all as missing
                found, confidence = False, 0.0
            else:
                # Non-album download (playlist/single track) — always check global
                for artist in artists:
                    # Handle both string format and Spotify API format {'name': 'Artist Name'}
                    if isinstance(artist, str):
                        artist_name = artist
                    elif isinstance(artist, dict) and 'name' in artist:
                        artist_name = artist['name']
                    else:
                        artist_name = str(artist)
                    db_track, track_confidence = db.check_track_exists(
                        track_name, artist_name, confidence_threshold=0.7, server_source=active_server
                    )
                    if db_track and track_confidence >= 0.7:
                        found, confidence = True, track_confidence
                        break

            analysis_results.append({
                'track_index': track_index, 'track': track_data, 'found': found, 'confidence': confidence
            })
            
            # WISHLIST REMOVAL: If track is found in database, check if it should be removed from wishlist
            if found and confidence >= 0.7:
                try:
                    deps.check_and_remove_track_from_wishlist_by_metadata(track_data)
                except Exception as wishlist_error:
                    logger.error(f"[Analysis] Error checking wishlist removal for found track: {wishlist_error}")

            with tasks_lock:
                if batch_id in download_batches:
                    download_batches[batch_id]['analysis_processed'] = i + 1
                    # Store incremental results for live updates
                    download_batches[batch_id]['analysis_results'] = analysis_results.copy()

        missing_tracks = [res for res in analysis_results if not res['found']]

        # Filter explicit tracks if content filter is enabled
        if not deps.config_manager.get('content_filter.allow_explicit', True):
            before_count = len(missing_tracks)
            missing_tracks = [res for res in missing_tracks if not deps.is_explicit_blocked(res.get('track', {}))]
            skipped = before_count - len(missing_tracks)
            if skipped > 0:
                logger.warning(f"[Content Filter] Filtered out {skipped} explicit track(s) from download queue")

        # Blocklist (Phase 2a): drop banned artists/albums/tracks before queueing,
        # so a blocked item can't slip in via playlist sync / album download /
        # discography. Same ID-cascade brain as the wishlist guard (Phase 1) —
        # the only other auto-acquisition path. Skipped when the user confirmed
        # "download anyway" at the modal (Phase 2b override).
        _ignore_blocklist = False
        with tasks_lock:
            if batch_id in download_batches:
                _ignore_blocklist = download_batches[batch_id].get('ignore_blocklist', False)
        if not _ignore_blocklist:
            try:
                _bl_before = len(missing_tracks)
                _bl_kept = []
                for res in missing_tracks:
                    reason = db.blocklist_reason_for_track(
                        batch_profile_id, res.get('track', {}), source=batch_source)
                    if reason:
                        logger.info("[Blocklist] Skipping %s '%s' from download queue (%s blocked)",
                                    reason[0], res.get('track', {}).get('name', '?'), reason[0])
                    else:
                        _bl_kept.append(res)
                if len(_bl_kept) != _bl_before:
                    logger.info("[Blocklist] Filtered out %d blocklisted track(s) from download queue",
                                _bl_before - len(_bl_kept))
                missing_tracks = _bl_kept
            except Exception as _bl_err:
                logger.debug("blocklist queue filter skipped: %s", _bl_err)

        with tasks_lock:
            if batch_id in download_batches:
                download_batches[batch_id]['analysis_results'] = analysis_results

        # PHASE 2: TRANSITION TO DOWNLOAD (if necessary)
        if not missing_tracks:
            logger.warning(f"Analysis for batch {batch_id} complete. No missing tracks.")

            # Record sync history — all tracks found, nothing to download
            tracks_found = sum(1 for r in analysis_results if r.get('found'))
            try:
                db_sh = MusicDatabase()
                db_sh.update_sync_history_completion(batch_id, tracks_found=tracks_found, tracks_downloaded=0, tracks_failed=0)
                # Save per-track results (all found, no downloads)
                track_results = []
                for res in analysis_results:
                    td = res.get('track', {})
                    artists = td.get('artists', [])
                    first_artist = (artists[0].get('name', artists[0]) if isinstance(artists[0], dict) else str(artists[0])) if artists else ''
                    alb = td.get('album', '')
                    # Extract image
                    _img = ''
                    _alb_obj = td.get('album', {})
                    if isinstance(_alb_obj, dict):
                        _alb_imgs = _alb_obj.get('images', [])
                        if _alb_imgs and isinstance(_alb_imgs, list) and len(_alb_imgs) > 0:
                            _img = _alb_imgs[0].get('url', '') if isinstance(_alb_imgs[0], dict) else ''
                    track_results.append({
                        'index': res.get('track_index', 0),
                        'name': td.get('name', ''),
                        'artist': first_artist,
                        'album': alb.get('name', '') if isinstance(alb, dict) else str(alb or ''),
                        'image_url': _img,
                        'duration_ms': td.get('duration_ms', 0),
                        'source_track_id': td.get('id', ''),
                        'status': 'found' if res.get('found') else 'not_found',
                        'confidence': round(res.get('confidence', 0.0), 3),
                        'matched_track': None,
                        'download_status': None,
                    })
                if track_results:
                    db_sh.update_sync_history_track_results(batch_id, json.dumps(track_results))
            except Exception as e:
                logger.debug("update sync_history track results failed: %s", e)

            is_auto_batch = False
            with tasks_lock:
                if batch_id in download_batches:
                    is_auto_batch = download_batches[batch_id].get('auto_initiated', False)
                    download_batches[batch_id]['phase'] = 'complete'
                    download_batches[batch_id]['completion_time'] = time.time()  # Track for auto-cleanup

                    # Update YouTube playlist phase to 'download_complete' if this is a YouTube playlist
                    if playlist_id.startswith('youtube_'):
                        url_hash = playlist_id.replace('youtube_', '')
                        if url_hash in deps.youtube_playlist_states:
                            deps.youtube_playlist_states[url_hash]['phase'] = 'download_complete'
                            logger.warning(f"Updated YouTube playlist {url_hash} to download_complete phase (no missing tracks)")

                    # Update Tidal playlist phase to 'download_complete' if this is a Tidal playlist
                    if playlist_id.startswith('tidal_'):
                        tidal_playlist_id = playlist_id.replace('tidal_', '')
                        if tidal_playlist_id in deps.tidal_discovery_states:
                            deps.tidal_discovery_states[tidal_playlist_id]['phase'] = 'download_complete'
                            logger.warning(f"Updated Tidal playlist {tidal_playlist_id} to download_complete phase (no missing tracks)")

                    # Update Deezer playlist phase to 'download_complete' if this is a Deezer playlist
                    if playlist_id.startswith('deezer_'):
                        deezer_playlist_id = playlist_id.replace('deezer_', '')
                        if deezer_playlist_id in deps.deezer_discovery_states:
                            deps.deezer_discovery_states[deezer_playlist_id]['phase'] = 'download_complete'
                            logger.warning(f"Updated Deezer playlist {deezer_playlist_id} to download_complete phase (no missing tracks)")

                    # Update Spotify Public playlist phase to 'download_complete' if this is a Spotify Public playlist
                    if playlist_id.startswith('spotify_public_'):
                        spotify_public_url_hash = playlist_id.replace('spotify_public_', '')
                        if spotify_public_url_hash in deps.spotify_public_discovery_states:
                            deps.spotify_public_discovery_states[spotify_public_url_hash]['phase'] = 'download_complete'
                            logger.warning(f"Updated Spotify Public playlist {spotify_public_url_hash} to download_complete phase (no missing tracks)")

            # Handle auto-initiated wishlist completion even when no missing tracks
            if is_auto_batch and playlist_id == 'wishlist':
                logger.warning("[Auto-Wishlist] No missing tracks found - calling auto-completion handler to toggle cycle and reschedule")
                deps.missing_download_executor.submit(deps.process_failed_tracks_to_wishlist_exact_with_auto_completion, batch_id)

            return

        logger.warning(f" transitioning batch {batch_id} to download phase with {len(missing_tracks)} tracks.")

        # Read batch context (quick lock) before doing any network I/O
        with tasks_lock:
            if batch_id not in download_batches: return
            batch = download_batches[batch_id]
            batch_album_context = batch.get('album_context')
            batch_artist_context = batch.get('artist_context')
            batch_is_album = batch.get('is_album_download', False)
            batch_private_album_bundle = bool(batch.get('album_bundle_private_staging'))
            batch_playlist_folder_mode = batch.get('playlist_folder_mode', False)
            batch_playlist_name = batch.get('playlist_name', 'Unknown Playlist')

        # Album-bundle sources download a whole release into private staging,
        # then the normal per-track workers claim those staged files. Run this
        # only after analysis has found missing tracks; otherwise an already
        # owned album would still trigger a release download.
        _bundle_state = _BatchStateAccessImpl()
        _album_bundle_source = _resolve_album_bundle_source(deps.config_manager)
        if _album_bundle_source and _album_bundle_source != 'soulseek':
            if _album_bundle_dispatch.try_dispatch(
                batch_id=batch_id,
                is_album=batch_is_album,
                album_context=batch_album_context,
                artist_context=batch_artist_context,
                config_get=deps.config_manager.get,
                plugin_resolver=deps.download_orchestrator.client,
                state=_bundle_state,
                source_override=_album_bundle_source,
            ):
                return

        # === ALBUM PRE-FLIGHT: Search for complete album folder before track-by-track ===
        # Only run pre-flight when Soulseek is the download source (or hybrid with soulseek)
        preflight_source = None
        preflight_tracks = None
        soulseek_is_source = _soulseek_album_preflight_enabled(deps.config_manager)
        if (batch_is_album and batch_album_context and batch_artist_context
                and soulseek_is_source and not batch_private_album_bundle):
            artist_name = batch_artist_context.get('name', '')
            album_name = batch_album_context.get('name', '')
            if artist_name and album_name:
                try:
                    _sr = deps.source_reuse_logger
                    _sr.info(f"[Album Pre-flight] Searching for '{artist_name} {album_name}'")
                    logger.info(f"[Album Pre-flight] Searching Soulseek for complete album: '{artist_name} - {album_name}'")

                    slsk = _resolve_soulseek_client(deps.download_orchestrator)

                    # Try multiple query variations (banned keywords in artist/album name can return 0 results)
                    album_queries = [f"{artist_name} {album_name}"]
                    # Clean artist name (remove feat., parentheticals)
                    clean_artist = re.sub(r'\s*\(.*?\)', '', artist_name).strip()
                    clean_artist = re.sub(r'\s*(feat\.?|ft\.?|featuring)\s+.*$', '', clean_artist, flags=re.IGNORECASE).strip()
                    if clean_artist != artist_name:
                        album_queries.append(f"{clean_artist} {album_name}")
                    # Album name only (some users file by album)
                    album_queries.append(album_name)

                    album_results = []
                    track_results = []
                    album_results_by_source = {}
                    for aq in album_queries:
                        _sr.info(f"[Album Pre-flight] Trying query: '{aq}'")
                        track_results, album_results = deps.run_async(slsk.search(aq, timeout=30))
                        if album_results:
                            _sr.info(f"[Album Pre-flight] Found {len(album_results)} album results with query: '{aq}'")
                            for ar in album_results:
                                key = (getattr(ar, 'username', ''), getattr(ar, 'album_path', ''))
                                if key[0] and key[1] and key not in album_results_by_source:
                                    album_results_by_source[key] = ar
                        else:
                            _sr.info(f"[Album Pre-flight] No album results for query: '{aq}'")

                    album_results = list(album_results_by_source.values())
                    if album_results:
                        # Score complete folders as releases before falling back to per-track search.
                        scored_albums = []
                        for ar in album_results:
                            filtered_tracks = slsk.filter_results_by_quality_preference(ar.tracks)
                            if filtered_tracks:
                                folder_score = _score_album_folder(
                                    ar,
                                    batch_album_context,
                                    batch_artist_context,
                                    tracks_json,
                                    len(filtered_tracks),
                                )
                                scored_albums.append((ar, len(filtered_tracks), folder_score))
                                _sr.info(
                                    f"[Album Pre-flight] Candidate {ar.username}:{ar.album_path} "
                                    f"score={folder_score:.3f}, tracks={ar.track_count}, "
                                    f"quality_tracks={len(filtered_tracks)}"
                                )

                        best_album = None
                        best_score = 0.0
                        if scored_albums:
                            scored_albums.sort(key=lambda x: (x[2], x[1], x[0].quality_score), reverse=True)
                            best_album, _best_filtered_count, best_score = scored_albums[0]
                            if best_score < _ALBUM_PREFLIGHT_MIN_SCORE:
                                _sr.info(
                                    f"[Album Pre-flight] Best folder score {best_score:.3f} below "
                                    f"threshold {_ALBUM_PREFLIGHT_MIN_SCORE:.2f}; falling back"
                                )
                                logger.warning("[Album Pre-flight] No Soulseek folder passed album-level validation")
                                best_album = None

                        if best_album:

                            _sr.info(f"[Album Pre-flight] Best album result: {best_album.username}:{best_album.album_path} "
                                     f"({best_album.track_count} tracks, quality={best_album.dominant_quality}, score={best_score:.3f})")
                            logger.info(f"[Album Pre-flight] Found album folder: {best_album.username} — "
                                  f"{best_album.track_count} tracks ({best_album.dominant_quality})")

                            # Browse the user's folder to get all tracks (may have more than search returned)
                            browse_files = deps.run_async(slsk.browse_user_directory(best_album.username, best_album.album_path))
                            if browse_files:
                                folder_tracks = slsk.parse_browse_results_to_tracks(
                                    best_album.username, browse_files, directory=best_album.album_path
                                )
                                if folder_tracks:
                                    preflight_source = {
                                        'username': best_album.username,
                                        'folder_path': best_album.album_path
                                    }
                                    preflight_tracks = folder_tracks
                                    _sr.info(f"[Album Pre-flight] Browsed folder: {len(folder_tracks)} audio tracks available")
                                    logger.info(f"[Album Pre-flight] Cached {len(folder_tracks)} tracks from {best_album.username} for source reuse")
                                else:
                                    _sr.info("[Album Pre-flight] Browse returned files but no audio tracks")
                            else:
                                # Browse failed — fall back to using the search result tracks directly
                                _sr.info("[Album Pre-flight] Browse failed, using search result tracks directly")
                                preflight_source = {
                                    'username': best_album.username,
                                    'folder_path': best_album.album_path
                                }
                                preflight_tracks = best_album.tracks
                                logger.info(f"[Album Pre-flight] Using {len(best_album.tracks)} tracks from search results (browse unavailable)")
                        elif not scored_albums:
                            _sr.info("[Album Pre-flight] No album results passed quality filter")
                            logger.warning("[Album Pre-flight] No album results matched quality preferences")
                    else:
                        _sr.info(f"[Album Pre-flight] Search returned no album results (got {len(track_results)} individual tracks)")
                        logger.warning("[Album Pre-flight] No complete album folders found, falling back to track-by-track search")

                except Exception as preflight_err:
                    logger.error(f"[Album Pre-flight] Search failed (non-fatal, falling back to track-by-track): {preflight_err}")
                    deps.source_reuse_logger.info(f"[Album Pre-flight] Exception: {preflight_err}")

        # Soulseek album bundles run after analysis so an already-owned
        # album does not get downloaded just because the source supports a
        # whole-folder flow. When preflight selected a folder, pass that
        # exact source into the bundle downloader so we keep the richer
        # tracklist-aware scoring instead of doing a weaker second pick.
        _bundle_state = _BatchStateAccessImpl()
        _album_bundle_source = _resolve_album_bundle_source(deps.config_manager)
        if _album_bundle_source == 'soulseek':
            if _album_bundle_dispatch.try_dispatch(
                batch_id=batch_id,
                is_album=batch_is_album,
                album_context=batch_album_context,
                artist_context=batch_artist_context,
                config_get=deps.config_manager.get,
                plugin_resolver=deps.download_orchestrator.client,
                state=_bundle_state,
                source_override=_album_bundle_source,
                plugin_kwargs={
                    'preferred_source': preflight_source,
                    'preferred_tracks': preflight_tracks,
                } if preflight_source and preflight_tracks else None,
            ):
                return

        with tasks_lock:
            if batch_id not in download_batches: return

            download_batches[batch_id]['phase'] = 'downloading'

            # Store album pre-flight results on batch for source reuse
            # unless the Soulseek album-bundle path already staged a private
            # release. Task workers check source reuse before staging match, so
            # preloading here would make the staged happy path re-download.
            if (
                preflight_source
                and preflight_tracks
                and not download_batches[batch_id].get('album_bundle_private_staging')
            ):
                download_batches[batch_id]['last_good_source'] = preflight_source
                download_batches[batch_id]['source_folder_tracks'] = preflight_tracks
                download_batches[batch_id]['failed_sources'] = set()
                logger.info(f"[Album Pre-flight] Pre-loaded source reuse data on batch {batch_id}")

            # Compute total_discs for multi-disc album subfolder support
            # Use ALL tracks (tracks_json), not just missing ones, to correctly detect multi-disc
            # even when only one disc has missing tracks
            if batch_is_album and batch_album_context:
                total_discs = max((t.get('disc_number') or 1 for t in tracks_json), default=1)
                batch_album_context['total_discs'] = total_discs
                if total_discs > 1:
                    logger.info(f"[Multi-Disc] Detected {total_discs} discs for album '{batch_album_context.get('name')}'")

            # Pre-compute per-album data for wishlist tracks (grouped by album ID)
            # Wishlist tracks aren't batch_is_album but each track has disc_number in spotify_data
            wishlist_album_disc_counts = {}
            wishlist_album_artist_map = {}  # album_id -> resolved artist context (consistent per album)
            wishlist_album_context_map = {}  # album_id -> richest shared album context
            if playlist_id == 'wishlist':
                import json as _json
                # First pass: collect disc_number and resolve ONE artist per album
                for t in tracks_json:
                    sp_data = t.get('spotify_data', {})
                    if isinstance(sp_data, str):
                        try:
                            sp_data = _json.loads(sp_data)
                        except:
                            sp_data = {}
                    album_val = sp_data.get('album')
                    album_id = album_val.get('id') if isinstance(album_val, dict) else album_val if isinstance(album_val, str) else None
                    # Fallback album key: use album name when ID is missing (e.g. mirrored playlist tracks)
                    if not album_id and isinstance(album_val, dict) and album_val.get('name'):
                        album_id = f"_name_{album_val['name'].lower().strip()}"
                    disc_num = sp_data.get('disc_number') or t.get('disc_number') or 1
                    if album_id:
                        wishlist_album_disc_counts[album_id] = max(
                            wishlist_album_disc_counts.get(album_id, 1), disc_num
                        )
                        if isinstance(album_val, dict):
                            existing_album_ctx = wishlist_album_context_map.get(album_id, {})
                            if _album_context_richness(album_val) > _album_context_richness(existing_album_ctx):
                                wishlist_album_context_map[album_id] = dict(album_val)
                        # Resolve album-level artist once per album (first track wins)
                        if album_id not in wishlist_album_artist_map:
                            _wl_source = t.get('source_info') or {}
                            if isinstance(_wl_source, str):
                                try:
                                    _wl_source = _json.loads(_wl_source)
                                except:
                                    _wl_source = {}
                            _wl_album = album_val if isinstance(album_val, dict) else {}
                            _wl_album_artists = _wl_album.get('artists', [])
                            # Priority: watchlist artist > album artists > track artists
                            if _wl_source.get('watchlist_artist_name'):
                                wishlist_album_artist_map[album_id] = {
                                    'name': _wl_source['watchlist_artist_name'],
                                    'id': _wl_source.get('watchlist_artist_id', '')
                                }
                            elif _wl_source.get('artist_name'):
                                wishlist_album_artist_map[album_id] = {'name': _wl_source['artist_name']}
                            elif _wl_album_artists:
                                _fa = _wl_album_artists[0]
                                wishlist_album_artist_map[album_id] = _fa if isinstance(_fa, dict) else {'name': str(_fa)}
                            else:
                                _wl_track_artists = sp_data.get('artists', [])
                                if _wl_track_artists:
                                    _fa = _wl_track_artists[0]
                                    wishlist_album_artist_map[album_id] = _fa if isinstance(_fa, dict) else {'name': str(_fa)}
                                else:
                                    # Try top-level 'artists' (wishlist format uses plural)
                                    _tl_artists = t.get('artists', [])
                                    if _tl_artists:
                                        _tla = _tl_artists[0]
                                        _fallback_name = _tla.get('name', str(_tla)) if isinstance(_tla, dict) else str(_tla)
                                    else:
                                        _fallback_name = t.get('artist', '')
                                    wishlist_album_artist_map[album_id] = {'name': _fallback_name or 'Unknown Artist'}
                            logger.info(f"[Wishlist Album Grouping] Album '{_wl_album.get('name', album_id)}' → artist: '{wishlist_album_artist_map[album_id].get('name', '?')}'")



            for res in missing_tracks:
                task_id = str(uuid.uuid4())
                track_info = res['track'].copy()

                # Add explicit album context to track_info for artist album downloads
                if batch_is_album and batch_album_context and batch_artist_context:
                    track_info['_explicit_album_context'] = batch_album_context
                    track_info['_explicit_artist_context'] = batch_artist_context
                    track_info['_is_explicit_album_download'] = True
                    logger.info(f"[Task Creation] Added explicit album context for: {track_info.get('name')}")

                # SPECIAL WISHLIST HANDLING: Inject album context if available to force grouping
                elif playlist_id == 'wishlist':
                    # Extract spotify_data again since it might be buried
                    spotify_data = track_info.get('spotify_data')
                    if isinstance(spotify_data, str):
                        try:
                            spotify_data = json.loads(spotify_data)
                        except:
                            spotify_data = {}
                    
                    if not spotify_data:
                        spotify_data = {}

                    s_album = spotify_data.get('album') or {}
                    if isinstance(s_album, str):
                        s_album = {'name': s_album}  # Normalize string album to dict
                    s_artists = spotify_data.get('artists', [])

                    # We need at least an album name and artist
                    if s_album and isinstance(s_album, dict) and s_album.get('name'):
                        # Use pre-computed album-level artist for folder consistency.
                        # All tracks from the same album get the same artist context,
                        # preventing folder splits on collab albums (KPOP Demon Hunters, etc.)
                        album_id_for_lookup = s_album.get('id')
                        # Fallback album key: match first-pass logic for missing IDs
                        if not album_id_for_lookup and s_album.get('name'):
                            album_id_for_lookup = f"_name_{s_album['name'].lower().strip()}"
                        if not album_id_for_lookup:
                            album_id_for_lookup = 'wishlist_album'
                        artist_ctx = wishlist_album_artist_map.get(album_id_for_lookup, {})
                        if not artist_ctx or not artist_ctx.get('name'):
                            # Fallback: per-track resolution from artists array
                            _fb_artists = track_info.get('artists', [])
                            if _fb_artists:
                                _fb_a = _fb_artists[0]
                                _fb_name = _fb_a.get('name', str(_fb_a)) if isinstance(_fb_a, dict) else str(_fb_a)
                            else:
                                _fb_name = track_info.get('artist', '')
                            artist_ctx = {'name': _fb_name or 'Unknown Artist'}

                        # Construct a shared album context from the richest track in
                        # this album group so release_date/year and artwork do not
                        # vary per track and split folders.
                        album_id = s_album.get('id', 'wishlist_album')
                        shared_album = wishlist_album_context_map.get(album_id_for_lookup, s_album)
                        album_ctx = {
                            'id': album_id,
                            'name': shared_album.get('name') or s_album.get('name'),
                            'release_date': shared_album.get('release_date', ''),
                            'total_tracks': shared_album.get('total_tracks') or s_album.get('total_tracks', 1),
                            'total_discs': wishlist_album_disc_counts.get(album_id_for_lookup, 1),
                            'album_type': shared_album.get('album_type') or s_album.get('album_type', 'album'),
                            'images': shared_album.get('images') or s_album.get('images', []),
                            'artists': shared_album.get('artists') or s_album.get('artists', []),
                        }

                        track_info['_explicit_album_context'] = album_ctx
                        track_info['_explicit_artist_context'] = artist_ctx
                        track_info['_is_explicit_album_download'] = True
                        logger.info(f"[Wishlist] Added album context for: '{track_info.get('name')}' -> '{album_ctx['name']}'")


                # Issue #797 — propagate the batch-level "skip AcoustID"
                # toggle onto each track so the per-track download context
                # (built in core/downloads/candidates.py) can set the
                # AcoustID quarantine bypass. Mirrors the _playlist_folder_mode
                # threading pattern below.
                if batch_skip_acoustid:
                    track_info['_skip_acoustid'] = True

                # Add playlist folder mode flag for sync page playlists and wishlist
                # tracks tied to a mirrored playlist with organize_by_playlist enabled.
                task_pl_folder_mode = batch_playlist_folder_mode
                task_pl_name = batch_playlist_name
                if not task_pl_folder_mode and playlist_id == 'wishlist':
                    wl_source = track_info.get('source_info') or {}
                    if isinstance(wl_source, str):
                        try:
                            wl_source = json.loads(wl_source)
                        except (json.JSONDecodeError, TypeError):
                            wl_source = {}
                    wl_pl_ref = wl_source.get('playlist_id')
                    wl_pl_name = wl_source.get('playlist_name')
                    wl_pl_source = wl_source.get('source') or 'spotify'
                    if wl_pl_ref and hasattr(db, 'resolve_mirrored_playlist'):
                        wl_mirrored = db.resolve_mirrored_playlist(
                            wl_pl_ref,
                            profile_id=batch_profile_id,
                            default_source=wl_pl_source,
                        )
                        if wl_mirrored and wl_mirrored.get('organize_by_playlist'):
                            task_pl_folder_mode = True
                            task_pl_name = wl_pl_name or wl_mirrored.get('name') or batch_playlist_name
                if task_pl_folder_mode:
                    track_info['_playlist_folder_mode'] = True
                    track_info['_playlist_name'] = task_pl_name
                    if batch_source_playlist_ref:
                        track_info['source_info'] = {
                            'playlist_id': batch_source_playlist_ref,
                            'playlist_name': task_pl_name,
                            'source': batch_source,
                        }
                    logger.info(
                        f"[Task Creation] Added playlist folder mode for: "
                        f"{track_info.get('name')} → {task_pl_name}"
                    )
                else:
                    logger.debug(
                        f"[Debug] Task Creation - playlist folder mode NOT enabled for: "
                        f"{track_info.get('name')}"
                    )

                # Download-origin provenance: stamp what TRIGGERED this download
                # so the history chokepoint can record it (origin-history modal).
                # Wishlist rows already ride their source_info in track_info
                # (watchlist_artist_name / playlist_name — the deriver reads
                # those directly); this stamp covers DIRECT playlist batches,
                # where the playlist context otherwise only survives in
                # folder mode.
                if '_dl_origin' not in track_info and batch_source_playlist_ref and batch_playlist_name:
                    _prov_si = track_info.get('source_info') or {}
                    if isinstance(_prov_si, str):
                        try:
                            _prov_si = json.loads(_prov_si)
                        except (json.JSONDecodeError, TypeError):
                            _prov_si = {}
                    if not _prov_si.get('watchlist_artist_name'):
                        track_info['_dl_origin'] = 'playlist'
                        track_info['_dl_origin_context'] = (
                            _prov_si.get('playlist_name') or batch_playlist_name
                        )

                download_tasks[task_id] = {
                    'status': 'pending', 'track_info': track_info,
                    'playlist_id': playlist_id, 'batch_id': batch_id,
                    'track_index': res['track_index'], 'retry_count': 0,
                    'cached_candidates': [], 'used_sources': set(),
                    'status_change_time': time.time(),
                    'metadata_enhanced': False
                }
                download_batches[batch_id]['queue'].append(task_id)

        deps.download_monitor.start_monitoring(batch_id)
        deps.start_next_batch_of_downloads(batch_id)

        # Album-bundle batches run on the dedicated album pool and pass
        # serialize=True: hold this pool slot until the album finishes so only a
        # few albums are ever in flight at once, instead of every album batch
        # immediately starting and flooding the shared download pool with
        # 'searching' tracks (#740 / Sokhi). The residual + playlist + manual
        # paths run on the shared download pool and DON'T serialize (blocking
        # there would steal an actual download worker).
        if serialize:
            _wait_for_batch_drain(batch_id)

    except Exception as e:
        logger.error(f"Master worker for batch {batch_id} failed: {e}")
        import traceback
        traceback.print_exc()

        is_auto_batch = False
        with tasks_lock:
            if batch_id in download_batches:
                is_auto_batch = download_batches[batch_id].get('auto_initiated', False)
                download_batches[batch_id]['phase'] = 'error'
                download_batches[batch_id]['error'] = str(e)

                # Reset YouTube playlist phase to 'discovered' if this is a YouTube playlist on error
                if playlist_id.startswith('youtube_'):
                    url_hash = playlist_id.replace('youtube_', '')
                    if url_hash in deps.youtube_playlist_states:
                        deps.youtube_playlist_states[url_hash]['phase'] = 'discovered'
                        logger.error(f"Reset YouTube playlist {url_hash} to discovered phase (error)")

        # Handle auto-initiated wishlist errors - reset flag
        if is_auto_batch and playlist_id == 'wishlist':
            logger.error("[Auto-Wishlist] Master worker error - resetting auto-processing flag")
            deps.reset_wishlist_auto_processing()
