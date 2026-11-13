"""the automations page's Activity view, the playlist a sync automation shows,
and how long a run stays in history."""

from datetime import datetime, timedelta, timezone

import pytest

from core.automation import api as auto_api
from database.music_database import MusicDatabase

NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "m.db"))
    return MusicDatabase()


def _auto(db, name, action="process_wishlist", **cols):
    config = cols.pop("action_config", "{}")
    with db._get_connection() as conn:
        keys = ["name", "enabled", "trigger_type", "trigger_config", "action_type", "action_config",
                "profile_id", *cols]
        cur = conn.execute(
            f"INSERT INTO automations({', '.join(keys)}) VALUES({', '.join('?' for _ in keys)})",
            (name, 1, "schedule", '{"interval": 1, "unit": "hours"}', action, config, 1, *cols.values()))
        conn.commit()
        return cur.lastrowid


def _run(db, automation_id, hours_ago, status="completed", summary=None, seconds=2.0):
    started = (NOW - timedelta(hours=hours_ago)).isoformat()
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO automation_run_history(automation_id, started_at, finished_at, "
            "duration_seconds, status, summary) VALUES(?,?,?,?,?,?)",
            (automation_id, started, started, seconds, status, summary))
        conn.commit()


def test_the_last_day_leads_with_the_automation_that_keeps_failing(db):
    fine, broken = _auto(db, "Fine"), _auto(db, "Broken", action="metasync_refresh_charts")
    for hour in range(1, 6):
        _run(db, fine, hour)
        _run(db, broken, hour, status="error", summary="older reason" if hour > 1 else "Reconnect MetaSync.")
    _run(db, broken, 0.5)                      # its latest run worked: last_error is clear
    _run(db, fine, 30)                         # yesterday: in the week, not in the day

    activity = auto_api.get_activity(db, 1, now=NOW)

    first, second = activity["recent"][:2]
    assert (first["name"], first["runs"], first["failed"]) == ("Broken", 6, 5)
    assert first["last_error"] == "Reconnect MetaSync."
    assert (second["name"], second["runs"], second["failed"], second["avg_seconds"]) == ("Fine", 5, 0, 2.0)
    assert activity["totals"] == {"runs": 11, "failed": 5, "skipped": 0}
    assert sum(d["completed"] + d["failed"] + d["skipped"] for d in activity["days"]) == 12


def test_each_side_only_counts_its_own_automations(db):
    music = _auto(db, "Music one")
    video = _auto(db, "Video one", action="video_rss_sync", owned_by="video")
    _run(db, music, 1)
    _run(db, video, 1, status="skipped")
    assert [r["name"] for r in auto_api.get_activity(db, 1, now=NOW)["recent"]] == ["Music one"]
    seen = auto_api.get_activity(db, 1, side="video", now=NOW)
    assert [r["name"] for r in seen["recent"]] == ["Video one"] and seen["totals"]["skipped"] == 1


def test_another_profiles_automation_is_left_out(db):
    theirs = _auto(db, "Theirs")
    with db._get_connection() as conn:
        conn.execute("UPDATE automations SET profile_id = 2 WHERE id = ?", (theirs,))
        conn.commit()
    _run(db, theirs, 1)
    assert auto_api.get_activity(db, 1, now=NOW)["recent"] == []


def test_a_playlist_sync_carries_its_playlist(db):
    with db._get_connection() as conn:
        pid = conn.execute(
            "INSERT INTO mirrored_playlists(source, source_playlist_id, name, image_url, profile_id) "
            "VALUES('deezer', 'x1', 'Fresh House', 'https://img/cover.jpg', 1)").lastrowid
        conn.commit()
    with_one = _auto(db, "Auto-Sync: Fresh House", action="playlist_pipeline",
                     action_config='{"playlist_id": "%d", "all": false}' % pid)
    gone = _auto(db, "Auto-Sync: Gone", action="playlist_pipeline",
                 action_config='{"playlist_id": "99999"}')
    plain = _auto(db, "Plain")
    rows = {a["id"]: a for a in auto_api.list_automations(db, 1)}
    assert rows[with_one]["playlist"] == {"id": pid, "name": "Fresh House",
                                          "image_url": "https://img/cover.jpg", "cover_tiles": ""}
    assert "playlist" not in rows[gone] and "playlist" not in rows[plain]


def test_a_week_of_runs_is_kept_past_the_hundredth(db):
    """the cap alone left a five minute automation eight hours of history."""
    busy = _auto(db, "Busy")
    now = datetime.now(timezone.utc)
    stamp = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
    for i in range(150):
        db.insert_automation_run_history(busy, stamp(1), stamp(1), 1.0, "completed")
    assert db.get_automation_run_history(busy, limit=1)["total"] == 150
    old = _auto(db, "Old")
    for i in range(120):
        db.insert_automation_run_history(old, stamp(30), stamp(30), 1.0, "completed")
    # older than the week: back to the newest hundred
    assert db.get_automation_run_history(old, limit=1)["total"] == 100
