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

from typing import Any, Callable, Dict, Optional

from core.automation.deps import AutomationDeps


def _default_server_refresh() -> Dict[str, Any]:
    """Production wiring: nudge the media server to rescan its video sections."""
    from core.video.sources import refresh_video_server_sections
    return refresh_video_server_sections()


def _default_run_video_scan(mode: str) -> Dict[str, Any]:
    """Production wiring: read the server into video.db (blocking)."""
    from api.video import get_video_db
    from core.video.scanner import get_video_scanner
    from core.video.sources import get_active_video_source
    return get_video_scanner(get_video_db()).scan_sync(get_active_video_source, mode)


def auto_video_scan_library(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    server_refresh: Optional[Callable[[], Dict[str, Any]]] = None,
    run_video_scan: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Trigger a server-side video rescan, then mirror the result into video.db.

    Returns one of:
      - ``{'status': 'completed', '_manages_own_progress': True, 'movies': .., 'shows': .., 'episodes': ..}``
      - ``{'status': 'error', 'error': '...', '_manages_own_progress': True}``
    """
    server_refresh = server_refresh or _default_server_refresh
    run_video_scan = run_video_scan or _default_run_video_scan

    automation_id = config.get('_automation_id')
    # 'full' is the safe default — upsert everything, never prune. 'deep' prunes
    # what the server no longer has; only use it when the config asks explicitly.
    mode = config.get('mode') or 'full'

    try:
        deps.update_progress(
            automation_id,
            phase='Asking media server to rescan video sections...',
            progress=10,
            log_line='Triggering server-side video scan',
            log_type='info',
        )

        # Step 1 — best-effort server nudge. A server that can't be triggered
        # (none configured, or an adapter without refresh support) is surfaced
        # as a warning, NOT a hard failure: the read below still mirrors whatever
        # the server currently reports, so the automation stays useful.
        refresh = server_refresh() or {}
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
            phase='Reading library into SoulSync...',
            progress=45,
        )
        result = run_video_scan(mode) or {}
        state = result.get('state')
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
        deps.update_progress(
            automation_id,
            status='finished',
            progress=100,
            phase='Complete',
            log_line=f'Video library scanned: {movies} movies, {shows} shows, {episodes} episodes',
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
