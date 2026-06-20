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
    'mirrored_playlist_select', 'personalized_playlist_select',
    'signal_input', 'script_select',
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


# ── scope filtering (music vs the isolated video builder) ────────────────

def test_video_only_block_hidden_from_music_builder():
    """The video action must never appear on the music builder."""
    music = blocks.blocks_for_scope('music')
    assert 'video_scan_library' not in {a['type'] for a in music['actions']}


def test_video_builder_gets_video_block_plus_generics():
    video = blocks.blocks_for_scope('video')
    action_types = {a['type'] for a in video['actions']}
    # its own action…
    assert 'video_scan_library' in action_types
    # …plus the generic (scope='both') ones it shares with music…
    assert 'notify_only' in action_types
    assert 'run_script' in action_types
    # …but NOT music-only actions.
    assert 'process_wishlist' not in action_types
    assert 'scan_library' not in action_types


def test_generic_blocks_appear_on_both_sides():
    """Every scope='both' block shows on music AND video."""
    music = blocks.blocks_for_scope('music')
    video = blocks.blocks_for_scope('video')
    for key in ('triggers', 'actions', 'notifications'):
        both = {b['type'] for b in getattr(blocks, key.upper()) if b.get('scope') == 'both'}
        assert both, f"expected at least one scope='both' {key}"
        assert both <= {b['type'] for b in music[key]}, f"music missing a 'both' {key}"
        assert both <= {b['type'] for b in video[key]}, f"video missing a 'both' {key}"


def test_music_scope_matches_legacy_full_lists_minus_video():
    """scope='music' must reproduce the pre-scope behaviour: everything that
    isn't explicitly video-only. Guards against accidentally hiding a music
    block when new scope tags are added."""
    music = blocks.blocks_for_scope('music')
    for key in ('triggers', 'actions', 'notifications'):
        expected = {b['type'] for b in getattr(blocks, key.upper()) if b.get('scope') != 'video'}
        assert {b['type'] for b in music[key]} == expected


def test_video_scan_library_block_shape():
    action = next(a for a in blocks.ACTIONS if a['type'] == 'video_scan_library')
    assert action['scope'] == 'video'
    mode = next(f for f in action['config_fields'] if f['key'] == 'mode')
    assert {o['value'] for o in mode['options']} == {'full', 'incremental', 'deep'}
    assert mode['default'] == 'full'
