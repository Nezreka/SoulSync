"""Wishlist processing helpers."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.downloads.history import ALBUM_PREFIXES
from core.downloads.origin import HAND_PICKED_ID_PREFIXES
from core.wishlist.payloads import build_failed_track_wishlist_context
from core.wishlist.selection import filter_wishlist_tracks_by_category, sanitize_and_dedupe_wishlist_tracks
from core.wishlist.service import get_wishlist_service
from core.wishlist.state import get_wishlist_cycle, set_wishlist_cycle
from utils.logging_config import get_logger


module_logger = get_logger("wishlist.processing")
logger = module_logger


# Album-bundle is wasteful for single-track wishlist items (downloads
# the entire album to claim one file), so the bundle path only engages
# when an album has ``N`` or more missing tracks in the wishlist. Default
# 2 catches the "this user is missing several tracks from one album"
# case while keeping single-track-per-album items on the cheaper
# per-track flow. Configurable via ``wishlist.album_bundle_min_tracks``
# for users who want different behaviour.
_DEFAULT_ALBUM_BUNDLE_MIN_TRACKS = 2


def _resolve_album_bundle_threshold() -> int:
    """Return the configured min-tracks-per-album threshold for the
    wishlist album-bundle grouper. Falls back to the default when the
    config key is missing or carries garbage — same defensive shape as
    the rest of the config-driven knobs in core/."""
    try:
        from core.settings import config_manager
        raw = config_manager.get('wishlist.album_bundle_min_tracks',
                                 _DEFAULT_ALBUM_BUNDLE_MIN_TRACKS)
        value = int(raw)
        if value >= 1:
            return value
    except Exception:  # noqa: S110 — defensive config-read fallback; uses default below
        pass
    return _DEFAULT_ALBUM_BUNDLE_MIN_TRACKS


@dataclass
class WishlistAutoProcessingRuntime:
    """Dependencies needed to run automatic wishlist processing outside the controller."""

    processing_guard: Callable[[], AbstractContextManager[bool]]
    is_actually_processing: Callable[[], bool]
    app_context_factory: Callable[[], AbstractContextManager[Any]]
    get_profiles_database: Callable[[], Any]
    get_music_database: Callable[[], Any]
    download_batches: Dict[str, Dict[str, Any]]
    tasks_lock: Any
    update_automation_progress: Callable[..., Any]
    automation_engine: Any
    missing_download_executor: Any
    run_full_missing_tracks_process: Callable[..., Any]  # (batch_id, playlist_id, tracks, serialize=False)
    get_batch_max_concurrent: Callable[[], int]
    get_active_server: Callable[[], str]
    current_time_fn: Callable[[], float]
    profile_id: int = 1
    logger: Any = module_logger
    # Dedicated pool for the inline-blocking per-album bundle downloads.
    # Album-bundle batches block their worker thread for the whole search +
    # download; running them on the shared ``missing_download_executor`` lets a
    # burst of album batches (e.g. a big Album-Completeness "Fix all" → wishlist)
    # starve the per-track flow AND the user's manual "Download Wishlist" (#740).
    # Routing them here keeps the shared pool free. Falls back to the shared
    # executor when unset (older callers / tests) — see the submit site below.
    album_bundle_executor: Any = None


def remove_completed_tracks_from_wishlist(
    batch: Dict[str, Any],
    download_tasks: Dict[str, Dict[str, Any]],
    remove_from_wishlist: Callable[[Dict[str, Any]], Any],
    *,
    logger=logger,
) -> int:
    """Remove completed batch tasks from the wishlist."""
    removed_count = 0
    for task_id in batch.get('queue', []):
        if task_id in download_tasks:
            task = download_tasks[task_id]
            if task.get('status') == 'completed':
                try:
                    track_info = task.get('track_info', {})
                    context = {'track_info': track_info, 'original_search_result': track_info}
                    # #1289: same proof the per-track callback needs. 'completed'
                    # is a task state, not a library state — the verification
                    # worker's no-context branch completes a task whose file
                    # never left the downloads folder, and an atomic batch's
                    # tasks complete while their files are still staged (those
                    # were already settled by the publish itself).
                    remove_from_wishlist(context, published_path=task.get('final_file_path'),
                                         quiet_refusal=True)
                    removed_count += 1
                except Exception as exc:
                    logger.error(f"[Wishlist Processing] Error removing completed track from wishlist: {exc}")
    return removed_count


def _tag_wishlist_owner(tracks, profile_id) -> None:
    """Mark each wishlist track with the profile it belongs to and the library
    that profile fills. A run spanning several profiles dispatches each one's
    tracks under its own profile -- which is also where its downloads land
    (#1199): the batch's library follows its profile -- and deduplicates per
    library, never across two of them."""
    from core.library_scope import library_scope_for_profile, owner_for_scope
    library = owner_for_scope(library_scope_for_profile(profile_id))
    for track in tracks or []:
        if isinstance(track, dict):
            track['_wishlist_profile_id'] = profile_id
            track['_wishlist_library'] = library
            # what upstream's per-owner steps read (H10 batching, the S13
            # backoff clear)
            track['profile_id'] = profile_id


def make_wishlist_batch_row(
    *,
    playlist_id: str,
    playlist_name: str,
    track_count: int,
    max_concurrent: int,
    profile_id: int,
    phase: str,
    run_id: str | None = None,
    is_album: bool = False,
    album_context: Optional[Dict[str, Any]] = None,
    artist_context: Optional[Dict[str, Any]] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Single source of truth for a wishlist ``download_batches`` row.

    The auto and manual wishlist flows used to build this ~20-field dict in four
    separate places, which let their batch shapes silently drift apart. They now
    all go through here so every wishlist batch has an IDENTICAL field shape; the
    genuinely per-flow differences (initial ``phase``, the auto-only
    ``auto_initiated`` / ``current_cycle`` fields, album vs residual contexts) are
    explicit arguments / ``extra_fields``.

    NOTE: this builds the row only — it does NOT decide grouping, batch-id
    allocation, or dispatch (parallel-submit vs serial), which legitimately
    differ between the flows and stay in their callers.
    """
    row: Dict[str, Any] = {
        'phase': phase,
        'playlist_id': playlist_id,
        'playlist_name': playlist_name,
        'queue': [],
        'active_count': 0,
        'max_concurrent': max_concurrent,
        'queue_index': 0,
        'analysis_total': track_count,
        'analysis_processed': 0,
        'analysis_results': [],
        'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
        'force_download_all': True,
        'profile_id': profile_id,
        'is_album_download': is_album,
        'album_context': album_context,
        'artist_context': artist_context,
        'wishlist_run_id': run_id,
    }
    if extra_fields:
        row.update(extra_fields)
    return row


def _run_wishlist_cycle(
    runtime,
    *,
    playlist_id: str,
    cycle: str,
    tracks: list,
    run_id: str,
    auto_initiated: bool,
    first_batch_id: Optional[str] = None,
    batch_extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """THE single wishlist orchestration engine — both the auto timer and the
    manual trigger call this, so a manual scan runs the exact same code path as
    an auto scan (group → per-album + residual batches → register → dispatch).

    Per-flow differences are arguments, not separate code:
      * ``auto_initiated`` stamps the auto-only fields (auto_initiated /
        auto_processing_timestamp / current_cycle, which also drives the
        once-per-run cycle toggle on completion) and selects the auto vs manual
        display-name + log style.
      * ``first_batch_id`` lets the manual flow reuse its synchronously-created
        placeholder batch so the modal's existing poll target stays valid.

    Album batches block their worker for the whole search+download, so they run
    on the dedicated album pool; the residual per-track batch runs on the shared
    pool. Returns a summary dict (submitted ids + album / residual counts).
    """
    logger = runtime.logger
    from core.wishlist.album_grouping import group_wishlist_tracks_by_album

    # stopped while the manual run was still preparing: the placeholder is
    # about to be rebuilt as the first album batch, which would quietly undo
    # the cancel and start the downloads anyway
    if first_batch_id is not None:
        with runtime.tasks_lock:
            placeholder = runtime.download_batches.get(first_batch_id) or {}
            if placeholder.get('phase') == 'cancelled':
                logger.info(f"[Manual-Wishlist] {first_batch_id} was stopped before dispatch")
                return {'submitted': [], 'album_batches': 0, 'residual_count': 0}

    extra_fields = dict(batch_extra_fields or {})
    if auto_initiated:
        extra_fields.update({
            'auto_initiated': True,
            'auto_processing_timestamp': runtime.current_time_fn(),
            'current_cycle': cycle,
        })

    # Reuse the caller-provided placeholder id for the FIRST batch created; every
    # other batch gets a fresh uuid.
    _reuse_id = first_batch_id

    def _alloc_id() -> str:
        nonlocal _reuse_id
        if _reuse_id is not None:
            bid, _reuse_id = _reuse_id, None
            return bid
        return str(uuid.uuid4())

    album_executor = runtime.album_bundle_executor or runtime.missing_download_executor
    submitted: list = []
    album_batch_total = 0
    residual_total = 0

    # H10: one batch set per OWNER profile. A track wishlisted by profile 2
    # must download into profile 2's library — stamping every batch with the
    # runtime's profile (the auto flow hardcodes 1) put other profiles'
    # tracks in the wrong library forever, because the scoped success DELETE
    # only clears the profiles that own the published path (#1199).
    owner_groups: dict[Any, list] = {}
    for track in tracks:
        owner = (track.get('_wishlist_profile_id') or track.get('profile_id')
                 or runtime.profile_id)
        owner_groups.setdefault(owner, []).append(track)

    for owner_profile_id, owner_tracks in owner_groups.items():
        # Albums cycle splits into per-album bundles; singles keep the single
        # per-track batch shape (Spotify already classifies them away from albums).
        grouping = (
            group_wishlist_tracks_by_album(
                owner_tracks, min_tracks_per_album=_resolve_album_bundle_threshold(),
            )
            if cycle == 'albums' else None
        )

        album_groups = grouping.album_groups if grouping else []
        for album_idx, group in enumerate(album_groups):
            album_batch_id = _alloc_id()
            album_name = group.album_context.get('name', 'Unknown')
            batch_name = (
                f"Wishlist (Auto - Album: {album_name})" if auto_initiated
                else f"Wishlist (Album: {album_name})"
            )
            with runtime.tasks_lock:
                runtime.download_batches[album_batch_id] = make_wishlist_batch_row(
                    playlist_id=playlist_id,
                    playlist_name=batch_name,
                    track_count=len(group.tracks),
                    max_concurrent=runtime.get_batch_max_concurrent(),
                    profile_id=owner_profile_id,
                    phase='queued',
                    run_id=run_id,
                    is_album=True,
                    album_context=group.album_context,
                    artist_context=group.artist_context,
                    extra_fields=extra_fields,
                )
            if auto_initiated:
                logger.info(
                    f"[Auto-Wishlist] Album sub-batch {album_idx + 1}/{len(album_groups)}: "
                    f"'{album_name}' by '{group.artist_context.get('name')}' "
                    f"({len(group.tracks)} tracks) → {album_batch_id} "
                    f"[run {run_id[:8]}] [profile {owner_profile_id}]"
                )
            else:
                logger.info(
                    f"[Manual-Wishlist] Album sub-batch {album_idx + 1}/{len(album_groups)}: "
                    f"'{album_name}' ({len(group.tracks)} tracks) → {album_batch_id} "
                    f"[profile {owner_profile_id}]"
                )
            submitted.append(album_batch_id)
            album_batch_total += 1
            # Album bundles block their worker for the whole search+download →
            # dedicated pool (falls back to the shared pool when unset). serialize=True
            # makes the worker actually HOLD its pool slot until the album drains, so
            # only a few albums are in flight at once instead of every album flooding
            # the shared download pool with 'searching' tracks (#740 / Sokhi).
            album_executor.submit(
                runtime.run_full_missing_tracks_process,
                album_batch_id, playlist_id, group.tracks, True,
            )

        residual_tracks = grouping.residual_tracks if grouping is not None else owner_tracks
        residual_count = len(residual_tracks) if residual_tracks else 0
        if residual_tracks:
            # #1548: group residual tracks by their source playlist so a
            # playlist sync's missing tracks download as a playlist-named
            # batch (firing batch_complete for the scanner) instead of
            # vanishing into the generic residual bucket.
            playlist_groups: dict[str, list] = {}
            generic_residual: list = []
            for track in residual_tracks:
                playlist_name = None
                if track.get('source_type') == 'playlist':
                    source_info = track.get('source_info') or {}
                    if isinstance(source_info, dict):
                        playlist_name = source_info.get('playlist_name')
                if playlist_name:
                    playlist_groups.setdefault(playlist_name, []).append(track)
                else:
                    generic_residual.append(track)

            # Playlist-named batches first, then the generic residual.
            batch_groups: list[tuple[str, list]] = [
                (name, tracks) for name, tracks in playlist_groups.items()
            ]
            if generic_residual:
                generic_name = (
                    f"Wishlist (Auto - {cycle.capitalize()})" if auto_initiated
                    else "Wishlist (Residual)"
                )
                batch_groups.append((generic_name, generic_residual))

            for batch_name, batch_tracks in batch_groups:
                batch_id = _alloc_id()
                batch_count = len(batch_tracks)
                is_playlist_batch = batch_name not in (
                    f"Wishlist (Auto - {cycle.capitalize()})",
                    "Wishlist (Residual)",
                )
                batch_extra = dict(extra_fields or {})
                if is_playlist_batch:
                    source_playlist_id = None
                    for _tr in batch_tracks:
                        _si = _tr.get('source_info') or {}
                        # Skip refs that carry nothing real (the
                        # 'wishlist' sentinel above all): the first
                        # truthy value wins, so a sentinel on an early
                        # track would hide a later track's real ref and
                        # the batch would resolve no playlist at all.
                        if isinstance(_si, dict):
                            _cleaned_ref = _clean_requeue_playlist_ref(
                                _si.get('playlist_id'))
                            if _cleaned_ref is not None:
                                source_playlist_id = _cleaned_ref
                                break
                    mirrored_playlist_id = None
                    try:
                        _music_db = getattr(runtime, 'get_music_database', None)
                        if callable(_music_db):
                            _mdb = _music_db()
                            _resolved = _mdb.resolve_mirrored_playlist_assignment(
                                playlist_ref=source_playlist_id,
                                playlist_name=batch_name,
                                profile_id=owner_profile_id,
                            )
                            if _resolved and _resolved.get('id'):
                                mirrored_playlist_id = _resolved['id']
                    except Exception as res_err:
                        logger.debug("Could not resolve mirrored playlist for wishlist batch: %s", res_err)
                    batch_extra.update({
                        'is_playlist_batch': True,
                        'source_playlist_name': batch_name,
                        'source_playlist_id': source_playlist_id,
                        'mirrored_playlist_id': mirrored_playlist_id,
                    })

                with runtime.tasks_lock:
                    runtime.download_batches[batch_id] = make_wishlist_batch_row(
                        playlist_id=playlist_id,
                        playlist_name=batch_name,
                        track_count=batch_count,
                        max_concurrent=runtime.get_batch_max_concurrent(),
                        profile_id=owner_profile_id,
                        phase='queued',
                        run_id=run_id,
                        extra_fields=batch_extra,
                    )
                submitted.append(batch_id)
                residual_total += batch_count
                runtime.missing_download_executor.submit(
                    runtime.run_full_missing_tracks_process,
                    batch_id, playlist_id, batch_tracks,
                )
                # #1548: playlist-named batches get their own log; the generic
                # residual keeps the old message so existing tests pass.
                if auto_initiated:
                    if is_playlist_batch:
                        logger.info(
                            f"Starting wishlist playlist batch {batch_id} '{batch_name}' "
                            f"with {batch_count} tracks [run {run_id[:8]}] [profile {owner_profile_id}]"
                        )
                    else:
                        logger.info(
                            f"Starting wishlist residual batch {batch_id} with {batch_count} tracks "
                            f"({'singles' if cycle == 'singles' else 'unbucketed albums'}) "
                            f"[run {run_id[:8]}] [profile {owner_profile_id}]"
                        )
                else:
                    if is_playlist_batch:
                        logger.info(
                            f"[Manual-Wishlist] Playlist batch {batch_id} '{batch_name}' "
                            f"with {batch_count} tracks [profile {owner_profile_id}]"
                        )
                    else:
                        logger.info(
                            f"[Manual-Wishlist] Residual per-track batch {batch_id} "
                            f"with {batch_count} tracks [profile {owner_profile_id}]"
                        )
            # Skip the old single-residual-batch block below.
            continue

    return {
        'submitted': submitted,
        'album_batches': album_batch_total,
        'residual_count': residual_total,
    }


def start_direct_track_download_batch(
    runtime: "WishlistManualDownloadRuntime",
    tracks: list[Dict[str, Any]],
) -> tuple[Dict[str, Any], int]:
    """Dispatch transient track payloads through the shared download pipeline.

    Unlike ``start_manual_wishlist_download_batch`` this entry point never
    reads from or writes to the Wishlist. It exists for an explicit scoped
    Automatic Search, where the click is a one-shot acquisition intent rather
    than a request to monitor the track persistently.
    """
    try:
        if not tracks:
            return {"success": False, "error": "No tracks supplied"}, 400

        batch_id = str(uuid.uuid4())
        with runtime.tasks_lock:
            runtime.download_batches[batch_id] = make_wishlist_batch_row(
                playlist_id="library_v2_search",
                playlist_name="Library v2 Automatic Search",
                track_count=0,
                max_concurrent=runtime.get_batch_max_concurrent(),
                profile_id=runtime.profile_id,
                phase='analysis',
                extra_fields={
                    'requeue_failed_to_wishlist': False,
                    'transient_search': True,
                },
            )

        runtime.missing_download_executor.submit(
            _prepare_and_run_direct_track_batch,
            runtime,
            batch_id,
            list(tracks),
        )
        return {"success": True, "batch_id": batch_id}, 200
    except Exception as exc:
        runtime.logger.error(f"Error starting direct track download process: {exc}")
        return {"success": False, "error": str(exc)}, 500


def _prepare_and_run_direct_track_batch(
    runtime: "WishlistManualDownloadRuntime",
    batch_id: str,
    tracks: list[Dict[str, Any]],
) -> None:
    """Background hand-off for a Wishlist-free scoped Automatic Search."""
    try:
        direct_tracks, duplicates = sanitize_and_dedupe_wishlist_tracks(tracks)
        if duplicates:
            runtime.logger.info(
                f"[Library-v2 Search] Removed {duplicates} duplicate transient payload(s)"
            )
        for index, track in enumerate(direct_tracks):
            track['_original_index'] = index

        if not direct_tracks:
            with runtime.tasks_lock:
                batch = runtime.download_batches.get(batch_id)
                if batch is not None:
                    batch['phase'] = 'complete'
                    batch['error'] = 'No tracks to search'
            return

        runtime.add_activity_item(
            "", "Automatic Search Started", f"{len(direct_tracks)} track(s)", "Now"
        )
        _run_wishlist_cycle(
            runtime,
            playlist_id='library_v2_search',
            cycle='singles',
            tracks=direct_tracks,
            run_id=str(uuid.uuid4()),
            auto_initiated=False,
            first_batch_id=batch_id,
            batch_extra_fields={
                'requeue_failed_to_wishlist': False,
                'transient_search': True,
            },
        )
    except Exception as exc:
        runtime.logger.error(f"Error preparing direct track batch {batch_id}: {exc}")
        with runtime.tasks_lock:
            batch = runtime.download_batches.get(batch_id)
            if batch is not None:
                batch['phase'] = 'error'
                batch['error'] = str(exc)


def add_cancelled_tracks_to_failed_tracks(
    batch: Dict[str, Any],
    download_tasks: Dict[str, Dict[str, Any]],
    permanently_failed_tracks: list[Dict[str, Any]],
    *,
    logger=logger,
) -> int:
    """Promote cancelled-but-missing tasks into the failed-track list.

    Every cancelled track is promoted — no cap. An earlier max_process=100
    slice silently dropped cancelled tracks past the first 100 queue
    entries, so they missed the M11 attempt stamp: a track cancelled in a
    big batch never escalated retry backoff and burned a fresh search every
    cycle. The per-track work here is cheap dict-building; the expensive
    wishlist re-add downstream is already capped (M11) with universal
    stamping.
    """
    cancelled_tracks = batch.get('cancelled_tracks', set())
    if not cancelled_tracks:
        return 0

    processed_count = 0
    for task_id in batch.get('queue', []):
        if task_id not in download_tasks:
            continue
        task = download_tasks[task_id]
        track_index = task.get('track_index', 0)
        if track_index not in cancelled_tracks:
            continue

        if task.get('status', 'unknown') == 'completed':
            continue

        original_track_info = task.get('track_info', {})
        cancelled_track_info = build_failed_track_wishlist_context(
            original_track_info,
            track_index=track_index,
            retry_count=0,
            failure_reason='Download cancelled',
            candidates=task.get('cached_candidates', []),
        )

        if any(t.get('table_index') == track_index for t in permanently_failed_tracks):
            continue

        permanently_failed_tracks.append(cancelled_track_info)
        processed_count += 1
        logger.error(
            f"[Wishlist Processing] Added cancelled missing track {cancelled_track_info['track_name']} to failed list for wishlist"
        )

    return processed_count


def record_failed_attempt(wishlist_service, track_data, failure_reason, profile_id,
                          *, logger=logger) -> bool:
    """Stamp one failed cycle attempt on the track's wishlist row
    (retry_count + 1, last_attempted = now).

    Runs for fresh adds AND duplicate-skips — the duplicate-skip IS the
    repeat-failure signal (javiavid: retry_count stayed 0 forever because the
    only increment path, mark_track_download_result, had no callers; that also
    left the 3.1.1 failing badge/filter dead on the music side). Best-effort:
    a stamp failure never disturbs wishlist processing.

    Wing It stubs used to be skipped here because they could never be on the
    wishlist to stamp. With wishlist.wing_it_guesses they can, and a stub that is
    NOT stamped keeps retry_count at 0 forever — which means retry_backoff never
    escalates it and every cycle burns a fresh search on a track that has failed
    for months, the exact waste that module exists to stop. Stamping is now
    unconditional; a stub that isn't on the wishlist simply updates no row."""
    sp_id = track_data.get('id') if isinstance(track_data, dict) else None
    if not sp_id:
        return False
    try:
        return bool(wishlist_service.mark_track_download_result(
            str(sp_id), False, error_message=str(failure_reason or '') or None,
            profile_id=profile_id))
    except Exception as e:
        logger.debug("wishlist attempt stamp failed for %s: %s", sp_id, e)
        return False


def bundle_failure_counts_as_attempt(reason: str) -> bool:
    """Whether a terminal album-bundle failure says anything about the release.

    A Prowlarr search that did not complete (an indexer down and held back by
    Prowlarr, a timeout, a 5xx) never looked at a release, so counting it
    would push every wishlist track of the batch into the retry backoff for
    something that is not theirs. Those tracks are simply tried again on the
    next cycle, when the search can run.
    """
    from core.prowlarr_client import SEARCH_FAILED_PREFIX
    return not str(reason or '').startswith(SEARCH_FAILED_PREFIX)


def record_failed_attempts_for_tracks(wishlist_service, tracks, failure_reason, profile_id,
                                      *, logger=logger) -> int:
    """Stamp one failed attempt on every track of a batch that failed as a whole.

    A terminal album-bundle failure (e.g. a Usenet release SABnzbd aborts)
    ends the batch before any per-track task exists, so
    ``add_failed_tracks_to_wishlist`` never sees these tracks and never calls
    ``record_failed_attempt``. They keep ``retry_count = 0`` and
    ``last_attempted = NULL``, which the retry backoff reads as "never tried":
    the same dead release is fetched again on every wishlist cycle.

    Tracks that are not on the wishlist update no row, so this is safe for
    any batch. Best-effort, returns the number of tracks it stamped.
    """
    stamped = 0
    for track in tracks or []:
        if not isinstance(track, dict):
            continue
        sp_id = track.get('id') or track.get('spotify_track_id')
        if not sp_id:
            continue
        try:
            if record_failed_attempt(
                    wishlist_service, {**track, 'id': sp_id}, failure_reason, profile_id):
                stamped += 1
        except Exception:
            logger.debug("attempt stamp failed for %s", sp_id, exc_info=True)
    return stamped


def recover_uncaptured_failed_tracks(
    batch: Dict[str, Any],
    download_tasks: Dict[str, Dict[str, Any]],
    permanently_failed_tracks: list[Dict[str, Any]],
    *,
    logger=logger,
) -> int:
    """Recover tasks force-marked failed/not_found so wishlist processing does not skip them."""
    recovered_count = 0
    for task_id in batch.get('queue', []):
        if task_id not in download_tasks:
            continue
        task = download_tasks[task_id]
        if task.get('status') not in ('failed', 'not_found'):
            continue

        track_index = task.get('track_index', 0)
        if any(t.get('table_index') == track_index for t in permanently_failed_tracks):
            continue

        original_track_info = task.get('track_info', {})
        recovered_track_info = build_failed_track_wishlist_context(
            original_track_info,
            track_index=track_index,
            retry_count=task.get('retry_count', 0),
            failure_reason=task.get('error_message', 'Download failed'),
            candidates=task.get('cached_candidates', []),
        )
        permanently_failed_tracks.append(recovered_track_info)
        recovered_count += 1
        logger.error(
            f"[Wishlist Processing] Recovered uncaptured failed track for wishlist: {recovered_track_info['track_name']}"
        )

    return recovered_count


def resolve_wishlist_source_type_for_batch(batch: Dict[str, Any]) -> str:
    """Pick the wishlist ``source_type`` for failed tracks coming out of a
    download batch.

    Album-context batches must produce ``'album'`` provenance — the legacy
    hardcoded ``'playlist'`` mislabels every album-batch failure, breaks
    the wishlist UI's source filter, and makes ``by_source_type`` stats
    look like all wishlist work came from playlists.
    """
    return 'album' if batch.get('is_album_download') else 'playlist'


def build_wishlist_source_context(batch: Dict[str, Any], current_time: datetime | None = None) -> Dict[str, Any]:
    """Build the source_context payload used when adding failed tracks back to the wishlist."""
    current_time = current_time or datetime.now()
    playlist_id = batch.get('source_playlist_ref') or batch.get('playlist_id')
    context = {
        'playlist_name': batch.get('playlist_name', 'Unknown Playlist'),
        'playlist_id': playlist_id,
        'added_from': 'webui_modal',
        'timestamp': current_time.isoformat(),
    }
    if batch.get('mirrored_playlist_id') is not None:
        context['mirrored_playlist_id'] = batch.get('mirrored_playlist_id')
    if batch.get('organize_by_playlist'):
        context['organize_by_playlist'] = True
    # Stamp the album batch's flags on the context: tracks that keep
    # the batch pair ARE album downloads, and derive_download_origin
    # reads source_info['is_album_download'] when they complete. (No
    # routing reads these keys from a row's source_info — album
    # grouping uses the track payloads. When a track's own playlist
    # provenance wins in the resolver, the flags are dropped again:
    # they describe this batch, not that track.)
    if batch.get('is_album_download'):
        context['is_album_download'] = True
        album_ctx = batch.get('album_context')
        if isinstance(album_ctx, dict):
            context['album_context'] = album_ctx
        artist_ctx = batch.get('artist_context')
        if isinstance(artist_ctx, dict):
            context['artist_context'] = artist_ctx
    return context


# Sentinel refs the wishlist batches stamp on themselves — never a real
# playlist ref. Compared stripped + case-folded so ' Wishlist' can't slip by.
_WISHLIST_SENTINEL_REFS = frozenset({'wishlist', 'wishlist_manual'})
# Names that are placeholders or the wishlist's own label, not a playlist:
# the bare 'Wishlist', build_wishlist_source_context's 'Unknown Playlist'
# default (cf. core/downloads/origin.py's placeholder names), and every
# synthetic batch label, which all start 'Wishlist (' — 'Wishlist (Album: X)',
# 'Wishlist (Auto - Album: X)', 'Wishlist (Residual)', ...
_WISHLIST_PLACEHOLDER_NAMES = frozenset({'wishlist', 'unknown playlist'})


def _clean_requeue_playlist_ref(value: Any) -> Optional[Any]:
    """A stored playlist ref cleaned, or None when it carries nothing
    real (missing, a sentinel, or a non-scalar value that must not be
    stored verbatim). A plain positive int is preserved AS an int, not
    stringified: register_completed_playlist_batch treats an int ref
    as a mirrored-playlist PK while a digit string is an upstream id,
    so stringifying here would silently change which playlist a later
    success resolves to. Bools and non-positive ints (0 / negatives
    are never a real id) do not survive."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value <= 0:
            return None
        return value
    if not isinstance(value, str):
        return None
    ref = value.strip()
    if not ref or ref.casefold() in _WISHLIST_SENTINEL_REFS:
        return None
    return ref


# batch['playlist_id'] values that name no playlist: the hand-picked
# synthetic prefixes (an album or a song someone picked downloads under
# a made-up id — core/downloads/origin.py's list, kept as the single
# source for those), the album-context prefixes from
# core/downloads/history.py's ALBUM_PREFIXES (imported, not copied, so
# the two lists cannot drift), the library redownload id, the
# playback prefetch queue's id, and the server/webui-made album ids
# the hand-picked list predates: a label page's release modal
# ('lbl_album_'), a history re-sync's fallback id ('resync_' — the
# React caller uses `resync_${entry.id}` when the entry has no
# playlist_id; 'resync_album_' is that flow's album_context id and is
# covered by the same prefix), and the iTunes-link discovery-state key
# ('ituneslink_', the no-underscore twin of the listed 'itunes_link_'
# download id). 'legacy_modal' is the legacy /api/tracks/download_missing
# endpoint's fixed id — today it rides only on that batch's TASKS, not
# the batch dict, but it names no playlist either way.
_SYNTHETIC_BATCH_PLAYLIST_ID_PREFIXES = HAND_PICKED_ID_PREFIXES + ALBUM_PREFIXES + (
    'redownload_', 'lbl_album_', 'resync_', 'ituneslink_', 'wing_it_',
)
_SYNTHETIC_BATCH_PLAYLIST_IDS = frozenset({'playback_queue', 'legacy_modal', 'library_v2_search'})


def _clean_direct_playlist_batch_ref(value: Any) -> Optional[Any]:
    """batch['playlist_id'] as a fresh-resolution ref, or None.

    A DIRECT playlist download (start-missing-process for a playlist
    the user opened) stamps the real upstream id ONLY here — no
    source_playlist_ref, no is_playlist_batch — so the freshness guard
    needs this candidate or a track's stale provenance overrides the
    batch's own playlist. The synthetic ids above name no playlist,
    so they never suppress a track's real provenance.
    """
    ref = _clean_requeue_playlist_ref(value)
    if ref is None:
        return None
    text = str(ref)
    if text in _SYNTHETIC_BATCH_PLAYLIST_IDS or text.startswith(
            _SYNTHETIC_BATCH_PLAYLIST_ID_PREFIXES):
        return None
    return ref


def _is_generated_batch_label(name: Any) -> bool:
    """Whether a name is a placeholder or a label the wishlist itself
    generated ('Wishlist (Album: X)', 'Wishlist (Auto - <Cycle>)',
    'Wishlist (Residual)'), as opposed to a playlist's real name —
    including a real name that merely starts 'Wishlist (' ('Wishlist
    (2024)'), which no generator produces."""
    if not isinstance(name, str):
        return True
    folded = name.strip().casefold()
    if not folded or folded in _WISHLIST_PLACEHOLDER_NAMES:
        return True
    return folded == 'wishlist (residual)' \
        or folded.startswith('wishlist (auto - ') \
        or folded.startswith('wishlist (album: ')


def _batch_own_playlist_name(batch: Dict[str, Any]) -> Optional[str]:
    """A residual playlist batch's OWN name, or None.

    Dispatch groups residual tracks by their stored ``playlist_name``
    verbatim and stamps that name on the batch as
    ``source_playlist_name`` (with ``is_playlist_batch`` set) — so for
    such a batch the name is the tracks' own playlist provenance, not
    a generated label, even when it textually looks like one (a real
    playlist named 'Wishlist (2024)'). Bare placeholder names still
    clean to None: a batch grouped under the bare word 'Wishlist' is
    indistinguishable from the sentinel label. The GENERATED labels
    clean to None too, even on a flagged batch: 'Wishlist (Residual)'
    and 'Wishlist (Auto - <Cycle>)' are the names dispatch itself
    stamps on the non-playlist residual group (dispatch never flags
    those batches ``is_playlist_batch``), and 'Wishlist (Album: X)'
    is the album-batch label — rows stored before the batch-type
    split carry it AS their playlist_name with source_type
    'playlist', so residual grouping can group them under the label
    and flag the batch. In every case the label names no playlist; a
    real playlist that happens to share one of those exact names is
    indistinguishable from the generated label, the same trade-off
    as the track side.
    """
    if not batch.get('is_playlist_batch'):
        return None
    name = batch.get('source_playlist_name')
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or _is_generated_batch_label(name):
        return None
    return name


def _clean_requeue_playlist_name(value: Any) -> Optional[str]:
    """A track's stored playlist name, stripped, or None when it is a
    placeholder / wishlist batch label rather than a playlist name.

    Trade-off: a playlist genuinely named 'Wishlist', 'Wishlist (2024)'
    or 'Unknown Playlist' is indistinguishable from the labels above, so
    its NAME is not preserved. Its ref still is (see the resolver), and
    registration resolves by ref, so the playlist still re-syncs; only a
    name-only row for such a playlist falls back to the batch pair.
    That is the TRACK side, where nothing corroborates the name; a
    residual playlist batch's own name IS corroborated (dispatch
    grouped the batch by it) and is preserved via
    ``_batch_own_playlist_name``.
    """
    if not isinstance(value, str):
        return None
    name = value.strip()
    folded = name.casefold()
    if not folded or folded in _WISHLIST_PLACEHOLDER_NAMES:
        return None
    if folded.startswith('wishlist ('):
        return None
    return name


def resolve_failed_track_requeue_provenance(
    batch: Dict[str, Any],
    track_data: Any,
    current_time: datetime | None = None,
    *,
    batch_context: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """Pick the (source_type, source_context) for ONE failed track's
    wishlist re-add.

    The batch-level pair describes the batch, not the track. A wishlist
    album batch is named ``Wishlist (Album: X)`` and its context says
    ``playlist_id='wishlist'`` — re-adding a playlist-origin track under
    that pair makes the wishlist DB refresh overwrite the row's real
    playlist provenance with the batch label (#1981), and when the track
    finally downloads, ``register_completed_playlist_batch`` resolves
    ``'wishlist'`` / ``'Wishlist (Album: X)'`` to no mirrored playlist, so
    the playlist is never re-synced to the media server.

    When the track itself carries playlist provenance (a formatted
    wishlist row keeps its ``source_type`` / ``source_info`` on the track
    payload), that provenance wins for the playlist keys. Only the
    playlist pair (``playlist_id`` / ``playlist_name``) is carried over
    from the track's ``source_info``; its other keys are not. The batch's
    album flags do NOT stay alongside: ``is_album_download`` /
    ``album_context`` / ``artist_context`` describe the batch the track
    failed in, and ``derive_download_origin`` reads
    ``source_info.is_album_download`` — keeping it would reclassify the
    restored playlist track as a hand-picked download when it finally
    downloads, losing its playlist origin. Nothing reads those keys
    from a wishlist row's ``source_info`` for routing. Anything else
    keeps the batch-level pair unchanged.

    What the preserved provenance restores depends on which half of it
    survives cleaning. The next cycle's residual grouping groups only
    by ``playlist_name`` (see ``_run_wishlist_cycle``), so a track with
    a name — name+ref or name-only — regains playlist-named grouping.
    A ref-only track (its name was the batch label / a placeholder, so
    it was cleaned away) still lands in the generic residual batch on
    the next cycle; for it, the playlist re-sync is restored via
    ``register_completed_playlist_batch``'s ref path — registration
    resolves the task's ``source_info`` by ``playlist_id`` — not via
    the playlist-named grouping.

    Two scopings keep stale track data from winning over fresher data:

    * A playlist-named residual batch already carries a fresh playlist
      resolution — its tracks were grouped by playlist at dispatch and
      its mirrored id resolved then (``_run_wishlist_cycle``) — so the
      batch pair stands and the track's older provenance is not applied.
      The batch pair is REPAIRED first (the builder misses
      ``source_playlist_id`` and passes label names through), and the
      repair applies to every track of the batch, playlist-typed or
      not: it describes the batch, not the track.
    * Partial provenance is all-or-clean per key: a key with nothing real
      is REMOVED from the context rather than left holding the batch
      sentinel, because grouping/registration read the pair together and
      take the first truthy value — a leftover ``'wishlist'`` ref next to
      a real name (or the batch label next to a real ref) poisons both.

    ``batch_context`` lets a caller resolving many tracks of one batch
    build the batch context once (one timestamp for the whole batch);
    it is copied before any per-track key is applied.
    """
    source_type = resolve_wishlist_source_type_for_batch(batch)
    if batch_context is not None:
        context = dict(batch_context)
    else:
        context = build_wishlist_source_context(batch, current_time=current_time)
    # Batch names are stored stripped, like track-side names: the
    # next cycle groups rows by the exact name text, so a padded
    # batch name would group apart from its stripped twin.
    if isinstance(context.get('playlist_name'), str):
        context['playlist_name'] = context['playlist_name'].strip()

    # The batch-side fresh-resolution repair runs for EVERY track of
    # the batch, before the per-track scoping below: the repaired pair
    # describes the batch itself, so a track whose payload lost its
    # source_type (or never carried one) must not requeue with the
    # builder's unrepaired pair (sentinel ref beside a real name, or a
    # real ref beside a batch label) while its batch-mates requeue
    # with the repaired one — the two rows would resolve differently
    # on the next cycle for no reason but the payload's shape.
    #
    # A fresh batch resolution suppresses the per-track override only
    # when it is actually usable — a truthy batch field is not enough:
    # a sentinel ref ('wishlist') carries no resolution at all. A real
    # ref on either source field is usable on its own (the
    # organize_download shape sets ONLY source_playlist_ref), and so
    # is a direct playlist download's real playlist_id (synthetic
    # hand-picked ids excluded — see _clean_direct_playlist_batch_ref).
    # An album batch's playlist_id never counts, whatever it holds:
    # is_album_download is the batch's own declaration that it is not
    # a playlist (origin.py's is_hand_picked treats it the same way),
    # and several album flows stamp ids the deny-list cannot enumerate
    # (per-release timestamps, history-entry ids). A
    # playlist batch with no usable ref is still fresh when it carries
    # a usable name: at dispatch the mirrored id was resolved by that
    # name (resolve_mirrored_playlist_assignment falls back to an
    # unambiguous name match when the ref is missing) and is carried
    # on the batch, build_wishlist_source_context takes the batch's
    # playlist_name — the same name — as the context name, and
    # register_completed_playlist_batch uses that mirrored id
    # directly, so a name-only fresh resolution stands. A playlist
    # batch with neither falls through to the track's own provenance.
    batch_ref = (
        _clean_requeue_playlist_ref(batch.get('source_playlist_id'))
        or _clean_requeue_playlist_ref(batch.get('source_playlist_ref'))
        or (None if batch.get('is_album_download')
            else _clean_direct_playlist_batch_ref(batch.get('playlist_id')))
    )
    if batch_ref is not None:
        # build_wishlist_source_context reads source_playlist_ref /
        # playlist_id, NOT source_playlist_id — a residual playlist
        # batch whose only ref lives in source_playlist_id would
        # otherwise requeue with the sentinel ('wishlist') beside the
        # real name, the exact poisoned pairing this resolver exists
        # to prevent. When the built context's ref is unusable, the
        # batch's cleaned ref wins; a usable built ref (e.g. one from
        # source_playlist_ref, which the builder already prefers)
        # stands.
        if batch.get('is_album_download') or \
                _clean_direct_playlist_batch_ref(context.get('playlist_id')) is None:
            # An album batch's own playlist_id never counts (see the
            # batch_ref gate), so when a source-field ref exists it
            # replaces whatever the builder took from that id, even a
            # non-synthetic album id no deny-list could enumerate.
            context['playlist_id'] = batch_ref
        # Symmetric name cleaning: a usable batch ref beside a
        # GENERATED batch label is the other poisoned pairing (a real
        # ref re-syncing under 'Wishlist (Album: X)'), so a generated
        # label is popped rather than stored — all-or-clean,
        # mirroring the name-only branch's ref pop below. A real name
        # stands even when it merely looks wishlist-ish ('Wishlist
        # (2024)'): on a direct batch the name arrived WITH the ref
        # from the same download origin, and on a residual playlist
        # batch it is the batch's own name — either way the pair is
        # corroborated, unlike the track side, where a bare name has
        # nothing to vouch for it.
        context_name = context.get('playlist_name')
        if context_name is None or (
                _clean_requeue_playlist_name(context_name) is None
                and _is_generated_batch_label(context_name)):
            context.pop('playlist_name', None)
        return source_type, context
    if batch.get('is_playlist_batch') and (
        _clean_requeue_playlist_name(batch.get('source_playlist_name')) is not None
        or _clean_requeue_playlist_name(batch.get('playlist_name')) is not None
        or _batch_own_playlist_name(batch) is not None
    ) and batch.get('mirrored_playlist_id') is not None:
        # Name-only fresh resolution: no usable batch ref exists, so
        # an unusable context ref is a dead key — remove it rather
        # than store the sentinel beside the real name (all-or-clean,
        # the same rule the track-override path below applies). The
        # name and the batch's mirrored id carry the resolution; a
        # usable built ref stands. The mirrored id is what makes this
        # a fresh resolution at all — with neither a ref nor a mirror
        # behind the name, the track's own provenance (checked below)
        # is the better evidence and must not be suppressed.
        if _clean_direct_playlist_batch_ref(context.get('playlist_id')) is None:
            context.pop('playlist_id', None)
        # The stored name comes from batch['playlist_name'] via the
        # builder, but this branch may have been entered on
        # source_playlist_name — if the two differ and the stored one
        # carries nothing real, store the checked one instead.
        if _clean_requeue_playlist_name(context.get('playlist_name')) is None:
            own_name = _batch_own_playlist_name(batch)
            if own_name is not None:
                context['playlist_name'] = own_name
            else:
                context.pop('playlist_name', None)
        return source_type, context

    if not isinstance(track_data, dict) or track_data.get('source_type') != 'playlist':
        return source_type, context

    source_info = track_data.get('source_info')
    if isinstance(source_info, str):
        try:
            source_info = json.loads(source_info)
        except (ValueError, TypeError, RecursionError):
            source_info = {}
    if not isinstance(source_info, dict):
        return source_type, context

    playlist_id = _clean_requeue_playlist_ref(source_info.get('playlist_id'))
    playlist_name = _clean_requeue_playlist_name(source_info.get('playlist_name'))
    if playlist_id is None and playlist_name is None:
        return source_type, context

    if playlist_name is not None:
        context['playlist_name'] = playlist_name
    else:
        context.pop('playlist_name', None)
    if playlist_id is not None:
        context['playlist_id'] = playlist_id
    else:
        context.pop('playlist_id', None)
    # The batch context's mirrored_playlist_id is DROPPED, not kept:
    # it was resolved for the BATCH's playlist, so beside the track's
    # own ref/name it would pair this track with a different playlist's
    # mirror. The track's own mirrored id is not copied either (nothing
    # reads it back from a wishlist row — registration reads the
    # download batch's mirrored_playlist_id and resolves task
    # source_info by playlist_id/playlist_name).
    context.pop('mirrored_playlist_id', None)
    # The batch's album flags are dropped for the same reason: they
    # describe the batch this track failed in, not the track, and
    # derive_download_origin reads source_info['is_album_download'] —
    # left in place it would reclassify the restored playlist track
    # as hand-picked when it finally downloads.
    context.pop('is_album_download', None)
    context.pop('album_context', None)
    context.pop('artist_context', None)
    return 'playlist', context


def _wishlist_run_has_siblings_still_active(
    download_batches: Dict[str, Dict[str, Any]],
    run_id: str,
    completing_batch_id: str,
) -> bool:
    """Return True if any sibling batch sharing ``run_id`` is still
    pre-terminal.

    Used by ``finalize_auto_wishlist_completion`` to gate the run-
    level cycle toggle. The caller already holds ``tasks_lock``.

    The completing batch may or may not have its phase flipped to
    'complete' yet by the time we land here; either way we skip it
    in the sibling scan since we're handling its completion now."""
    terminal_phases = {'complete', 'error', 'cancelled'}
    for sibling_id, sibling in download_batches.items():
        if sibling_id == completing_batch_id:
            continue
        if not isinstance(sibling, dict):
            continue
        if sibling.get('wishlist_run_id') != run_id:
            continue
        if sibling.get('phase') in terminal_phases:
            continue
        return True
    return False


def finalize_auto_wishlist_completion(
    batch_id: str,
    completion_summary: Dict[str, Any],
    *,
    download_batches: Dict[str, Dict[str, Any]],
    tasks_lock,
    reset_processing_state: Callable[[], None],
    add_activity_item: Callable[[Any, Any, Any, Any], Any],
    automation_engine,
    db_factory: Callable[[], Any],
    logger=logger,
) -> Dict[str, Any]:
    """Finalize auto wishlist processing after a batch finishes.

    For wishlist runs that split into multiple sub-batches (Phase
    1c.2.1: per-album bundle dispatch), the cycle toggle + state
    reset only fire when the LAST sibling sub-batch of the same
    ``wishlist_run_id`` completes. Earlier completions just record
    their per-batch summary and return without toggling.

    Back-compat: legacy single-batch runs (no ``wishlist_run_id``
    field on the batch) keep the original toggle-immediately
    behavior — the gate treats a missing run_id as "lone batch"."""
    tracks_added = completion_summary.get('tracks_added', 0)
    total_failed = completion_summary.get('total_failed', 0)
    # INFO, not ERROR: this is the normal end of every sub-batch, and "0 added
    # to wishlist" is the healthy case (nothing failed, so nothing to re-queue).
    # At ERROR it fired several times per cycle and made a working wishlist run
    # read as a string of failures.
    logger.info(
        f"[Auto-Wishlist] Background processing complete: {tracks_added} added to wishlist, {total_failed} failed"
    )

    if tracks_added > 0:
        add_activity_item("", "Wishlist Updated", f"{tracks_added} failed tracks added to wishlist", "Now")

    # Run-level gate: if siblings of the same wishlist run are still
    # active, defer cycle toggle + state reset until they finish.
    with tasks_lock:
        run_id = ''
        toggle_cycle = True
        if batch_id in download_batches:
            completing_batch = download_batches[batch_id]
            run_id = completing_batch.get('wishlist_run_id') or ''
            toggle_cycle = completing_batch.get('toggle_wishlist_cycle', True) is not False
        siblings_active = bool(run_id) and _wishlist_run_has_siblings_still_active(
            download_batches, run_id, batch_id,
        )
    if siblings_active:
        logger.info(
            f"[Auto-Wishlist] Sub-batch {batch_id[:8]} done; waiting on sibling sub-batches "
            f"of run {run_id[:8]} before toggling cycle"
        )
        return completion_summary

    if toggle_cycle:
        try:
            with tasks_lock:
                if batch_id in download_batches:
                    current_cycle = download_batches[batch_id].get('current_cycle', 'albums')
                else:
                    current_cycle = 'albums'

            next_cycle = 'singles' if current_cycle == 'albums' else 'albums'

            db = db_factory()
            with db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                        INSERT OR REPLACE INTO metadata (key, value, updated_at)
                        VALUES ('wishlist_cycle', ?, CURRENT_TIMESTAMP)
                    """,
                    (next_cycle,),
                )
                conn.commit()

            logger.info(f"[Auto-Wishlist] Cycle toggled after completion: {current_cycle} → {next_cycle}")
        except Exception as cycle_error:
            logger.error(f"[Auto-Wishlist] Error toggling cycle: {cycle_error}")
    else:
        logger.info('[Auto-Wishlist] Scoped run complete; global cycle left unchanged')

    reset_processing_state()

    try:
        if automation_engine:
            automation_engine.emit('wishlist_processing_completed', {
                'tracks_processed': str(total_failed),
                'tracks_found': str(tracks_added),
                'tracks_failed': str(total_failed - tracks_added),
            })
    except Exception as e:
        logger.debug("emit wishlist_processing_completed failed: %s", e)

    return completion_summary


def remove_tracks_already_in_library(
    wishlist_service,
    profiles_database,
    music_database,
    active_server: str,
    *,
    logger=logger,
    skip_track_fn: Callable[[dict[str, Any]], bool] | None = None,
    log_prefix: str = "[Auto-Wishlist]",
) -> int:
    """Remove wishlist entries that are already present in the library."""
    from core.library import manual_library_match as _mlm

    all_profiles = profiles_database.get_all_profiles()
    # Carry (profile_id, track) so the match check can be profile-scoped.
    cleanup_tracks: list[tuple[int, dict]] = []
    for profile in all_profiles:
        pid = profile["id"]
        for t in wishlist_service.get_wishlist_tracks_for_download(profile_id=pid):
            cleanup_tracks.append((pid, t))

    from core.library_scope import library_scope_for_profile, reset_library_scope, set_library_scope

    cleanup_removed = 0
    for profile_id, track in cleanup_tracks:
        if skip_track_fn and skip_track_fn(track):
            continue
        # this runs as a background job, so "already in the library" has to be
        # asked through the wishlist owner's library, not the job's (#1199): an
        # own-library profile's wishlist entry is not cleared by the admin
        # owning the track
        _scope_token = set_library_scope(library_scope_for_profile(profile_id))
        try:
            removed_here = _cleanup_one(
                wishlist_service, music_database, _mlm, profile_id, track, active_server,
                logger=logger, log_prefix=log_prefix)
        finally:
            reset_library_scope(_scope_token)
        cleanup_removed += removed_here
    return cleanup_removed


def _cleanup_one(wishlist_service, music_database, _mlm, profile_id, track, active_server, *, logger, log_prefix) -> int:
    """one wishlist entry: 1 when it was removed as already owned, else 0.
    the loop body of remove_tracks_already_in_library, lifted so it runs
    inside that profile's library scope."""
    cleanup_removed = 0
    if True:

        track_name = track.get('name', '')
        artists = track.get('artists', [])
        spotify_track_id = track.get('spotify_track_id') or track.get('id')
        track_album = track.get('album', {}).get('name') if isinstance(track.get('album'), dict) else track.get('album')

        if not track_name or not artists or not spotify_track_id:
            return 0

        # Manual match check — skip fuzzy search if user already linked this track.
        manual_match = _mlm.get_match_for_track(music_database, profile_id, track, default_source='wishlist')
        if manual_match and _mlm.match_is_live(music_database, manual_match):
            try:
                from core.wishlist.removal_guard import REASON_MANUAL_MATCH
                removed = wishlist_service.mark_track_download_result(
                    spotify_track_id, success=True, profile_id=profile_id,
                    profile_ids=[profile_id],
                    audit={'reason': REASON_MANUAL_MATCH})
                if removed:
                    cleanup_removed += 1
                    logger.info(f"{log_prefix} [Manual Match] Skipped already-matched track: '{track_name}'")
            except Exception as _mlm_err:
                logger.error(f"{log_prefix} [Manual Match] Error removing track: {_mlm_err}")
            return cleanup_removed

        from core.wishlist.library_match import find_owned_match, wishlist_row_requires_album
        from core.wishlist.removal_guard import REASON_ALREADY_OWNED

        match = find_owned_match(
            music_database, track_name, artists, track_album, active_server,
            strict_identity=True, require_album=wishlist_row_requires_album(track),
            log=logger, log_prefix=log_prefix)

        if match:
            db_track, confidence, matched_artist_name = match
            try:
                removed = wishlist_service.mark_track_download_result(
                    spotify_track_id, success=True, profile_id=profile_id,
                    profile_ids=[profile_id],
                    audit={'reason': REASON_ALREADY_OWNED,
                           'final_path': getattr(db_track, 'file_path', '') or ''})
                if removed:
                    cleanup_removed += 1
                    logger.info(f"{log_prefix} Removed already-owned track: '{track_name}' by {matched_artist_name}")
            except Exception as remove_error:
                logger.error(f"{log_prefix} Error removing track from wishlist: {remove_error}")

    return cleanup_removed


@dataclass
class WishlistManualDownloadRuntime:
    """Dependencies needed to start a manual wishlist download batch outside the controller."""

    get_music_database: Callable[[], Any]
    download_batches: Dict[str, Dict[str, Any]]
    tasks_lock: Any
    missing_download_executor: Any
    run_full_missing_tracks_process: Callable[..., Any]  # (batch_id, playlist_id, tracks, serialize=False)
    get_batch_max_concurrent: Callable[[], int]
    add_activity_item: Callable[[Any, Any, Any, Any], Any]
    active_server: str
    profile_id: int
    logger: Any = module_logger
    # Dedicated album-bundle pool, shared with the auto flow via
    # _run_wishlist_cycle. Falls back to missing_download_executor when unset.
    album_bundle_executor: Any = None


def start_manual_wishlist_download_batch(
    runtime: WishlistManualDownloadRuntime,
    *,
    track_ids=None,
    category: str | None = None,
    force_download_all: bool = False,
) -> tuple[Dict[str, Any], int]:
    """Submit a manual wishlist batch.

    The batch entry is created synchronously so the frontend can start polling
    status immediately. The slow library-cleanup pass and master-worker hand-off
    run in the background, freeing the request handler from a 30s+ block on
    per-track DB checks for large wishlists.
    """
    logger = runtime.logger

    try:
        batch_id = str(uuid.uuid4())
        playlist_id = "wishlist"
        playlist_name = "Wishlist"

        with runtime.tasks_lock:
            # analysis_total starts at 0; the bg job updates it after cleanup
            # finishes and the real track count is known.
            runtime.download_batches[batch_id] = make_wishlist_batch_row(
                playlist_id=playlist_id,
                playlist_name=playlist_name,
                track_count=0,
                max_concurrent=runtime.get_batch_max_concurrent(),
                profile_id=runtime.profile_id,
                phase='analysis',
            )

        runtime.missing_download_executor.submit(
            _prepare_and_run_manual_wishlist_batch,
            runtime,
            batch_id,
            track_ids,
            category,
        )

        return {"success": True, "batch_id": batch_id}, 200

    except Exception as e:
        logger.error(f"Error starting wishlist download process: {e}")
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}, 500


def _prepare_and_run_manual_wishlist_batch(
    runtime: WishlistManualDownloadRuntime,
    batch_id: str,
    track_ids,
    category: str | None,
) -> None:
    """Background worker for the manual wishlist batch — does the slow cleanup
    + sanitize + filter + master-worker hand-off off the request thread."""
    logger = runtime.logger

    try:
        wishlist_service = get_wishlist_service()
        db = runtime.get_music_database()
        manual_profile_id = runtime.profile_id

        logger.warning("[Manual-Wishlist] Cleaning duplicate tracks before download...")
        duplicates_removed = db.remove_wishlist_duplicates(profile_id=manual_profile_id)
        if duplicates_removed > 0:
            logger.warning(f"[Manual-Wishlist] Removed {duplicates_removed} duplicate tracks")

        # NOTE: We deliberately do NOT call remove_tracks_already_in_library here.
        # Wishlist tracks are already known-missing (force_download_all=True is set on
        # the batch). The library check duplicates the work the master worker would
        # skip, and on large wishlists costs ~1s per track in serial DB lookups.
        # The standalone /api/wishlist/cleanup endpoint still runs that pass when
        # users explicitly ask for maintenance.
        #
        # H9 resolved the owned-track re-download loop for the AUTOMATIC cycle
        # (unattended: surviving owned rows re-downloaded every hour with no
        # user present). The manual flow keeps its force semantics on purpose:
        # the user explicitly asked for these tracks, and the ownership check
        # is fuzzy enough to false-positive — silently dropping an explicitly
        # chosen track is worse than a redundant download. See
        # test_start_manual_wishlist_download_batch_does_not_run_library_cleanup.

        # a profile that asks first (no download rights) keeps a wishlist of
        # requests. an admin working in its library (E-12) downloads what was
        # approved; the rest waits on the Requests page, which is where the
        # requester hears about it -- same rule as the scheduled run
        from core.library2.mirror_outbox import profile_asks_first
        if profile_asks_first(db, manual_profile_id):
            raw_wishlist_tracks = wishlist_service.get_wishlist_tracks_for_download(
                profile_id=manual_profile_id, approved_only=True)
            logger.info("[Manual-Wishlist] Profile %s asks first: approved requests only", manual_profile_id)
        else:
            raw_wishlist_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=manual_profile_id)
        if not raw_wishlist_tracks:
            logger.warning("[Manual-Wishlist] No tracks in wishlist after cleanup — marking batch complete")
            with runtime.tasks_lock:
                if batch_id in runtime.download_batches:
                    runtime.download_batches[batch_id]['phase'] = 'complete'
                    runtime.download_batches[batch_id]['error'] = 'No tracks in wishlist'
            return

        wishlist_tracks, duplicates_found = sanitize_and_dedupe_wishlist_tracks(raw_wishlist_tracks)
        if duplicates_found > 0:
            logger.warning(f"[Manual-Wishlist] Found and removed {duplicates_found} duplicate tracks during sanitization")
        logger.info(f"[Manual-Wishlist] Sanitized {len(wishlist_tracks)} tracks from wishlist service")

        # #705: don't burn a search+timeout on tracks that aren't out yet
        # (watchlist scans add announced albums on purpose). They stay in the
        # wishlist and start processing the cycle after their release date
        # passes. An explicit track selection overrides the gate — the user
        # asked for those specifically.
        if not track_ids:
            from core.metadata.release_dates import split_released_unreleased
            wishlist_tracks, _unreleased = split_released_unreleased(wishlist_tracks)
            if _unreleased:
                logger.info(
                    f"[Manual-Wishlist] Skipping {len(_unreleased)} unreleased track(s) "
                    f"(future release date) — they'll be searched once released")

        if track_ids:
            track_lookup = {}
            for track in wishlist_tracks:
                spotify_track_id = track.get('spotify_track_id') or track.get('id')
                if spotify_track_id and spotify_track_id not in track_lookup:
                    track_lookup[spotify_track_id] = track

            filtered_tracks = []
            seen_track_ids = set()
            for frontend_index, tid in enumerate(track_ids):
                if tid in track_lookup and tid not in seen_track_ids:
                    track = track_lookup[tid]
                    track['_original_index'] = frontend_index
                    filtered_tracks.append(track)
                    seen_track_ids.add(tid)

            wishlist_tracks = filtered_tracks
            logger.info(f"[Manual-Wishlist] Filtered to {len(wishlist_tracks)} specific tracks by ID (preserving frontend display order)")
        else:
            if category:
                wishlist_tracks, _ = filter_wishlist_tracks_by_category(wishlist_tracks, category)
                logger.info(f"[Manual-Wishlist] Filtered to {len(wishlist_tracks)} tracks for category: {category}")
            for i, track in enumerate(wishlist_tracks):
                track['_original_index'] = i
        # a picked selection keeps each track's row in the modal: renumbering it
        # after a track was dropped (a duplicate, one already gone) moved every
        # later status up a row (#1925)

        runtime.add_activity_item("", "Wishlist Download Started", f"{len(wishlist_tracks)} tracks", "Now")

        if not wishlist_tracks:
            # Nothing to download — clear out the placeholder batch.
            with runtime.tasks_lock:
                if batch_id in runtime.download_batches:
                    runtime.download_batches[batch_id]['analysis_total'] = 0
                    runtime.download_batches[batch_id]['phase'] = 'complete'
            return

        # Run the selection through the SHARED engine — the exact code path the
        # auto timer uses (group → album bundles + per-track residual → parallel
        # dispatch on the album / shared pools). cycle='albums' bundles whatever
        # forms an album and drops the rest (singles / ungroupable) into the
        # per-track residual, so this single call covers the whole selection.
        # The placeholder batch_id is reused as the first sub-batch so the
        # modal's existing poll target stays valid.
        result = _run_wishlist_cycle(
            runtime,
            playlist_id='wishlist',
            cycle='albums',
            tracks=wishlist_tracks,
            run_id=str(uuid.uuid4()),
            auto_initiated=False,
            first_batch_id=batch_id,
        )
        logger.info(
            f"[Manual-Wishlist] Dispatched {result['album_batches']} album batch(es) + "
            f"{result['residual_count']} residual track(s) via the shared engine"
        )

    except Exception as exc:
        logger.error(f"Error preparing manual wishlist batch {batch_id}: {exc}")
        import traceback

        traceback.print_exc()
        with runtime.tasks_lock:
            if batch_id in runtime.download_batches:
                runtime.download_batches[batch_id]['phase'] = 'error'
                runtime.download_batches[batch_id]['error'] = str(exc)


def cleanup_wishlist_against_library(
    wishlist_service,
    music_database,
    profile_id: int,
    active_server: str,
    *,
    logger=logger,
) -> tuple[Dict[str, Any], int]:
    """Remove wishlist tracks that already exist in the library for one profile."""
    try:
        logger.info("[Wishlist Cleanup] Starting wishlist cleanup process...")

        wishlist_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=profile_id)
        if not wishlist_tracks:
            return {"success": True, "message": "No tracks in wishlist to clean up", "removed_count": 0}, 200

        logger.info(f"[Wishlist Cleanup] Found {len(wishlist_tracks)} tracks in wishlist")

        removed_count = remove_tracks_already_in_library(
            wishlist_service,
            SimpleNamespace(get_all_profiles=lambda: [{"id": profile_id}]),
            music_database,
            active_server,
            logger=logger,
            log_prefix="[Wishlist Cleanup]",
        )

        logger.info(f"[Wishlist Cleanup] Completed cleanup: {removed_count} tracks removed from wishlist")
        return {
            "success": True,
            "message": f"Wishlist cleanup completed: {removed_count} tracks removed",
            "removed_count": removed_count,
            "processed_count": len(wishlist_tracks),
        }, 200

    except Exception as e:
        logger.error(f"Error in wishlist cleanup: {e}")
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}, 500


def _bare_scope_track_id(value: Any) -> str:
    return str(value or '').split('::', 1)[0].strip()


def _normalize_track_scope(track_ids: Iterable[Any]) -> set[str]:
    return {
        normalized
        for value in track_ids
        if (normalized := str(value or '').strip())
    }


def _track_scope_identities(track: Dict[str, Any]) -> set[str]:
    nested = track.get('track_data') or track.get('spotify_data') or {}
    if not isinstance(nested, dict):
        nested = {}
    candidates = {
        str(value).strip()
        for value in (
        track.get('track_id'),
        track.get('spotify_track_id'),
        track.get('id'),
        nested.get('id'),
        )
        if value
    }
    album = nested.get('album') if isinstance(nested, dict) else None
    album_id = album.get('id') if isinstance(album, dict) else None
    if album_id:
        candidates.update(
            f'{_bare_scope_track_id(value)}::{album_id}'
            for value in tuple(candidates)
            if _bare_scope_track_id(value)
        )
    return candidates


def _tracks_in_scope(
    tracks: Iterable[Dict[str, Any]], track_ids: set[str]
) -> List[Dict[str, Any]]:
    """Resolve composite scope exactly; accept legacy bare ids only uniquely."""
    rows = list(tracks)
    identities = [_track_scope_identities(track) for track in rows]
    exact = {value for value in track_ids if '::' in value}
    bare = {value for value in track_ids if '::' not in value}
    bare_matches: Dict[str, List[int]] = {value: [] for value in bare}
    for index, row_ids in enumerate(identities):
        row_bases = {_bare_scope_track_id(value) for value in row_ids}
        for value in bare & row_bases:
            bare_matches[value].append(index)
    unique_indexes = {
        indexes[0] for indexes in bare_matches.values() if len(indexes) == 1
    }
    return [
        track
        for index, (track, row_ids) in enumerate(zip(rows, identities, strict=True))
        if row_ids & exact or index in unique_indexes
    ]


def process_wishlist_automatically(
    runtime: WishlistAutoProcessingRuntime,
    automation_id=None,
    *,
    apply_backoff=None,
    track_ids: Optional[Iterable[str]] = None,
    profile_ids: Optional[Iterable[int]] = None,
):
    """Run automatic Wishlist processing, optionally within an exact scope.

    ``track_ids`` is used by a single-playlist Pipeline run. In that mode the
    processor reads and dispatches only matching Wishlist rows, skips global
    duplicate cleanup, processes every category, and leaves the timer's global
    album/single cycle untouched.

    ``apply_backoff`` gates the progressive retry-backoff filter below; unset,
    it defaults to on for scheduled runs (``automation_id`` present, including
    a playlist scope) and off for the manual "Process Wishlist Now" trigger.
    """
    logger = runtime.logger
    scoped = track_ids is not None
    requested_track_ids = _normalize_track_scope(track_ids or ())
    requested_profile_ids = {
        int(profile_id) for profile_id in (profile_ids or ())
        if str(profile_id).strip().lstrip('-').isdigit()
    }
    log_source = 'Playlist scope' if scoped else 'Timer'
    logger.info(
        f"[Auto-Wishlist] {log_source} triggered - starting automatic wishlist processing..."
    )

    try:
        # CRITICAL FIX: Use smart stuck detection BEFORE acquiring lock
        # This prevents deadlock and handles stuck flags (2-hour timeout)
        if runtime.is_actually_processing():
            logger.info("[Auto-Wishlist] Already processing (verified with stuck detection), skipping.")
            return

        with runtime.processing_guard() as acquired:
            if not acquired:
                logger.info("[Auto-Wishlist] Already processing (race condition check), skipping.")
                return

            with runtime.app_context_factory():
                wishlist_service = get_wishlist_service()

                # Check if wishlist has tracks across all profiles
                database = runtime.get_profiles_database()
                # a profile without download rights keeps a wishlist of
                # requests: the scheduler only downloads the rows an admin
                # approved (it used to grab every profile's whole list)
                from core.permissions import profile_can_download
                all_profiles = database.get_all_profiles()
                if scoped:
                    scoped_profile_ids = requested_profile_ids or {int(runtime.profile_id)}
                    all_profiles = [
                        profile for profile in all_profiles
                        if int(profile['id']) in scoped_profile_ids
                    ]
                    if len(scoped_profile_ids) == 1:
                        runtime.profile_id = next(iter(scoped_profile_ids))
                approved_only = {p['id']: not profile_can_download(p) for p in all_profiles}

                def _count(p):
                    if approved_only[p['id']]:
                        return wishlist_service.get_wishlist_count(profile_id=p['id'], approved_only=True)
                    return wishlist_service.get_wishlist_count(profile_id=p['id'])

                def _tracks(p):
                    if approved_only[p['id']]:
                        tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=p['id'],
                                                                                   approved_only=True)
                    else:
                        tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=p['id'])
                    # A6: remembered so a multi-profile playlist scope
                    # can dispatch each profile's tracks under its own
                    # profile_id instead of collapsing everything onto
                    # whatever runtime.profile_id happens to be set to.
                    _tag_wishlist_owner(tracks, p['id'])
                    return tracks

                raw_wishlist_tracks = []
                if scoped:
                    if not requested_track_ids or not all_profiles:
                        logger.info('[Auto-Wishlist] Empty playlist scope; nothing to process')
                        return
                    for profile in all_profiles:
                        raw_wishlist_tracks.extend(
                            _tracks_in_scope(_tracks(profile), requested_track_ids)
                        )
                    count = len(raw_wishlist_tracks)
                else:
                    count = sum(_count(p) for p in all_profiles)
                logger.info(f"[Auto-Wishlist] Wishlist count check: {count} tracks found across {len(all_profiles)} profiles")
                runtime.update_automation_progress(automation_id, progress=10, phase='Checking wishlist',
                                                   log_line=f'{count} tracks across {len(all_profiles)} profiles', log_type='info')
                if count == 0:
                    logger.warning("ℹ️ [Auto-Wishlist] No tracks in wishlist for auto-processing.")
                    return

                logger.info(f"[Auto-Wishlist] Found {count} tracks in wishlist, starting automatic processing...")

                # Age alone does not prove a download is dead. The batch healer
                # owns recovery using task/worker state; keep blocking while the
                # batch is non-terminal, including slow downloads and held queues.
                playlist_id = "wishlist"
                with runtime.tasks_lock:
                    for _batch_id, batch_data in runtime.download_batches.items():
                        batch_playlist_id = batch_data.get('playlist_id')
                        if (batch_playlist_id in ['wishlist', 'wishlist_manual'] and
                                batch_data.get('phase') not in ['complete', 'error', 'cancelled', 'failed']):
                            logger.info(
                                f"Wishlist processing already active in another batch "
                                f"({batch_playlist_id}), skipping automatic start"
                            )
                            return

                # CRITICAL: Clean duplicates BEFORE fetching tracks to prevent count mismatches
                # This prevents the "11 tracks shown but 12 counted" bug
                music_database = runtime.get_music_database()

                if not scoped:
                    logger.warning("[Auto-Wishlist] Cleaning duplicate tracks before processing...")
                    for profile in all_profiles:
                        duplicates_removed = music_database.remove_wishlist_duplicates(profile_id=profile['id'])
                        if duplicates_removed > 0:
                            logger.warning(f"[Auto-Wishlist] Removed {duplicates_removed} duplicate tracks from profile {profile['id']}")

                # Cycle-start library sweep (H9): drop wishlist rows for tracks
                # the library already owns BEFORE the batch is built. Every
                # wishlist batch sets force_download_all=True, so the master
                # worker skips its per-track ownership check — without this
                # sweep an owned track (hand-imported, row survived a
                # completion-hook miss) re-downloads every cycle. Measured
                # ~40ms/track on the indexed exact-norm path (the old
                # "~1s/track" claim predates it), so this is a cheap pre-pass.
                # A sweep failure must not abort the cycle — it degrades to
                # the old behavior and the standalone cleanup endpoint.
                try:
                    _owned_removed = remove_tracks_already_in_library(
                        wishlist_service,
                        database,
                        music_database,
                        runtime.get_active_server(),
                        logger=logger,
                        log_prefix="[Auto-Wishlist]",
                    )
                    if _owned_removed:
                        logger.warning(
                            f"[Auto-Wishlist] Removed {_owned_removed} already-owned track(s) before processing")
                except Exception as _sweep_err:
                    logger.warning(f"[Auto-Wishlist] Library sweep failed, continuing without it: {_sweep_err}")
                runtime.update_automation_progress(automation_id, progress=25, phase='Preparing wishlist',
                                                   log_line='Ran library sweep — owned tracks removed before batch',
                                                   log_type='info')

                # Get wishlist tracks for processing - combine all profiles
                if not scoped:
                    raw_wishlist_tracks = []
                    for profile in all_profiles:
                        raw_wishlist_tracks.extend(_tracks(profile))
                if not raw_wishlist_tracks:
                    logger.warning("No tracks returned from wishlist service.")
                    return

                # SANITIZE: Ensure consistent data format from wishlist service
                wishlist_tracks, duplicates_found = sanitize_and_dedupe_wishlist_tracks(raw_wishlist_tracks)
                if duplicates_found > 0:
                    logger.warning(f"[Auto-Wishlist] Found and removed {duplicates_found} duplicate tracks during sanitization")
                logger.info(f"[Auto-Wishlist] Sanitized {len(wishlist_tracks)} tracks from wishlist service")

                # #705: skip tracks with a future release date — searching for
                # them burns a full search+timeout per track, every cycle, for
                # files that can't exist yet. They stay in the wishlist and
                # join the cycle automatically once their date passes.
                from core.metadata.release_dates import split_released_unreleased
                wishlist_tracks, _unreleased = split_released_unreleased(wishlist_tracks)
                if _unreleased:
                    logger.info(
                        f"[Auto-Wishlist] Skipping {len(_unreleased)} unreleased track(s) "
                        f"(future release date) — they'll be searched once released")

                # Progressive retry backoff (javiavid): repeatedly-failing tracks
                # earn a growing cooldown (4h → 24h → 7d) instead of burning a
                # search every hourly cycle forever. SCHEDULED work only — the
                # manual "Process Wishlist Now" button retries everything (the
                # click is the override, like Sonarr's manual search). Callers
                # may state intent via apply_backoff; otherwise a present
                # automation_id marks the run as scheduled — including a
                # playlist-scoped Pipeline trigger, which is scheduled work
                # too, not a manual override.
                _backoff = apply_backoff if apply_backoff is not None else (automation_id is not None)
                # "The user asked for this run" — the same condition that skips
                # backoff. Named because the empty-category branch below needs it too.
                _is_user_initiated = not _backoff

                if _backoff:
                    from datetime import datetime as _dt, timezone as _tz

                    from core.wishlist.retry_backoff import (
                        get_active_retry_profile, split_due_for_retry)
                    # NAIVE UTC on purpose: last_attempted is SQLite's naive-UTC
                    # CURRENT_TIMESTAMP and the parser yields naive datetimes —
                    # an aware 'now' would TypeError on comparison.
                    _retry_profile = get_active_retry_profile(music_database)
                    wishlist_tracks, _cooling = split_due_for_retry(
                        wishlist_tracks, _dt.now(_tz.utc).replace(tzinfo=None),
                        profile=_retry_profile)
                    if _cooling:
                        _profile_label = _retry_profile.get('label') or 'Standard'
                        logger.info(
                            f"[Auto-Wishlist] {len(_cooling)} track(s) cooling down after "
                            f"repeated failures ({_profile_label} retry profile) — "
                            f"skipped this cycle")
                        runtime.update_automation_progress(
                            automation_id, log_line=(
                                f'{len(_cooling)} repeatedly-failing track(s) on backoff '
                                f'cooldown ({_profile_label} profile) — retried later '
                                f'automatically'),
                            log_type='info')

                # A playlist run must cover every one of its tracks in this
                # invocation. The timer retains its alternating category cycle.
                current_cycle = 'playlist' if scoped else get_wishlist_cycle(
                    lambda: music_database
                )
                filtered_tracks = wishlist_tracks
                if not scoped:
                    filtered_tracks, _ = filter_wishlist_tracks_by_category(
                        wishlist_tracks, current_cycle
                    )

                logger.info(f"[Auto-Wishlist] Current cycle: {current_cycle}")
                logger.info(f"[Auto-Wishlist] Filtered {len(filtered_tracks)}/{len(wishlist_tracks)} tracks for '{current_cycle}' category")
                runtime.update_automation_progress(automation_id, progress=40, phase=f'Processing {current_cycle}',
                                                   log_line=f'Cycle: {current_cycle} — {len(filtered_tracks)} tracks to process', log_type='info')

                # If no tracks in this category, skip to next cycle immediately
                if len(filtered_tracks) == 0 and not scoped:
                    next_cycle = 'singles' if current_cycle == 'albums' else 'albums'

                    # A SCHEDULED run can afford to wait for its next tick, but
                    # a user click must never be a silent no-op: Zombiehamser
                    # pressed "process wishlist" against 674 tracks and got no
                    # batch and "no active work" because the hidden cycle
                    # happened to sit on the empty category (#1196). One click =
                    # one real attempt, so try the other category right now.
                    _other, _ = filter_wishlist_tracks_by_category(wishlist_tracks, next_cycle)
                    if _is_user_initiated and _other:
                        logger.info(
                            f"[Auto-Wishlist] No {current_cycle} tracks — user-initiated run, "
                            f"switching to {next_cycle} ({len(_other)} track(s)) instead of idling")
                        set_wishlist_cycle(lambda: music_database, next_cycle)
                        current_cycle = next_cycle
                        filtered_tracks = _other
                    else:
                        logger.warning(f"ℹ️ [Auto-Wishlist] No {current_cycle} tracks in wishlist, toggling cycle and scheduling next run")
                        set_wishlist_cycle(lambda: music_database, next_cycle)
                        logger.info(f"[Auto-Wishlist] Cycle toggled: {current_cycle} → {next_cycle}")
                        runtime.update_automation_progress(
                            automation_id, log_line=(
                                f'No {current_cycle} tracks this cycle — switched to '
                                f'{next_cycle} for the next run'),
                            log_type='info')
                        return

                # Use filtered tracks for processing — stamp original index
                wishlist_tracks = filtered_tracks

                # A manual run is the user saying "the source is back, try
                # again". Selection already ignored backoff for these, but the
                # stored counters were left untouched, so the NEXT scheduled
                # cycle still saw retry_count=4 and sat the track out for
                # another 7 days — tracks that failed during an outage stayed
                # stranded long after it ended (#1196, Zombiehamser: 634 of 674
                # stuck at retry 3-4). Clearing the clock for the tracks this
                # run is about to attempt makes the recovery real.
                #
                # S13: the clear is scoped to the cycle-filtered tracks and to
                # each track's owning profile. The old version ran before the
                # cycle filter with profile_id=None, so one click cleared the
                # retry clock for every profile's rows — including categories
                # this cycle never attempts.
                if _is_user_initiated:
                    try:
                        _backoff_ids: dict = {}
                        for _t in wishlist_tracks:
                            _tid = (_t.get('spotify_track_id')
                                    or _t.get('track_id') or _t.get('id'))
                            if not _tid:
                                continue
                            _pid = _t.get('profile_id') or runtime.profile_id
                            _backoff_ids.setdefault(_pid, []).append(_tid)
                        _cleared = 0
                        for _pid, _ids in _backoff_ids.items():
                            _cleared += music_database.reset_wishlist_retry_backoff(
                                _ids, profile_id=_pid)
                        if _cleared:
                            logger.info(
                                f"[Auto-Wishlist] Manual run — cleared retry backoff on "
                                f"{_cleared} previously-failing track(s)")
                    except Exception:
                        # bookkeeping must never stop the actual run
                        logger.exception("[Auto-Wishlist] Could not clear retry backoff")

                for i, track in enumerate(wishlist_tracks):
                    track['_original_index'] = i

                # Reify one "wishlist run" id (the completion handler gates the
                # once-per-run cycle toggle on it) and hand off to the SHARED
                # wishlist engine — the same code path the manual trigger uses.
                wishlist_run_id = str(uuid.uuid4())
                _track_profiles = {
                    int(t.get('_wishlist_profile_id') or runtime.profile_id) for t in wishlist_tracks
                }
                _cycle_extra = {
                    'wishlist_scope': 'playlist',
                    'toggle_wishlist_cycle': False,
                } if scoped else None
                _runtime_profile = runtime.profile_id
                if len(_track_profiles) == 1:
                    # one profile's tracks: dispatched under THAT profile, which
                    # is also the library they land in (#1199) -- not under
                    # whichever profile the runtime was built for
                    runtime.profile_id = next(iter(_track_profiles))
                if len(_track_profiles) > 1:
                    # A6: tracks in this playlist scope span more than one
                    # wishlist profile. _run_wishlist_cycle stamps every batch
                    # it creates with the single runtime.profile_id in effect
                    # at call time, so each profile's tracks get their own
                    # cycle run (own batches, own profile_id, own quality
                    # settings) instead of merging into one dispatch that
                    # would land entirely under whichever profile
                    # runtime.profile_id last happened to be.
                    grouped_by_profile: Dict[int, list] = {}
                    for track in wishlist_tracks:
                        group_id = int(track.get('_wishlist_profile_id') or runtime.profile_id)
                        grouped_by_profile.setdefault(group_id, []).append(track)
                    _cycle_result = {'submitted': [], 'album_batches': 0, 'residual_count': 0}
                    for group_profile_id, group_tracks in grouped_by_profile.items():
                        runtime.profile_id = group_profile_id
                        group_result = _run_wishlist_cycle(
                            runtime,
                            playlist_id=playlist_id,
                            cycle=current_cycle,
                            tracks=group_tracks,
                            run_id=wishlist_run_id,
                            auto_initiated=True,
                            batch_extra_fields=_cycle_extra,
                        )
                        _cycle_result['submitted'].extend(group_result.get('submitted') or [])
                        _cycle_result['album_batches'] += group_result['album_batches']
                        _cycle_result['residual_count'] += group_result['residual_count']
                else:
                    _cycle_result = _run_wishlist_cycle(
                        runtime,
                        playlist_id=playlist_id,
                        cycle=current_cycle,
                        tracks=wishlist_tracks,
                        run_id=wishlist_run_id,
                        auto_initiated=True,
                        batch_extra_fields=_cycle_extra,
                    )
                runtime.profile_id = _runtime_profile

                _summary_parts: list[str] = []
                if _cycle_result['album_batches']:
                    _summary_parts.append(f"{_cycle_result['album_batches']} album batch(es)")
                if _cycle_result['residual_count']:
                    _summary_parts.append(f"{_cycle_result['residual_count']} per-track")
                _summary_text = ', '.join(_summary_parts) or 'no batches'
                runtime.update_automation_progress(
                    automation_id, progress=50,
                    phase=f'Downloading {len(wishlist_tracks)} tracks',
                    log_line=f'Started: {_summary_text} for cycle {current_cycle}',
                    log_type='success',
                )

    except Exception as e:
        logger.error(f"Error in automatic wishlist processing: {e}")
        import traceback

        traceback.print_exc()
        runtime.update_automation_progress(automation_id, log_line=f'Error: {str(e)}', log_type='error')
        raise


# jadux's stall: every DB-update completion fires a cleanup, and on a big
# wishlist (3450 tracks) one pass takes HOURS (per-track fuzzy matching), so
# they stacked until every worker of the shared pool was grinding cleanups and
# completed downloads queued behind them forever. One cleanup at a time —
# a request that arrives while one is running is SKIPPED (the next DB update
# will fire another against fresher state; nothing is lost by not stacking).
_auto_cleanup_lock = threading.Lock()
_auto_cleanup_running = False


def automatic_wishlist_cleanup_after_db_update(
    *,
    wishlist_service=None,
    profiles_database=None,
    music_database=None,
    active_server: str | None = None,
    logger=logger,
) -> int:
    """Remove wishlist entries that already exist in the library after a DB update.

    Overlap-guarded: only one cleanup runs at a time; concurrent requests are
    skipped (returning 0), never queued."""
    global _auto_cleanup_running
    with _auto_cleanup_lock:
        if _auto_cleanup_running:
            logger.info("[Auto Cleanup] Previous cleanup still running — skipping this one")
            return 0
        _auto_cleanup_running = True
    try:
        return _automatic_wishlist_cleanup_inner(
            wishlist_service=wishlist_service,
            profiles_database=profiles_database,
            music_database=music_database,
            active_server=active_server,
            logger=logger,
        )
    finally:
        with _auto_cleanup_lock:
            _auto_cleanup_running = False


def _automatic_wishlist_cleanup_inner(
    *,
    wishlist_service=None,
    profiles_database=None,
    music_database=None,
    active_server: str | None = None,
    logger=logger,
) -> int:
    try:
        from core.settings import config_manager
        from database.music_database import MusicDatabase, get_database

        wishlist_service = wishlist_service or get_wishlist_service()
        profiles_database = profiles_database or get_database()
        music_database = music_database or MusicDatabase()
        active_server = active_server or config_manager.get_active_media_server()

        logger.info("[Auto Cleanup] Starting automatic wishlist cleanup after database update...")

        all_profiles = profiles_database.get_all_profiles()
        wishlist_tracks = []
        for profile in all_profiles:
            wishlist_tracks.extend(wishlist_service.get_wishlist_tracks_for_download(profile_id=profile["id"]))

        if not wishlist_tracks:
            logger.warning("[Auto Cleanup] No tracks in wishlist to clean up")
            return 0

        logger.info(f"[Auto Cleanup] Found {len(wishlist_tracks)} tracks in wishlist")

        removed_count = remove_tracks_already_in_library(
            wishlist_service,
            profiles_database,
            music_database,
            active_server,
            logger=logger,
            log_prefix="[Auto Cleanup]",
        )

        logger.info(f"[Auto Cleanup] Completed automatic cleanup: {removed_count} tracks removed from wishlist")
        return removed_count

    except Exception as e:
        logger.error(f"[Auto Cleanup] Error in automatic wishlist cleanup: {e}")
        import traceback

        traceback.print_exc()
        return 0


__all__ = [
    "remove_completed_tracks_from_wishlist",
    "add_cancelled_tracks_to_failed_tracks",
    "recover_uncaptured_failed_tracks",
    "build_wishlist_source_context",
    "resolve_failed_track_requeue_provenance",
    "finalize_auto_wishlist_completion",
    "automatic_wishlist_cleanup_after_db_update",
    "WishlistAutoProcessingRuntime",
    "WishlistManualDownloadRuntime",
    "process_wishlist_automatically",
    "start_direct_track_download_batch",
    "start_manual_wishlist_download_batch",
    "cleanup_wishlist_against_library",
    "remove_tracks_already_in_library",
]
