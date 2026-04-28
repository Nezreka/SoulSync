"""Tests for core/automation/api.py — CRUD + run + history helpers."""

from __future__ import annotations

import json

from core.automation import api


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeDB:
    def __init__(self):
        self._next_id = 1
        self.automations: dict[int, dict] = {}
        self.history: dict[int, list] = {}
        self.batch_group_calls = []
        self.bulk_set_calls = []

    def get_automations(self, profile_id=None):
        if profile_id is None:
            return list(self.automations.values())
        return [a for a in self.automations.values() if a.get('profile_id') == profile_id]

    def get_automation(self, automation_id):
        return dict(self.automations[automation_id]) if automation_id in self.automations else None

    def create_automation(self, name, trigger_type, trigger_config, action_type, action_config,
                          profile_id, notify_type, notify_config, then_actions, group_name):
        aid = self._next_id
        self._next_id += 1
        self.automations[aid] = {
            'id': aid, 'name': name, 'trigger_type': trigger_type,
            'trigger_config': trigger_config, 'action_type': action_type,
            'action_config': action_config, 'profile_id': profile_id,
            'notify_type': notify_type, 'notify_config': notify_config,
            'then_actions': then_actions, 'group_name': group_name,
            'enabled': 1, 'is_system': 0,
        }
        return aid

    def update_automation(self, automation_id, **fields):
        if automation_id not in self.automations:
            return False
        self.automations[automation_id].update(fields)
        return True

    def delete_automation(self, automation_id):
        if automation_id not in self.automations:
            return False
        del self.automations[automation_id]
        return True

    def toggle_automation(self, automation_id):
        if automation_id not in self.automations:
            return False
        a = self.automations[automation_id]
        a['enabled'] = 0 if a['enabled'] else 1
        return True

    def batch_update_group(self, ids, group_name):
        self.batch_group_calls.append((ids, group_name))
        return len(ids)

    def bulk_set_enabled(self, ids, enabled):
        self.bulk_set_calls.append((ids, enabled))
        for aid in ids:
            if aid in self.automations:
                self.automations[aid]['enabled'] = 1 if enabled else 0
        return len(ids)

    def get_automation_run_history(self, automation_id, limit=50, offset=0):
        return {'history': self.history.get(automation_id, [])[offset:offset + limit]}


class _FakeEngine:
    def __init__(self, cycles_to_return=None):
        self.scheduled = []
        self.cancelled = []
        self.run_now_calls = []
        self._cycles = cycles_to_return or []

    def schedule_automation(self, aid):
        self.scheduled.append(aid)

    def cancel_automation(self, aid):
        self.cancelled.append(aid)

    def detect_signal_cycles(self, autos):
        return list(self._cycles)

    def run_now(self, aid, profile_id=None):
        self.run_now_calls.append((aid, profile_id))
        return True


# ---------------------------------------------------------------------------
# _hydrate_automation
# ---------------------------------------------------------------------------

def test_hydrate_parses_json_columns():
    raw = {
        'trigger_config': '{"interval": 6}',
        'action_config': '{"category": "all"}',
        'notify_config': '{"webhook": "x"}',
        'last_result': '{"ok": true}',
        'then_actions': '[{"type": "discord", "config": {"webhook": "y"}}]',
        'notify_type': None,
    }
    out = api._hydrate_automation(dict(raw))
    assert out['trigger_config'] == {'interval': 6}
    assert out['action_config'] == {'category': 'all'}
    assert out['then_actions'][0]['type'] == 'discord'


def test_hydrate_invalid_json_falls_back_to_default():
    raw = {
        'trigger_config': 'not json',
        'action_config': 'not json',
        'notify_config': 'not json',
        'last_result': 'not json',
        'then_actions': 'not json',
        'notify_type': None,
    }
    out = api._hydrate_automation(dict(raw))
    assert out['trigger_config'] == {}
    assert out['action_config'] == {}
    assert out['notify_config'] == {}
    assert out['last_result'] is None
    assert out['then_actions'] == []


def test_hydrate_backfills_then_actions_from_legacy_notify_type():
    raw = {
        'trigger_config': '{}',
        'action_config': '{}',
        'notify_config': {'webhook_url': 'http://x'},
        'last_result': None,
        'then_actions': '[]',
        'notify_type': 'discord',
    }
    out = api._hydrate_automation(dict(raw))
    assert out['then_actions'] == [{'type': 'discord', 'config': {'webhook_url': 'http://x'}}]


# ---------------------------------------------------------------------------
# list_automations
# ---------------------------------------------------------------------------

def test_list_automations_filters_by_profile():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'profile_id': 1, 'trigger_config': '{}', 'action_config': '{}',
                          'notify_config': '{}', 'last_result': None, 'then_actions': '[]', 'notify_type': None}
    db.automations[2] = {'id': 2, 'profile_id': 2, 'trigger_config': '{}', 'action_config': '{}',
                          'notify_config': '{}', 'last_result': None, 'then_actions': '[]', 'notify_type': None}
    out = api.list_automations(db, profile_id=1)
    assert len(out) == 1
    assert out[0]['id'] == 1


# ---------------------------------------------------------------------------
# get_automation
# ---------------------------------------------------------------------------

def test_get_automation_returns_none_for_missing():
    assert api.get_automation(_FakeDB(), 99) is None


def test_get_automation_returns_hydrated():
    db = _FakeDB()
    db.automations[5] = {'id': 5, 'trigger_config': '{"x":1}', 'action_config': '{}',
                          'notify_config': '{}', 'last_result': None, 'then_actions': '[]',
                          'notify_type': None}
    out = api.get_automation(db, 5)
    assert out['trigger_config'] == {'x': 1}


# ---------------------------------------------------------------------------
# create_automation
# ---------------------------------------------------------------------------

def test_create_requires_name():
    body, status = api.create_automation(_FakeDB(), _FakeEngine(), profile_id=1, data={'name': '   '})
    assert status == 400
    assert 'Name is required' in body['error']


def test_create_happy_path_schedules():
    db = _FakeDB()
    eng = _FakeEngine()
    body, status = api.create_automation(db, eng, profile_id=1, data={
        'name': 'My Auto', 'trigger_type': 'schedule',
        'trigger_config': {'interval': 6, 'unit': 'hours'},
        'action_type': 'process_wishlist',
    })
    assert status == 200
    assert body['success'] is True
    assert body['id'] == 1
    assert eng.scheduled == [1]


def test_create_blocks_signal_cycle():
    eng = _FakeEngine(cycles_to_return=['sig_a', 'sig_b', 'sig_a'])
    body, status = api.create_automation(_FakeDB(), eng, profile_id=1, data={
        'name': 'Loopy',
        'trigger_type': 'signal_received',
        'trigger_config': {'signal_name': 'sig_a'},
        'then_actions': [{'type': 'fire_signal', 'config': {'signal_name': 'sig_b'}}],
    })
    assert status == 400
    assert 'Signal cycle detected' in body['error']
    assert eng.scheduled == []


def test_create_skips_cycle_check_when_no_signals():
    eng = _FakeEngine(cycles_to_return=['shouldnt fire'])
    body, status = api.create_automation(_FakeDB(), eng, profile_id=1, data={
        'name': 'Plain', 'trigger_type': 'schedule',
        'action_type': 'process_wishlist',
        'then_actions': [{'type': 'discord', 'config': {}}],
    })
    assert status == 200


def test_create_then_actions_back_compat_first_item_becomes_notify_type():
    db = _FakeDB()
    api.create_automation(db, _FakeEngine(), profile_id=1, data={
        'name': 'X', 'trigger_type': 'schedule', 'action_type': 'process_wishlist',
        'then_actions': [{'type': 'discord', 'config': {'webhook': 'http://x'}}],
    })
    stored = db.automations[1]
    assert stored['notify_type'] == 'discord'
    assert json.loads(stored['notify_config']) == {'webhook': 'http://x'}


# ---------------------------------------------------------------------------
# update_automation
# ---------------------------------------------------------------------------

def test_update_with_no_fields_returns_400():
    body, status = api.update_automation(_FakeDB(), _FakeEngine(), automation_id=1, data={})
    assert status == 400


def test_update_missing_id_returns_404():
    body, status = api.update_automation(_FakeDB(), _FakeEngine(), automation_id=99, data={'name': 'x'})
    assert status == 404


def test_update_blocks_signal_cycle():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'name': 'a', 'trigger_type': 'schedule', 'trigger_config': '{}',
                          'then_actions': '[]', 'enabled': 1, 'is_system': 0}
    eng = _FakeEngine(cycles_to_return=['sig_a', 'sig_a'])
    body, status = api.update_automation(db, eng, automation_id=1, data={
        'trigger_type': 'signal_received',
        'trigger_config': {'signal_name': 'sig_a'},
        'then_actions': [{'type': 'fire_signal', 'config': {'signal_name': 'sig_a'}}],
    })
    assert status == 400
    assert 'Signal cycle detected' in body['error']


def test_update_reschedules_when_enabled():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'name': 'a', 'enabled': 1}
    eng = _FakeEngine()
    body, status = api.update_automation(db, eng, automation_id=1, data={'name': 'renamed'})
    assert status == 200
    assert eng.scheduled == [1]


def test_update_cancels_when_disabled():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'name': 'a', 'enabled': 0}
    eng = _FakeEngine()
    body, status = api.update_automation(db, eng, automation_id=1, data={'name': 'r'})
    assert status == 200
    assert eng.cancelled == [1]


def test_update_then_actions_clears_notify_when_empty():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'name': 'a', 'enabled': 0}
    api.update_automation(db, _FakeEngine(), automation_id=1, data={'then_actions': []})
    assert db.automations[1]['notify_type'] is None
    assert db.automations[1]['notify_config'] == '{}'


# ---------------------------------------------------------------------------
# batch_update_group
# ---------------------------------------------------------------------------

def test_batch_group_requires_list():
    body, status = api.batch_update_group(_FakeDB(), [], 'group')
    assert status == 400


def test_batch_group_rejects_non_int_ids():
    body, status = api.batch_update_group(_FakeDB(), ['abc'], 'g')
    assert status == 400


def test_batch_group_happy_path():
    db = _FakeDB()
    body, status = api.batch_update_group(db, [1, 2, 3], 'mygroup')
    assert status == 200
    assert body['updated'] == 3
    assert db.batch_group_calls == [([1, 2, 3], 'mygroup')]


def test_batch_group_can_ungroup_with_none():
    db = _FakeDB()
    body, status = api.batch_update_group(db, [1], None)
    assert status == 200
    assert db.batch_group_calls == [([1], None)]


# ---------------------------------------------------------------------------
# bulk_toggle
# ---------------------------------------------------------------------------

def test_bulk_toggle_reschedules_enabled():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'enabled': 1}
    db.automations[2] = {'id': 2, 'enabled': 1}
    eng = _FakeEngine()
    body, status = api.bulk_toggle(db, eng, [1, 2], enabled=True)
    assert status == 200
    assert body['updated'] == 2


def test_bulk_toggle_cancels_disabled():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'enabled': 1}
    eng = _FakeEngine()
    api.bulk_toggle(db, eng, [1], enabled=False)
    assert eng.cancelled == [1]


def test_bulk_toggle_requires_non_empty_list():
    body, status = api.bulk_toggle(_FakeDB(), _FakeEngine(), [], True)
    assert status == 400


# ---------------------------------------------------------------------------
# delete_automation
# ---------------------------------------------------------------------------

def test_delete_protects_system_automations():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'is_system': 1}
    body, status = api.delete_automation(db, _FakeEngine(), 1)
    assert status == 403


def test_delete_cancels_engine_then_db():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'is_system': 0}
    eng = _FakeEngine()
    body, status = api.delete_automation(db, eng, 1)
    assert status == 200
    assert eng.cancelled == [1]
    assert 1 not in db.automations


def test_delete_missing_returns_404():
    body, status = api.delete_automation(_FakeDB(), _FakeEngine(), 99)
    assert status == 404


# ---------------------------------------------------------------------------
# duplicate_automation
# ---------------------------------------------------------------------------

def test_duplicate_appends_copy_suffix_and_schedules():
    db = _FakeDB()
    db.automations[1] = {
        'id': 1, 'name': 'Orig', 'trigger_type': 'schedule', 'trigger_config': '{}',
        'action_type': 'process_wishlist', 'action_config': '{}', 'is_system': 0,
        'notify_type': None, 'notify_config': '{}', 'then_actions': '[]', 'group_name': None,
    }
    db._next_id = 2  # bump so create_automation doesn't overwrite id 1
    eng = _FakeEngine()
    body, status = api.duplicate_automation(db, eng, profile_id=1, automation_id=1)
    assert status == 200
    assert db.automations[2]['name'] == 'Orig (Copy)'
    assert eng.scheduled == [2]


def test_duplicate_protects_system():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'is_system': 1, 'name': 'sys'}
    body, status = api.duplicate_automation(db, _FakeEngine(), profile_id=1, automation_id=1)
    assert status == 403


def test_duplicate_missing_returns_404():
    body, status = api.duplicate_automation(_FakeDB(), _FakeEngine(), profile_id=1, automation_id=99)
    assert status == 404


# ---------------------------------------------------------------------------
# toggle_automation
# ---------------------------------------------------------------------------

def test_toggle_reschedules_when_now_enabled():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'enabled': 0}  # currently off
    eng = _FakeEngine()
    body, status = api.toggle_automation(db, eng, 1)
    assert status == 200
    assert eng.scheduled == [1]


def test_toggle_cancels_when_now_disabled():
    db = _FakeDB()
    db.automations[1] = {'id': 1, 'enabled': 1}  # currently on
    eng = _FakeEngine()
    api.toggle_automation(db, eng, 1)
    assert eng.cancelled == [1]


def test_toggle_missing_returns_404():
    body, status = api.toggle_automation(_FakeDB(), _FakeEngine(), 99)
    assert status == 404


# ---------------------------------------------------------------------------
# run_automation
# ---------------------------------------------------------------------------

def test_run_calls_engine_run_now_with_profile():
    eng = _FakeEngine()
    body, status = api.run_automation(eng, automation_id=5, profile_id=2)
    assert status == 200
    assert eng.run_now_calls == [(5, 2)]


def test_run_no_engine_returns_500():
    body, status = api.run_automation(None, 1, 1)
    assert status == 500


def test_run_missing_automation_returns_404():
    class _MissEngine(_FakeEngine):
        def run_now(self, aid, profile_id=None):
            return False
    body, status = api.run_automation(_MissEngine(), 1, 1)
    assert status == 404


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------

def test_history_parses_log_lines_and_result_json():
    db = _FakeDB()
    db.history[1] = [
        {'id': 1, 'log_lines': '[{"text":"ok"}]', 'result_json': '{"k":"v"}'},
        {'id': 2, 'log_lines': '', 'result_json': None},
    ]
    out = api.get_history(db, 1, limit=10, offset=0)
    assert out['automation_id'] == 1
    assert out['history'][0]['log_lines'] == [{'text': 'ok'}]
    assert out['history'][0]['result_json'] == {'k': 'v'}
    assert out['history'][1]['log_lines'] == []


def test_history_invalid_json_falls_back():
    db = _FakeDB()
    db.history[1] = [{'id': 1, 'log_lines': 'not json', 'result_json': 'not json'}]
    out = api.get_history(db, 1, limit=10, offset=0)
    assert out['history'][0]['log_lines'] == []
    # result_json stays as the original string when not parseable
    assert out['history'][0]['result_json'] == 'not json'
