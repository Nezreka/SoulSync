"""Filtered overlay targeting: a scope's template applies only to items matching
a smart-rule filter (same language as collections; seasons/episodes filter the
PARENT SHOW), and items a tightened filter releases get their clean art
RESTORED — no stale overlays left behind (Kometa leaves them)."""

from __future__ import annotations

import pytest

from core.video.collections.smart_filter import SmartFilterError
from core.video.overlays.service import OverlayApplyService
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db):
    conn = db._get_connection()
    try:
        for mid, title, year, genre in ((1, "Old", 1985, "Action"), (2, "New", 2020, "Action"),
                                        (3, "Drama", 2021, "Drama")):
            conn.execute("INSERT INTO movies (id, server_source, server_id, title, year, has_file) "
                         "VALUES (?,?,?,?,?,1)", (mid, "plex", f"m{mid}", title, year))
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (genre,))
            gid = conn.execute("SELECT id FROM genres WHERE name=?", (genre,)).fetchone()[0]
            conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
        # A show with a season + an episode (for parent-show filtering).
        conn.execute("INSERT INTO shows (id, server_source, server_id, title, year) "
                     "VALUES (10, 'plex', 'sh10', 'Anime Show', 2020)")
        conn.execute("INSERT OR IGNORE INTO genres (name) VALUES ('Animation')")
        gid = conn.execute("SELECT id FROM genres WHERE name='Animation'").fetchone()[0]
        conn.execute("INSERT INTO show_genres (show_id, genre_id) VALUES (10, ?)", (gid,))
        conn.execute("INSERT INTO shows (id, server_source, server_id, title, year) "
                     "VALUES (11, 'plex', 'sh11', 'Live Action', 2020)")
        conn.execute("INSERT INTO seasons (id, show_id, season_number, server_id) VALUES (100, 10, 1, 'se100')")
        conn.execute("INSERT INTO seasons (id, show_id, season_number, server_id) VALUES (101, 11, 1, 'se101')")
        conn.execute("INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, "
                     "title, server_source, server_id) VALUES (200, 10, 100, 1, 1, 'Ep', 'plex', 'ep200')")
        conn.commit()
    finally:
        conn.close()


_YEAR_2000_PLUS = {"match": "all", "rules": [{"field": "year", "op": "gte", "value": 2000}]}
_ANIMATION = {"match": "all", "rules": [{"field": "genre", "op": "in", "value": ["Animation"]}]}


# ── DB: filtered scope items ─────────────────────────────────────────────────
def test_scope_items_filtered_by_rules(db):
    _seed(db)
    assert len(db.overlay_scope_items("movie")) == 3
    got = db.overlay_scope_items("movie", filter_definition=_YEAR_2000_PLUS)
    assert sorted(m["title"] for m in got) == ["Drama", "New"]


def test_season_episode_filters_target_the_parent_show(db):
    _seed(db)
    seasons = db.overlay_scope_items("season", filter_definition=_ANIMATION)
    assert len(seasons) == 1 and "Anime Show" in seasons[0]["title"]
    eps = db.overlay_scope_items("episode", filter_definition=_ANIMATION)
    assert len(eps) == 1 and "Anime Show" in eps[0]["title"]
    # Unfiltered still returns everything.
    assert len(db.overlay_scope_items("season")) == 2


def test_bad_filter_raises_not_misapplies(db):
    _seed(db)
    with pytest.raises(SmartFilterError):
        db.overlay_scope_items("movie", filter_definition={"rules": [{"field": "nope", "op": "is", "value": 1}]})


# ── assignments persist the filter ───────────────────────────────────────────
def test_assignment_filter_roundtrip_and_clear(db):
    tid = db.create_overlay_template("T", definition={"layers": []})
    assert db.set_overlay_assignment("movie", tid, True, filter_definition=_YEAR_2000_PLUS)
    a = db.get_overlay_assignments()["movie"]
    assert a["filter"] == _YEAR_2000_PLUS
    assert db.set_overlay_assignment("movie", tid, True, filter_definition=None)
    assert db.get_overlay_assignments()["movie"]["filter"] is None


# ── service: filtered jobs + the restore pass ────────────────────────────────
def test_build_jobs_respects_filter_and_bad_filter_skips_scope(db):
    _seed(db)
    tid = db.create_overlay_template("T", definition={"layers": []})
    db.set_overlay_assignment("movie", tid, True, filter_definition=_YEAR_2000_PLUS)
    svc = OverlayApplyService(db)
    jobs = svc.build_jobs(["movie"])
    assert sorted(j["title"] for j in jobs) == ["Drama", "New"]

    # Corrupt the stored filter → the scope is SKIPPED, never over-applied.
    conn = db._get_connection()
    conn.execute("UPDATE overlay_assignment SET filter='{\"rules\":[{\"field\":\"zz\",\"op\":\"is\",\"value\":1}]}' "
                 "WHERE scope='movie'")
    conn.commit(); conn.close()
    assert svc.build_jobs(["movie"]) == []


def test_restore_jobs_release_items_the_filter_dropped(db):
    _seed(db)
    tid = db.create_overlay_template("T", definition={"layers": []})
    db.set_overlay_assignment("movie", tid, True, filter_definition=_YEAR_2000_PLUS)
    # Overlays were previously applied to ALL three movies (filter added later).
    for mid in (1, 2, 3):
        db.record_overlay_apply("movie", mid, tid, "sha", "sig")
    svc = OverlayApplyService(db)
    restores = svc.build_restore_jobs(["movie"])
    assert [j["item_id"] for j in restores] == [1]           # 'Old' (1985) released
    # No filter → nothing restored this way.
    db.set_overlay_assignment("movie", tid, True, filter_definition=None)
    assert svc.build_restore_jobs(["movie"]) == []
