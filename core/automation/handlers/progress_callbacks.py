"""Progress + history callbacks the automation engine invokes around
each handler run.

Lifted from the closures at the bottom of
``web_server._register_automation_handlers``:
- ``_progress_init``               → :func:`progress_init`
- ``_progress_finish``             → :func:`progress_finish`
- ``_record_automation_history``   → :func:`record_history`
- ``_on_library_scan_completed``   → :func:`on_library_scan_completed`

The engine accepts four callables via
``register_progress_callbacks(init, finish, update, history)``;
``registration.register_all`` wires these here. The
``library_scan_completed`` callback is registered separately on the
``web_scan_manager`` (when one is available) -- see
``register_library_scan_completed_emitter``.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def progress_init(aid: Any, name: str, action_type: str, deps: AutomationDeps) -> None:
    """Initialize per-automation progress state when the engine starts
    a handler. Thin wrapper so the engine receives a closure that
    delegates into the live progress tracker."""
    deps.init_automation_progress(aid, name, action_type)


def progress_finish(aid: Any, result: Dict[str, Any], deps: AutomationDeps) -> None:
    """Emit the final progress update when a handler returns.

    Skipped for handlers that manage their own progress lifecycle
    (they call ``update_progress(status='finished')`` themselves and
    set ``_manages_own_progress: True`` in the returned dict).
    Otherwise translates the handler's status into a finished/error
    progress emit with a status-appropriate phase + log line.
    """
    if result.get('_manages_own_progress'):
        return
    result_status = result.get('status', '')
    status = 'error' if result_status == 'error' else 'finished'
    msg = result.get('error', result.get('reason', result_status or 'done'))
    deps.update_progress(
        aid,
        status=status,
        progress=100,
        phase='Error' if status == 'error' else 'Complete',
        log_line=msg,
        log_type='error' if status == 'error' else 'success',
    )


def record_history(aid: Any, result: Dict[str, Any], deps: AutomationDeps) -> None:
    """Capture progress state into run history before the engine's
    cleanup pass clears it. Thin wrapper so the engine sees a stable
    callable."""
    deps.record_progress_history(aid, result, deps.get_database())


def on_library_scan_completed(deps: AutomationDeps) -> None:
    """Emit the ``library_scan_completed`` automation event with the
    active media-server type. Replaces the hard-coded
    ``scan_completion_callback → trigger_automatic_database_update``
    chain so any automation can listen for scan completion as a
    trigger."""
    if not deps.engine:
        return
    server_type = (
        getattr(deps.web_scan_manager, '_current_server_type', None)
        or 'unknown'
    )
    deps.engine.emit('library_scan_completed', {
        'server_type': server_type,
    })


def register_library_scan_completed_emitter(deps: AutomationDeps) -> None:
    """Wire :func:`on_library_scan_completed` to the
    ``web_scan_manager``'s scan-completion callback list. No-op when
    no scan manager is configured (e.g. headless / test contexts)."""
    if not deps.web_scan_manager:
        return
    deps.web_scan_manager.add_scan_completion_callback(
        lambda: on_library_scan_completed(deps),
    )
