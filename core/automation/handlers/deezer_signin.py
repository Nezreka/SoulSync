"""Automation handler: ``check_deezer_signin``.

Asks Deezer whether the configured ARL still signs in. A dead ARL is
otherwise silent until a download needs Deezer, and then that download just
falls back to the next source. The client sends the ``deezer_signin_failed``
and ``deezer_signin_restored`` events itself; this only makes it look.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_check_deezer_signin(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')
    orchestrator = deps.download_orchestrator
    client = orchestrator.client('deezer_dl') if orchestrator else None
    if client is None or not hasattr(client, 'check_signin'):
        deps.update_progress(automation_id, log_line='Deezer downloads are not set up', log_type='info')
        return {'status': 'skipped', 'signin': 'not set up'}
    result = client.check_signin()
    state = result.get('status', 'unknown')
    lines = {
        'ok': ('Deezer accepts the ARL', 'success'),
        'failed': ('Deezer rejected the ARL', 'error'),
        'unreachable': ("Couldn't reach Deezer; the ARL wasn't checked", 'warning'),
        'skipped': ('No Deezer ARL set', 'info'),
    }
    line, kind = lines.get(state, (f'Deezer sign-in: {state}', 'info'))
    deps.update_progress(automation_id, log_line=line, log_type=kind)
    return {'status': 'skipped' if state == 'skipped' else 'completed', 'signin': state}
