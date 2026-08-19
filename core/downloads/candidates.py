"""Candidate fallback download logic.

`attempt_download_with_candidates(task_id, candidates, track, batch_id, deps)`
is the function the search/match pipeline calls once it has a sorted list of
Soulseek candidates for a track. It walks the candidates by descending
confidence and starts the first one that:

1. Hasn't been tried for this task already (`used_sources` dedup).
2. Isn't blacklisted (user-flagged bad match).
3. Doesn't trigger a cancellation race (checked at three points).

When a candidate accepts:

- Stores rich post-processing context in `matched_downloads_context` keyed by
  `make_context_key(username, filename)` — clean Spotify metadata, album
  context (real or synthesized), `is_album_download` flag, batch/task IDs.
- For tracks with clean Spotify data, resolves track_number / disc_number
  from (1) track_info → (2) track object → (3) Spotify API call, with album
  metadata backfilled from the API response when local context is incomplete.
- Updates the task with the assigned `download_id`, falls through with a
  "searching" reset on failure so the next attempt finds a clean state.

On cancellation mid-download, attempts to cancel the active Soulseek transfer
and notifies the lifecycle via `on_download_completed(success=False)` so the
worker slot frees up.

Lifted verbatim from web_server.py. Wide dependency surface
(download_orchestrator, spotify_client, lifecycle callback, context-key helper,
status updater, DB) all injected via `CandidatesDeps`.
"""

from __future__ import annotations

from utils.logging_config import get_logger
import os
from dataclasses import dataclass
from typing import Any, Callable

from core.downloads.track_metadata_backfill import hydrate_download_metadata
from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)

logger = get_logger("downloads.candidates")


def preferred_version_stamp(candidate, preferred_version):
    """Whether this pick is a version we went after on purpose, and what to
    measure its length against.

    Returns ``(version, duration_ms)``. ``version`` is ``''`` for every ordinary
    download, so nothing is written to the context and the import gates run
    exactly as they always have.

    It fills in only when the user asked for a version (Settings → prefer a
    version) AND the matching engine confirmed this file is that version OF
    THIS SONG. Carrying the label is not enough — "Song Two (Extended Mix)" can
    still be walked to as a fallback candidate, and loosening the import gates
    for a file the preference never chose is exactly the wrong trade.

    Downstream it does two jobs, both because the file is deliberately a
    different recording than the source describes:
      - the source's duration belongs to the other cut (an extended mix runs
        minutes past the radio edit Spotify listed), so integrity would
        quarantine a file we went looking for;
      - AcoustID's version gate compares the source title's version against the
        fingerprinted recording's, and would call the difference a wrong song.

    ``duration_ms`` is the length the peer advertised, which still catches a
    truncated transfer. None means the peer advertised nothing, so there's no
    honest reference and the duration leg gets skipped rather than guessed at.

    Pure helper. No I/O, no config reads — the caller passes the preference in.
    """
    if not preferred_version:
        return '', None
    picked = getattr(candidate, 'version_type', 'original')
    if picked != preferred_version or picked == 'original':
        return '', None
    if not getattr(candidate, 'preferred_version_hit', False):
        return '', None
    advertised = getattr(candidate, 'duration', None)
    try:
        advertised = int(advertised) if advertised else None
    except (TypeError, ValueError):
        advertised = None
    return picked, (advertised if advertised and advertised > 0 else None)


def _preferred_version_hit(r):
    """1 when the matching engine stamped this file as the version the user
    asked for, 0 otherwise.

    Nothing stamps it on any other flow, so the term is 0 for every candidate
    and the orders below are byte-for-byte what they were.
    """
    return 1 if getattr(r, 'preferred_version_hit', False) else 0


# Streaming plugins identify themselves via ``TrackResult.username``. Soulseek
# peers use the sharing username, so they are everything else. Keep this list
# in sync with ``core.downloads.validation._STREAMING_USERNAMES`` — imported
# lazily would cycle (validation → candidates).
_STREAMING_USERNAMES = frozenset({
    'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'soundcloud',
    'amazon', 'torrent', 'usenet', 'lidarr',
})


def _candidate_source_name(r) -> str:
    """Map a search hit onto a hybrid-chain source id."""
    name = (getattr(r, 'username', None) or '').lower()
    if name in _STREAMING_USERNAMES:
        return name
    return 'soulseek'


def _is_mixed_source_pool(candidates) -> bool:
    """True when the walk contains more than one download source."""
    sources = {
        _candidate_source_name(c)
        for c in candidates
        if getattr(c, 'username', None)
    }
    return len(sources) > 1


def _source_order_index(r, source_order) -> int:
    """Position in the user's hybrid chain. Unknown sources sort last."""
    if not source_order:
        return 0
    name = _candidate_source_name(r)
    try:
        return list(source_order).index(name)
    except ValueError:
        return len(source_order)


def _priority_sort_key(r):
    """Today's confidence-first key: never download a high-quality WRONG file."""
    return (
        getattr(r, 'confidence', 0) or 0,
        getattr(r, 'quality_score', 0) or 0,
        getattr(r, 'upload_speed', 0) or 0,
        -(getattr(r, 'queue_length', 0) or 0),
        getattr(r, 'free_upload_slots', 0) or 0,
        getattr(r, 'size', 0) or 0,
    )


def _quality_first_sort_key(r, targets, source_order=None):
    """Best-quality key: the user's profile quality rank dominates; all the
    priority-mode signals (confidence, speed, …) become tiebreakers.

    Every candidate reaching this point already passed match filtering, so it
    is "correct enough" — ordering by quality among correct candidates is safe.
    Candidates with no usable quality info, or that match no target, sort last
    (never dropped). Lower target index = better target, so it's negated to fit
    the descending (reverse=True) sort.

    After target index, the hybrid chain order breaks ties so "YouTube first"
    actually prefers YouTube when two hits satisfy the same rung (Opus 256 vs
    Opus 256), without letting a later-source FLAC outrank a first-source hit
    that matches a higher-priority target.
    """
    from core.quality.model import rank_candidate

    aq = getattr(r, 'audio_quality', None)
    if aq is None or not targets:
        target_idx, tier = (len(targets) if targets else 0), 0.0
    else:
        try:
            target_idx, tier = rank_candidate(aq, targets)
        except Exception:
            target_idx, tier = len(targets), 0.0
    src = _source_order_index(r, source_order)
    return (-target_idx, -src, tier) + _priority_sort_key(r)


def order_candidates(candidates, *, quality_first=False, targets=None,
                     source_order=None):
    """Return *candidates* ordered best-first for the download walk.

    ``quality_first=False`` (priority mode) → confidence-first, byte-for-byte
    today's behaviour. ``quality_first=True`` (best-quality mode) → the user's
    profile quality rank dominates, confidence/peer signals break ties.

    Mixed-source pools (YouTube + Soulseek from best-quality search) always
    use the profile rank. The legacy ``quality_score`` puts FLAC at 1.0 and
    Opus at 0.3, so a confidence-first walk would download Soulseek even when
    YouTube itag 774 matches the top target.

    The asked-for version leads BOTH keys. A preference picks the RECORDING,
    and a different recording is a different song, while confidence and quality
    both rank files OF THE SAME song — so neither gets to outvote it. This is
    also the sort that decides what actually downloads: the matching engine
    ranks the preferred version first and this used to re-sort it straight back
    down, so the setting looked applied and changed nothing.

    Ordering only. The quality profile filters upstream, so a preferred version
    that fails the user's quality ladder never arrives here to be ordered — the
    preference chooses between candidates the profile already accepted.
    """
    rows = list(candidates)
    use_quality = quality_first or _is_mixed_source_pool(rows)
    if use_quality:
        key = lambda r: (
            (_preferred_version_hit(r),)
            + _quality_first_sort_key(r, targets or [], source_order)
        )
    else:
        key = lambda r: (_preferred_version_hit(r),) + _priority_sort_key(r)
    return sorted(rows, key=key, reverse=True)


@dataclass
class CandidatesDeps:
    """Bundle of cross-cutting deps the candidate-fallback logic needs."""
    download_orchestrator: Any
    spotify_client: Any
    run_async: Callable[..., Any]
    get_database: Callable[[], Any]
    update_task_status: Callable
    make_context_key: Callable[[str, str], str]
    on_download_completed: Callable


def attempt_download_with_candidates(task_id, candidates, track, batch_id=None,
                                     deps: CandidatesDeps = None, *,
                                     quality_first=False, quality_targets=None):
    """
    Attempts to download with fallback candidate logic (matches GUI's retry_parallel_download_with_fallback).
    Returns True if successful, False if all candidates fail.

    ``quality_first`` (best-quality search mode) orders the walk by the user's
    profile quality rank instead of confidence-first; ``quality_targets`` is the
    profile target list used for that ranking. Defaults preserve priority-mode
    behaviour exactly.
    """
    # Sort candidates. Priority mode: confidence-first, then peer quality —
    # upstream Soulseek validation already considers peer speed/slots/queue when
    # scores are close; preserve that signal instead of flattening ties back to
    # arbitrary slskd response order. Best-quality mode: profile quality rank
    # dominates (all candidates here already passed match filtering).
    source_order = None
    orch = getattr(deps, 'download_orchestrator', None) if deps else None
    if orch is not None:
        source_order = list(getattr(orch, 'hybrid_order', None) or [])
    if not source_order:
        try:
            from core.settings import config_manager
            source_order = list(
                config_manager.get('download_source.hybrid_order') or []
            )
        except Exception:  # noqa: BLE001 - ranking still works without chain order
            source_order = None

    candidates = order_candidates(
        candidates, quality_first=quality_first, targets=quality_targets,
        source_order=source_order,
    )
    
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return False
        used_sources = task.get('used_sources', set())
        # User-initiated manual picks (candidates modal) bypass quarantine
        # gates downstream. The user already accepted the risk by choosing
        # the file; we trust their selection over AcoustID disagreement so
        # repeated manual picks don't loop back into quarantine.
        user_manual_pick = bool(task.get('_user_manual_pick', False))
    
    # Try each candidate until one succeeds (like GUI's fallback logic)
    for candidate_index, candidate in enumerate(candidates):
        # Check cancellation before each attempt
        with tasks_lock:
            if task_id not in download_tasks:
                logger.info(f"[Modal Worker] Task {task_id} was deleted during candidate {candidate_index + 1}")
                return False
            if download_tasks[task_id]['status'] == 'cancelled':
                logger.warning(f"[Modal Worker] Task {task_id} cancelled during candidate {candidate_index + 1}")
                # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                return False
            download_tasks[task_id]['current_candidate_index'] = candidate_index
            
        # Create source key to avoid duplicate attempts (like GUI)
        source_key = f"{candidate.username}_{candidate.filename}"
        if source_key in used_sources:
            logger.info(f"[Modal Worker] Skipping already tried source: {source_key}")
            continue

        # Blacklist check — skip sources the user has flagged as bad matches
        try:
            _bl_db = deps.get_database()
            if _bl_db.is_blacklisted(candidate.username, candidate.filename):
                logger.info(f"[Modal Worker] Skipping blacklisted source: {source_key}")
                continue
        except Exception as e:
            logger.debug("blacklist check failed: %s", e)
        
        # CRITICAL: Add source to used_sources IMMEDIATELY to prevent race conditions
        # This must happen BEFORE starting download to prevent multiple retries from picking same source
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['used_sources'].add(source_key)
                logger.info(f"[Modal Worker] Marked source as used before download attempt: {source_key}")
            
        logger.info(f"[Modal Worker] Trying candidate {candidate_index + 1}/{len(candidates)}: {candidate.filename} (Confidence: {candidate.confidence:.2f})")
        
        try:
            # Update task status to downloading
            deps.update_task_status(task_id, 'downloading')

            # Prepare download - check if we have explicit album context from artist page
            track_info = {}
            with tasks_lock:
                if task_id in download_tasks:
                    raw_track_info = download_tasks[task_id].get('track_info')
                    track_info = raw_track_info if isinstance(raw_track_info, dict) else {}

            # Use explicit album/artist context if available (from artist album downloads)
            has_explicit_context = track_info and track_info.get('_is_explicit_album_download', False)

            if has_explicit_context:
                # Use the real Spotify album/artist data from the UI
                explicit_album = track_info.get('_explicit_album_context', {})
                explicit_artist = track_info.get('_explicit_artist_context', {})
                # Normalize artist context if it's a plain string (e.g. from wishlist spotify_data)
                if isinstance(explicit_artist, str):
                    explicit_artist = {'name': explicit_artist}

                spotify_artist_context = {
                    'id': explicit_artist.get('id', 'explicit_artist'),
                    'name': explicit_artist.get('name', track.artists[0] if track.artists else 'Unknown'),
                    'genres': explicit_artist.get('genres', [])
                }
                # Handle both image_url formats (direct string or images array)
                album_image_url = None
                if explicit_album.get('image_url'):
                    # Backend API returns image_url as direct string
                    album_image_url = explicit_album.get('image_url')
                elif explicit_album.get('images'):
                    # Fallback: images array format from Spotify API
                    album_image_url = explicit_album.get('images', [{}])[0].get('url')

                spotify_album_context = {
                    'id': explicit_album.get('id', 'explicit_album'),
                    'name': explicit_album.get('name', track.album),
                    'release_date': explicit_album.get('release_date', ''),
                    'image_url': album_image_url,
                    'total_tracks': explicit_album.get('total_tracks', 0),
                    'total_discs': explicit_album.get('total_discs', 1),
                    'album_type': explicit_album.get('album_type', 'album'),
                    'artists': explicit_album.get('artists', [{'name': spotify_artist_context.get('name', '')}])
                }
                logger.info(f"[Explicit Context] Using real album data: '{spotify_album_context['name']}' ({spotify_album_context['album_type']}, {spotify_album_context['total_discs']} disc(s))")
            else:
                # Fallback to generic context for playlists/wishlists
                # Extract album metadata from track_info if available (discovery enriches tracks with full album objects)
                fallback_album = track_info.get('album', {}) if track_info else {}
                if isinstance(fallback_album, str):
                    fallback_album = {'name': fallback_album}
                elif not isinstance(fallback_album, dict):
                    fallback_album = {}
                fallback_image_url = None
                fallback_images = fallback_album.get('images', [])
                if fallback_album.get('image_url'):
                    fallback_image_url = fallback_album['image_url']
                elif fallback_images and isinstance(fallback_images, list) and len(fallback_images) > 0:
                    fallback_image_url = fallback_images[0].get('url') if isinstance(fallback_images[0], dict) else None
                spotify_artist_context = {'id': 'from_sync_modal', 'name': track.artists[0] if track.artists else 'Unknown', 'genres': []}
                # Preserve album-level artists for consistent folder naming
                _fallback_album_artists = fallback_album.get('artists', [])
                if not _fallback_album_artists:
                    _fallback_album_artists = [{'name': track.artists[0]}] if track.artists else []
                spotify_album_context = {
                    'id': fallback_album.get('id', 'from_sync_modal'),
                    'name': fallback_album.get('name', '') or track.album,
                    'release_date': fallback_album.get('release_date', ''),
                    'image_url': fallback_image_url,
                    'album_type': fallback_album.get('album_type', 'album'),
                    'total_tracks': fallback_album.get('total_tracks', 0),
                    'total_discs': fallback_album.get('total_discs', 1),
                    'artists': _fallback_album_artists
                }

            # #915: parity with Reorganize / manual Enrich. If the album context is lean
            # (no release_date) and the user's PRIMARY metadata source isn't Spotify, hydrate
            # it from that source — the same place a reorganize reads — so the download's
            # $year folder, release_date and album_type match instead of dropping the year /
            # defaulting to YYYY-01-01 and forcing a manual reorganize afterwards.
            try:
                from core.downloads.track_metadata_backfill import backfill_album_context_from_source
                from core.metadata import registry as _meta_registry
                from core.metadata.album_tracks import get_album_for_source as _get_album_for_source
                backfill_album_context_from_source(
                    spotify_album_context, _meta_registry.get_primary_source(), _get_album_for_source,
                )
            except Exception as _bf_err:  # noqa: BLE001 — never let backfill break a download
                logger.debug("[Context] primary-source album backfill skipped: %s", _bf_err)

            download_payload = candidate.__dict__

            username = download_payload.get('username')
            filename = download_payload.get('filename')
            size = download_payload.get('size', 0)

            if not username or not filename:
                logger.error("[Modal Worker] Invalid candidate data: missing username or filename")
                continue

            # PROTECTION: Check if there's already an active download for this task
            current_download_id = None
            with tasks_lock:
                if task_id in download_tasks:
                    current_download_id = download_tasks[task_id].get('download_id')
            
            if current_download_id:
                logger.info(f"[Modal Worker] Task {task_id} already has active download {current_download_id} - skipping new download attempt")
                logger.info("[Modal Worker] This prevents race condition where multiple retries start overlapping downloads")
                continue

            # Initiate download
            logger.info(f"[Modal Worker] Starting download: {username} / {os.path.basename(filename)}")
            download_id = deps.run_async(deps.download_orchestrator.download(username, filename, size))

            if download_id:
                # Store context for post-processing with complete Spotify metadata (GUI PARITY)
                context_key = deps.make_context_key(username, filename)
                with matched_context_lock:
                    # Create WebUI equivalent of GUI's SpotifyBasedSearchResult data structure
                    enhanced_payload = download_payload.copy()
                    
                    # Extract clean Spotify metadata from track object (same as GUI)
                    has_clean_spotify_data = track and hasattr(track, 'name') and hasattr(track, 'album')
                    if has_clean_spotify_data:
                        # Use clean Spotify metadata (matches GUI's SpotifyBasedSearchResult)
                        enhanced_payload['spotify_clean_title'] = track.name
                        enhanced_payload['spotify_clean_album'] = track.album
                        enhanced_payload['spotify_clean_artist'] = track.artists[0] if track.artists else enhanced_payload.get('artist', '')
                        # Preserve all artists for metadata tagging
                        enhanced_payload['artists'] = [{'name': artist} for artist in track.artists] if track.artists else []
                        logger.info(f"[Context] Using clean Spotify metadata - Album: '{track.album}', Title: '{track.name}'")
                        
                        # Resolve track_number / disc_number and hydrate
                        # lean album context. Extracted to
                        # track_metadata_backfill.hydrate_download_metadata
                        # — see that module for the precedence chain.
                        # Why the extract: the inline pre-fix coupled
                        # album-backfill to the "track_number missing"
                        # branch. When wishlist payloads carried a poisoned
                        # default-1 track_number (older routes.py used
                        # ``.get('track_number', 1)``) the API call short-
                        # circuited and the lean album_context (no
                        # release_date / total_tracks for Deezer-sourced
                        # discovery matches) survived untouched, producing
                        # folders without a year subfolder.
                        resolved = hydrate_download_metadata(
                            track, track_info, spotify_album_context, deps.spotify_client,
                        )
                        if resolved.track_number is not None:
                            enhanced_payload['track_number'] = resolved.track_number
                            enhanced_payload['disc_number'] = resolved.disc_number
                            logger.info(
                                f"[Context] Added track_number from {resolved.source}: "
                                f"{resolved.track_number}, disc_number: {resolved.disc_number}"
                            )
                        else:
                            enhanced_payload.setdefault('track_number', 0)
                            enhanced_payload.setdefault('disc_number', 1)
                            logger.warning("[Context] No track_number found from any source")
                        
                        # Determine if this should be treated as album download
                        # First check if we have explicit album context from artist page
                        if has_explicit_context:
                            is_album_context = True
                            logger.info("[Context] Using explicit album context flag from artist page")
                        else:
                            # Fall back to guessing based on clean data
                            is_album_context = (
                                track.album and
                                track.album.strip() and
                                track.album != "Unknown Album" and
                                track.album.lower() != track.name.lower()  # Album different from track
                            )
                    else:
                        # Fallback to original data
                        enhanced_payload['spotify_clean_title'] = enhanced_payload.get('title', '')
                        enhanced_payload['spotify_clean_album'] = enhanced_payload.get('album', '')
                        enhanced_payload['spotify_clean_artist'] = enhanced_payload.get('artist', '')
                        # Preserve existing artists array if available, otherwise create from single artist
                        if 'artists' not in enhanced_payload and enhanced_payload.get('artist'):
                            enhanced_payload['artists'] = [{'name': enhanced_payload['artist']}]
                        enhanced_payload['track_number'] = track_info.get('track_number', 1)  # Fallback when no clean Spotify data
                        is_album_context = False
                        logger.warning(f"[Context] Using fallback data - no clean Spotify metadata available, track_number={enhanced_payload['track_number']}")
                    
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist_context,
                        "spotify_album": spotify_album_context,
                        "original_search_result": enhanced_payload,
                        "is_album_download": is_album_context,  # Critical fix: Use actual album context
                        "has_clean_spotify_data": has_clean_spotify_data,  # Flag for post-processing
                        "task_id": task_id,  # Add task_id for completion callbacks
                        "batch_id": batch_id,  # Add batch_id for completion callbacks
                        "track_info": track_info,  # Add track_info for playlist folder mode
                        "_download_username": username,  # Source username for AcoustID skip logic
                    }
                    try:
                        from core.matching_engine import MusicMatchingEngine
                        _took, _adv_ms = preferred_version_stamp(
                            candidate, MusicMatchingEngine._preferred_version())
                        if _took:
                            matched_downloads_context[context_key]['_preferred_version_taken'] = _took
                            matched_downloads_context[context_key]['_preferred_version_duration_ms'] = _adv_ms
                            logger.info(
                                "[Context] Took preferred version '%s' on purpose — length checked "
                                "against the peer's %s, and AcoustID may report it as '%s'",
                                _took,
                                f"{_adv_ms}ms" if _adv_ms else "(none advertised)",
                                _took,
                            )
                    except Exception as _pref_err:
                        logger.debug("[Context] preferred-version stamp skipped: %s", _pref_err)

                    if user_manual_pick:
                        # The user explicitly picked this candidate via the
                        # candidates modal — trust their metadata judgement
                        # over AcoustID disagreement so manual picks don't
                        # loop back into quarantine. Integrity + bit-depth
                        # gates still run because those check the new file's
                        # actual condition, not its identity.
                        matched_downloads_context[context_key]['_skip_quarantine_check'] = 'acoustid'
                        matched_downloads_context[context_key]['_user_manual_pick'] = True
                        logger.info(
                            "[Context] User manual pick — bypassing AcoustID for "
                            "task=%s username=%s filename=%s",
                            task_id, username, os.path.basename(filename),
                        )
                    elif track_info and track_info.get('_skip_acoustid'):
                        # Issue #797 — the album-download request had the
                        # per-request "Skip AcoustID verification" toggle on.
                        # Bypass only the AcoustID gate (same as a manual
                        # pick); integrity + bit-depth still run.
                        matched_downloads_context[context_key]['_skip_quarantine_check'] = 'acoustid'
                        logger.info(
                            "[Context] Skip-AcoustID toggle — bypassing AcoustID for "
                            "task=%s filename=%s",
                            task_id, os.path.basename(filename),
                        )

                    logger.info(f"[Context] Set is_album_download: {is_album_context} (has clean data: {has_clean_spotify_data})")
                
                # Update task with successful download info
                _cancelled_after_start = False
                with tasks_lock:
                    if task_id in download_tasks:
                        # PHASE 3: Final cancellation check after download started (GUI PARITY)
                        if download_tasks[task_id]['status'] == 'cancelled':
                            _cancelled_after_start = True
                            logger.warning(f"[Modal Worker] Task {task_id} cancelled after download {download_id} started - attempting to cancel download")
                            # Try to cancel the download immediately
                            try:
                                logger.info(
                                    f"[CancelTrigger:candidates.worker_cancelled_during_download] "
                                    f"download_id={download_id} username={username} task_id={task_id}"
                                )
                                deps.run_async(deps.download_orchestrator.cancel_download(download_id, username, remove=True))
                                logger.warning(f"Successfully cancelled active download {download_id}")
                            except Exception as cancel_error:
                                logger.error(f"Failed to cancel active download {download_id}: {cancel_error}")
                        else:
                            # Store download information - use real download ID from download_orchestrator
                            # CRITICAL FIX: Trust the download ID returned by download_orchestrator.download()
                            download_tasks[task_id]['download_id'] = download_id
                            download_tasks[task_id]['username'] = username
                            download_tasks[task_id]['filename'] = filename

                if _cancelled_after_start:
                    # Free the worker slot OUTSIDE tasks_lock: on_download_completed
                    # re-acquires it and tasks_lock is non-reentrant, so calling it
                    # in-lock deadlocked the worker WHILE HOLDING the global lock,
                    # freezing all downloads. Idempotent, so it's safe here.
                    if batch_id:
                        deps.on_download_completed(batch_id, task_id, success=False)
                    return False

                logger.info(f"[Modal Worker] Download started successfully for '{filename}'. Download ID: {download_id}")
                return True  # Success!
            else:
                logger.error(f"[Modal Worker] Failed to start download for '{filename}'")
                # Reset status back to searching for next attempt
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'searching'
                continue
                
        except Exception as e:
            import traceback
            logger.error(f"[Modal Worker] Error attempting download for '{candidate.filename}': {e}")
            traceback.print_exc()
            # Reset status back to searching for next attempt
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'searching'
            continue

    # All candidates failed
    logger.error(f"[Modal Worker] All {len(candidates)} candidates failed for '{track.name}'")
    return False
