"""Shared helpers between mirrored + personalized playlist pipelines.

Both pipelines end in the same shape:
1. SYNC each playlist to the active media server.
2. WISHLIST: trigger the wishlist processor for missing tracks.

The differing prefix (mirrored = REFRESH external sources + DISCOVER
metadata; personalized = SNAPSHOT manager-backed playlists) is owned
by each pipeline. This module owns the SYNC + WISHLIST tail so both
pipelines stay consistent + DRY.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from core.automation.deps import AutomationDeps


# Per-playlist sync poll cap (mirrored side already used this).
_SYNC_PER_PLAYLIST_TIMEOUT_SECONDS = 600
# Sync-status final-state markers.
_SYNC_TERMINAL_STATUSES = ('finished', 'complete', 'error', 'failed')


def run_sync_and_wishlist(
    deps: AutomationDeps,
    automation_id: Optional[str],
    playlists: List[Dict[str, Any]],
    *,
    sync_one_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    sync_id_for_fn: Callable[[Dict[str, Any]], str],
    skip_wishlist: bool = False,
    progress_start: int = 56,
    progress_end: int = 85,
    sync_phase_label: str = 'Phase: Syncing to server...',
    sync_phase_start_log: str = 'Sync',
    wishlist_phase_label: str = 'Phase: Processing wishlist...',
    wishlist_phase_start_log: str = 'Wishlist',
) -> Dict[str, int]:
    """Run the SYNC + WISHLIST tail of a playlist pipeline.

    The caller supplies:
    - ``playlists``: list of playlist payload dicts. Each must have at
      least a ``name`` key (used in progress logs). The shape beyond
      ``name`` is opaque to the helper — ``sync_one_fn`` receives the
      payload and returns a sync_result dict.
    - ``sync_one_fn(payload) -> sync_result``: launches sync for one
      playlist. Result dict must carry ``status`` ∈ ``('started',
      'skipped', 'error')`` and may carry ``reason``.
    - ``sync_id_for_fn(payload) -> str``: returns the sync-state key
      the helper polls on (so we can wait for the background sync
      thread to complete + read the matched_tracks count).

    Returns ``{'synced': int, 'skipped': int, 'errors': int,
    'wishlist_queued': int}`` so the caller can stitch it into its
    final status.
    """
    deps.update_progress(
        automation_id,
        progress=progress_start,
        phase=sync_phase_label,
        log_line=sync_phase_start_log,
        log_type='info',
    )

    total_synced = 0
    total_skipped = 0
    sync_errors = 0
    sync_states = deps.get_sync_states()
    n_playlists = max(1, len(playlists))
    progress_span = max(1, progress_end - progress_start - 1)

    for pl_idx, pl in enumerate(playlists):
        pl_name = pl.get('name', '')
        sync_result = sync_one_fn(pl)
        sync_status = sync_result.get('status', '')

        if sync_status == 'started':
            sync_id = sync_id_for_fn(pl)
            sync_poll_start = time.time()
            while time.time() - sync_poll_start < _SYNC_PER_PLAYLIST_TIMEOUT_SECONDS:
                if (sync_id in sync_states
                        and sync_states[sync_id].get('status') in _SYNC_TERMINAL_STATUSES):
                    break
                time.sleep(2)
                elapsed = int(time.time() - sync_poll_start)
                sub_progress = progress_start + 1 + ((pl_idx + 1) / n_playlists) * progress_span
                deps.update_progress(
                    automation_id,
                    progress=min(int(sub_progress), progress_end - 1),
                    phase=f'{sync_phase_label.rstrip(".")} — "{pl_name}" ({elapsed}s)',
                )

            ss = sync_states.get(sync_id, {})
            ss_result = ss.get('result', ss.get('progress', {}))
            matched = ss_result.get('matched_tracks', 0) if isinstance(ss_result, dict) else 0
            total_synced += int(matched) if matched else 0
            deps.update_progress(
                automation_id,
                log_line=f'Synced "{pl_name}": {matched} tracks matched',
                log_type='success',
            )

        elif sync_status == 'skipped':
            total_skipped += 1
            reason = sync_result.get('reason', 'unchanged')
            deps.update_progress(
                automation_id,
                log_line=f'Skipped "{pl_name}": {reason}',
                log_type='skip',
            )
        elif sync_status == 'error':
            sync_errors += 1
            deps.update_progress(
                automation_id,
                log_line=f'Sync error "{pl_name}": {sync_result.get("reason", "unknown")}',
                log_type='error',
            )

    deps.update_progress(
        automation_id,
        progress=progress_end,
        phase=f'{sync_phase_label.rstrip(".")} complete',
        log_line=f'Sync done: {total_synced} matched, {total_skipped} skipped, {sync_errors} errors',
        log_type='success' if sync_errors == 0 else 'warning',
    )

    wishlist_queued = run_wishlist_phase(
        deps, automation_id,
        skip=skip_wishlist,
        progress_pct=progress_end + 1,
        wishlist_phase_label=wishlist_phase_label,
        wishlist_phase_start_log=wishlist_phase_start_log,
    )

    return {
        'synced': total_synced,
        'skipped': total_skipped,
        'errors': sync_errors,
        'wishlist_queued': wishlist_queued,
    }


def run_wishlist_phase(
    deps: AutomationDeps,
    automation_id: Optional[str],
    *,
    skip: bool,
    progress_pct: int,
    wishlist_phase_label: str = 'Phase: Processing wishlist...',
    wishlist_phase_start_log: str = 'Wishlist',
) -> int:
    """Trigger the wishlist processor unless skipped or already running.

    Returns 1 when the processor was triggered, 0 otherwise. Errors are
    logged but never raised — wishlist failure should not abort the
    pipeline."""
    if skip:
        deps.update_progress(
            automation_id,
            progress=progress_pct,
            log_line=f'{wishlist_phase_start_log}: skipped (disabled)',
            log_type='skip',
        )
        return 0

    deps.update_progress(
        automation_id,
        progress=progress_pct,
        phase=wishlist_phase_label,
        log_line=wishlist_phase_start_log,
        log_type='info',
    )

    try:
        if not deps.is_wishlist_actually_processing():
            deps.process_wishlist_automatically(automation_id=None)
            deps.update_progress(
                automation_id,
                log_line='Wishlist processing triggered',
                log_type='success',
            )
            return 1
        deps.update_progress(
            automation_id,
            log_line='Wishlist already running — skipped',
            log_type='skip',
        )
        return 0
    except Exception as e:  # noqa: BLE001 — wishlist failure must never abort pipeline
        deps.update_progress(
            automation_id,
            log_line=f'Wishlist error: {e}',
            log_type='warning',
        )
        return 0


__all__ = ['run_sync_and_wishlist', 'run_wishlist_phase']
