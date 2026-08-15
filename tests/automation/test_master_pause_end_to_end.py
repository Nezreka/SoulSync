"""The master pause, against a REAL database and real seeded system rows.

`test_master_pause.py` covers the same contract with a MagicMock db, which is
the right shape for the unit rules but cannot catch the one class of bug that
matters most here: a system automation whose stored row makes
`automation_side` classify it as the OTHER side. A mock returns whatever the
test hands it; only a real seeded row proves what production actually stores.

This exists because users reported automations appearing to run while the
music master was paused. The engine gate read as correct on inspection; these
tests are the evidence, so the next person does not have to re-derive it.
"""

from __future__ import annotations

import pytest

from core.automation_engine import SYSTEM_AUTOMATIONS, AutomationEngine
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'automations.db'))


@pytest.fixture()
def engine(db):
    eng = AutomationEngine(db)
    eng._running = True
    # No real timers: _finish_run reschedules, and a live Timer would fire
    # during the test run.
    eng.schedule_automation = lambda automation_id: None
    return eng


def _seeded(engine, db, action_type):
    engine.ensure_system_automations()
    row = db.get_system_automation_by_action(action_type)
    assert row, f'{action_type} was not seeded'
    return row


def _arm(engine, action_type):
    """Replace a handler with a recorder; returns the call list."""
    calls = []
    engine._action_handlers[action_type] = {
        'handler': lambda config: calls.append(config) or {'status': 'completed'},
        'guard': None,
    }
    return calls


# ── classification, against the rows production actually writes ──────────────

def test_every_music_system_automation_really_classifies_as_music(engine, db):
    """The mislabel check.

    If a music system row were stored with owned_by='video' (or gained a
    video_* action), the MUSIC master would stop gating it — and with the
    video master on, it would run happily while the music page showed
    "Paused". That is the exact shape of the reported symptom, so it is
    asserted against seeded rows rather than against the spec literals.
    """
    engine.ensure_system_automations()

    for spec in SYSTEM_AUTOMATIONS:
        expected = 'video' if spec.get('owned_by') == 'video' else None
        row = db.get_system_automation_by_action(spec['action_type'])
        assert row, f"{spec['name']} was not seeded"
        side = AutomationEngine.automation_side(row)
        if expected == 'video':
            assert side == 'video', f"{spec['name']} should be video-side"
        else:
            assert side == 'music', (
                f"{spec['name']} classifies as {side!r}; the music master "
                f"would not gate it"
            )


def test_the_five_minute_cleanup_is_music_side(engine, db):
    """Named explicitly: this is the row from the user's screenshot showing a
    real result while the music side was paused."""
    row = _seeded(engine, db, 'clean_completed_downloads')

    assert AutomationEngine.automation_side(row) == 'music'


# ── the gate, end to end ─────────────────────────────────────────────────────

def test_a_paused_music_automation_does_not_execute(engine, db):
    row = _seeded(engine, db, 'clean_completed_downloads')
    calls = _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', False)

    engine.run_automation(row['id'])

    assert calls == [], 'the handler ran while the music side was paused'


def test_the_skip_is_recorded_so_the_card_can_say_why(engine, db):
    row = _seeded(engine, db, 'clean_completed_downloads')
    _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', False)

    engine.run_automation(row['id'])

    after = db.get_automation(row['id'])
    assert 'paused' in (after.get('last_result') or ''), (
        'a paused run must leave the reason on the row — otherwise the card '
        'shows a bare timestamp and looks exactly like a real run'
    )


def test_unpausing_lets_it_run_again(engine, db):
    row = _seeded(engine, db, 'clean_completed_downloads')
    calls = _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', False)
    engine.run_automation(row['id'])
    engine.set_master_enabled('music', True)

    engine.run_automation(row['id'])

    assert len(calls) == 1


def test_turning_the_video_side_ON_does_not_release_music(engine, db):
    """Cross-contamination check. The two masters are independent, and a
    user who enables video automations must not silently un-pause music."""
    row = _seeded(engine, db, 'clean_completed_downloads')
    calls = _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', False)
    engine.set_master_enabled('video', True)

    engine.run_automation(row['id'])

    assert calls == []


def test_the_pause_survives_a_restart(engine, db):
    """It lives in the DB, not in memory — a paused install that reboots must
    come back paused."""
    row = _seeded(engine, db, 'clean_completed_downloads')
    engine.set_master_enabled('music', False)

    reborn = AutomationEngine(db)
    reborn._running = True
    reborn.schedule_automation = lambda automation_id: None
    calls = _arm(reborn, 'clean_completed_downloads')
    reborn.run_automation(row['id'])

    assert reborn.master_enabled('music') is False
    assert calls == []


def test_a_manual_run_still_works_while_paused(engine, db):
    """Deliberate: an explicit click outranks the pause. Asserted so nobody
    'fixes' it into silence later."""
    row = _seeded(engine, db, 'clean_completed_downloads')
    calls = _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', False)

    engine.run_automation(row['id'], skip_delay=True)

    assert len(calls) == 1


# ── the event path ───────────────────────────────────────────────────────────

def test_a_paused_event_automation_does_not_execute(engine, db):
    row = _seeded(engine, db, 'scan_library')     # Auto-Scan After Downloads
    calls = _arm(engine, 'scan_library')
    engine.set_master_enabled('music', False)

    engine._run_event_automation(row, row['id'], {})

    assert calls == []


def test_direct_effect_callers_see_a_paused_side_as_off(engine, db):
    """The seam for code that fires an effect without routing through the
    engine — core/imports/pipeline.py asks this before its own library scan."""
    _seeded(engine, db, 'scan_library')
    engine.set_master_enabled('music', False)

    assert engine.is_event_action_enabled('batch_complete', 'scan_library') is False

    engine.set_master_enabled('music', True)

    assert engine.is_event_action_enabled('batch_complete', 'scan_library') is True


# ── the delay window ─────────────────────────────────────────────────────────
#
# The gate runs ONCE, at the top of the run. An automation with a configured
# action delay then sleeps for up to that many minutes before doing anything.
# Pausing during that window used to have no effect: the run had already
# cleared the check, so it woke up and fired anyway — with the page showing
# "Paused" the whole time. This is the one real gate hole found in the review.

def _pause_during_delay(monkeypatch, engine, side='music'):
    """Make time.sleep instant, and flip the master off on the first tick."""
    import core.automation_engine as engine_module

    ticks = {'n': 0}

    def fake_sleep(_seconds):
        ticks['n'] += 1
        if ticks['n'] == 1:
            engine.set_master_enabled(side, False)

    monkeypatch.setattr(engine_module.time, 'sleep', fake_sleep)
    return ticks


def test_pausing_DURING_a_delay_stops_the_scheduled_run(engine, db, monkeypatch):
    row = _seeded(engine, db, 'clean_completed_downloads')
    db.update_automation(row['id'], action_config='{"delay": 5}')
    calls = _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', True)
    _pause_during_delay(monkeypatch, engine)

    engine.run_automation(row['id'])

    assert calls == [], (
        'the automation woke from its delay and ran even though the side was '
        'paused while it slept'
    )


def test_the_abandoned_run_says_why_on_the_card(engine, db, monkeypatch):
    row = _seeded(engine, db, 'clean_completed_downloads')
    db.update_automation(row['id'], action_config='{"delay": 5}')
    _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', True)
    _pause_during_delay(monkeypatch, engine)

    engine.run_automation(row['id'])

    assert 'paused' in (db.get_automation(row['id']).get('last_result') or '')


def test_a_delayed_run_still_completes_when_nobody_pauses(engine, db, monkeypatch):
    """The guard must not break the ordinary delayed run."""
    import core.automation_engine as engine_module

    row = _seeded(engine, db, 'clean_completed_downloads')
    db.update_automation(row['id'], action_config='{"delay": 1}')
    calls = _arm(engine, 'clean_completed_downloads')
    engine.set_master_enabled('music', True)
    monkeypatch.setattr(engine_module.time, 'sleep', lambda _s: None)

    engine.run_automation(row['id'])

    assert len(calls) == 1


def test_pausing_DURING_a_delay_stops_the_event_run(engine, db, monkeypatch):
    row = _seeded(engine, db, 'scan_library')
    db.update_automation(row['id'], action_config='{"delay": 5}')
    calls = _arm(engine, 'scan_library')
    engine.set_master_enabled('music', True)
    _pause_during_delay(monkeypatch, engine)

    engine._run_event_automation(db.get_automation(row['id']), row['id'], {})

    assert calls == []


# ── shared cleanup handlers ──────────────────────────────────────────────────

def test_the_cleanup_handlers_are_deliberately_shared_across_sides(engine, monkeypatch):
    """`video_clean_completed_downloads` and `clean_completed_downloads` are
    bound to the SAME handler with the SAME deps, so the video-side row also
    tidies the music download queue.

    That is FINE and intentional. Both actions do idempotent housekeeping —
    clear completed entries, sweep empty directories — so running it from
    either side is harmless, and the real failure mode is it not running at
    all (a full slskd transfer list is what makes downloads look stuck).

    Contrast `video_backup_database`, which DOES get its own handler: a backup
    writes a file, so pointing it at the wrong database matters.

    This test exists as a tripwire. If either cleanup handler ever gains a
    destructive step, the sharing stops being harmless and this should be
    split the way backup was.
    """
    from unittest.mock import MagicMock

    import core.automation.handlers.registration as registration

    seen = []
    monkeypatch.setattr(
        registration, 'auto_clean_completed_downloads',
        lambda config, deps: seen.append(deps) or {'status': 'completed'},
    )
    deps = MagicMock()
    deps.engine = engine
    registration.register_all(deps)

    engine._action_handlers['video_clean_completed_downloads']['handler']({})
    engine._action_handlers['clean_completed_downloads']['handler']({})

    assert len(seen) == 2
    assert seen[0] is seen[1]
