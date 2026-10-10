"""Phase 2 DB: fetch history, daily quota, retryable queue, re-key, video info."""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── fetch history ───────────────────────────────────────────────────────────

def test_history_logs_and_reads_back(db):
    db.subtitle_log_fetch("download", 7, "en", "downloaded", provider="opensubtitles",
                          candidate_title="Movie.srt", score=120.5)
    db.subtitle_log_fetch("download", 7, "en", "miss", provider="")
    hist = db.subtitle_get_history("download", 7)
    assert len(hist) == 2
    assert hist[0]["outcome"] == "miss"          # newest first
    assert hist[1]["outcome"] == "downloaded"
    assert hist[1]["provider"] == "opensubtitles"
    assert hist[1]["score"] == 120.5
    assert hist[1]["candidate_title"] == "Movie.srt"


def test_history_never_raises_on_garbage(db):
    db.subtitle_log_fetch(None, "bogus", None, "miss")  # must not raise
    assert db.subtitle_get_history("nope", 999) == []


# ── quota ───────────────────────────────────────────────────────────────────

def test_quota_starts_at_zero_and_bumps(db):
    assert db.subtitle_quota_used("opensubtitles") == 0
    assert db.subtitle_quota_bump("opensubtitles") == 1
    assert db.subtitle_quota_bump("opensubtitles") == 2
    assert db.subtitle_quota_used("opensubtitles") == 2


def test_quota_is_per_provider(db):
    db.subtitle_quota_bump("opensubtitles")
    assert db.subtitle_quota_used("other") == 0


# ── retryable queue ─────────────────────────────────────────────────────────

def test_retryable_returns_wanted_and_failed(db):
    db.subtitle_want("download", 1, "en")
    db.subtitle_want("download", 2, "en")
    db.subtitle_mark("download", 2, "en", "failed")
    db.subtitle_want("download", 3, "en")
    db.subtitle_mark("download", 3, "en", "have")
    kinds = {(r["video_id"], r["status"]) for r in db.subtitle_get_retryable()}
    assert (1, "wanted") in kinds
    assert (2, "failed") in kinds
    assert not any(v == 3 for v, _ in kinds)


def test_retryable_oldest_first_and_limited(db):
    for i in range(5):
        db.subtitle_want("download", i, "en")
    rows = db.subtitle_get_retryable(limit=2)
    assert [r["video_id"] for r in rows] == [0, 1]


# ── backoff ────────────────────────────────────────────────────────────────

def test_backoff_doubles_and_caps():
    b = VideoDatabase._subtitle_backoff_hours
    assert b(0) == 1
    assert b(1) == 2
    assert b(2) == 4
    assert b(10) == 168
    assert b(100) == 168
    assert b("garbage") == 1


# ── re-key ──────────────────────────────────────────────────────────────────

def test_rekey_moves_download_rows_to_library(db):
    db.subtitle_want("download", 7, "en")
    db.subtitle_want("download", 7, "es")
    assert db.subtitle_rekey(7, "movie", 42) == 2
    rows = db.subtitle_get_for_video("movie", 42)
    assert {r["language"] for r in rows} == {"en", "es"}
    assert db.subtitle_get_for_video("download", 7) == []


def test_rekey_drops_duplicate_instead_of_violating_unique(db):
    db.subtitle_want("download", 7, "en")
    db.subtitle_want("movie", 42, "en")  # target already has en
    assert db.subtitle_rekey(7, "movie", 42) == 1
    rows = db.subtitle_get_for_video("movie", 42)
    assert len(rows) == 1  # no duplicate, no crash


def test_rekey_no_rows_is_zero(db):
    assert db.subtitle_rekey(999, "movie", 1) == 0


# ── video info ──────────────────────────────────────────────────────────────

def test_video_info_download_row(db):
    dl_id = db.add_video_download({
        "kind": "movie", "title": "T", "status": "completed",
        "media_id": "603", "media_source": "tmdb",
        "search_ctx": '{"season": 1, "episode": 2}',
    })
    db.update_video_download(dl_id, dest_path="/vids/movie.mkv")
    info = db.subtitle_video_info("download", dl_id)
    assert info["tmdb_id"] == 603
    assert info["season"] == 1 and info["episode"] == 2
    assert info["dest_path"] == "/vids/movie.mkv"


def test_video_info_missing_row_is_empty_dict(db):
    assert db.subtitle_video_info("movie", 999999) == {}
    assert db.subtitle_video_info("bogus", 1) == {}


# ── orphan cleanup ──────────────────────────────────────────────────────────

def test_source_exists_download_row(db):
    dl_id = db.add_video_download({"kind": "movie", "title": "T", "status": "completed"})
    assert db.subtitle_source_exists("download", dl_id) is True
    assert db.subtitle_source_exists("download", 999999) is False


def test_source_exists_movie_and_episode_rows(db):
    conn = db._get_connection()
    movie_id = conn.execute("INSERT INTO movies (title) VALUES ('M')").lastrowid
    show_id = conn.execute("INSERT INTO shows (title) VALUES ('S')").lastrowid
    season_id = conn.execute(
        "INSERT INTO seasons (show_id, season_number) VALUES (?, 1)", (show_id,)).lastrowid
    ep_id = conn.execute(
        "INSERT INTO episodes (show_id, season_id, season_number, episode_number, title)"
        " VALUES (?, ?, 1, 1, 'E')", (show_id, season_id)).lastrowid
    conn.commit()
    assert db.subtitle_source_exists("movie", movie_id) is True
    assert db.subtitle_source_exists("movie", 999999) is False
    assert db.subtitle_source_exists("episode", ep_id) is True
    assert db.subtitle_source_exists("episode", 999999) is False


def test_source_exists_unknown_kind_is_false(db):
    assert db.subtitle_source_exists("bogus", 1) is False
    assert db.subtitle_source_exists("download", "garbage") is False


def test_delete_for_video_removes_rows(db):
    db.subtitle_want("download", 7, "en")
    db.subtitle_want("download", 7, "es")
    db.subtitle_want("download", 8, "en")
    assert db.subtitle_delete_for_video("download", 7) == 2
    assert db.subtitle_get_for_video("download", 7) == []
    assert len(db.subtitle_get_for_video("download", 8)) == 1


# ── history retention ───────────────────────────────────────────────────────

def test_history_prunes_rows_older_than_90_days(db):
    db.subtitle_log_fetch("download", 7, "en", "miss")
    conn = db._get_connection()
    try:
        conn.execute(
            "UPDATE subtitle_fetch_history SET attempted_at = datetime('now', '-100 days')")
        conn.commit()
    finally:
        conn.close()
    db.subtitle_log_fetch("download", 7, "en", "miss")  # triggers the prune
    hist = db.subtitle_get_history("download", 7)
    assert len(hist) == 1  # the 100-day-old row is gone
