"""Season monitoring is a per-season flag the Your library tab can read back,
episodes added later follow it, and the 'Monitor Specials' setting turns
season 0 off everywhere except a show that is all specials or a Specials
season the user set by hand."""

from __future__ import annotations

import sqlite3

from core.video import library_manage
from core.video.monitor_policy import episodes_airing_between
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


def _set(db, on):
    db.set_setting("monitor_specials", "1" if on else "0")
    return db.apply_specials_policy()


def test_monitor_specials_off_and_back_on(tmp_path):
    db = _db(tmp_path)
    regular = _show(db, "a")
    only_specials = _show(db, "b", seasons=(0,))
    assert _set(db, False) == 1
    assert _tab(db, regular) == {0: False, 1: True, 2: True}
    assert _eps(db, regular, 0) == [0]
    assert _tab(db, only_specials) == {0: True}, "a specials-only show keeps them"
    assert _set(db, True) == 1
    assert _tab(db, regular)[0] is True and _eps(db, regular, 0) == [1]


def test_monitor_specials_never_overrides_a_hand_set_season(tmp_path):
    db = _db(tmp_path)
    kept = _show(db, "a")
    db.set_season_monitored(kept, 0, True)        # switched on by hand
    _set(db, False)
    assert _tab(db, kept)[0] is True
    off = _show(db, "c")
    db.set_season_monitored(off, 0, False)        # switched off by hand
    _set(db, False)
    _set(db, True)
    assert _tab(db, off)[0] is False


def test_new_shows_and_new_specials_follow_the_setting(tmp_path):
    db = _db(tmp_path)
    db.set_setting("monitor_specials", "0")
    sid = _show(db, "a")                          # scanned in with the setting off
    assert _tab(db, sid)[0] is False and _eps(db, sid, 0) == [0]
    db.backfill_episodes(sid, 0, [{"episode_number": 2, "title": "SP2"}])
    assert _eps(db, sid, 0) == [0, 0]
    # a specials-only show gains its first real season: the policy reaches it then
    late = _show(db, "b", seasons=(0,))
    assert _tab(db, late) == {0: True}
    db.backfill_episodes(late, 1, [{"episode_number": 1, "title": "E1"}])
    assert _tab(db, late) == {0: False, 1: True}


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


class _Engine:
    def tmdb_detail(self, kind, tmdb_id):
        return {"poster_url": None, "seasons": [{"season_number": n} for n in self.seasons]}

    def tmdb_season(self, tmdb_id, sn):
        return {"episodes": [{"episode_number": 1, "air_date": "2026-11-05"}]}


def test_unowned_follow_airing_drops_specials_when_off():
    eng = _Engine()
    eng.seasons = [0, 1, 2]
    got = lambda inc: sorted(e["season_number"] for e in episodes_airing_between(
        eng, 1, "2026-11-01", "2026-11-07", include_specials=inc)["episodes"])
    assert got(True) == [0, 1, 2]
    assert got(False) == [1, 2]
    eng.seasons = [0]                              # all specials: kept either way
    assert got(False) == [0]


def test_a_special_unmonitored_by_hand_stays_off_when_the_setting_comes_back(tmp_path):
    """#2017 follow-up: turning Monitor Specials back on re-monitored a
    special the user had switched off by hand"""
    db = _db(tmp_path)
    sid = _show(db, "a")
    db.backfill_episodes(sid, 0, [{"episode_number": 2, "title": "SP2"}])
    conn = sqlite3.connect(db.database_path)
    conn.execute("UPDATE shows SET tmdb_id=4242 WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    assert db.set_episode_monitored(4242, 0, 2, False) == 1
    _set(db, False)
    _set(db, True)
    assert _eps(db, sid, 0) == [1, 0]


def _rows(db, sql, *args):
    conn = sqlite3.connect(db.database_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _dated_tree(seasons):
    return {"server_id": "s1", "title": "Show", "seasons": [
        {"season_number": n, "server_id": "se%d" % n, "episodes": [
            {"episode_number": 1, "title": "E1", "server_id": "ep%d" % n,
             "air_date": "2020-01-0%d" % (n + 1)}]} for n in seasons]}


def test_a_season_whose_files_all_left_keeps_its_episodes_and_choice(tmp_path):
    """review 5 R5-M-1: the scan demoted the vanished episodes, then deleted
    their season row, and the FK cascade took the episodes and the user's
    unmonitor with it. the TMDB backfill then re-created them monitored"""
    db = _db(tmp_path)
    sid = db.upsert_show_tree("plex", _dated_tree((1, 2)))
    db.backfill_episodes(sid, 2, [{"episode_number": 2, "title": "E2", "air_date": "2020-02-01"}])
    db.set_season_monitored(sid, 2, False)

    db.upsert_show_tree("plex", _dated_tree((1,)))     # season 2's files left the server

    assert _rows(db, "SELECT episode_number, monitored, server_id FROM episodes "
                     "WHERE show_id=? AND season_number=2 ORDER BY 1", sid) == [
        (1, 0, None), (2, 0, None)]
    assert _rows(db, "SELECT monitored, monitor_locked, server_id FROM seasons "
                     "WHERE show_id=? AND season_number=2", sid) == [(0, 1, None)]
    db.backfill_episodes(sid, 2, [{"episode_number": 3, "title": "E3", "air_date": "2020-03-01"}])
    assert _eps(db, sid, 2) == [0, 0, 0]
    # the files come back: the season is the server's again
    db.upsert_show_tree("plex", _dated_tree((1, 2)))
    assert _rows(db, "SELECT server_id, monitored FROM seasons WHERE show_id=? "
                     "AND season_number=2", sid) == [("se2", 0)]


def test_a_vanished_season_with_nothing_left_is_still_removed(tmp_path):
    db = _db(tmp_path)
    sid = _show(db, seasons=(1, 2))                    # episodes with no identity
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show s1", "seasons": [
        {"season_number": 1, "server_id": "s1-se1", "episodes": [
            {"episode_number": 1, "title": "E1", "server_id": "s1-1-1"}]}]})
    assert _rows(db, "SELECT season_number FROM seasons WHERE show_id=?", sid) == [(1,)]


def test_carry_over_locks_a_specials_season_set_by_hand_in_part(tmp_path):
    """review 5 R5-M-2: before season locks the toggle wrote only episodes.
    the carry-over locked a season only when ALL its episodes were off, so
    Specials with SP2 off by hand stayed unlocked and Monitor Specials off
    then on monitored SP2 again"""
    path = tmp_path / "video_library.db"
    db = VideoDatabase(database_path=str(path))
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "seasons": [
        {"season_number": 0, "server_id": "se0", "episodes": [
            {"episode_number": e, "title": "SP%d" % e, "server_id": "sp%d" % e} for e in (1, 2)]},
        {"season_number": 1, "server_id": "se1", "episodes": [
            {"episode_number": e, "title": "E%d" % e, "server_id": "e%d" % e} for e in (1, 2)]}]})
    conn = sqlite3.connect(path)
    conn.execute("UPDATE episodes SET monitored=0 WHERE show_id=? AND episode_number=2", (sid,))
    conn.execute("UPDATE seasons SET monitored=1, monitor_locked=0")
    conn.execute("DELETE FROM video_settings WHERE key='season_monitor_backfill_done'")
    conn.commit()
    conn.row_factory = sqlite3.Row
    VideoDatabase._run_data_migrations(conn, SCHEMA_VERSION)
    conn.commit()
    conn.close()

    _set(db, False)
    _set(db, True)
    assert _eps(db, sid, 0) == [1, 0]
    # only Specials: a regular season in part is never bulk-set, so not locked
    assert _rows(db, "SELECT season_number, monitored, monitor_locked FROM seasons "
                     "WHERE show_id=? ORDER BY 1", sid) == [(0, 1, 1), (1, 1, 0)]
