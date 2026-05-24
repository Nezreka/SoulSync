"""Automation handler: ``playlist_pipeline`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_playlist_pipeline`` closure). Runs the full playlist
lifecycle in a single trigger:

  Phase 1: REFRESH    -- pull fresh track lists from sources
  Phase 2: DISCOVER   -- look up official Spotify/iTunes metadata
  Phase 3: SYNC       -- push the result to the active media server
  Phase 4: WISHLIST   -- queue any missing tracks for download

Each phase emits its own progress range so the trigger card shows
useful per-phase percentages instead of "loading...". Phase 4 is
optional via ``skip_wishlist`` config.

Composition: this handler invokes ``auto_refresh_mirrored`` and
``auto_sync_playlist`` directly (passing ``_automation_id: None`` so
the sub-handlers don't hijack pipeline progress) instead of going
through the engine — keeps the four phases observable as one
trigger from the user's perspective. Pipeline-level guard
(``state.pipeline_running``) prevents overlapping runs.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict

from core.automation.deps import AutomationDeps
from core.automation.handlers._pipeline_shared import run_sync_and_wishlist
from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.automation.handlers.sync_playlist import auto_sync_playlist


# Per-playlist sync poll cap inside Phase 3.
# Discovery poll cap inside Phase 2.
_DISCOVERY_TIMEOUT_SECONDS = 3600


def auto_playlist_pipeline(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run REFRESH → DISCOVER → SYNC → WISHLIST in sequence.

    Sets / clears ``deps.state.pipeline_running`` around the whole
    run so the registration guard can short-circuit overlapping
    triggers.
    """
    deps.state.set_pipeline_running(True)
    automation_id = config.get('_automation_id')
    pipeline_start = time.time()

    try:
        db = deps.get_database()
        playlist_id = config.get('playlist_id')
        process_all = config.get('all', False)
        skip_wishlist = config.get('skip_wishlist', False)

        # Resolve playlists.
        if process_all:
            playlists = db.get_mirrored_playlists()
        elif playlist_id:
            p = db.get_mirrored_playlist(int(playlist_id))
            playlists = [p] if p else []
        else:
            deps.state.set_pipeline_running(False)
            return {'status': 'error', 'error': 'No playlist specified'}

        playlists = [pl for pl in playlists if pl.get('source', '') not in ('file', 'beatport')]
        if not playlists:
            deps.state.set_pipeline_running(False)
            return {'status': 'error', 'error': 'No refreshable playlists found'}

        pl_names = ', '.join(p.get('name', '?') for p in playlists[:3])
        if len(playlists) > 3:
            pl_names += f' (+{len(playlists) - 3} more)'

        deps.update_progress(
            automation_id,
            progress=2,
            phase=f'Pipeline: {len(playlists)} playlist(s)',
            log_line=f'Starting pipeline for: {pl_names}',
            log_type='info',
        )

        # ── PHASE 1: REFRESH ──────────────────────────────────────────
        deps.update_progress(
            automation_id,
            progress=3,
            phase='Phase 1/4: Refreshing playlists...',
            log_line='Phase 1: Refresh',
            log_type='info',
        )

        refresh_config = dict(config)
        refresh_config['_automation_id'] = None  # Don't let sub-handler hijack pipeline progress.
        refresh_result = auto_refresh_mirrored(refresh_config, deps)
        refreshed = int(refresh_result.get('refreshed', 0))
        refresh_errors = int(refresh_result.get('errors', 0))

        deps.update_progress(
            automation_id,
            progress=25,
            phase='Phase 1/4: Refresh complete',
            log_line=f'Phase 1 done: {refreshed} refreshed, {refresh_errors} errors',
            log_type='success' if refresh_errors == 0 else 'warning',
        )

        # ── PHASE 2: DISCOVER ─────────────────────────────────────────
        deps.update_progress(
            automation_id,
            progress=26,
            phase='Phase 2/4: Discovering metadata...',
            log_line='Phase 2: Discover',
            log_type='info',
        )

        # Reload playlists (refresh may have updated them).
        if process_all:
            disc_playlists = db.get_mirrored_playlists()
        else:
            disc_playlists = [db.get_mirrored_playlist(int(playlist_id))]
        disc_playlists = [p for p in disc_playlists if p]

        # Run discovery in a thread and wait for it.
        disc_done = threading.Event()

        def _disc_wrapper(pls):
            try:
                # The worker updates automation_progress internally,
                # but we pass None so it doesn't conflict with our
                # pipeline progress.
                deps.run_playlist_discovery_worker(pls, automation_id=None)
            except Exception as e:
                deps.logger.error(f"[Pipeline] Discovery error: {e}")
            finally:
                disc_done.set()

        threading.Thread(
            target=_disc_wrapper, args=(disc_playlists,),
            daemon=True, name='pipeline-discover',
        ).start()

        # Poll for completion with progress updates.
        poll_start = time.time()
        while not disc_done.wait(timeout=3):
            elapsed = int(time.time() - poll_start)
            deps.update_progress(
                automation_id,
                progress=min(26 + elapsed // 4, 54),
                phase=f'Phase 2/4: Discovering... ({elapsed}s)',
            )
            if elapsed > _DISCOVERY_TIMEOUT_SECONDS:
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

        # ── PHASE 3 + 4: SYNC + WISHLIST (delegated to shared helper) ──
        # Each mirrored playlist payload only needs `id` + `name` for
        # the helper; `auto_sync_playlist` reads the rest from the
        # mirrored DB by id.
        sync_summary = run_sync_and_wishlist(
            deps,
            automation_id,
            [pl for pl in playlists if pl.get('id')],
            sync_one_fn=lambda pl: auto_sync_playlist(
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
        total_synced = sync_summary['synced']
        total_skipped = sync_summary['skipped']
        sync_errors = sync_summary['errors']
        wishlist_queued = sync_summary['wishlist_queued']

        # ── COMPLETE ──────────────────────────────────────────────────
        duration = int(time.time() - pipeline_start)
        deps.update_progress(
            automation_id,
            status='finished',
            progress=100,
            phase='Pipeline complete',
            log_line=f'Pipeline finished in {duration // 60}m {duration % 60}s',
            log_type='success',
        )

        deps.state.set_pipeline_running(False)
        return {
            'status': 'completed',
            '_manages_own_progress': True,
            'playlists_refreshed': str(refreshed),
            'tracks_discovered': 'completed',
            'tracks_synced': str(total_synced),
            'sync_skipped': str(total_skipped),
            'wishlist_queued': str(wishlist_queued),
            'duration_seconds': str(duration),
        }

    except Exception as e:
        deps.state.set_pipeline_running(False)
        deps.update_progress(
            automation_id,
            status='error',
            progress=100,
            phase='Pipeline error',
            log_line=f'Pipeline failed: {e}',
            log_type='error',
        )
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
