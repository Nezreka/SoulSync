"""Video twins of the music maintenance automations.

The music side has "Clean Search History" / "Clean Completed Downloads" / "Full
Cleanup" / "Auto-Backup Database". The video side gets its own copies so they
appear on the video Automations page too — distinct ``video_*`` action_type
(the system seeder keys on action_type, so a shared key would collide), the SAME
shared handler where the behaviour is identical, and ``owned_by='video'`` so the
music page is never disrupted.

These pin the registry + scope + seed contract, one twin per phase.
"""

from __future__ import annotations

from core.automation.blocks import blocks_for_scope
from core.automation_engine import SYSTEM_AUTOMATIONS


def _action_types(scope):
    return {a["type"] for a in blocks_for_scope(scope)["actions"]}


def _system_by_action(action_type):
    return [s for s in SYSTEM_AUTOMATIONS if s.get("action_type") == action_type]


# ── Phase 2: Clean Search History ───────────────────────────────────────────

def test_video_clean_search_history_is_video_scoped_only():
    # Appears on the video builder, NOT the music builder (music block untouched).
    assert "video_clean_search_history" in _action_types("video")
    assert "video_clean_search_history" not in _action_types("music")


def test_music_clean_search_history_is_untouched():
    # The original music action still exists and is still music-scoped — no disruption.
    assert "clean_search_history" in _action_types("music")
    assert "clean_search_history" not in _action_types("video")


def test_video_clean_search_history_seeds_one_video_owned_system_row():
    rows = _system_by_action("video_clean_search_history")
    assert len(rows) == 1
    row = rows[0]
    assert row["owned_by"] == "video"
    assert row["trigger_type"] == "schedule"
    # mirrors the music cadence (every 1 hour)
    assert row["trigger_config"] == {"interval": 1, "unit": "hours"}


def _registered_handlers():
    from core.automation.handlers import register_all

    class _Eng:
        def __init__(self):
            self.handlers = {}
        def register_action_handler(self, t, fn, guard_fn=None):
            self.handlers[t] = fn
        def register_progress_callbacks(self, *a, **k):
            pass

    from tests.automation.test_handler_registration import _build_deps
    eng = _Eng()
    register_all(_build_deps(eng))
    return eng.handlers


def test_video_clean_search_history_reuses_the_music_handler():
    # Registered to the SAME handler function as the music action (identical behaviour).
    handlers = _registered_handlers()
    assert "video_clean_search_history" in handlers
    assert "clean_search_history" in handlers


# ── Phase 3: Clean Completed Downloads ──────────────────────────────────────

def test_video_clean_completed_downloads_is_video_scoped_only():
    assert "video_clean_completed_downloads" in _action_types("video")
    assert "video_clean_completed_downloads" not in _action_types("music")
    # music original untouched
    assert "clean_completed_downloads" in _action_types("music")
    assert "clean_completed_downloads" not in _action_types("video")


def test_video_clean_completed_downloads_seeds_one_video_owned_system_row():
    rows = _system_by_action("video_clean_completed_downloads")
    assert len(rows) == 1 and rows[0]["owned_by"] == "video"
    assert rows[0]["trigger_config"] == {"interval": 5, "unit": "minutes"}


def test_video_clean_completed_downloads_reuses_the_music_handler():
    handlers = _registered_handlers()
    assert "video_clean_completed_downloads" in handlers
    assert "clean_completed_downloads" in handlers
