"""Automation handler: ``search_and_download`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_search_and_download`` closure). Searches for a track by
name/artist string and dispatches the best match through the
download orchestrator. Query can come from the trigger config
(direct value) or from event data (e.g. webhook payload).
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_search_and_download(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    automation_id = config.get('_automation_id')
    query = config.get('query', '').strip()
    # Event-triggered: pull query from event data (e.g. webhook_received).
    if not query:
        event_data = config.get('_event_data', {})
        query = (event_data.get('query', '') or '').strip()
    if not query:
        if automation_id:
            deps.update_progress(
                automation_id, log_line='No search query provided', log_type='error',
            )
        return {'status': 'error', 'error': 'No search query provided'}
    try:
        if automation_id:
            deps.update_progress(
                automation_id, phase='Searching',
                log_line=f'Searching: {query}', log_type='info',
            )
        result = deps.run_async(deps.download_orchestrator.search_and_download_best(query))
        if result:
            if automation_id:
                deps.update_progress(
                    automation_id,
                    log_line=f'Download started for: {query}',
                    log_type='success',
                )
            return {'status': 'completed', 'query': query, 'download_id': result}
        if automation_id:
            deps.update_progress(
                automation_id,
                log_line=f'No match found for: {query}',
                log_type='warning',
            )
        return {'status': 'not_found', 'query': query, 'error': 'No match found'}
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise
        if automation_id:
            deps.update_progress(
                automation_id, log_line=f'Error: {e}', log_type='error',
            )
        return {'status': 'error', 'query': query, 'error': str(e)}
