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
# the music side: tell the server to rescan, WAIT until its scan queue is actually idle
# (a big library can take 10-20 min — a fixed wait would read too early), then fire
# 'video_library_scan_completed'; stage 2 listens for that and reads the fresh state.

def _default_scan_status(media_type: str = "all"):
    """Production wiring: True/False if the active server is/ isn't scanning, or None
    when it can't report (so the caller falls back to a fixed wait)."""
    from core.video.sources import video_server_scan_in_progress
    return video_server_scan_in_progress(media_type)


def _default_latest_completed(media_type: str):
    """The newest completed grab of a type — the probe target for the smart skip."""
    from api.video import get_video_db
    return get_video_db().latest_completed_download(media_type)


def _default_server_has_item(media_type: str, item) -> bool:
    """Does the active server already have this specific grab indexed?"""
    from core.video.sources import video_server_has_item
    return video_server_has_item(media_type, item)


def wait_for_server_scan(scan_status, sleep, *, grace_seconds: int = 15,
                         interval_seconds: int = 10, cap_seconds: int = 3600,
                         fallback_seconds: int = 120) -> int:
    """Block until the media server's scan queue goes idle, then return ~seconds waited.

    ``scan_status()`` returns True (scanning), False (idle) or None (can't tell). After
    a short grace — so the just-triggered scan has time to register as running — poll
    every ``interval_seconds`` until idle or ``cap_seconds`` (a generous backstop for a
    huge library). If the server can't report its state, fall back to a fixed
    ``fallback_seconds`` wait — exactly the old behaviour. ``sleep`` is injected and
    elapsed is counted from the sleeps, so tests never actually block."""
    grace = max(0, grace_seconds)
    if grace:
        sleep(grace)
    waited = grace
    try:
        status = scan_status()
    except Exception:   # noqa: BLE001 - status is best-effort
        status = None
    if status is None:
        extra = max(0, fallback_seconds - waited)
        if extra:
            sleep(extra)
        return waited + extra
    while status is True and waited < cap_seconds:
        sleep(interval_seconds)
        waited += interval_seconds
        try:
            status = scan_status()
        except Exception:   # noqa: BLE001
            status = None
        if status is None:   # lost the ability to tell — stop waiting, don't hang
            break
    return waited


_LIB = {'movie': 'Movie', 'show': 'TV'}


def auto_video_scan_server(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    server_refresh: Optional[Callable[..., Dict[str, Any]]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    emit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    scan_status: Optional[Callable[..., Any]] = None,
    latest_completed: Optional[Callable[[str], Any]] = None,
    server_has_item: Optional[Callable[[str, Any], bool]] = None,
) -> Dict[str, Any]:
    """Stage 1: get the server to index our new downloads, then fire
    'video_library_scan_completed' so the DB-update twin reads the fresh state.

    SMART SKIP (``skip_if_present``, default on): scanning is expensive, and many
    servers auto-ingest new files. So per library, we first probe whether the server
    already has the NEWEST grab of that type (from download history) — if it does, it
    already picked everything up, and we skip that library's crawl. Only libraries the
    server is missing get the (expensive) rescan + poll-until-idle. We always emit so
    stage 2 still READS the new items into video.db. (Video twin of music's scan_library.)"""
    server_refresh = server_refresh or _default_server_refresh
    sleep = sleep or time.sleep
    emit = emit or deps.engine.emit
    scan_status = scan_status or _default_scan_status
    latest_completed = latest_completed or _default_latest_completed
    server_has_item = server_has_item or _default_server_has_item
    automation_id = config.get('_automation_id')
    media_type = config.get('media_type') or 'all'
    skip_if_present = config.get('skip_if_present', True)
    try:
        fallback = int(config.get('debounce_seconds') or 120)
    except (TypeError, ValueError):
        fallback = 120
    try:
        cap = int(config.get('max_wait_minutes') or 60) * 60
    except (TypeError, ValueError):
        cap = 3600
    try:
        # Which libraries actually need the expensive crawl? Skip any the server
        # already has the newest download for (it auto-ingested → nothing to find).
        scopes = ['movie', 'show'] if media_type == 'all' else \
            ([media_type] if media_type in ('movie', 'show') else ['movie', 'show'])
        to_scan = []
        for sc in scopes:
            if skip_if_present:
                latest = None
                try:
                    latest = latest_completed(sc)
                except Exception:   # noqa: BLE001
                    latest = None
                if latest:
                    try:
                        present = bool(server_has_item(sc, latest))
                    except Exception:   # noqa: BLE001
                        present = False
                    if present:
                        deps.update_progress(
                            automation_id, log_line=f'{_LIB[sc]} library: server already has the newest grab — skipping its scan',
                            log_type='info')
                        continue
            to_scan.append(sc)

        waited, server_name = 0, ''
        if to_scan:
            scan_scope = 'all' if set(to_scan) == {'movie', 'show'} else to_scan[0]
            lib_label = _LIB.get(scan_scope, 'video')
            deps.update_progress(automation_id, phase=f'Asking media server to rescan {lib_label}…', progress=20,
                                 log_line=f'Triggering server-side {lib_label} scan', log_type='info')
            refresh = server_refresh(scan_scope) or {}
            if not refresh.get('ok'):
                deps.update_progress(automation_id,
                                     log_line='Server scan trigger unavailable: ' + str(refresh.get('error') or 'unknown'),
                                     log_type='warning')
            server_name = refresh.get('server') or ''
            deps.update_progress(automation_id, phase='Waiting for the server to finish indexing…', progress=55)
            waited = wait_for_server_scan(lambda: scan_status(scan_scope), sleep,
                                          fallback_seconds=fallback, cap_seconds=cap)
        else:
            deps.update_progress(automation_id, progress=55,
                                 log_line='Server already has all the new downloads — no scan needed',
                                 log_type='success')

        # Always emit with the ORIGINAL scope so stage 2 reads everything we grabbed.
        emit('video_library_scan_completed', {'server': server_name, 'media_type': media_type})
        done = (f'Server scan finished (~{waited}s) — updating the database' if to_scan
                else 'Skipped server scan — updating the database')
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', '_manages_own_progress': True,
                'scanned': to_scan, 'skipped': [s for s in scopes if s not in to_scan]}
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
