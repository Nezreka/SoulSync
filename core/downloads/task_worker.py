"""Per-task download worker.

Runs as a background thread (one per task) that:
1. Tries source-reuse (use the batch's last good slskd peer if available)
2. Tries staging-match (file already in staging folder, no download needed)
3. Generates smart search queries via the matching engine + legacy fallbacks
4. Iterates queries sequentially against the soulseek client
5. For each query: validates results, attempts download with fallback candidates
6. If hybrid mode: falls back to remaining sources (youtube/tidal/qobuz/hifi/deezer_dl)
7. On total failure: marks task not_found + records search diagnostics
8. On any uncaught exception: marks failed + emergency worker-slot recovery

Lifted verbatim from web_server.py's `_download_track_worker`. The helpers
this calls into (try_source_reuse, store_batch_source, try_staging_match,
get_valid_candidates, attempt_download_with_candidates, on_download_completed,
recover_worker_slot) are passed via `TaskWorkerDeps` since each is itself
a large web_server.py helper that will get its own lift in subsequent PRs.
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.runtime_state import download_batches, download_tasks, tasks_lock
from core.spotify_client import Track as SpotifyTrack
from utils.logging_config import get_logger

# Must live under the soulsync.* namespace — handlers only attach there. The
# old bare getLogger(__name__) ("core.downloads.task_worker") had no handler,
# so the entire [Modal Worker] story — search queries, retry walks, candidate
# decisions — never reached app.log.
logger = get_logger("downloads.task_worker")


def _notify_acquisition_retry_exhausted(track_info: Any, error: str) -> bool:
    """Notify persistent Acquisition state; ordinary tasks are a no-op."""
    if not isinstance(track_info, dict):
        return False
    try:
        from core.acquisition.pipeline_callback import (
            notify_pipeline_retry_exhausted,
        )
        return notify_pipeline_retry_exhausted(track_info, error=error)
    except Exception:
        logger.exception(
            "[Modal Worker] Could not persist Acquisition retry exhaustion")
        return False


def _resolve_worker_source(username):
    """Logical source bucket for a candidate's username (Soulseek peers all
    collapse to 'soulseek'; streaming sources keep their name). Mirrors the
    monitor's resolver — imported lazily to avoid an import cycle."""
    try:
        from core.downloads.monitor import _resolve_download_source
        return _resolve_download_source(username)
    except Exception:
        return 'soulseek'


def _cand_user_file(candidate):
    """Read (username, filename) from a candidate that may be a TrackResult
    object or a plain dict (tests / cached raw rows)."""
    if isinstance(candidate, dict):
        return candidate.get('username'), candidate.get('filename')
    return getattr(candidate, 'username', None), getattr(candidate, 'filename', None)


def _youtube_ytsearch_fallback(deps, query, track, tracks_result, profile_id=None):
    """ytsearch after a YouTube Music catalog batch that the matcher rejected.

    Catalog ``filter=songs`` misses remixes/lives that only exist as videos.
    One extra YouTube search. Skipped when this result set was not a catalog
    batch (empty catalog already fell through to ytsearch inside search()).
    """
    if not any(
        getattr(t, 'username', None) == 'youtube'
        and (getattr(t, '_source_metadata', None) or {}).get('catalog')
        for t in (tracks_result or [])
    ):
        return None
    orch = getattr(deps, 'download_orchestrator', None)
    if orch is None or not hasattr(orch, 'client'):
        return None
    try:
        youtube = orch.client('youtube')
    except Exception:
        return None
    if youtube is None or not hasattr(youtube, 'search'):
        return None
    try:
        extra, _ = deps.run_async(youtube.search(query, timeout=30, use_catalog=False))
    except TypeError:
        extra, _ = deps.run_async(youtube.search(query, timeout=30))
    except Exception as e:
        logger.debug("YouTube ytsearch fallback skipped: %s", e)
        return None
    if not extra:
        return None
    logger.info(
        "YouTube catalog missed the matcher; ytsearch returned %d rows",
        len(extra),
    )
    return deps.get_valid_candidates(extra, track, query, profile_id) or None


def _candidate_ordering(track_info: Optional[dict] = None):
    """Return ``(quality_first, targets)`` for the active search mode + toggle.

    The candidate walk is ordered by the user's profile quality rank
    (best→worst) instead of confidence-first when EITHER:
      - best-quality search mode is active (always quality-first), OR
      - priority mode and the ``rank_candidates_by_quality`` toggle is on
        (opt-in; default off keeps the byte-for-byte confidence-first walk).

    When ``track_info`` carries its own ``quality_profile_id`` (a wishlist row
    — see ``add_to_wishlist``/``core/downloads/master.py``), THAT profile's
    search_mode/rank_candidates_by_quality/ranked_targets are used instead of
    the global default, so per-item profile assignment actually changes
    download-time candidate ordering. Falls back to the global profile when
    absent (manual downloads, staging imports — unaffected).

    Quality-first ordering also makes the version-mismatch force-import pick
    the highest-quality candidate, because that fallback accepts the
    first-tried (= best-ordered) quarantined entry.

    Fails closed to confidence-first ordering on any error so a profile/DB
    hiccup never blocks a download. See
    docs/superpowers/specs/2026-06-14-best-quality-search-mode-design.md.
    """
    try:
        from core.quality.selection import targets_from_profile, load_profile_by_id

        profile_id = track_info.get('quality_profile_id') if track_info else None
        profile = load_profile_by_id(profile_id)
        if profile.get('search_mode') == 'best_quality' or profile.get('rank_candidates_by_quality'):
            targets, _ = targets_from_profile(profile)
            return True, targets
    except Exception as exc:
        logger.debug("[Modal Worker] quality ordering unavailable: %s", exc)
    return False, None


def _try_cached_candidates(task_id, batch_id, track, deps):
    """Quarantine-retry fast path: attempt the already-found candidates before
    re-searching anything.

    When a verified-bad file is re-queued, the connection was fine (the file
    downloaded, it was just the wrong/broken content) — so the next-best pick is
    almost always already sitting in ``cached_candidates``. Walk those (skipping
    sources already tried or budget-exhausted) and hand them to the normal
    download path. Returns True if a download was started; False to fall through
    to a fresh search (which only happens for a not-yet-searched source).
    """
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return False
        cached = list(task.get('cached_candidates') or [])
        used = set(task.get('used_sources') or ())
        exhausted = {str(s).lower() for s in (task.get('exhausted_download_sources') or ())}
        task_track_info = task.get('track_info')

    remaining = []
    for c in cached:
        uname, fname = _cand_user_file(c)
        if not uname or not fname:
            continue
        if f"{uname}_{fname}" in used:
            continue
        if _resolve_worker_source(uname).lower() in exhausted:
            continue
        remaining.append(c)

    if not remaining:
        return False

    logger.info(
        f"[Modal Worker] Quarantine retry: trying {len(remaining)} cached "
        f"candidate(s) before re-searching (task {task_id})"
    )
    _qf, _qt = _candidate_ordering(task_track_info)
    return deps.attempt_download_with_candidates(
        task_id, remaining, track, batch_id, quality_first=_qf, quality_targets=_qt,
    )


def _private_album_bundle_staging_miss_reason(batch_id: Optional[str], deps: Any) -> Optional[str]:
    """Return a user-facing miss reason when per-track search should stop.

    Torrent / usenet album batches first download one private staged release,
    then each track claims the matching staged file. If that claim fails after
    the release is already staged, falling through to the normal per-track
    search only retries release-level sources N times and can keep re-adding
    the same torrent/NZB. For those two sources we treat the staged release as
    authoritative for this pass.

    Soulseek is deliberately NOT short-circuited. A Soulseek album bundle stages
    whichever single folder scored best, and ``album_bundle_partial`` only
    reflects whether the files found IN that folder downloaded — not whether the
    folder actually contained every track the album needs. So a track the album
    needs but that wasn't in the chosen folder would otherwise be marked
    not_found with no fallback (#743). Unlike torrent/usenet, Soulseek per-track
    search is a genuine per-file network search — it doesn't re-add a release —
    so letting these misses fall through to the normal per-track flow (and, in
    hybrid mode, onward to the next source) is correct and cheap.
    """
    if not batch_id:
        return None

    batch = download_batches.get(batch_id)
    if not isinstance(batch, dict):
        return None

    source = (batch.get('album_bundle_source') or '').lower()
    mode = (getattr(deps.download_orchestrator, 'mode', '') or '').lower()
    hybrid_first = ''
    if mode == 'hybrid':
        order = getattr(deps.download_orchestrator, 'hybrid_order', None) or []
        if order:
            hybrid_first = str(order[0] or '').lower()
        else:
            hybrid_first = str(getattr(deps.download_orchestrator, 'hybrid_primary', '') or '').lower()
    if (
        batch.get('album_bundle_private_staging')
        and batch.get('album_bundle_state') == 'staged'
        and not batch.get('album_bundle_partial')
        and source in ('torrent', 'usenet')
        and (mode == source or (mode == 'hybrid' and hybrid_first == source))
    ):
        return f'Track was not found in the staged {source} album release'

    return None


@dataclass
class TaskWorkerDeps:
    """Bundle of cross-cutting deps the per-task download worker needs."""
    download_orchestrator: Any
    matching_engine: Any
    run_async: Callable
    try_source_reuse: Callable                    # (task_id, batch_id, track) -> bool
    store_batch_source: Callable                  # (batch_id, username, filename) -> None
    try_staging_match: Callable                   # (task_id, batch_id, track) -> bool
    get_valid_candidates: Callable                # (results, spotify_track, query, profile_id=None) -> list
    attempt_download_with_candidates: Callable    # (task_id, candidates, track, batch_id) -> bool
    on_download_completed: Callable               # (batch_id, task_id, success) -> None
    recover_worker_slot: Callable                 # (batch_id, task_id) -> None
    try_version_mismatch_fallback: Optional[Callable] = None  # (title, artist, task_id, batch_id) -> bool


# A sibling may only be skipped against once it has actually OBTAINED the file
# AND finished with it. 'searching'/'downloading' is a promise, not a file, and
# 'post_processing' is only a file on disk — the integrity, quality and AcoustID
# gates that run after it can still quarantine, requeue or fail that owner. A
# task that stood down against either is stranded with nothing and no retry
# (L2-003), so only genuinely terminal, successful owners count.
_SIBLING_OWNED_STATUSES = frozenset({'completed', 'already_owned'})

# Two runs of the same song rarely differ by more than tagging jitter; a gap
# this large means the sibling is a different recording (radio edit, live
# version, extended mix) and must not be deduped away.
_DEDUP_DURATION_TOLERANCE_MS = 5000


def _dedup_provider_identity(track_info: Any) -> Optional[tuple]:
    """``(namespace, id)`` naming the exact recording, or ``None``.

    Metadata alone cannot tell a remaster from its original — same title, same
    artist, same album title, different recording. When both sides carry an id
    in the SAME namespace it is authoritative in both directions: equal ids are
    the same recording, different ids are not.
    """
    from core.downloads.origin import _parse_source_info

    if not isinstance(track_info, dict):
        return None
    source_info = _parse_source_info(track_info.get('source_info'))
    lib2_id = source_info.get('lib2_track_id') or track_info.get('lib2_track_id')
    if lib2_id not in (None, ''):
        return ('lib2', str(lib2_id))
    source = str(source_info.get('source') or track_info.get('source') or '').strip().lower()
    provider_id = (track_info.get('provider_track_id')
                   or source_info.get('track_id')
                   or track_info.get('id'))
    if source and provider_id not in (None, ''):
        return (source, str(provider_id))
    return None


def _dedup_profile_id(track_info: Any) -> Optional[int]:
    """The quality profile this request was made under, if it declared one."""
    from core.downloads.origin import _parse_source_info

    if not isinstance(track_info, dict):
        return None
    raw = track_info.get('quality_profile_id')
    if raw in (None, ''):
        raw = _parse_source_info(track_info.get('source_info')).get('quality_profile_id')
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _dedup_int(track_info: Any, key: str) -> Optional[int]:
    if not isinstance(track_info, dict):
        return None
    raw = track_info.get(key)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _same_recording(mine: Any, theirs: Any) -> bool:
    """Do these two ``track_info`` payloads name the same recording?

    Provider identity decides when both sides speak the same namespace. Only
    when they don't do we fall back to metadata, and then the title/artist/album
    triple has to be backed by duration and disc/track agreement — the triple on
    its own conflates every alternate take of a song.
    """
    from core.downloads.status import track_info_identity

    mine_id = _dedup_provider_identity(mine)
    theirs_id = _dedup_provider_identity(theirs)
    if mine_id and theirs_id and mine_id[0] == theirs_id[0]:
        return mine_id[1] == theirs_id[1]

    # Both sides go through track_info_identity, so the artist normalisation is
    # identical. Building one side out of the SpotifyTrack's first artist only
    # (as this used to) made every collaboration credit a false negative.
    if track_info_identity(mine) != track_info_identity(theirs):
        return False

    mine_ms = _dedup_int(mine, 'duration_ms')
    theirs_ms = _dedup_int(theirs, 'duration_ms')
    if mine_ms and theirs_ms and abs(mine_ms - theirs_ms) > _DEDUP_DURATION_TOLERANCE_MS:
        return False

    for key in ('disc_number', 'track_number'):
        a, b = _dedup_int(mine, key), _dedup_int(theirs, key)
        if a and b and a != b:
            return False
    return True


def _find_owning_sibling(task_id: str, track: SpotifyTrack):
    """The task in ANOTHER batch that already owns this exact recording.

    Returns ``(other_task_id, other_task)``, or ``(None, None)``.

    Two concurrently-running batches routinely contain the same song (a
    playlist and the artist's album, two playlists sharing a hit). Both used to
    download and import it; the second import lands as "already owned" and
    leaves a second Completed row with no AcoustID badge, which reads as a
    failure.

    CROSS-batch only, deliberately. A task with no batch is a one-off the user
    asked for by hand — a re-download to get a better rip, say — and silently
    turning that into "you already have it" would take away an action they
    explicitly took. Within one batch the queue is the caller's own list, so a
    repeat there is also their choice.

    Three things must hold before we stand a task down (L2-003): the sibling has
    to name the SAME recording (``_same_recording``), it has to have finished
    successfully with a file to show for it, and it has to have been fetched
    under the same quality profile — otherwise a deliberate upgrade request
    silently inherits the low-quality copy it was meant to replace.
    """
    with tasks_lock:
        own = download_tasks.get(task_id) or {}
        own_batch = own.get('batch_id')
        if not own_batch:
            return None, None
        mine = own.get('track_info')
        if not isinstance(mine, dict):
            mine = {
                'name': getattr(track, 'name', ''),
                'artists': list(getattr(track, 'artists', None) or []),
                'album': getattr(track, 'album', ''),
                'duration_ms': getattr(track, 'duration_ms', 0),
            }
        from core.downloads.status import track_info_identity
        if not track_info_identity(mine)[0] and not _dedup_provider_identity(mine):
            # No title and no id is not an identity — it would match every other
            # untitled row.
            return None, None
        my_profile = _dedup_profile_id(mine)
        for other_id, other in download_tasks.items():
            if other_id == task_id or other.get('batch_id') == own_batch:
                continue
            if other.get('status') not in _SIBLING_OWNED_STATUSES:
                continue
            if not (other.get('file_path') or other.get('filename')):
                # Terminal but with nothing on disk to point at — treating that
                # as ownership hands this task a success it has no file for.
                continue
            if _dedup_profile_id(other.get('track_info')) != my_profile:
                continue
            if _same_recording(mine, other.get('track_info')):
                return other_id, other
    return None, None


def download_track_worker(task_id: str, batch_id: Optional[str], deps: TaskWorkerDeps) -> None:
    """Enhanced download worker that matches the GUI's exact retry logic.

    Implements sequential query retry, fallback candidates, and download
    failure retry.
    """
    try:
        # Retrieve task details from global state
        with tasks_lock:
            _task_missing = task_id not in download_tasks
            if not _task_missing:
                task = download_tasks[task_id].copy()
        if _task_missing:
            logger.warning(f"[Modal Worker] Task {task_id} not found in download_tasks")
            # The task was deleted between dispatch and this worker running (cleanup,
            # dedup, or an atomic cancel). Every OTHER worker exit frees the batch
            # slot via on_download_completed; this path must too, or the reserved
            # slot leaks — active_count stays inflated, the next queued task never
            # starts, and the batch can never reach active_count==0 to complete, so
            # it wedges in 'downloading' forever (healing loops every 30s without
            # progress; enough leaks exhaust the shared worker pool). Call it OUTSIDE
            # tasks_lock: the callback re-acquires it and tasks_lock is non-reentrant.
            # on_download_completed is idempotent (_completed_task_ids dedup), so this
            # is a no-op if the slot was already freed.
            if batch_id:
                deps.on_download_completed(batch_id, task_id, False)
            return

        # Cancellation Checkpoint 1: Before doing anything
        _cancelled_before_start = False
        _free_legacy_slot = False
        with tasks_lock:
            if task_id not in download_tasks:
                logger.info(f"[Modal Worker] Task {task_id} was deleted before starting")
                return
            if download_tasks[task_id]['status'] == 'cancelled':
                _cancelled_before_start = True
                logger.warning(f"[Modal Worker] Task {task_id} cancelled before starting")
                # V2 FIX: Don't call _on_download_completed for cancelled V2 tasks
                # V2 system handles worker slot freeing in atomic cancel function
                task_playlist_id = download_tasks[task_id].get('playlist_id')
                if task_playlist_id:
                    logger.warning(f"[Modal Worker] V2 task {task_id} cancelled - worker slot already freed by V2 system")
                elif batch_id:
                    # Legacy system - use old completion callback (fired OUTSIDE the
                    # lock below).
                    logger.warning(f"[Modal Worker] Legacy task {task_id} cancelled - using legacy completion callback")
                    _free_legacy_slot = True
        if _cancelled_before_start:
            # Free the legacy slot OUTSIDE tasks_lock: on_download_completed
            # re-acquires it, and tasks_lock is a plain non-reentrant Lock — calling
            # it in-lock deadlocked the worker WHILE HOLDING the global lock, which
            # freezes the entire download subsystem. Idempotent (_completed_task_ids
            # dedup), so it's a no-op if the slot was already freed by atomic cancel.
            if _free_legacy_slot:
                deps.on_download_completed(batch_id, task_id, False)
            return

        track_data = task['track_info']
        track_name = track_data.get('name', 'Unknown Track')

        logger.info(f"[Modal Worker] Task {task_id} starting search for track: '{track_name}'")

        # Recreate a SpotifyTrack object for the matching engine
        # Handle both string format and Spotify API format for artists
        raw_artists = track_data.get('artists', [])
        processed_artists = []
        for artist in raw_artists:
            if isinstance(artist, str):
                processed_artists.append(artist)
            elif isinstance(artist, dict) and 'name' in artist:
                processed_artists.append(artist['name'])
            else:
                processed_artists.append(str(artist))

        # Handle album field - extract name if it's a dictionary
        raw_album = track_data.get('album', '')
        if isinstance(raw_album, dict) and 'name' in raw_album:
            album_name = raw_album['name']
        elif isinstance(raw_album, str):
            album_name = raw_album
        else:
            album_name = str(raw_album)

        track = SpotifyTrack(
            id=track_data.get('id', ''),
            name=track_data.get('name', ''),
            artists=processed_artists,
            album=album_name,
            duration_ms=track_data.get('duration_ms', 0),
            popularity=track_data.get('popularity', 0),
        )
        logger.info(f"[Modal Worker] Starting download task for: {track.name} by {track.artists[0] if track.artists else 'Unknown'}")

        # === CROSS-BATCH DEDUP: has a sibling batch already obtained this file? ===
        _owner_id, _owner = _find_owning_sibling(task_id, track)
        if _owner_id:
            logger.info(
                "[Modal Worker] Task %s: '%s' is already owned by task %s — "
                "skipping the download instead of importing a second copy",
                task_id, track.name, _owner_id)
            with tasks_lock:
                if task_id in download_tasks:
                    _row = download_tasks[task_id]
                    _row['status'] = 'already_owned'
                    _row['_dedup_owned_by'] = _owner_id
                    # Inherit the owner's outcome. Without this the row shows a
                    # blank quality and an empty verification badge, which reads
                    # as "this one failed" for a track that is present and fine.
                    for _field in ('verification_status', 'quality', 'file_path',
                                   'download_source'):
                        if _owner.get(_field) is not None:
                            _row[_field] = _owner[_field]
            if batch_id:
                deps.on_download_completed(batch_id, task_id, True)
            return

        # === SOURCE REUSE: Check batch's last good source before searching ===
        if deps.try_source_reuse(task_id, batch_id, track):
            # Store source for next worker (cascading reuse)
            with tasks_lock:
                used_filename = download_tasks.get(task_id, {}).get('filename')
                used_username = download_tasks.get(task_id, {}).get('username')
            if used_filename and used_username:
                deps.store_batch_source(batch_id, used_username, used_filename)
            return

        # === STAGING CHECK: Check staging folder for existing file before searching ===
        if deps.try_staging_match(task_id, batch_id, track):
            return
        staging_miss_reason = _private_album_bundle_staging_miss_reason(batch_id, deps)
        if staging_miss_reason:
            logger.warning(
                "[Modal Worker] %s for '%s'; skipping redundant per-track %s search",
                staging_miss_reason,
                track.name,
                getattr(deps.download_orchestrator, 'mode', 'release-source'),
            )
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'not_found'
                    download_tasks[task_id]['error_message'] = staging_miss_reason
            if batch_id:
                deps.on_download_completed(batch_id, task_id, False)
            return

        # Initialize task state tracking (like GUI's parallel_search_tracking)
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'searching'  # Now actively being processed
                download_tasks[task_id]['current_query_index'] = 0
                download_tasks[task_id]['current_candidate_index'] = 0
                download_tasks[task_id]['retry_count'] = 0
                download_tasks[task_id]['candidates'] = []
                # CRITICAL: Preserve used_sources from previous retry attempts (don't reset to empty set)
                # If this is a retry, the monitor will have already marked failed sources
                if 'used_sources' not in download_tasks[task_id]:
                    download_tasks[task_id]['used_sources'] = set()
                # Else: keep existing used_sources to avoid retrying same failed hosts

        # Cached-first quarantine retry. The monitor sets ``_quarantine_retry``
        # when a verified-bad file is re-queued; in that case we walk the
        # already-found candidates before re-searching (the connection was fine,
        # just the content was wrong). A NON-quarantine entry (fresh download, or
        # the monitor's dead-connection/stuck retry) instead starts a new search
        # generation: clear the searched-source memory so each source can be
        # searched fresh again.
        with tasks_lock:
            _t = download_tasks.get(task_id, {})
            is_quarantine_retry = bool(_t.pop('_quarantine_retry', False))
            if not is_quarantine_retry:
                _t.pop('searched_queries', None)
        if is_quarantine_retry and _try_cached_candidates(task_id, batch_id, track, deps):
            with tasks_lock:
                used_filename = download_tasks.get(task_id, {}).get('filename')
                used_username = download_tasks.get(task_id, {}).get('username')
            if used_filename and used_username:
                deps.store_batch_source(batch_id, used_username, used_filename)
            return

        # 1. Generate multiple search queries (like GUI's generate_smart_search_queries)
        artist_name = track.artists[0] if track.artists else None
        track_name = track.name

        release_queries = []
        try:
            _download_mode = (getattr(deps.download_orchestrator, 'mode', '') or '').lower()
            _track_album = (getattr(track, 'album', '') or '').strip()
            _track_title = (getattr(track, 'name', '') or '').strip()
            _track_artists = list(getattr(track, 'artists', []) or [])
            _first_artist = _track_artists[0] if _track_artists else ''
            _primary_artist = (
                (_first_artist.get('name', '') if isinstance(_first_artist, dict) else str(_first_artist))
                or ''
            ).strip()
            if (
                _download_mode in ('torrent', 'usenet')
                and _primary_artist
                and _track_album
                and _track_album.lower() not in ('unknown album', _track_title.lower())
            ):
                release_queries.append(f"{_primary_artist} {_track_album}".strip())
        except Exception as _release_query_exc:
            logger.debug("[Modal Worker] release query hint failed: %s", _release_query_exc)

        # Start with matching engine queries
        search_queries = deps.matching_engine.generate_download_queries(track)

        # Add legacy fallback queries (like GUI does)
        legacy_queries = []

        if artist_name:
            # Add first word of artist approach (legacy compatibility)
            artist_words = artist_name.split()
            if artist_words:
                first_word = artist_words[0]
                if first_word.lower() == 'the' and len(artist_words) > 1:
                    first_word = artist_words[1]

                if len(first_word) > 1:
                    legacy_queries.append(f"{track_name} {first_word}".strip())

        # Add track-only query only when it is distinctive enough to broadcast.
        # generate_download_queries() already enforces this guard; the legacy
        # fallback must honor the same contract or short wishlist titles like
        # "Vortex" still fan out to hundreds of noisy Soulseek responses.
        if (
            track_name.strip()
            and deps.matching_engine._title_is_distinctive_enough_to_broadcast(track_name.strip())
        ):
            legacy_queries.append(track_name.strip())

        # Add traditional cleaned queries
        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()

        if (
            cleaned_name
            and cleaned_name.lower() != track_name.lower()
            and deps.matching_engine._title_is_distinctive_enough_to_broadcast(cleaned_name)
        ):
            legacy_queries.append(cleaned_name.strip())

        # Combine enhanced queries with legacy fallbacks.
        #
        # Torrent / usenet can use full album releases as a fallback for
        # single-track requests, but trying the album release first makes
        # playlist batches download whole albums before checking whether a
        # track-shaped release exists. Keep release queries last so singles
        # stay light when the indexer has a direct result.
        all_queries = search_queries + legacy_queries + release_queries

        # Remove duplicates while preserving order
        unique_queries = []
        seen = set()
        for query in all_queries:
            if query and query.lower() not in seen:
                unique_queries.append(query)
                seen.add(query.lower())

        search_queries = unique_queries
        # Where we're about to look, for the live status payload (#1156). The
        # chain label is what the orchestrator will actually walk; the hybrid
        # fallback loop below overwrites it with the specific source it tries.
        _mode = (getattr(deps.download_orchestrator, 'mode', '') or '').lower()
        if _mode == 'hybrid':
            _order = list(getattr(deps.download_orchestrator, 'hybrid_order', None) or [])
            if not _order:
                _order = [str(getattr(deps.download_orchestrator, 'hybrid_primary', '') or 'soulseek')]
            _chain_label = ' → '.join(str(s) for s in _order if s) or 'soulseek'
        else:
            _chain_label = _mode or 'soulseek'
        # Expose the query count so the quarantine-retry budget (exhaustive mode)
        # can size each source's budget as query_count × retries_per_query.
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['query_count'] = len(search_queries)
                download_tasks[task_id]['current_source'] = _chain_label
        logger.info(f"[Modal Worker] Generated {len(search_queries)} smart search queries for '{track.name}': {search_queries}")
        logger.info(f"[Modal Worker] About to start search loop for task {task_id} (track: '{track.name}')")

        # Best-quality search mode: the orchestrator already pooled candidates
        # across every source for each query, so order the candidate walk by the
        # user's profile quality rank (best→worst). Computed once per task.
        _best_quality, _quality_targets = _candidate_ordering(track_data)
        # The item's own quality profile, if it has one (wishlist rows carry it).
        # Ordering above already honours it; the Soulseek quality FILTER didn't,
        # so per-item profiles changed what survived import but not what was
        # considered in the first place (#1150). None = app-wide default.
        _profile_id = track_data.get('quality_profile_id') if isinstance(track_data, dict) else None
        from core.quality.selection import load_search_mode
        _search_mode = load_search_mode(_profile_id)

        # 2. Sequential Query Search (matches GUI's start_search_worker_parallel logic)
        search_diagnostics = []  # Track what happened per query for detailed error messages
        all_raw_results = []  # Collect raw results across queries for candidate review modal
        # Sources whose per-source quarantine-retry budget is spent (exhaustive
        # mode). The monitor sets this when a source gives up; we exclude those
        # sources from the hybrid search so the chain falls through to the next
        # source instead of re-fetching the same exhausted one (e.g. Soulseek
        # keeps returning fresh wrong peers — once its budget is gone, switch to
        # HiFi/Tidal/…). See monitor.requeue_quarantined_task_for_retry.
        #
        # On a quarantine retry we do NOT exclude a source just because it was
        # searched once: the first run only ran ONE query before starting a
        # download, so the later queries (e.g. "artist + album") have never hit
        # that source yet and may surface the correct upload. Instead we remember
        # which QUERIES already ran (``searched_queries``) and skip re-running
        # only those — their candidates are walked via the cached-first path
        # above. The not-yet-searched queries still search the same source, so
        # every query is exhausted per source before the chain switches sources.
        # Fresh / dead-connection runs cleared searched_queries above, so they
        # search everything again.
        with tasks_lock:
            _t = download_tasks.get(task_id, {})
            _exhausted_sources = [str(s) for s in (_t.get('exhausted_download_sources') or ())]
            _searched_queries = (
                set(_t.get('searched_queries') or ()) if is_quarantine_retry else set()
            )
        for query_index, query in enumerate(search_queries):
            # Cancellation check before each query
            with tasks_lock:
                if task_id not in download_tasks:
                    logger.debug(f"[Modal Worker] Task {task_id} was deleted during query {query_index + 1}")
                    return
                if download_tasks[task_id]['status'] == 'cancelled':
                    logger.debug(f"[Modal Worker] Task {task_id} cancelled during query {query_index + 1}")
                    # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                    return
                download_tasks[task_id]['current_query_index'] = query_index
                download_tasks[task_id]['current_query'] = query
                # each query gets a fresh live ticker
                download_tasks[task_id].pop('search_live', None)

            # Cached-first: a query already run last generation has its candidates
            # sitting in cache (walked above) — re-searching it is the wasteful
            # repeat the cached-first design removes. Skip it; the not-yet-run
            # queries below still search this source.
            if is_quarantine_retry and query in _searched_queries:
                logger.debug(
                    f"[Modal Worker] Skipping already-searched query '{query}' "
                    f"(candidates served from cache) for task {task_id}"
                )
                continue

            logger.debug(f"[Modal Worker] Query {query_index + 1}/{len(search_queries)}: '{query}'")
            logger.debug(f"About to call soulseek search for task {task_id}")

            try:
                # Hybrid + album-context batches must skip torrent / usenet during
                # the per-track loop — they're release-level sources, can't match
                # individual tracks meaningfully, and album-bundle handling only
                # fires in single-source mode (see core/downloads/master.py). The
                # exclusion lets the hybrid chain fall through to per-track-
                # compatible sources (soulseek / streaming) instead of attempting
                # N redundant Prowlarr searches that all download the same album
                # torrent and rely on the auto-import sweep to clean up.
                _exclude_for_hybrid_album = None
                try:
                    _batch_is_album = False
                    if batch_id:
                        from core.runtime_state import download_batches as _db
                        _b = _db.get(batch_id)
                        if isinstance(_b, dict):
                            _batch_is_album = bool(_b.get('is_album_download'))
                    if _batch_is_album and getattr(deps.download_orchestrator, 'mode', '') == 'hybrid':
                        _exclude_for_hybrid_album = ['torrent', 'usenet']
                except Exception as _exc_filter_err:
                    logger.debug("[Modal Worker] album-source-exclusion check failed: %s", _exc_filter_err)
                # Fold in budget-exhausted sources (per-source quarantine retry).
                _exclude_sources = list(_exhausted_sources)
                if _exclude_for_hybrid_album:
                    _exclude_sources.extend(_exclude_for_hybrid_album)
                # Live search ticker (#1156): slskd's poller fires this on every
                # response tick — the hook existed through the whole plugin chain
                # but the download path never passed one.
                #
                # NO tasks_lock in here, and that is load-bearing: this callback
                # runs ON the shared async-loop thread (utils/async_helpers runs
                # one loop for the whole process), and other threads hold
                # tasks_lock while BLOCKING on that loop (candidates.py's
                # cancel-after-start does run_async inside the lock). The loop
                # waiting on the lock while the lock-holder waits on the loop
                # deadlocks every download in the process. Single dict-item
                # writes are atomic under the GIL; the worst race is a tick
                # landing on a task that just went terminal, which the status
                # builder ignores (live_detail is only built for live states).
                def _search_progress(found_tracks, _found_albums, response_count, _q=query):
                    try:
                        _t = download_tasks.get(task_id)
                        if _t is not None and _t.get('status') == 'searching':
                            _t['search_live'] = {
                                'responses': response_count,
                                'results': len(found_tracks or []),
                            }
                    except Exception as _tick_exc:
                        logger.debug("[Modal Worker] search ticker failed: %s", _tick_exc)

                # Perform search with timeout
                _search_kwargs = {
                    'timeout': 30,
                    'exclude_sources': _exclude_sources or None,
                    'progress_callback': _search_progress,
                    'search_mode': _search_mode,
                }
                # Upstream's delta: an assigned item profile decides whether
                # hybrid search stops at the first source or pools every source
                # for best-quality selection. Passed conditionally so the old
                # call shape (and light-weight test doubles) still work for a
                # default-profile task.
                if _profile_id is not None:
                    _search_kwargs['quality_profile_id'] = _profile_id
                tracks_result, _ = deps.run_async(
                    deps.download_orchestrator.search(query, **_search_kwargs)
                )
                logger.debug(f"Search completed for task {task_id}, got {len(tracks_result) if tracks_result else 0} results")

                # CRITICAL: Check cancellation immediately after search returns
                with tasks_lock:
                    if task_id not in download_tasks:
                        logger.info(f"[Modal Worker] Task {task_id} was deleted after search returned")
                        return
                    # Remember this query ran so a later quarantine retry skips
                    # re-searching it (its candidates are walked via cached-first).
                    # Recorded regardless of result count: re-running a query is
                    # deterministic, so a query that returned nothing won't return
                    # anything new next time either.
                    _sq = download_tasks[task_id].get('searched_queries')
                    if not isinstance(_sq, set):
                        _sq = set()
                    _sq.add(query)
                    download_tasks[task_id]['searched_queries'] = _sq
                    # Final per-source split for this query (#1156) — best-quality
                    # pools mix every source into one list, and the split is the
                    # "soulseek 12 · youtube 3" narration the live view shows.
                    try:
                        _by_source = {}
                        for _r in (tracks_result or []):
                            _bucket = _resolve_worker_source(getattr(_r, 'username', ''))
                            _by_source[_bucket] = _by_source.get(_bucket, 0) + 1
                        _live = {'results': len(tracks_result or [])}
                        # keep the ticker's peer count — the split replaces the
                        # running totals, not the fact that N peers answered
                        _prev_live = download_tasks[task_id].get('search_live')
                        if isinstance(_prev_live, dict) and _prev_live.get('responses') is not None:
                            _live['responses'] = _prev_live['responses']
                        if _by_source:
                            _live['by_source'] = _by_source
                        download_tasks[task_id]['search_live'] = _live
                    except Exception as _live_exc:
                        logger.debug("[Modal Worker] search_live split failed: %s", _live_exc)
                    if download_tasks[task_id]['status'] == 'cancelled':
                        logger.warning(f"[Modal Worker] Task {task_id} cancelled after search returned - ignoring results")
                        # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                        # The cancellation endpoint already handles batch management properly
                        return

                if tracks_result:
                    result_count = len(tracks_result)
                    # Validate candidates using GUI's get_valid_candidates logic
                    candidates = deps.get_valid_candidates(tracks_result, track, query, _profile_id)
                    if not candidates:
                        # Catalog-first YouTube can return official songs that
                        # all fail the matcher (obscure remix, live, etc.).
                        # ytsearch still finds those as videos.
                        extra = _youtube_ytsearch_fallback(
                            deps, query, track, tracks_result, _profile_id)
                        if extra:
                            candidates = extra
                    if candidates:
                        logger.debug(f"[Modal Worker] Found {len(candidates)} valid candidates for query '{query}'")

                        # CRITICAL: Check cancellation before processing candidates
                        with tasks_lock:
                            if task_id not in download_tasks:
                                logger.info(f"[Modal Worker] Task {task_id} was deleted before processing candidates")
                                return
                            if download_tasks[task_id]['status'] == 'cancelled':
                                logger.warning(f"[Modal Worker] Task {task_id} cancelled before processing candidates")
                                # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                                return
                            # Store candidates for retry fallback (like GUI). A
                            # later quarantine retry walks these via cached-first
                            # and skips re-searching this query (searched_queries).
                            download_tasks[task_id]['cached_candidates'] = candidates

                        # Try to download with these candidates
                        success = deps.attempt_download_with_candidates(
                            task_id, candidates, track, batch_id,
                            quality_first=_best_quality, quality_targets=_quality_targets,
                        )
                        if success:
                            # Download initiated successfully - let the download monitoring system handle completion
                            if batch_id:
                                logger.info(f"[Modal Worker] Download initiated successfully for task {task_id} - monitoring will handle completion")
                            # Store this source for batch reuse
                            with tasks_lock:
                                used_filename = download_tasks.get(task_id, {}).get('filename')
                                used_username = download_tasks.get(task_id, {}).get('username')
                            if used_filename and used_username:
                                deps.store_batch_source(batch_id, used_username, used_filename)
                            return  # Success, exit the worker
                        else:
                            search_diagnostics.append(f'"{query}": {result_count} results, {len(candidates)} passed filters but download failed to start')
                    else:
                        search_diagnostics.append(f'"{query}": {result_count} results but none passed quality/artist filters')
                        # Strip SoundCloud preview snippets before caching for the
                        # review modal — the user can't pick something useful from
                        # a 30s preview clip, and clicking one bypasses validation
                        # and downloads it anyway.
                        from core.downloads.validation import filter_soundcloud_previews
                        _filtered_raw = filter_soundcloud_previews(tracks_result[:20], track)
                        all_raw_results.extend(_filtered_raw)
                else:
                    search_diagnostics.append(f'"{query}": no results found')

            except Exception as e:
                logger.debug(f"[Modal Worker] Search failed for query '{query}': {e}")
                search_diagnostics.append(f'"{query}": search error — {e}')
                continue

        # === HYBRID FALLBACK: If primary source failed, try remaining sources directly ===
        # The orchestrator's hybrid search stops at the first source with results, even if
        # those results all fail quality filtering. Try remaining sources individually.
        #
        # Best-quality mode already searched EVERY source per query (the pool), so this
        # block would only re-search the same sources — skip it there.
        if not _best_quality and getattr(deps.download_orchestrator, 'mode', '') == 'hybrid':
            try:
                orch = deps.download_orchestrator
                hybrid_order = getattr(orch, 'hybrid_order', None) or []
                if not hybrid_order:
                    primary = getattr(orch, 'hybrid_primary', 'soulseek')
                    secondary = getattr(orch, 'hybrid_secondary', '')
                    hybrid_order = [primary, secondary] if secondary and secondary != primary else [primary]

                # Resolve via the orchestrator's generic accessor — the
                # legacy per-source attrs were dropped in the registry
                # refactor, so getattr(orch, 'soulseek', None) etc. all
                # silently returned None and the fallback never fired.
                source_clients = {
                    name: orch.client(name)
                    for name in ('soulseek', 'youtube', 'tidal', 'qobuz',
                                 'hifi', 'deezer_dl', 'lidarr', 'soundcloud', 'amazon')
                }

                # The orchestrator tried sources in order but stopped at the first with results.
                # We don't know which it stopped at, so try ALL sources except the first
                # (which was definitely tried). If the first was skipped (unconfigured),
                # the orchestrator would have tried the second — but trying it again is
                # harmless (streaming sources return fast).
                _exhausted_lower = {s.lower() for s in _exhausted_sources}
                remaining_sources = [
                    s for s in hybrid_order[1:]
                    if s in source_clients and source_clients[s]
                    and s.lower() not in _exhausted_lower
                ]
                if remaining_sources:
                    logger.warning(f"[Hybrid Fallback] Primary source had no valid matches. Trying fallback sources: {remaining_sources}")

                for fallback_source in remaining_sources:
                    fb_client = source_clients[fallback_source]
                    if hasattr(fb_client, 'is_configured') and not fb_client.is_configured():
                        continue

                    # Use first 2 queries only for speed
                    for fb_query in search_queries[:2]:
                        try:
                            logger.warning(f"[Hybrid Fallback] Trying {fallback_source}: '{fb_query}'")
                            with tasks_lock:
                                if task_id in download_tasks:
                                    download_tasks[task_id]['current_source'] = fallback_source
                                    download_tasks[task_id]['current_query'] = fb_query
                                    download_tasks[task_id].pop('search_live', None)
                            fb_results, _ = deps.run_async(fb_client.search(fb_query, timeout=20))
                            if not fb_results:
                                continue
                            fb_candidates = deps.get_valid_candidates(fb_results, track, fb_query, _profile_id)
                            if not fb_candidates:
                                extra = _youtube_ytsearch_fallback(
                                    deps, fb_query, track, fb_results, _profile_id)
                                if extra:
                                    fb_candidates = extra
                            if fb_candidates:
                                logger.warning(f"[Hybrid Fallback] {fallback_source} found {len(fb_candidates)} valid candidates!")
                                with tasks_lock:
                                    if task_id in download_tasks:
                                        download_tasks[task_id]['cached_candidates'] = fb_candidates
                                success = deps.attempt_download_with_candidates(task_id, fb_candidates, track, batch_id)
                                if success:
                                    return
                        except Exception as e:
                            logger.error(f"[Hybrid Fallback] {fallback_source} search failed: {e}")
                            continue

                    logger.warning(f"[Hybrid Fallback] {fallback_source} returned no valid candidates")

            except Exception as e:
                logger.error(f"[Hybrid Fallback] Error in fallback logic: {e}")

        # If we get here, all search queries and hybrid fallbacks failed
        logger.warning(f"[Modal Worker] No valid candidates found for '{track.name}' after trying all {len(search_queries)} queries.")

        # Last-resort: quarantine retry with no new candidates — the retry search
        # exhausted all sources.  If the setting is enabled, accept the best
        # already-quarantined candidate rather than leaving the track missing.
        if is_quarantine_retry and deps.try_version_mismatch_fallback:
            _fallback_artist = track.artists[0] if track.artists else ''
            if deps.try_version_mismatch_fallback(track.name, _fallback_artist, task_id, batch_id):
                return  # fallback re-dispatched; batch completion handled by reprocess thread

        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'not_found'
                _diag_summary = ' | '.join(search_diagnostics) if search_diagnostics else 'no queries attempted'
                download_tasks[task_id]['error_message'] = f'No match found for "{track_name}" by {artist_name or "Unknown"} after {len(search_queries)} queries. Breakdown: {_diag_summary}'
                # Store raw results so the user can review what Soulseek returned
                if all_raw_results and not download_tasks[task_id].get('cached_candidates'):
                    download_tasks[task_id]['cached_candidates'] = all_raw_results

        _notify_acquisition_retry_exhausted(
            track_data,
            f'No match found after {len(search_queries)} shared-pipeline queries',
        )

        # Notify batch manager that this task completed (failed) - THREAD SAFE
        if batch_id:
            try:
                deps.on_download_completed(batch_id, task_id, False)
            except Exception as completion_error:
                logger.error(f"Error in batch completion callback for {task_id}: {completion_error}")

    except Exception as e:
        track_name_safe = locals().get('track_name', 'unknown')  # Safe fallback for track_name
        logger.error(f"CRITICAL ERROR in download task for '{track_name_safe}' (task_id: {task_id}): {e}")
        traceback.print_exc()

        # Update task status safely with timeout
        try:
            lock_acquired = tasks_lock.acquire(timeout=2.0)
            if lock_acquired:
                try:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        download_tasks[task_id]['error_message'] = f'Unexpected error during download: {type(e).__name__}: {e}'
                        logger.error(f"[Exception Recovery] Set task {task_id} status to 'failed'")
                finally:
                    tasks_lock.release()
            else:
                logger.error(f"[Exception Recovery] Could not acquire lock to update task {task_id} status")
        except Exception as status_error:
            logger.error(f"Error updating task status in exception handler: {status_error}")

        task_info = locals().get('track_data')
        _notify_acquisition_retry_exhausted(
            task_info,
            f'Unexpected shared-pipeline retry error: {type(e).__name__}',
        )

        # Notify batch manager that this task completed (failed) - THREAD SAFE with RECOVERY
        if batch_id:
            try:
                deps.on_download_completed(batch_id, task_id, False)
                logger.error(f"[Exception Recovery] Successfully freed worker slot for task {task_id}")
            except Exception as completion_error:
                logger.error(f"[Exception Recovery] Error in batch completion callback for {task_id}: {completion_error}")
                # CRITICAL: If batch completion fails, we need to manually recover the worker slot
                try:
                    logger.error(f"[Exception Recovery] Attempting manual worker slot recovery for batch {batch_id}")
                    deps.recover_worker_slot(batch_id, task_id)
                except Exception as recovery_error:
                    logger.error(f"[Exception Recovery] FATAL: Could not recover worker slot: {recovery_error}")
