"""Automation progress tracking.

Owns the in-memory progress state dict that backs both
`/api/automations/progress` polling and the WebSocket
`automation:progress` push emitter. State is per-automation, capped at
50 log entries each, and finished/error states are reaped 60s after
they finish so the frontend has a window to show the final state.

Functions are written so the route layer / engine callbacks can pass
their own socketio emitter, db handle, and shutdown flag. The progress
state dict (`progress_states`) and its lock (`progress_lock`) are
module-level so all callers share one view — same as the original
web_server.py globals.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Shared mutable state — module globals so every caller (routes, engine
# progress callbacks, emit loop) sees the same dict. Mirrors the original
# `automation_progress_states` / `automation_progress_lock` in web_server.
progress_states: dict[int, dict] = {}
progress_lock = threading.Lock()


def init_progress(automation_id: int, automation_name: str, action_type: str) -> None:
    """Initialize progress state when an automation starts running."""
    with progress_lock:
        progress_states[automation_id] = {
            'status': 'running',
            'action_type': action_type,
            'progress': 0,
            'phase': 'Starting...',
            'current_item': '',
            'processed': 0,
            'total': 0,
            'log': [{'type': 'info', 'text': f'Starting {automation_name}'}],
            'started_at': datetime.now(timezone.utc).isoformat(),
            'finished_at': None,
        }


def update_progress(
    automation_id: Optional[int],
    *,
    socketio_emit: Optional[Callable[[str, Any], None]] = None,
    **kwargs,
) -> None:
    """Update progress state from handler threads. Thread-safe.

    `socketio_emit` lets callers wire in the live socketio.emit so that
    finished/error transitions push immediately without waiting for the
    1s emitter loop. Falls back to no-op if not provided.
    """
    if automation_id is None:
        return
    with progress_lock:
        state = progress_states.get(automation_id)
        if not state:
            return
        for k, v in kwargs.items():
            if k == 'log_line':
                state['log'].append({'type': kwargs.get('log_type', 'info'), 'text': v})
                if len(state['log']) > 50:
                    state['log'] = state['log'][-50:]
            elif k != 'log_type':
                state[k] = v
        if kwargs.get('status') in ('finished', 'error'):
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            if socketio_emit is not None:
                try:
                    socketio_emit('automation:progress', {str(automation_id): dict(state)})
                except Exception:
                    pass


def get_running_progress() -> dict[str, dict]:
    """Snapshot of running/finished/error states for the polling endpoint."""
    with progress_lock:
        result: dict[str, dict] = {}
        for aid, state in progress_states.items():
            if state['status'] in ('running', 'finished', 'error'):
                cp = dict(state)
                cp['log'] = list(state['log'])
                result[str(aid)] = cp
    return result


def record_history(
    automation_id: int,
    result: dict,
    database,
) -> None:
    """Capture progress state into run history before cleanup clears it.

    `database` is passed in so the function works without a `get_database()`
    global.
    """
    try:
        with progress_lock:
            state = progress_states.get(automation_id)
            if state:
                started_at = state.get('started_at')
                finished_at = state.get('finished_at') or datetime.now(timezone.utc).isoformat()
                log_entries = list(state.get('log', []))
            else:
                started_at = datetime.now(timezone.utc).isoformat()
                finished_at = datetime.now(timezone.utc).isoformat()
                log_entries = []

        duration = None
        if started_at and finished_at:
            try:
                t0 = datetime.fromisoformat(started_at)
                t1 = datetime.fromisoformat(finished_at)
                duration = (t1 - t0).total_seconds()
            except Exception:
                pass

        r_status = result.get('status', 'completed') if result else 'completed'
        if r_status == 'error':
            status = 'error'
        elif r_status == 'skipped':
            status = 'skipped'
        elif r_status == 'timeout':
            status = 'timeout'
        else:
            status = 'completed'

        summary = None
        for entry in reversed(log_entries):
            if entry.get('type') in ('success', 'error'):
                summary = entry.get('text', '')
                break
        if not summary and log_entries:
            summary = log_entries[-1].get('text', '')
        if not summary and result:
            summary = result.get('reason') or result.get('error') or result.get('status', '')

        result_json = json.dumps({k: v for k, v in result.items() if not k.startswith('_')}) if result else None
        log_json = json.dumps(log_entries) if log_entries else None

        database.insert_automation_run_history(
            automation_id=automation_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            status=status,
            summary=summary,
            result_json=result_json,
            log_lines=log_json,
        )
    except Exception as e:
        logger.error(f"Error recording automation history for {automation_id}: {e}")


def emit_progress_loop(
    socketio,
    *,
    is_shutting_down: Callable[[], bool],
    poll_interval: float = 1.0,
    timeout_seconds: int = 7200,
    cleanup_after_seconds: int = 60,
) -> None:
    """Push `automation:progress` events for active automations.

    Long-running loop — caller wires this into a socketio background task.
    - Times out zombie running states after `timeout_seconds` (default 2h).
    - Reaps finished/error states `cleanup_after_seconds` after finish so the
      frontend has a final-state window before they disappear.
    """
    while not is_shutting_down():
        socketio.sleep(poll_interval)
        try:
            with progress_lock:
                active: dict[str, dict] = {}
                stale: list[int] = []
                now = datetime.now()
                for aid, state in progress_states.items():
                    if state['status'] == 'running':
                        try:
                            started = datetime.fromisoformat(state.get('started_at', ''))
                            if (now - started).total_seconds() > timeout_seconds:
                                state['status'] = 'error'
                                state['phase'] = 'Timed out'
                                state['finished_at'] = now.isoformat()
                                state['log'].append({'type': 'error', 'text': f'Timed out after {timeout_seconds // 3600} hours'})
                                cp = dict(state)
                                cp['log'] = list(state['log'])
                                active[str(aid)] = cp
                                continue
                        except (ValueError, TypeError):
                            pass
                        cp = dict(state)
                        cp['log'] = list(state['log'])
                        active[str(aid)] = cp
                    elif state['status'] in ('finished', 'error') and state.get('finished_at'):
                        try:
                            finished_time = datetime.fromisoformat(state['finished_at'])
                            if (now - finished_time).total_seconds() > cleanup_after_seconds:
                                stale.append(aid)
                        except (ValueError, TypeError):
                            stale.append(aid)
                for aid in stale:
                    del progress_states[aid]
            if active:
                socketio.emit('automation:progress', active)
        except Exception as e:
            logger.debug(f"Error emitting automation progress: {e}")
