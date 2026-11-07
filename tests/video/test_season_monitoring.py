"""Season monitoring is a per-season flag the Your library tab can read back,
and episodes added later follow it."""

from __future__ import annotations

import sqlite3

from core.video import library_manage
from database.video_database import SCHEMA_VERSION, VideoDatabase


def _db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _show(db, server_id="s1", seasons=(0, 1, 2)):
    return db.upsert_show_tree("plex", {"server_id": server_id, "title": "Show " + server_id,
                                        "seasons": [
        {"season_number": n, "server_id": "%s-se%d" % (server_id, n), "episodes": [
            {"episode_number": 1, "title": "E1", "server_id": "%s-%d-1" % (server_id, n)}]}
        for n in seasons]})


def _tab(db, sid):
    return {s["season_number"]: s["monitored"]
            for s in library_manage.show_library(db, sid, today="2026-11-07")["seasons"]}


def _eps(db, sid, season):
    conn = sqlite3.connect(db.database_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT monitored FROM episodes WHERE show_id=? AND season_number=? "
            "ORDER BY episode_number", (sid, season))]
    finally:
        conn.close()


def test_unmonitoring_a_season_reads_back_after_a_reload(tmp_path):
    db = _db(tmp_path)
    sid = _show(db)
    assert db.set_season_monitored(sid, 0, False) == 1
    assert _tab(db, sid) == {0: False, 1: True, 2: True}      # was {0: True, ...}
    assert _eps(db, sid, 0) == [0]
    db.set_season_monitored(sid, 0, True)
    assert _tab(db, sid)[0] is True and _eps(db, sid, 0) == [1]


def test_a_rescan_keeps_the_choice(tmp_path):
    db = _db(tmp_path)
    sid = _show(db)
    db.set_season_monitored(sid, 1, False)
    assert _show(db) == sid
    assert _tab(db, sid)[1] is False and _eps(db, sid, 1) == [0]


def test_episodes_added_later_follow_their_season(tmp_path):
    db = _db(tmp_path)
    sid = _show(db)
    db.set_season_monitored(sid, 1, False)
    db.backfill_episodes(sid, 1, [{"episode_number": 2, "title": "E2"}])
    db.backfill_episodes(sid, 2, [{"episode_number": 2, "title": "E2"}])
    assert _eps(db, sid, 1) == [0, 0]
    assert _eps(db, sid, 2) == [1, 1]
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show s1", "seasons": [
        {"season_number": n, "server_id": "s1-se%d" % n, "episodes": [
            {"episode_number": e, "title": "E", "server_id": "s1-%d-%d" % (n, e)}
            for e in (1, 2, 3)]} for n in (0, 1, 2)]})
    assert _eps(db, sid, 1) == [0, 0, 0]


def test_one_time_carry_over_of_seasons_already_unmonitored(tmp_path):
    """Before this change the toggle wrote only episodes. A season whose
    episodes are all off was switched off by hand: on the first start it
    reads back off and counts as set by hand."""
    path = tmp_path / "video_library.db"
    db = VideoDatabase(database_path=str(path))
    sid = _show(db)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE episodes SET monitored=0 WHERE show_id=? AND season_number=0", (sid,))
    conn.execute("UPDATE seasons SET monitored=1, monitor_locked=0")
    conn.execute("DELETE FROM video_settings WHERE key='season_monitor_backfill_done'")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    VideoDatabase._run_data_migrations(conn, SCHEMA_VERSION)   # an existing DB: no numbered step runs
    conn.commit()
    rows = {r["season_number"]: (r["monitored"], r["monitor_locked"]) for r in conn.execute(
        "SELECT season_number, monitored, monitor_locked FROM seasons WHERE show_id=?", (sid,))}
    conn.close()
    assert rows == {0: (0, 1), 1: (1, 0), 2: (1, 0)}
