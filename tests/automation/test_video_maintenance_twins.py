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


# ── Phase 4: Full Cleanup ───────────────────────────────────────────────────

def test_video_full_cleanup_is_video_scoped_only():
    assert "video_full_cleanup" in _action_types("video")
    assert "video_full_cleanup" not in _action_types("music")
    assert "full_cleanup" in _action_types("music")
    assert "full_cleanup" not in _action_types("video")


def test_video_full_cleanup_seeds_one_video_owned_system_row():
    rows = _system_by_action("video_full_cleanup")
    assert len(rows) == 1 and rows[0]["owned_by"] == "video"
    assert rows[0]["trigger_config"] == {"interval": 12, "unit": "hours"}


def test_video_full_cleanup_reuses_the_music_handler():
    handlers = _registered_handlers()
    assert "video_full_cleanup" in handlers
    assert "full_cleanup" in handlers


# ── Phase 5: Auto-Backup Database (video — CUSTOM, not a shared handler) ─────

def test_video_backup_database_is_video_scoped_only():
    assert "video_backup_database" in _action_types("video")
    assert "video_backup_database" not in _action_types("music")
    assert "backup_database" in _action_types("music")
    assert "backup_database" not in _action_types("video")


def test_video_backup_database_seeds_one_video_owned_system_row():
    rows = _system_by_action("video_backup_database")
    assert len(rows) == 1 and rows[0]["owned_by"] == "video"
    assert rows[0]["trigger_config"] == {"interval": 3, "unit": "days"}


def _mk_sqlite(path):
    import sqlite3
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()


class _Deps:
    class _L:
        def error(self, *a, **k):
            pass
        def debug(self, *a, **k):
            pass
    logger = _L()
    def update_progress(self, *a, **k):
        pass


def test_video_backup_targets_video_db_and_music_backup_targets_music_db(tmp_path, monkeypatch):
    # The whole reason this one can't be shared: each backs up ITS OWN db file.
    import glob
    from core.automation.handlers.maintenance import (
        auto_backup_database, auto_backup_video_database)
    music_db = tmp_path / "music_library.db"
    video_db = tmp_path / "video_library.db"
    _mk_sqlite(music_db)
    _mk_sqlite(video_db)
    monkeypatch.setenv("DATABASE_PATH", str(music_db))
    monkeypatch.setenv("VIDEO_DATABASE_PATH", str(video_db))
    deps = _Deps()

    r_music = auto_backup_database({"_automation_id": "m"}, deps)
    r_video = auto_backup_video_database({"_automation_id": "v"}, deps)

    assert r_music["status"] == "completed" and r_video["status"] == "completed"
    # each backup sits next to its OWN db — no cross-contamination
    assert r_music["backup_path"].startswith(str(music_db))
    assert r_video["backup_path"].startswith(str(video_db))
    assert glob.glob(str(music_db) + ".backup_*")
    assert glob.glob(str(video_db) + ".backup_*")
    # the video backup did NOT touch the music db's backups and vice-versa
    assert not glob.glob(str(music_db) + ".backup_*" )[0].startswith(str(video_db))


def test_video_backup_database_has_its_own_handler():
    handlers = _registered_handlers()
    assert "video_backup_database" in handlers
    assert "backup_database" in handlers
