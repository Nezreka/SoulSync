"""Tests for core/automation/blocks.py — static block definitions for the builder UI.

Catches accidental schema regressions in the builder block list (missing
`type`/`label`, malformed config_fields options, etc.).
"""

from __future__ import annotations

from core.automation import blocks


def _shape_check(items, allowed_types):
    """Every item has type+label+description, plus type-specific shape rules."""
    seen_types = set()
    for item in items:
        assert 'type' in item, item
        assert 'label' in item, item
        assert isinstance(item.get('available'), bool), item
        # No duplicate types within a list
        assert item['type'] not in seen_types, f"Duplicate type {item['type']!r}"
        seen_types.add(item['type'])

        if 'config_fields' in item:
            for field in item['config_fields']:
                assert 'key' in field
                assert 'type' in field
                assert field['type'] in allowed_types, f"Unknown field type {field['type']!r} in {item['type']}"
                if field['type'] == 'select':
                    assert 'options' in field
                    for opt in field['options']:
                        assert 'value' in opt
                        assert 'label' in opt


_FIELD_TYPES = {
    'number', 'select', 'time', 'multi_select', 'checkbox', 'text',
    'mirrored_playlist_select', 'signal_input', 'script_select',
}


def test_triggers_shape():
    _shape_check(blocks.TRIGGERS, _FIELD_TYPES)


def test_actions_shape():
    _shape_check(blocks.ACTIONS, _FIELD_TYPES)


def test_notifications_shape():
    _shape_check(blocks.NOTIFICATIONS, _FIELD_TYPES)


def test_signal_received_trigger_present():
    types = {t['type'] for t in blocks.TRIGGERS}
    assert 'signal_received' in types


def test_fire_signal_notification_present():
    types = {n['type'] for n in blocks.NOTIFICATIONS}
    assert 'fire_signal' in types


def test_run_script_in_both_actions_and_notifications():
    """run_script can be either an action or a then-action — both lists own it."""
    action_types = {a['type'] for a in blocks.ACTIONS}
    notif_types = {n['type'] for n in blocks.NOTIFICATIONS}
    assert 'run_script' in action_types
    assert 'run_script' in notif_types


def test_schedule_trigger_default_unit_is_hours():
    schedule = next(t for t in blocks.TRIGGERS if t['type'] == 'schedule')
    unit_field = next(f for f in schedule['config_fields'] if f['key'] == 'unit')
    assert unit_field['default'] == 'hours'


def test_event_triggers_with_conditions_have_condition_fields():
    for t in blocks.TRIGGERS:
        if t.get('has_conditions'):
            assert 'condition_fields' in t, f"{t['type']} marked has_conditions but no condition_fields"
            assert isinstance(t['condition_fields'], list)
            assert len(t['condition_fields']) > 0
