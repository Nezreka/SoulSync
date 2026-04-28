"""Automation REST API helpers.

CRUD + run + progress + history logic for /api/automations/* routes.
Each function takes the deps it needs (database, automation_engine,
profile_id) so the route layer is left as pure HTTP shuffling.

Out of scope:
- /api/automations/blocks — static JSON + one call into signals.py;
  stays inline in web_server.py for now.
- /api/test/automation — touches scan manager + media clients +
  config_manager; stays inline.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hydration helpers — convert raw DB rows to API-friendly dicts
# ---------------------------------------------------------------------------

_JSON_FIELDS = ('trigger_config', 'action_config', 'notify_config', 'last_result')
_JSON_DEFAULT_DICT = {'trigger_config', 'action_config', 'notify_config'}


def _hydrate_automation(auto: dict) -> dict:
    """Parse JSON columns and backfill `then_actions` from legacy notify_*."""
    for field in _JSON_FIELDS:
        try:
            auto[field] = json.loads(auto[field]) if isinstance(auto[field], str) else auto[field]
        except (json.JSONDecodeError, TypeError):
            auto[field] = {} if field in _JSON_DEFAULT_DICT else None

    try:
        raw = auto.get('then_actions')
        auto['then_actions'] = json.loads(raw or '[]') if isinstance(raw, str) else (raw or [])
    except (json.JSONDecodeError, TypeError):
        auto['then_actions'] = []

    if not auto['then_actions'] and auto.get('notify_type'):
        auto['then_actions'] = [{
            'type': auto['notify_type'],
            'config': auto.get('notify_config', {}),
        }]
    return auto


# ---------------------------------------------------------------------------
# Signal cycle detection
# ---------------------------------------------------------------------------

def _has_signal_concern(trigger_type: str, then_actions: list[dict]) -> bool:
    return trigger_type == 'signal_received' or any(
        t.get('type') == 'fire_signal' for t in then_actions
    )


def _check_create_cycle(
    automation_engine,
    database,
    profile_id: int,
    trigger_type: str,
    trigger_config_json: str,
    then_actions_json: str,
    then_actions: list[dict],
) -> Optional[str]:
    """Return cycle path string if creating this automation would loop, else None."""
    if not automation_engine or not _has_signal_concern(trigger_type, then_actions):
        return None
    all_autos = database.get_automations(profile_id)
    test_auto = {
        'trigger_type': trigger_type,
        'trigger_config': trigger_config_json,
        'then_actions': then_actions_json,
        'enabled': True,
    }
    all_autos.append(test_auto)
    cycle = automation_engine.detect_signal_cycles(all_autos)
    if cycle:
        return ' → '.join(cycle)
    return None


def _check_update_cycle(
    automation_engine,
    database,
    automation_id: int,
    data: dict,
) -> Optional[str]:
    """Return cycle path string if updating this automation would loop, else None."""
    if not automation_engine:
        return None
    trigger_type = data.get('trigger_type', '')
    then_actions = data.get('then_actions', [])
    if not _has_signal_concern(trigger_type, then_actions):
        return None

    all_autos = database.get_automations()
    test_autos = []
    for a in all_autos:
        if a['id'] == automation_id:
            merged = dict(a)
            if 'trigger_type' in data:
                merged['trigger_type'] = data['trigger_type']
            if 'trigger_config' in data:
                merged['trigger_config'] = json.dumps(data['trigger_config'])
            if 'then_actions' in data:
                merged['then_actions'] = json.dumps(data['then_actions'])
            merged['enabled'] = True
            test_autos.append(merged)
        else:
            test_autos.append(a)
    cycle = automation_engine.detect_signal_cycles(test_autos)
    if cycle:
        return ' → '.join(cycle)
    return None


# ---------------------------------------------------------------------------
# CRUD helpers — return (response_dict, http_status)
# ---------------------------------------------------------------------------

def list_automations(database, profile_id: int) -> list[dict]:
    """All automations for the profile, with JSON columns parsed."""
    automations = database.get_automations(profile_id)
    return [_hydrate_automation(a) for a in automations]


def get_automation(database, automation_id: int) -> Optional[dict]:
    """One automation, hydrated. Returns None if not found."""
    auto = database.get_automation(automation_id)
    if not auto:
        return None
    return _hydrate_automation(auto)


def create_automation(
    database,
    automation_engine,
    profile_id: int,
    data: dict,
) -> tuple[dict, int]:
    """Create + schedule an automation. Returns (response_body, http_status)."""
    name = (data.get('name') or '').strip()
    if not name:
        return {'error': 'Name is required'}, 400

    trigger_type = data.get('trigger_type', 'schedule')
    trigger_config = json.dumps(data.get('trigger_config', {}))
    action_type = data.get('action_type', 'process_wishlist')
    action_config = json.dumps(data.get('action_config', {}))
    then_actions = data.get('then_actions', [])
    then_actions_json = json.dumps(then_actions)

    if then_actions:
        notify_type = then_actions[0].get('type')
        notify_config = json.dumps(then_actions[0].get('config', {}))
    else:
        notify_type = data.get('notify_type') or None
        notify_config = json.dumps(data.get('notify_config', {})) if notify_type else '{}'

    cycle_path = _check_create_cycle(
        automation_engine, database, profile_id,
        trigger_type, trigger_config, then_actions_json, then_actions,
    )
    if cycle_path:
        return {'error': f'Signal cycle detected: {cycle_path}. This would cause an infinite loop.'}, 400

    group_name = data.get('group_name') or None
    auto_id = database.create_automation(
        name, trigger_type, trigger_config, action_type, action_config,
        profile_id, notify_type, notify_config, then_actions_json, group_name,
    )
    if auto_id is None:
        return {'error': 'Failed to create automation'}, 500

    if automation_engine:
        automation_engine.schedule_automation(auto_id)
    return {'success': True, 'id': auto_id}, 200


def update_automation(
    database,
    automation_engine,
    automation_id: int,
    data: dict,
) -> tuple[dict, int]:
    """Update + reschedule an automation. Returns (response_body, http_status)."""
    update_fields: dict[str, Any] = {}
    if 'name' in data:
        update_fields['name'] = data['name'].strip()
    if 'trigger_type' in data:
        update_fields['trigger_type'] = data['trigger_type']
    if 'trigger_config' in data:
        update_fields['trigger_config'] = json.dumps(data['trigger_config'])
    if 'action_type' in data:
        update_fields['action_type'] = data['action_type']
    if 'action_config' in data:
        update_fields['action_config'] = json.dumps(data['action_config'])
    if 'then_actions' in data:
        then_actions = data['then_actions']
        update_fields['then_actions'] = json.dumps(then_actions)
        if then_actions:
            update_fields['notify_type'] = then_actions[0].get('type')
            update_fields['notify_config'] = json.dumps(then_actions[0].get('config', {}))
        else:
            update_fields['notify_type'] = None
            update_fields['notify_config'] = '{}'
    elif 'notify_type' in data:
        update_fields['notify_type'] = data['notify_type'] or None
    if 'notify_config' in data and 'then_actions' not in data:
        update_fields['notify_config'] = json.dumps(data['notify_config'])
    if 'group_name' in data:
        update_fields['group_name'] = data['group_name'] or None

    if not update_fields:
        return {'error': 'No fields to update'}, 400

    cycle_path = _check_update_cycle(automation_engine, database, automation_id, data)
    if cycle_path:
        return {'error': f'Signal cycle detected: {cycle_path}. This would cause an infinite loop.'}, 400

    success = database.update_automation(automation_id, **update_fields)
    if not success:
        return {'error': 'Automation not found'}, 404

    if automation_engine:
        auto = database.get_automation(automation_id)
        if auto and auto.get('enabled'):
            automation_engine.schedule_automation(automation_id)
        else:
            automation_engine.cancel_automation(automation_id)
    return {'success': True}, 200


def batch_update_group(database, automation_ids: list, group_name: Optional[str]) -> tuple[dict, int]:
    """Move/rename a set of automations into a single group (or ungroup)."""
    if not automation_ids or not isinstance(automation_ids, list):
        return {'error': 'automation_ids must be a non-empty list'}, 400
    try:
        automation_ids = [int(aid) for aid in automation_ids]
    except (ValueError, TypeError):
        return {'error': 'automation_ids must contain integers'}, 400

    updated = database.batch_update_group(automation_ids, group_name)
    return {'success': True, 'updated': updated}, 200


def bulk_toggle(
    database,
    automation_engine,
    automation_ids: list,
    enabled: bool,
) -> tuple[dict, int]:
    """Bulk enable/disable a set of automations + reschedule each affected."""
    if not automation_ids or not isinstance(automation_ids, list):
        return {'error': 'automation_ids must be a non-empty list'}, 400
    try:
        automation_ids = [int(aid) for aid in automation_ids]
    except (ValueError, TypeError):
        return {'error': 'automation_ids must contain integers'}, 400

    updated = database.bulk_set_enabled(automation_ids, bool(enabled))

    if automation_engine and updated > 0:
        for aid in automation_ids:
            auto = database.get_automation(aid)
            if auto:
                if auto.get('enabled'):
                    automation_engine.schedule_automation(auto)
                else:
                    automation_engine.cancel_automation(aid)
    return {'success': True, 'updated': updated}, 200


def delete_automation(database, automation_engine, automation_id: int) -> tuple[dict, int]:
    """Delete an automation. System automations are protected."""
    auto = database.get_automation(automation_id)
    if auto and auto.get('is_system'):
        return {'error': 'System automations cannot be deleted'}, 403
    if automation_engine:
        automation_engine.cancel_automation(automation_id)
    success = database.delete_automation(automation_id)
    if not success:
        return {'error': 'Automation not found'}, 404
    return {'success': True}, 200


def duplicate_automation(
    database,
    automation_engine,
    profile_id: int,
    automation_id: int,
) -> tuple[dict, int]:
    """Duplicate an automation. System automations are protected."""
    auto = database.get_automation(automation_id)
    if not auto:
        return {'error': 'Automation not found'}, 404
    if auto.get('is_system'):
        return {'error': 'System automations cannot be duplicated'}, 403
    new_id = database.create_automation(
        name=f"{auto['name']} (Copy)",
        trigger_type=auto['trigger_type'],
        trigger_config=auto.get('trigger_config', '{}'),
        action_type=auto['action_type'],
        action_config=auto.get('action_config', '{}'),
        profile_id=profile_id,
        notify_type=auto.get('notify_type'),
        notify_config=auto.get('notify_config', '{}'),
        then_actions=auto.get('then_actions', '[]'),
        group_name=auto.get('group_name'),
    )
    if new_id is None:
        return {'error': 'Failed to duplicate automation'}, 500
    if automation_engine:
        automation_engine.schedule_automation(new_id)
    return {'success': True, 'id': new_id}, 200


def toggle_automation(database, automation_engine, automation_id: int) -> tuple[dict, int]:
    """Toggle an automation's enabled state + reschedule/cancel."""
    success = database.toggle_automation(automation_id)
    if not success:
        return {'error': 'Automation not found'}, 404

    if automation_engine:
        auto = database.get_automation(automation_id)
        if auto and auto.get('enabled'):
            automation_engine.schedule_automation(automation_id)
        else:
            automation_engine.cancel_automation(automation_id)
    return {'success': True}, 200


def run_automation(automation_engine, automation_id: int, profile_id: int) -> tuple[dict, int]:
    """Manually trigger an automation."""
    if not automation_engine:
        return {'error': 'Automation engine not available'}, 500
    success = automation_engine.run_now(automation_id, profile_id=profile_id)
    if not success:
        return {'error': 'Automation not found'}, 404
    return {'success': True}, 200


def get_history(database, automation_id: int, *, limit: int, offset: int) -> dict:
    """Run-history page for an automation, with log_lines/result_json parsed."""
    data = database.get_automation_run_history(automation_id, limit=limit, offset=offset)
    for entry in data.get('history', []):
        if entry.get('log_lines'):
            try:
                entry['log_lines'] = json.loads(entry['log_lines'])
            except (json.JSONDecodeError, TypeError):
                entry['log_lines'] = []
        else:
            entry['log_lines'] = []
        if entry.get('result_json'):
            try:
                entry['result_json'] = json.loads(entry['result_json'])
            except (json.JSONDecodeError, TypeError):
                pass
    data['automation_id'] = automation_id
    return data
