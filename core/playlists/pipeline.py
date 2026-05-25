"""Mirrored playlist lifecycle pipeline.

This module is the playlist-domain home for the all-in-one mirrored
playlist pipeline:

    refresh source -> discover metadata -> sync to server -> process wishlist

Automation remains one caller, but the orchestration itself lives here so a
future playlist-card "Run Pipeline" button can call the same command.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List


DISCOVERY_TIMEOUT_SECONDS = 3600


RefreshFn = Callable[[Dict[str, Any], Any], Dict[str, Any]]
SyncOneFn = Callable[[Dict[str, Any], Any], Dict[str, Any]]
SyncAndWishlistFn = Callable[..., Dict[str, int]]


def run_mirrored_playlist_pipeline(
    config: Dict[str, Any],
    deps: Any,
    *,
    refresh_fn: RefreshFn,
    sync_one_fn: SyncOneFn,
    sync_and_wishlist_fn: SyncAndWishlistFn,
) -> Dict[str, Any]:
    """Run REFRESH -> DISCOVER -> SYNC -> WISHLIST in sequence.

    ``deps`` intentionally uses duck typing. Today it is ``AutomationDeps``;
    a future web/UI runner can provide the same small surface without becoming
    an automation.
    """
    if hasattr(deps.state, 'try_start_pipeline'):
        if not deps.state.try_start_pipeline():
            return {
                'status': 'skipped',
                'reason': 'playlist_pipeline is already running',
                '_manages_own_progress': True,
            }
    else:
        deps.state.set_pipeline_running(True)
    automation_id = config.get('_automation_id')
    trigger_source = config.get('_trigger_source') or (
        'manual' if str(automation_id or '').startswith('mirrored_') else 'automation'
    )
    pipeline_start = time.time()
    history_playlists: List[Dict[str, Any]] = []
    before_snapshots: Dict[int, Dict[str, Any]] = {}

    try:
        db = deps.get_database()
        playlist_id = config.get('playlist_id')
        process_all = config.get('all', False)
        skip_wishlist = config.get('skip_wishlist', False)

        playlists = _resolve_pipeline_playlists(db, playlist_id, process_all)
        if playlists is None:
            deps.state.set_pipeline_running(False)
            return {'status': 'error', 'error': 'No playlist specified'}

        playlists = _filter_refreshable_playlists(playlists)
        if not playlists:
            deps.state.set_pipeline_running(False)
            return {'status': 'error', 'error': 'No refreshable playlists found'}
        history_playlists = list(playlists)
        before_snapshots = {
            int(pl['id']): _playlist_history_snapshot(db, pl)
            for pl in history_playlists
            if pl.get('id')
        }

        deps.update_progress(
            automation_id,
            progress=2,
            phase=f'Pipeline: {len(playlists)} playlist(s)',
            log_line=f'Starting pipeline for: {_summarize_playlist_names(playlists)}',
            log_type='info',
        )

        refreshed, refresh_errors = _run_refresh_phase(
            config,
            deps,
            automation_id,
            refresh_fn=refresh_fn,
        )

        _run_discovery_phase(
            deps,
            automation_id,
            db=db,
            playlist_id=playlist_id,
            process_all=process_all,
        )

        sync_summary = sync_and_wishlist_fn(
            deps,
            automation_id,
            [pl for pl in playlists if pl.get('id')],
            sync_one_fn=lambda pl: sync_one_fn(
                {'playlist_id': str(pl['id']), '_automation_id': None},
                deps,
            ),
            sync_id_for_fn=lambda pl: f"auto_mirror_{pl['id']}",
            skip_wishlist=skip_wishlist,
            progress_start=56,
            progress_end=85,
            sync_phase_label='Phase 3/4: Syncing to server...',
            sync_phase_start_log='Phase 3: Sync',
            wishlist_phase_label='Phase 4/4: Processing wishlist...',
            wishlist_phase_start_log='Phase 4: Wishlist',
        )

        duration = int(time.time() - pipeline_start)
        deps.update_progress(
            automation_id,
            status='finished',
            progress=100,
            phase='Pipeline complete',
            log_line=f'Pipeline finished in {duration // 60}m {duration % 60}s',
            log_type='success',
        )

        result = {
            'status': 'completed',
            '_manages_own_progress': True,
            'playlists_refreshed': str(refreshed),
            'tracks_discovered': 'completed',
            'tracks_synced': str(sync_summary['synced']),
            'sync_skipped': str(sync_summary['skipped']),
            'wishlist_queued': str(sync_summary['wishlist_queued']),
            'duration_seconds': str(duration),
        }
        try:
            _record_playlist_pipeline_history(
                db,
                history_playlists,
                before_snapshots,
                result,
                status='completed',
                started_at=pipeline_start,
                finished_at=time.time(),
                trigger_source=trigger_source,
            )
        except Exception as history_error:  # noqa: BLE001 - history should never fail a successful pipeline
            deps.logger.debug(f"[Pipeline] History recording failed: {history_error}")
        deps.state.set_pipeline_running(False)
        return result

    except Exception as e:  # noqa: BLE001 - pipeline callers should receive status dicts
        deps.state.set_pipeline_running(False)
        try:
            if history_playlists:
                _record_playlist_pipeline_history(
                    db,
                    history_playlists,
                    before_snapshots,
                    {'status': 'error', 'error': str(e), '_manages_own_progress': True},
                    status='error',
                    started_at=pipeline_start,
                    finished_at=time.time(),
                    trigger_source=trigger_source,
                )
        except Exception as history_error:  # noqa: BLE001 - history should never mask pipeline errors
            deps.logger.debug(f"[Pipeline] History recording failed after error: {history_error}")
        deps.update_progress(
            automation_id,
            status='error',
            progress=100,
            phase='Pipeline error',
            log_line=f'Pipeline failed: {e}',
            log_type='error',
        )
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}


def _pipeline_history_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _playlist_history_snapshot(db: Any, playlist: Dict[str, Any]) -> Dict[str, Any]:
    playlist_id = int(playlist['id'])
    current = db.get_mirrored_playlist(playlist_id) or playlist
    counts = db.get_mirrored_playlist_status_counts(playlist_id)
    return {
        'playlist_id': playlist_id,
        'name': current.get('name') or playlist.get('name') or '',
        'source': current.get('source') or playlist.get('source') or '',
        'track_count': int(counts.get('total') or current.get('track_count') or 0),
        'discovered_count': int(counts.get('discovered') or 0),
        'wishlisted_count': int(counts.get('wishlisted') or 0),
        'in_library_count': int(counts.get('in_library') or 0),
    }


def _playlist_history_summary(before: Dict[str, Any], after: Dict[str, Any], status: str) -> str:
    before_tracks = int(before.get('track_count') or 0)
    after_tracks = int(after.get('track_count') or 0)
    track_delta = after_tracks - before_tracks
    before_discovered = int(before.get('discovered_count') or 0)
    after_discovered = int(after.get('discovered_count') or 0)
    discovered_delta = after_discovered - before_discovered
    parts = [status.capitalize()]
    parts.append(f"{before_tracks} -> {after_tracks} tracks")
    if track_delta:
        parts.append(f"{track_delta:+d} tracks")
    if discovered_delta:
        parts.append(f"{discovered_delta:+d} discovered")
    return ' | '.join(parts)


def _record_playlist_pipeline_history(
    db: Any,
    playlists: List[Dict[str, Any]],
    before_snapshots: Dict[int, Dict[str, Any]],
    result: Dict[str, Any],
    *,
    status: str,
    started_at: float,
    finished_at: float,
    trigger_source: str,
) -> None:
    if not hasattr(db, 'insert_playlist_pipeline_run_history'):
        return
    duration = max(0, finished_at - started_at)
    for playlist in playlists:
        if not playlist.get('id'):
            continue
        playlist_id = int(playlist['id'])
        before = before_snapshots.get(playlist_id, {})
        after = _playlist_history_snapshot(db, playlist)
        db.insert_playlist_pipeline_run_history(
            playlist_id=playlist_id,
            playlist_name=after.get('name') or playlist.get('name') or '',
            source=after.get('source') or playlist.get('source') or '',
            profile_id=int(playlist.get('profile_id') or 1),
            trigger_source=trigger_source,
            started_at=_pipeline_history_timestamp(started_at),
            finished_at=_pipeline_history_timestamp(finished_at),
            duration_seconds=duration,
            status=status,
            summary=_playlist_history_summary(before, after, status),
            before_json=json.dumps(before),
            after_json=json.dumps(after),
            result_json=json.dumps(result),
            log_lines=None,
        )


def _resolve_pipeline_playlists(db: Any, playlist_id: Any, process_all: bool) -> List[Dict[str, Any]] | None:
    if process_all:
        return db.get_mirrored_playlists()
    if playlist_id:
        playlist = db.get_mirrored_playlist(int(playlist_id))
        return [playlist] if playlist else []
    return None


def _filter_refreshable_playlists(playlists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [pl for pl in playlists if pl.get('source', '') not in ('file', 'beatport')]


def _summarize_playlist_names(playlists: List[Dict[str, Any]]) -> str:
    pl_names = ', '.join(p.get('name', '?') for p in playlists[:3])
    if len(playlists) > 3:
        pl_names += f' (+{len(playlists) - 3} more)'
    return pl_names


def _run_refresh_phase(
    config: Dict[str, Any],
    deps: Any,
    automation_id: Any,
    *,
    refresh_fn: RefreshFn,
) -> tuple[int, int]:
    deps.update_progress(
        automation_id,
        progress=3,
        phase='Phase 1/4: Refreshing playlists...',
        log_line='Phase 1: Refresh',
        log_type='info',
    )

    refresh_config = dict(config)
    refresh_config['_automation_id'] = None
    refresh_result = refresh_fn(refresh_config, deps)
    refreshed = int(refresh_result.get('refreshed', 0))
    refresh_errors = int(refresh_result.get('errors', 0))

    deps.update_progress(
        automation_id,
        progress=25,
        phase='Phase 1/4: Refresh complete',
        log_line=f'Phase 1 done: {refreshed} refreshed, {refresh_errors} errors',
        log_type='success' if refresh_errors == 0 else 'warning',
    )
    return refreshed, refresh_errors


def _run_discovery_phase(
    deps: Any,
    automation_id: Any,
    *,
    db: Any,
    playlist_id: Any,
    process_all: bool,
) -> None:
    deps.update_progress(
        automation_id,
        progress=26,
        phase='Phase 2/4: Discovering metadata...',
        log_line='Phase 2: Discover',
        log_type='info',
    )

    if process_all:
        disc_playlists = db.get_mirrored_playlists()
    else:
        disc_playlists = [db.get_mirrored_playlist(int(playlist_id))]
    disc_playlists = [p for p in disc_playlists if p]

    disc_done = threading.Event()

    def _disc_wrapper(pls):
        try:
            deps.run_playlist_discovery_worker(pls, automation_id=None)
        except Exception as e:  # noqa: BLE001 - logged into pipeline progress
            deps.logger.error(f"[Pipeline] Discovery error: {e}")
        finally:
            disc_done.set()

    threading.Thread(
        target=_disc_wrapper,
        args=(disc_playlists,),
        daemon=True,
        name='pipeline-discover',
    ).start()

    poll_start = time.time()
    while not disc_done.wait(timeout=3):
        elapsed = int(time.time() - poll_start)
        deps.update_progress(
            automation_id,
            progress=min(26 + elapsed // 4, 54),
            phase=f'Phase 2/4: Discovering... ({elapsed}s)',
        )
        if elapsed > DISCOVERY_TIMEOUT_SECONDS:
            deps.update_progress(
                automation_id,
                log_line='Discovery timed out after 1 hour',
                log_type='warning',
            )
            break

    deps.update_progress(
        automation_id,
        progress=55,
        phase='Phase 2/4: Discovery complete',
        log_line='Phase 2 done: discovery complete',
        log_type='success',
    )
