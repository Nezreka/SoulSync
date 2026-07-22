"""Video failing-wishlist visibility (LiveLeak hub, phase 3).

The drain now records each search outcome on the wishlist row: a genuinely
fruitless search (no results / all rejected) increments search_attempts, a
grab resets it, and a search that never RAN (slskd down) records nothing —
it says nothing about whether the release exists. The wishlist page badges
rows at 3+ attempts. Columns ride _COLUMN_MIGRATIONS (live server upgrades
in place).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.automation.handlers.video_process_wishlist import auto_video_process_wishlist
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


def _seed_movie(db, tmdb_id=101, title="Stuck Movie"):
    conn = db._get_connection()
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, status) "
                 "VALUES ('movie', ?, ?, 'wanted')", (tmdb_id, title))
    conn.commit()
    conn.close()


def _seed_episode(db, tmdb_id=202, s=1, e=3):
    conn = db._get_connection()
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, season_number, "
                 "episode_number, status) VALUES ('episode', ?, 'Show', ?, ?, 'wanted')",
                 (tmdb_id, s, e))
    conn.commit()
    conn.close()


def _attempts(db, kind, tmdb_id, s=None, e=None):
    conn = db._get_connection()
    try:
        q = "SELECT search_attempts, last_search_at FROM video_wishlist WHERE kind=? AND tmdb_id=?"
        args = [kind, tmdb_id]
        if s is not None:
            q += " AND season_number=? AND episode_number=?"
            args += [s, e]
        r = conn.execute(q, args).fetchone()
        return (r["search_attempts"] or 0, r["last_search_at"]) if r else (None, None)
    finally:
        conn.close()


class TestSchema:
    def test_new_columns_exist_on_a_fresh_db(self, db):
        conn = db._get_connection()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(video_wishlist)")}
        conn.close()
        assert "search_attempts" in cols and "last_search_at" in cols

    def test_columns_ride_the_migration_list(self):
        src = (_ROOT / "database" / "video_database.py").read_text(encoding="utf-8", errors="replace")
        assert '("video_wishlist", "search_attempts", "INTEGER DEFAULT 0")' in src
        assert '("video_wishlist", "last_search_at", "TEXT")' in src


class TestRecordOutcome:
    def test_fruitless_search_increments(self, db):
        _seed_movie(db)
        db.record_wishlist_search_outcome("movie", 101, grabbed=False)
        db.record_wishlist_search_outcome("movie", 101, grabbed=False)
        n, last = _attempts(db, "movie", 101)
        assert n == 2 and last

    def test_grab_resets(self, db):
        _seed_movie(db)
        for _ in range(4):
            db.record_wishlist_search_outcome("movie", 101, grabbed=False)
        db.record_wishlist_search_outcome("movie", 101, grabbed=True)
        assert _attempts(db, "movie", 101)[0] == 0

    def test_episode_rows_key_on_season_episode(self, db):
        _seed_episode(db, 202, 1, 3)
        _seed_episode(db, 202, 1, 4)
        db.record_wishlist_search_outcome("movie", 202, grabbed=False)  # wrong kind: no-op
        db.record_wishlist_search_outcome("episode", 202, grabbed=False,
                                          season_number=1, episode_number=3)
        assert _attempts(db, "episode", 202, 1, 3)[0] == 1
        assert _attempts(db, "episode", 202, 1, 4)[0] == 0


class TestDrainWiring:
    def _run(self, db, items, media_type, *, cands, enqueue_ok=True, search_ret=None):
        outcomes = []

        def record(item, mt, ok):
            outcomes.append((item.get("tmdb_id") or item.get("show_tmdb_id"), ok))
            # exercise the real recorder too
            from core.automation.handlers.video_process_wishlist import _default_record_outcome
            _default_record_outcome(item, mt, ok)

        import api.video as videoapi
        videoapi._video_db = db
        try:
            from types import SimpleNamespace

            class _Deps:
                def update_progress(self, automation_id, **kw):
                    pass
            auto_video_process_wishlist(
                {"_automation_id": None}, _Deps(), media_type=media_type,
                fetch_items=lambda mt: items,
                active_keys=lambda mt: set(),
                target_dir=lambda mt: "/tmp/target",
                search=lambda it, mt: (cands, None) if search_ret is None else search_ret,
                enqueue=lambda *a, **k: enqueue_ok,
                record_outcome=record,
            )
        finally:
            videoapi._video_db = None
        return outcomes

    def test_fruitless_movie_search_records_a_failure(self, db):
        _seed_movie(db, 101)
        out = self._run(db, [{"tmdb_id": 101, "title": "Stuck Movie"}], "movie", cands=[])
        assert out == [(101, False)]
        assert _attempts(db, "movie", 101)[0] == 1

    def test_grab_records_success_and_resets(self, db):
        _seed_movie(db, 101)
        db.record_wishlist_search_outcome("movie", 101, grabbed=False)
        cand = {"filename": "f", "title": "f", "username": "u", "size_bytes": 1,
                "quality_label": "WEBDL-1080p", "accepted": True, "score": 5}
        out = self._run(db, [{"tmdb_id": 101, "title": "Stuck Movie"}], "movie", cands=[cand])
        assert out == [(101, True)]
        assert _attempts(db, "movie", 101)[0] == 0

    def test_search_that_never_ran_records_nothing(self, db):
        _seed_movie(db, 101)
        out = self._run(db, [{"tmdb_id": 101, "title": "Stuck Movie"}], "movie",
                        cands=None, search_ret=(None, "slskd down"))
        assert out == []
        assert _attempts(db, "movie", 101)[0] == 0

    def test_episode_items_record_via_show_tmdb_id(self, db):
        _seed_episode(db, 202, 1, 3)
        item = {"show_tmdb_id": 202, "show_title": "Show",
                "season_number": 1, "episode_number": 3}
        out = self._run(db, [item], "episode", cands=[])
        assert out == [(202, False)]
        assert _attempts(db, "episode", 202, 1, 3)[0] == 1


class TestSurface:
    def test_query_wishlist_returns_the_fields(self, db):
        _seed_movie(db, 101)
        db.record_wishlist_search_outcome("movie", 101, grabbed=False)
        items = db.query_wishlist("movie")["items"]
        assert items and items[0]["search_attempts"] == 1
        assert items[0]["last_search_at"]

    def test_ui_renders_the_failing_marker(self):
        js = (_ROOT / "webui" / "static" / "video" / "video-wishlist.js").read_text(
            encoding="utf-8", errors="replace")
        css = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(
            encoding="utf-8", errors="replace")
        assert "vwsh-failing" in js and "search_attempts" in js
        assert ".vwsh-failing" in css and ".vwsh-failing-inline" in css
