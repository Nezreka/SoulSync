"""Automation handler: ``process_wishlist`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_process_wishlist`` closure). Wishlist processing is async —
the helper submits a batch to an executor and returns immediately;
per-track stats arrive later via batch-completion callbacks.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_process_wishlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Kick off the wishlist processor for an automation trigger.

    Returns immediately after submission; the wishlist worker emits
    per-batch progress via its own callbacks. We only report
    ``status: completed`` to mark the trigger fired successfully.
    """
    try:
        deps.process_wishlist_automatically(automation_id=config.get('_automation_id'))
        return {'status': 'completed'}
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise into the engine
        return {'status': 'error', 'error': str(e)}
