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

        def record(item, mt, ok, refusal=None):
            outcomes.append((item.get("tmdb_id") or item.get("show_tmdb_id"), ok))
            # exercise the real recorder too — including the refusal receipt, so
            # the DB write path for it is covered here and not only in theory.
            from core.automation.handlers.video_process_wishlist import _default_record_outcome
            _default_record_outcome(item, mt, ok, refusal)

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


# ── the receipt: WHY the last search came back empty ─────────────────────────
# search_attempts told you a row had searched forty times; it never told you what
# it found. Three rows on Boulder's install sit at thirteen fruitless searches
# (Aussie Shore S02E03/E09/E10) with nothing on screen to explain any of them.
# The drain already judges every candidate and then discards the verdicts — this
# keeps the useful one.

def test_the_refusal_is_stored_on_the_row(db):
    """Real DB write through the real recorder, not a mock of it."""
    _seed_movie(db, tmdb_id=501, title="Only In 720p")
    db.record_wishlist_search_outcome(
        "movie", 501, False,
        refusal="Best found: 720p WEB — 720p WEB isn't in your enabled tiers (12 releases)",
        refusal_quality="720p WEB")
    row = db._get_connection().execute(
        "SELECT search_attempts, last_refusal, last_refusal_quality "
        "FROM video_wishlist WHERE tmdb_id=501").fetchone()
    assert row["search_attempts"] == 1
    assert "720p WEB" in row["last_refusal"] and "enabled tiers" in row["last_refusal"]
    assert row["last_refusal_quality"] == "720p WEB"


def test_a_grab_clears_the_receipt(db):
    """Leaving it would explain a row that is no longer stuck."""
    _seed_movie(db, tmdb_id=502, title="Landed Eventually")
    db.record_wishlist_search_outcome("movie", 502, False, refusal="Best found: 720p — nope",
                                      refusal_quality="720p")
    db.record_wishlist_search_outcome("movie", 502, True)
    row = db._get_connection().execute(
        "SELECT search_attempts, last_refusal, last_refusal_quality "
        "FROM video_wishlist WHERE tmdb_id=502").fetchone()
    assert row["search_attempts"] == 0
    assert row["last_refusal"] is None and row["last_refusal_quality"] is None


def test_a_search_that_finds_nothing_clears_a_stale_explanation(db):
    """Last week's "best found: 720p" must not stand over a search that found
    nothing at all — it would describe a release no longer on offer."""
    _seed_movie(db, tmdb_id=503, title="Vanished")
    db.record_wishlist_search_outcome("movie", 503, False, refusal="Best found: 720p — nope",
                                      refusal_quality="720p")
    db.record_wishlist_search_outcome("movie", 503, False)          # nothing found this time
    row = db._get_connection().execute(
        "SELECT search_attempts, last_refusal FROM video_wishlist WHERE tmdb_id=503").fetchone()
    assert row["search_attempts"] == 2, "it is still a fruitless search"
    assert row["last_refusal"] is None


def test_an_episode_row_records_against_its_own_episode(db):
    """The WHERE clause carries season+episode; without it one episode's receipt
    would land on every episode of the show."""
    _seed_episode(db, tmdb_id=601, s=2, e=3)
    _seed_episode(db, tmdb_id=601, s=2, e=9)
    db.record_wishlist_search_outcome("episode", 601, False, season_number=2, episode_number=3,
                                      refusal="Best found: 720p — not in your tiers",
                                      refusal_quality="720p")
    rows = {(r["season_number"], r["episode_number"]): r["last_refusal"] for r in
            db._get_connection().execute(
                "SELECT season_number, episode_number, last_refusal FROM video_wishlist "
                "WHERE tmdb_id=601")}
    assert rows[(2, 3)] and "720p" in rows[(2, 3)]
    assert rows[(2, 9)] is None, "the other episode must be untouched"


def test_the_wishlist_listing_returns_the_receipt(db):
    """It has to reach the page, not just the table."""
    _seed_movie(db, tmdb_id=504, title="Shown To The User")
    db.record_wishlist_search_outcome("movie", 504, False,
                                      refusal="Best found: 720p WEB — not in your tiers",
                                      refusal_quality="720p WEB")
    page = db.query_wishlist("movie")
    item = [i for i in page["items"] if i["tmdb_id"] == 504][0]
    assert "720p WEB" in (item.get("last_refusal") or "")
    assert item.get("last_refusal_quality") == "720p WEB"


def test_the_columns_ride_the_migration_list():
    """Live installs upgrade in place — a column only in CREATE TABLE would be
    missing on every existing database."""
    src = (_ROOT / "database" / "video_database.py").read_text(encoding="utf-8")
    assert '("video_wishlist", "last_refusal", "TEXT")' in src
    assert '("video_wishlist", "last_refusal_quality", "TEXT")' in src



def test_reset_wishlist_search_state_clears_one_episode(db):
    _seed_episode(db, tmdb_id=801, s=1, e=1)
    _seed_episode(db, tmdb_id=801, s=1, e=2)
    db.record_wishlist_search_outcome("episode", 801, False, season_number=1, episode_number=2,
                                      refusal="Best found: nope", refusal_quality="SD",
                                      snapshot={"chain": ["torrent"], "sources": {}})
    assert db.reset_wishlist_search_state("episode", 801, season_number=1, episode_number=2) == 1
    rows = {(r["season_number"], r["episode_number"]): r for r in db._get_connection().execute(
        "SELECT season_number, episode_number, search_attempts, last_refusal, search_snapshot "
        "FROM video_wishlist WHERE tmdb_id=801")}
    assert rows[(1, 2)]["search_attempts"] == 0 and rows[(1, 2)]["last_refusal"] is None
    assert rows[(1, 1)]["search_attempts"] == 0 and rows[(1, 1)]["last_refusal"] is None


def test_search_note_updates_reason_without_incrementing_attempts(db):
    _seed_movie(db, tmdb_id=701, title="Client Stuck")
    db.record_wishlist_search_outcome("movie", 701, False)
    db.record_wishlist_search_note("movie", 701, "Download client refused: already queued",
                                   refusal_quality="WEBDL-1080p")
    row = db._get_connection().execute(
        "SELECT search_attempts, last_refusal, last_refusal_quality, last_search_at "
        "FROM video_wishlist WHERE tmdb_id=701").fetchone()
    assert row["search_attempts"] == 1
    assert row["last_refusal"] == "Download client refused: already queued"
    assert row["last_refusal_quality"] == "WEBDL-1080p"
    assert row["last_search_at"]


def test_episode_search_note_targets_one_episode(db):
    _seed_episode(db, tmdb_id=702, s=1, e=1)
    _seed_episode(db, tmdb_id=702, s=1, e=2)
    db.record_wishlist_search_note("episode", 702, "Download client refused: no files",
                                   season_number=1, episode_number=2)
    rows = {(r["season_number"], r["episode_number"]): r["last_refusal"] for r in
            db._get_connection().execute(
                "SELECT season_number, episode_number, last_refusal FROM video_wishlist "
                "WHERE tmdb_id=702")}
    assert rows[(1, 1)] is None
    assert rows[(1, 2)] == "Download client refused: no files"
