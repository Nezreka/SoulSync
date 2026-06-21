"""Automation handler: ``video_scan_library`` action.

The VIDEO twin of music's ``scan_library``. It does two things, in order:

  1. Tells the active media server (Plex/Jellyfin) to rescan ONLY the
     user's selected VIDEO sections (the movie + TV libraries chosen in
     Settings) — never the music library.
  2. Reads the server's current state into ``video.db`` so freshly-added
     media shows up as owned on the video side.

Both run through injected seams (``server_refresh`` / ``run_video_scan``)
so the handler stays a pure function: production lazily binds the real
``core.video`` functions; tests pass fakes and never spin up Flask, a DB,
or a media-server client.

ISOLATION NOTE: this handler lives on the SHARED automation side, so it
is allowed to import ``core.video`` — the isolation contract only forbids
``core/video`` and ``api/video`` from importing the MUSIC side, not the
other way round. Video core stays import-clean; the bridge lives here.

Like ``scan_library``, the handler owns its own progress reporting
(``_manages_own_progress: True``) so the engine doesn't stomp the live
phase string with a generic 'completed' label.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from core.automation.deps import AutomationDeps


def _default_server_refresh(media_type: str = "all") -> Dict[str, Any]:
    """Production wiring: nudge the media server to rescan its video sections.
    ``media_type`` scopes it to the Movie or TV section; 'all' nudges both."""
    from core.video.sources import refresh_video_server_sections
    return refresh_video_server_sections(media_type)


def _default_run_video_scan(mode: str, media_type: str = "all") -> Dict[str, Any]:
    """Production wiring: read the server into video.db (blocking). ``media_type``
    scopes it to one library ('movie' / 'show'); 'all' does both."""
    from api.video import get_video_db
    from core.video.scanner import get_video_scanner
    from core.video.sources import get_active_video_source
    return get_video_scanner(get_video_db()).scan_sync(get_active_video_source, mode, media_type)


def auto_video_scan_library(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    server_refresh: Optional[Callable[[], Dict[str, Any]]] = None,
    run_video_scan: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Trigger a server-side video rescan, then mirror the result into video.db.

    ``config['media_type']`` scopes the scan to one library — 'movie' or 'show'
    (TV); 'all' (default) does both. Movies and TV are independent libraries, so
    the Movie scan never touches TV and vice-versa.

    Returns one of:
      - ``{'status': 'completed', '_manages_own_progress': True, 'movies': .., 'shows': .., 'episodes': ..}``
      - ``{'status': 'skipped', 'reason': '...'}`` (another scan was already running)
      - ``{'status': 'error', 'error': '...', '_manages_own_progress': True}``
    """
    server_refresh = server_refresh or _default_server_refresh
    run_video_scan = run_video_scan or _default_run_video_scan

    automation_id = config.get('_automation_id')
    # 'full' is the safe default — upsert everything, never prune. 'deep' prunes
    # what the server no longer has; only use it when the config asks explicitly.
    mode = config.get('mode') or 'full'
    media_type = config.get('media_type') or 'all'
    lib_label = {'movie': 'Movie', 'show': 'TV'}.get(media_type, 'video')

    try:
        deps.update_progress(
            automation_id,
            phase=f'Asking media server to rescan {lib_label} sections...',
            progress=10,
            log_line=f'Triggering server-side {lib_label} scan',
            log_type='info',
        )

        # Step 1 — best-effort server nudge, scoped to the same library we're about
        # to read. A server that can't be triggered (none configured, or an adapter
        # without refresh support) is surfaced as a warning, NOT a hard failure: the
        # read below still mirrors whatever the server currently reports.
        refresh = server_refresh(media_type) or {}
        if not refresh.get('ok'):
            deps.update_progress(
                automation_id,
                log_line='Server scan trigger unavailable: ' + str(refresh.get('error') or 'unknown'),
                log_type='warning',
            )
        else:
            sections = refresh.get('sections')
            detail = str(sections) + ' video section(s)' if sections else 'selected video section(s)'
            deps.update_progress(
                automation_id,
                progress=30,
                log_line='Server rescanning ' + detail,
                log_type='success',
            )

        # Step 2 — read the server into video.db (blocking; mirrors music's
        # scan handler blocking until the scan resolves).
        deps.update_progress(
            automation_id,
            phase=f'Reading {lib_label} library into SoulSync...',
            progress=45,
        )
        result = run_video_scan(mode, media_type) or {}
        state = result.get('state')
        # Singleton scanner already busy with another scan (e.g. the Movie + TV
        # deep scans firing close together) — skip cleanly, don't error.
        if state == 'in_progress':
            deps.update_progress(
                automation_id, status='finished', phase='Skipped',
                log_line='Another video scan is already running — skipping this run',
                log_type='info',
            )
            return {'status': 'skipped', 'reason': 'a video scan is already running',
                    '_manages_own_progress': True}
        if state == 'error':
            err = result.get('error') or 'Video library scan failed'
            deps.update_progress(
                automation_id,
                status='error',
                phase='Error',
                log_line=err,
                log_type='error',
            )
            return {'status': 'error', 'error': err, '_manages_own_progress': True}

        movies = int(result.get('movies', 0) or 0)
        shows = int(result.get('shows', 0) or 0)
        episodes = int(result.get('episodes', 0) or 0)
        # Summary names only the scanned library so a TV scan doesn't read "0 movies".
        if media_type == 'movie':
            summary = f'Movie library scanned: {movies} movies'
        elif media_type == 'show':
            summary = f'TV library scanned: {shows} shows, {episodes} episodes'
        else:
            summary = f'Video library scanned: {movies} movies, {shows} shows, {episodes} episodes'
        deps.update_progress(
            automation_id,
            status='finished',
            progress=100,
            phase='Complete',
            log_line=summary,
            log_type='success',
        )
        return {
            'status': 'completed',
            '_manages_own_progress': True,
            'movies': movies,
            'shows': shows,
            'episodes': episodes,
        }

    except Exception as e:  # noqa: BLE001 — automation handlers must never raise into the engine
        deps.update_progress(
            automation_id,
            status='error',
            phase='Error',
            log_line=str(e),
            log_type='error',
        )
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}


# ── post-download chain (video twin of music's scan_library → start_database_update) ──
# Split into two stages so the calendar/library reflect a download promptly, mirroring
# the music side: tell the server to rescan, then (after it has had time to index) read
# the new state into video.db. Stage 1 emits 'video_library_scan_completed' on a debounce
# (like music's web_scan_manager time-based completion); stage 2 listens for that.

def auto_video_scan_server(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    server_refresh: Optional[Callable[[], Dict[str, Any]]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    emit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Stage 1: tell the media server to rescan the video sections, wait a debounce
    for it to index, then fire 'video_library_scan_completed' so the DB-update twin
    reads the fresh state. (Video twin of music's scan_library.)"""
    server_refresh = server_refresh or _default_server_refresh
    sleep = sleep or time.sleep
    emit = emit or deps.engine.emit
    automation_id = config.get('_automation_id')
    media_type = config.get('media_type') or 'all'
    lib_label = {'movie': 'Movie', 'show': 'TV'}.get(media_type, 'video')
    try:
        debounce = int(config.get('debounce_seconds') or 120)
    except (TypeError, ValueError):
        debounce = 120
    try:
        deps.update_progress(automation_id, phase=f'Asking media server to rescan {lib_label}…', progress=20,
                             log_line=f'Triggering server-side {lib_label} scan', log_type='info')
        refresh = server_refresh(media_type) or {}
        if not refresh.get('ok'):
            deps.update_progress(automation_id,
                                 log_line='Server scan trigger unavailable: ' + str(refresh.get('error') or 'unknown'),
                                 log_type='warning')
        deps.update_progress(automation_id, phase=f'Waiting for the server to index ({debounce}s)…', progress=55)
        sleep(debounce)
        # Carry the scope so stage 2 (Update DB) reads only the library we rescanned.
        emit('video_library_scan_completed',
             {'server': refresh.get('server') or '', 'media_type': media_type})
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line='Server scan done — updating the database', log_type='success')
        return {'status': 'completed', '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}


def auto_video_update_database(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    run_video_scan: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Stage 2: read the (now-rescanned) server into video.db — INCREMENTAL by default
    (newest-first, stop after N consecutive known). Video twin of start_database_update.

    ``media_type`` scopes it to the Movie or TV library ('all' does both). When fired by
    the post-download chain it inherits the scope of the scan that ran (carried on the
    event), so a TV-only rescan updates only TV."""
    run_video_scan = run_video_scan or _default_run_video_scan
    automation_id = config.get('_automation_id')
    mode = config.get('mode') or 'incremental'
    media_type = (config.get('media_type')
                  or (config.get('_event_data') or {}).get('media_type')
                  or 'all')
    lib_label = {'movie': 'Movie', 'show': 'TV'}.get(media_type, 'video')
    # 'deep'/'full' re-read the whole library; 'incremental' only grabs new items.
    phase = (f'Re-reading the {lib_label} library from the server…' if mode in ('deep', 'full')
             else f'Reading new {lib_label} media into SoulSync…')
    try:
        deps.update_progress(automation_id, phase=phase, progress=40)
        result = run_video_scan(mode, media_type) or {}
        if result.get('state') == 'in_progress':
            deps.update_progress(automation_id, status='finished', phase='Skipped',
                                 log_line='Another video scan is already running — skipping this run',
                                 log_type='info')
            return {'status': 'skipped', 'reason': 'a video scan is already running',
                    '_manages_own_progress': True}
        if result.get('state') == 'error':
            err = result.get('error') or 'Video database update failed'
            deps.update_progress(automation_id, status='error', phase='Error', log_line=err, log_type='error')
            return {'status': 'error', 'error': err, '_manages_own_progress': True}
        movies = int(result.get('movies', 0) or 0)
        shows = int(result.get('shows', 0) or 0)
        episodes = int(result.get('episodes', 0) or 0)
        if media_type == 'movie':
            summary = f'Video database updated: {movies} movies'
        elif media_type == 'show':
            summary = f'Video database updated: {shows} shows, {episodes} episodes'
        else:
            summary = f'Video database updated: {movies} movies, {shows} shows, {episodes} episodes'
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=summary, log_type='success')
        return {'status': 'completed', '_manages_own_progress': True,
                'movies': movies, 'shows': shows, 'episodes': episodes}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
