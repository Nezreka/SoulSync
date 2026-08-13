"""The music twin of 'Auto-Update Video Database (Hourly)'.

The music library only got re-read after SoulSync itself finished a download —
``batch_complete`` → ``scan_library`` → ``library_scan_completed`` →
``start_database_update``. Music added any other way (dropped in by hand,
ripped, moved from another box) was indexed by Plex/Jellyfin/Navidrome within
minutes but did not reach SoulSync's own database until the WEEKLY deep scan.
The video side already had the hourly safety net for exactly this; the music
side did not.

What these pin is the shape of that net, because every part of it is a place it
could silently stop working:

- a DISTINCT action_type, because ``ensure_system_automations`` looks rows up by
  action_type — reusing ``start_database_update`` would find the event-driven
  row and never seed the schedule.
- ``full_refresh`` forced off, because the same handler backs both, and an
  hourly FULL library walk is a very different thing from an hourly delta.
- the db_update_state guard, so an hourly tick during a long deep scan is
  skipped rather than stacked on top of it.
"""

from __future__ import annotations

from core.automation.blocks import blocks_for_scope
from core.automation_engine import SYSTEM_AUTOMATIONS

HOURLY = "start_database_update_hourly"


def _action_types(scope):
    return {a["type"] for a in blocks_for_scope(scope)["actions"]}


def _system_by_action(action_type):
    return [s for s in SYSTEM_AUTOMATIONS if s.get("action_type") == action_type]


def test_the_music_side_seeds_exactly_one_hourly_row():
    rows = _system_by_action(HOURLY)
    assert len(rows) == 1
    row = rows[0]
    # Music-owned: no owned_by tag, so the music page shows it and the video
    # page (which lists only owned_by='video') does not.
    assert row.get("owned_by") is None
    assert row["trigger_type"] == "schedule"
    assert row["trigger_config"] == {"interval": 1, "unit": "hours"}


def test_it_is_a_distinct_action_from_the_after_scan_row():
    """The seeder keys on action_type. Sharing one would mean the schedule row
    is never created — get_system_automation_by_action would find the existing
    event-driven row and treat the job as already seeded."""
    after_scan = _system_by_action("start_database_update")
    assert len(after_scan) == 1
    assert after_scan[0]["trigger_type"] == "library_scan_completed"
    assert after_scan[0]["action_type"] != HOURLY


def test_the_hourly_row_never_asks_for_a_full_refresh():
    # A full refresh walks the entire library. Hourly, that is not a safety net,
    # it is a permanent scan.
    assert _system_by_action(HOURLY)[0]["action_config"] == {"full_refresh": False}


def test_it_starts_off_the_boot_path():
    # 15 minutes, like the other scan-ish system rows — a fresh start already has
    # plenty to do without a library read racing it.
    assert _system_by_action(HOURLY)[0]["initial_delay"] == 900


def test_the_block_is_music_scoped_and_the_video_twin_is_untouched():
    assert HOURLY in _action_types("music")
    assert HOURLY not in _action_types("video")
    # The video original still exists, still video-only.
    assert "video_update_database_hourly" in _action_types("video")
    assert "video_update_database_hourly" not in _action_types("music")


def _registered():
    from core.automation.handlers import register_all

    class _Eng:
        def __init__(self):
            self.handlers = {}
            self.guards = {}

        def register_action_handler(self, t, fn, guard_fn=None):
            self.handlers[t] = fn
            self.guards[t] = guard_fn

        def register_progress_callbacks(self, *a, **k):
            pass

    from tests.automation.test_handler_registration import _build_deps

    eng = _Eng()
    register_all(_build_deps(eng))
    return eng


def test_the_handler_is_registered_alongside_the_original():
    eng = _registered()
    assert HOURLY in eng.handlers
    assert "start_database_update" in eng.handlers


def test_it_shares_the_db_update_busy_guard():
    """Without this an hourly tick landing mid-deep-scan would queue a second
    scan behind the first instead of standing down."""
    eng = _registered()
    assert eng.guards[HOURLY] is not None
    assert eng.guards[HOURLY]() == eng.guards["start_database_update"]()


def test_the_wrapper_pins_incremental_but_an_explicit_config_still_wins():
    """The registration spreads config OVER the default, so a hand-built
    automation can still ask for a full refresh — the pin only protects the
    seeded schedule, it does not take the option away."""
    seen = {}

    from core.automation.handlers import registration

    def _capture(config, deps):
        seen.update(config)
        return {"status": "completed"}

    original = registration.auto_start_database_update
    registration.auto_start_database_update = _capture
    try:
        eng = _registered()
        eng.handlers[HOURLY]({})
        assert seen["full_refresh"] is False
        seen.clear()
        eng.handlers[HOURLY]({"full_refresh": True})
        assert seen["full_refresh"] is True
    finally:
        registration.auto_start_database_update = original
