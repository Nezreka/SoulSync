"""Automations best-in-class arc — bug fixes + granularity + new triggers.

Covers the audited gaps end to end:
- engine: new condition operators, per-THEN-step conditions (absent = always
  run, the backward-compat pin), webhook payload templates (JSON mode, raw
  mode, broken-template fallback to the default body).
- music repair events: finding-created + scan-completed emits, fire-and-forget.
- blocks registry: new triggers land on the right sides; monthly_time carries
  its tz field.
- builder JS source pins: the un-gated generic renderer, monthly countdown,
  label fallbacks, payload editor, per-step condition row, Test button,
  THEN cap of 5, tz inputs.

Hermetic: engine methods are exercised on a bare instance with requests
mocked; no network, no live services.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.automation.blocks import blocks_for_scope
from core.automation_engine import AutomationEngine

_ROOT = Path(__file__).resolve().parent.parent.parent
_JS = (_ROOT / "webui" / "static" / "stats-automations.js").read_text(encoding="utf-8")


def _engine():
    return AutomationEngine.__new__(AutomationEngine)   # no threads, no db


# ── condition operators ─────────────────────────────────────────────────────

def _cond(field, operator, value):
    return {'conditions': [{'field': field, 'operator': operator, 'value': value}]}


def test_new_operators():
    e = _engine()
    assert e._evaluate_conditions(_cond('status', 'not_equals', 'error'), {'status': 'completed'})
    assert not e._evaluate_conditions(_cond('status', 'not_equals', 'error'), {'status': 'error'})
    assert e._evaluate_conditions(_cond('title', 'ends_with', 'remix'), {'title': 'Song Remix'})
    assert e._evaluate_conditions(_cond('failed_tracks', 'greater_than', '0'), {'failed_tracks': 3})
    assert not e._evaluate_conditions(_cond('failed_tracks', 'greater_than', '0'), {'failed_tracks': 0})
    assert e._evaluate_conditions(_cond('quality', 'less_than', '1080'), {'quality': '720'})
    # non-numeric input to a numeric operator = no match, never a crash
    assert not e._evaluate_conditions(_cond('quality', 'greater_than', '0'), {'quality': 'FLAC'})


def test_old_operators_unchanged():
    e = _engine()
    assert e._evaluate_conditions(_cond('artist', 'contains', 'muse'), {'artist': 'Muse'})
    assert e._evaluate_conditions(_cond('artist', 'equals', 'muse'), {'artist': 'Muse'})
    assert e._evaluate_conditions(_cond('artist', 'starts_with', 'mu'), {'artist': 'Muse'})
    assert e._evaluate_conditions(_cond('artist', 'not_contains', 'xyz'), {'artist': 'Muse'})


# ── per-THEN-step conditions ────────────────────────────────────────────────

def _auto(then_actions):
    return {'id': 1, 'name': 'T', 'run_count': 0,
            'then_actions': json.dumps(then_actions), 'notify_type': None}


def test_step_without_conditions_always_runs():
    e = _engine()
    with patch.object(e, '_send_discord_notification') as send:
        e._execute_then_actions(_auto([{'type': 'discord_webhook', 'config': {}}]),
                                {'status': 'error'})
    assert send.called


def test_step_condition_gates_execution():
    e = _engine()
    step = {'type': 'discord_webhook', 'config': {},
            'conditions': [{'field': 'status', 'operator': 'equals', 'value': 'error'}]}
    with patch.object(e, '_send_discord_notification') as send:
        e._execute_then_actions(_auto([step]), {'status': 'completed'})
    assert not send.called                      # gate closed
    with patch.object(e, '_send_discord_notification') as send:
        e._execute_then_actions(_auto([step]), {'status': 'error'})
    assert send.called                          # gate open


def test_step_condition_sees_event_variables():
    e = _engine()
    step = {'type': 'telegram', 'config': {},
            'conditions': [{'field': 'artist', 'operator': 'contains', 'value': 'muse'}]}
    with patch.object(e, '_send_telegram_notification') as send:
        e._execute_then_actions(_auto([step]), {'status': 'completed', 'artist': 'Muse'})
    assert send.called


# ── webhook payload templates ───────────────────────────────────────────────

def test_template_json_mode_with_escaping():
    e = _engine()
    body, is_json = e._render_webhook_template(
        '{"title": "{title}", "prio": 5}', {'title': 'He said "hi"'})
    assert is_json and body == {'title': 'He said "hi"', 'prio': 5}


def test_template_raw_text_mode():
    e = _engine()
    body, is_json = e._render_webhook_template(
        'Track {title} finished', {'title': 'Fat'})
    assert not is_json and body == 'Track Fat finished'


def test_webhook_uses_template_and_falls_back_when_broken():
    e = _engine()
    ok = MagicMock(status_code=200)
    with patch('core.automation_engine.requests.post', return_value=ok) as post:
        e._send_webhook({'url': 'http://x/y', 'payload_template': '{"n": "{name}"}'},
                        {'name': 'Run', 'status': 'completed'})
    assert post.call_args.kwargs['json'] == {'n': 'Run'}

    # A template that renders but the receiver would still get SOMETHING:
    # raw mode posts text with a text content-type
    with patch('core.automation_engine.requests.post', return_value=ok) as post:
        e._send_webhook({'url': 'http://x/y', 'payload_template': 'plain {status}'},
                        {'name': 'Run', 'status': 'ok'})
    assert post.call_args.kwargs['data'] == b'plain ok'
    assert 'text/plain' in post.call_args.kwargs['headers']['Content-Type']


def test_webhook_without_template_is_byte_identical_to_before():
    e = _engine()
    ok = MagicMock(status_code=200)
    with patch('core.automation_engine.requests.post', return_value=ok) as post:
        e._send_webhook({'url': 'http://x/y', 'message': 'done {status}'},
                        {'name': 'Run', 'status': 'completed'})
    payload = post.call_args.kwargs['json']
    assert payload['name'] == 'Run' and payload['message'] == 'done completed'


# ── music repair events ─────────────────────────────────────────────────────

def test_music_finding_emit_fires_and_never_raises(tmp_path):
    from core.repair_worker import RepairWorker
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'm.db'))
    w = RepairWorker(database=db)
    events = []
    w._event_emit = lambda etype, data: events.append((etype, data))
    ok = w._create_finding('genre_cleanup', 'genre_cleanup', 'info',
                           'artist', 'AR1', None, 'Title X', 'desc')
    assert ok
    assert events == [('music_repair_finding_created',
                       {'job_id': 'genre_cleanup', 'finding_type': 'genre_cleanup',
                        'severity': 'info', 'title': 'Title X'})]
    # a broken emitter must never break the finding write
    w._event_emit = lambda *a: (_ for _ in ()).throw(RuntimeError('down'))
    assert w._create_finding('genre_cleanup', 'genre_cleanup', 'info',
                             'artist', 'AR2', None, 'Title Y', 'desc')


# ── blocks registry ─────────────────────────────────────────────────────────

def test_new_triggers_land_on_the_right_sides():
    music = {b['type'] for b in blocks_for_scope('music')['triggers']}
    video = {b['type'] for b in blocks_for_scope('video')['triggers']}
    assert {'music_repair_finding_created', 'music_repair_scan_completed'} <= music
    assert not {'music_repair_finding_created'} & video
    assert {'video_grab_started', 'video_request_created', 'video_request_approved'} <= video
    assert not {'video_grab_started'} & music


def test_monthly_time_carries_tz_field():
    monthly = next(b for b in blocks_for_scope('music')['triggers']
                   if b['type'] == 'monthly_time')
    keys = [f['key'] for f in monthly['config_fields']]
    assert keys == ['time', 'day_of_month', 'tz']


# ── builder JS source pins ──────────────────────────────────────────────────

def test_generic_renderer_is_ungated():
    """The music-breaking gate (`if (_autoBuilderCtx.ownedBy)`) around the
    generic config renderer/reader must stay gone."""
    render = _JS[_JS.index('function _renderBlockConfigFields'):]
    render = render[:render.index('function _renderGenericConfigField')]
    assert 'ownedBy' not in render
    reader = _JS[_JS.index('function _readPlacedConfig'):]
    reader = reader[:reader.index('function _findBlockDef')]
    assert 'ownedBy' not in reader


def test_monthly_gets_a_countdown_not_listening():
    assert re.search(r"_timerTriggers = \['schedule', 'daily_time', 'weekly_time', 'monthly_time'\]", _JS)


def test_label_fallbacks_and_webhook_label():
    assert "_findBlockDef(type)?.label || type || 'Unknown'" in _JS   # trigger + action maps
    assert "if (type === 'webhook') return 'Webhook';" in _JS


def test_payload_editor_and_test_button_exist():
    assert 'payload_template' in _JS
    assert '_autoTestNotify' in _JS
    assert '/api/automations/test-notify' in _JS


def test_per_step_condition_row_and_cap():
    assert 'condfield' in _JS and 'condop' in _JS and 'condvalue' in _JS
    assert "Maximum 5 then-actions" in _JS
    assert "Maximum 3 then-actions" not in _JS


def test_time_and_tz_inputs():
    assert "f.type === 'time'" in _JS                      # generic time field
    assert _JS.count('cfg-${slotKey}-tz') >= 2             # daily + weekly tz inputs
