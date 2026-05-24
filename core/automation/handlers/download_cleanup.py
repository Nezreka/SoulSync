"""Automation handlers: download-queue cleanup actions.

Lifted from ``web_server._register_automation_handlers``:
- ``clean_search_history``       → :func:`auto_clean_search_history`
- ``clean_completed_downloads``  → :func:`auto_clean_completed_downloads`
- ``full_cleanup``               → :func:`auto_full_cleanup`

All three share the download-orchestrator + tasks_lock /
download_batches / download_tasks accessors. ``full_cleanup`` is a
multi-step orchestration that pulls in quarantine purge + staging
sweep on top of the queue cleanup -- kept as one big handler since
its phases share state-detection logic.
"""

from __future__ import annotations

import os
import shutil as _shutil
from typing import Any, Dict

from core.automation.deps import AutomationDeps


# ─── clean_search_history ────────────────────────────────────────────


def auto_clean_search_history(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Remove old searches from Soulseek when configured."""
    automation_id = config.get('_automation_id')
    # Skip if soulseek is not the active download source or in hybrid order.
    dl_mode = deps.config_manager.get('download_source.mode', 'hybrid')
    hybrid_order = deps.config_manager.get(
        'download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'],
    )
    soulseek_active = (
        dl_mode == 'soulseek'
        or (dl_mode == 'hybrid' and 'soulseek' in hybrid_order)
    )
    # Reach the underlying SoulseekClient via the orchestrator's
    # generic accessor.
    slskd = deps.download_orchestrator.client('soulseek') if deps.download_orchestrator else None
    if not soulseek_active or not slskd or not slskd.base_url:
        deps.update_progress(automation_id, log_line='Soulseek not active — skipped', log_type='skip')
        return {'status': 'skipped'}
    if not deps.config_manager.get('soulseek.auto_clear_searches', True):
        deps.update_progress(
            automation_id, log_line='Auto-clear disabled in settings', log_type='skip',
        )
        return {'status': 'skipped'}
    try:
        success = deps.run_async(deps.download_orchestrator.maintain_search_history_with_buffer(
            keep_searches=50, trigger_threshold=200,
        ))
        if success:
            deps.update_progress(
                automation_id,
                log_line='Search history maintenance completed',
                log_type='success',
            )
            return {'status': 'completed'}
        else:
            deps.update_progress(automation_id, log_line='No cleanup needed', log_type='skip')
            return {'status': 'completed'}
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise
        return {'status': 'error', 'error': str(e)}


# ─── clean_completed_downloads ───────────────────────────────────────


def auto_clean_completed_downloads(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Clear completed downloads + sweep empty download directories.
    Skips when active batches or post-processing is in flight."""
    automation_id = config.get('_automation_id')
    try:
        has_active_batches = False
        has_post_processing = False
        with deps.tasks_lock:
            batches = deps.get_download_batches()
            for batch_data in batches.values():
                if batch_data.get('phase') not in ['complete', 'error', 'cancelled', None]:
                    has_active_batches = True
                    break
            if not has_active_batches:
                tasks = deps.get_download_tasks()
                for task_data in tasks.values():
                    if task_data.get('status') == 'post_processing':
                        has_post_processing = True
                        break

        if has_active_batches:
            deps.update_progress(
                automation_id, log_line='Skipped — downloads active', log_type='skip',
            )
            return {'status': 'completed'}

        deps.run_async(deps.download_orchestrator.clear_all_completed_downloads())
        if not has_post_processing:
            deps.sweep_empty_download_directories()
        deps.update_progress(
            automation_id, log_line='Download cleanup completed', log_type='success',
        )
        return {'status': 'completed'}
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise
        return {'status': 'error', 'reason': str(e)}


# ─── full_cleanup ────────────────────────────────────────────────────


def auto_full_cleanup(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run all cleanup tasks: quarantine purge → download queue clear
    → empty-dir sweep → staging sweep → search history."""
    automation_id = config.get('_automation_id')
    steps = []

    # --- 1. Clear quarantine ---
    deps.update_progress(automation_id, phase='Clearing quarantine...', progress=0)
    quarantine_path = os.path.join(
        deps.docker_resolve_path(deps.config_manager.get('soulseek.download_path', './downloads')),
        'ss_quarantine',
    )
    q_removed = 0
    if os.path.exists(quarantine_path):
        for f in os.listdir(quarantine_path):
            fp = os.path.join(quarantine_path, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    q_removed += 1
                elif os.path.isdir(fp):
                    _shutil.rmtree(fp)
                    q_removed += 1
            except Exception as e:  # noqa: BLE001 — best-effort purge
                deps.logger.debug("quarantine entry purge failed: %s", e)
    steps.append(f'Quarantine: removed {q_removed} items')
    deps.update_progress(
        automation_id,
        log_line=f'Quarantine: removed {q_removed} items',
        log_type='success' if q_removed else 'info',
    )

    # --- 2. Clear completed/errored/cancelled downloads from Soulseek queue ---
    deps.update_progress(automation_id, phase='Clearing download queue...', progress=20)
    has_active_batches = False
    has_post_processing = False
    with deps.tasks_lock:
        batches = deps.get_download_batches()
        for batch_data in batches.values():
            if batch_data.get('phase') not in ['complete', 'error', 'cancelled', None]:
                has_active_batches = True
                break
        if not has_active_batches:
            tasks = deps.get_download_tasks()
            for task_data in tasks.values():
                if task_data.get('status') == 'post_processing':
                    has_post_processing = True
                    break
    if has_active_batches:
        steps.append('Download queue: skipped (active batches)')
        deps.update_progress(
            automation_id,
            log_line='Download queue: skipped (active batches)',
            log_type='skip',
        )
    else:
        try:
            deps.run_async(deps.download_orchestrator.clear_all_completed_downloads())
            steps.append('Download queue: cleared')
            deps.update_progress(
                automation_id, log_line='Download queue: cleared', log_type='success',
            )
        except Exception as e:  # noqa: BLE001 — per-step best-effort
            steps.append(f'Download queue: error ({e})')
            deps.update_progress(
                automation_id,
                log_line=f'Download queue: error ({e})',
                log_type='error',
            )

    # --- 3. Sweep empty download directories ---
    deps.update_progress(automation_id, phase='Sweeping empty directories...', progress=40)
    if has_active_batches or has_post_processing:
        reason = 'active batches' if has_active_batches else 'post-processing active'
        steps.append(f'Empty directories: skipped ({reason})')
        deps.update_progress(
            automation_id,
            log_line=f'Empty directories: skipped ({reason})',
            log_type='skip',
        )
    else:
        dirs_removed = deps.sweep_empty_download_directories()
        steps.append(f'Empty directories: removed {dirs_removed}')
        deps.update_progress(
            automation_id,
            log_line=f'Empty directories: removed {dirs_removed}',
            log_type='success' if dirs_removed else 'info',
        )

    # --- 4. Sweep empty staging directories ---
    deps.update_progress(automation_id, phase='Sweeping import folder...', progress=60)
    staging_path = deps.get_staging_path()
    s_removed = 0
    if os.path.isdir(staging_path):
        for dirpath, _dirnames, _filenames in os.walk(staging_path, topdown=False):
            if os.path.normpath(dirpath) == os.path.normpath(staging_path):
                continue
            try:
                entries = os.listdir(dirpath)
            except OSError:
                continue
            visible = [e for e in entries if not e.startswith('.')]
            if not visible:
                for hidden in entries:
                    try:
                        os.remove(os.path.join(dirpath, hidden))
                    except Exception as e:  # noqa: BLE001 — best-effort
                        deps.logger.debug("hidden file cleanup failed: %s", e)
                try:
                    os.rmdir(dirpath)
                    s_removed += 1
                except OSError:
                    pass
    steps.append(f'Staging: removed {s_removed} empty directories')
    deps.update_progress(
        automation_id,
        log_line=f'Staging: removed {s_removed} empty directories',
        log_type='success' if s_removed else 'info',
    )

    # --- 5. Clean search history (if enabled) ---
    deps.update_progress(automation_id, phase='Cleaning search history...', progress=80)
    try:
        if not deps.config_manager.get('soulseek.auto_clear_searches', True):
            steps.append('Search cleanup: disabled in settings')
            deps.update_progress(
                automation_id, log_line='Search cleanup: disabled in settings', log_type='skip',
            )
        else:
            deps.run_async(deps.download_orchestrator.maintain_search_history_with_buffer(
                keep_searches=50, trigger_threshold=200,
            ))
            steps.append('Search history: cleaned')
            deps.update_progress(
                automation_id, log_line='Search history: cleaned', log_type='success',
            )
    except Exception as e:  # noqa: BLE001 — per-step best-effort
        steps.append(f'Search history: error ({e})')
        deps.update_progress(
            automation_id, log_line=f'Search history: error ({e})', log_type='error',
        )

    total_removed = q_removed + s_removed
    deps.update_progress(
        automation_id, status='finished', progress=100,
        phase='Complete',
        log_line=f'Full cleanup complete — {total_removed} items removed',
        log_type='success',
    )
    return {
        'status': 'completed',
        'quarantine_removed': str(q_removed),
        'staging_removed': str(s_removed),
        'total_removed': str(total_removed),
        'steps': steps,
        '_manages_own_progress': True,
    }
