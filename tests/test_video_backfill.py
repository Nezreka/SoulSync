"""Seam tests for the video BACKFILL workers (artwork / subtitles / no-key
YouTube extras). The network fetch is stubbed so the loop/queue/record/status
logic is tested without hitting fanart.tv / OpenSubtitles / RYD / SponsorBlock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from database.video_database import VideoDatabase
from core.video.enrichment.backfill import (
    RydWorker, SponsorBlockWorker, FanartWorker, OpenSubtitlesWorker,
    VideoBackfillWorker, _RateLimited, _Unauthorized, build_backfill_workers,
)


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── DB seams ──────────────────────────────────────────────────────────────────
def test_youtube_enrich_queue_apply_breakdown(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "a", "title": "A"},
                                    {"youtube_id": "b", "title": "B"}])
    assert db.youtube_enrich_breakdown("ryd_status")["video"]["pending"] == 2
    nxt = db.youtube_enrich_next("ryd_status")
    assert nxt["youtube_id"] in ("a", "b") and nxt["kind"] == "video"

    db.apply_youtube_votes("a", 100, 5, "ok")
    db.apply_youtube_votes("b", None, None, "not_found")
    bd = db.youtube_enrich_breakdown("ryd_status")["video"]
    assert bd == {"matched": 1, "not_found": 1, "errors": 0, "pending": 0}
    # likes/dislikes merge onto the cached catalog on read
    vids = {v["youtube_id"]: v for v in db.get_channel_videos("UC1")}
    assert vids["a"]["like_count"] == 100 and vids["a"]["dislike_count"] == 5


def test_youtube_enrich_shared_video_counted_once(db):
    # Same video cached under two channels/playlists → one enrichment unit.
    db.cache_channel_videos("UC1", [{"youtube_id": "x", "title": "X"}])
    db.cache_channel_videos("PLfoo", [{"youtube_id": "x", "title": "X"}])
    assert db.youtube_enrich_breakdown("sb_status")["video"]["pending"] == 1


def test_youtube_segments_stored_and_read(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "v", "title": "V"}])
    segs = [{"category": "sponsor", "start_sec": 10.0, "end_sec": 20.0, "votes": 3, "uuid": "u1"}]
    db.apply_youtube_segments("v", segs, "ok")
    got = db.youtube_video_segments("v")
    assert got == [{"category": "sponsor", "start_sec": 10.0, "end_sec": 20.0, "votes": 3}]


def test_backfill_next_mark_breakdown(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Fight Club", "year": 1999})
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_id=550, imdb_id='tt0137523' WHERE id=?", (mid,))
        c.commit()
    nxt = db.backfill_next("fanart")
    assert nxt["kind"] == "movie" and nxt["tmdb_id"] == 550

    db.backfill_mark("fanart", "movie", mid, "ok",
                     columns={"logo_url": "http://x/l.png", "bogus_col": "nope"})
    with db.connect() as c:
        r = c.execute("SELECT logo_url, fanart_status FROM movies WHERE id=?", (mid,)).fetchone()
    assert r["logo_url"] == "http://x/l.png" and r["fanart_status"] == "ok"   # whitelist drops bogus_col
    assert db.backfill_breakdown("fanart")["movie"]["matched"] == 1


def test_backfill_mark_never_clobbers(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_id=1, logo_url='SERVER_LOGO' WHERE id=?", (mid,))
        c.commit()
    db.backfill_mark("fanart", "movie", mid, "ok", columns={"logo_url": "FANART_LOGO"})
    with db.connect() as c:
        r = c.execute("SELECT logo_url FROM movies WHERE id=?", (mid,)).fetchone()
    assert r["logo_url"] == "SERVER_LOGO"   # gap-fill only


def test_backfill_show_requires_tvdb_id(db):
    with db.connect() as c:
        c.execute("INSERT INTO shows (title, year) VALUES ('S', 2008)")
        sid = c.execute("SELECT id FROM shows WHERE title='S'").fetchone()["id"]
        c.commit()
    # No tvdb_id → fanart has nothing to key on, so it's not queued.
    assert db.backfill_next("fanart") is None
    with db.connect() as c:
        c.execute("UPDATE shows SET tvdb_id=81189 WHERE id=?", (sid,))
        c.commit()
    assert db.backfill_next("fanart")["kind"] == "show"


# ── worker loop seams (stubbed fetch) ─────────────────────────────────────────
def _seed_video(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "v", "title": "V"}])


def test_ryd_worker_records_each_outcome(db):
    _seed_video(db)
    w = RydWorker(db)
    w.fetch = lambda item: {"likes": 9, "dislikes": 2}
    assert w.process_one() is True
    assert db.youtube_enrich_breakdown("ryd_status")["video"]["matched"] == 1
    assert w.stats["matched"] == 1


def test_ryd_worker_empty_marks_not_found(db):
    _seed_video(db)
    w = RydWorker(db)
    w.fetch = lambda item: None
    w.process_one()
    assert db.youtube_enrich_breakdown("ryd_status")["video"]["not_found"] == 1


def test_worker_call_error_records_error_not_notfound(db):
    _seed_video(db)
    w = SponsorBlockWorker(db)

    def boom(item):
        raise RuntimeError("network")
    w.fetch = boom
    w.process_one()
    bd = db.youtube_enrich_breakdown("sb_status")["video"]
    assert bd["errors"] == 1 and bd["not_found"] == 0
    assert w.stats["errors"] == 1


def test_rate_limit_sets_cooldown_and_pauses_pending(db):
    _seed_video(db)
    w = SponsorBlockWorker(db)

    def limited(item):
        raise _RateLimited(30)
    w.fetch = limited
    assert w.process_one() is False           # not consumed; will retry
    assert w._cooldown_until > 0
    assert w.get_stats()["cooldown"] is True


def test_unauthorized_pauses_worker(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_id=1 WHERE id=?", (mid,))
        c.commit()
    db.set_setting("fanart_api_key", "KEY")
    w = FanartWorker(db)

    def denied(item):
        raise _Unauthorized()
    w.fetch = denied
    w.process_one()
    assert w.paused is True and "key" in (w.note or "").lower()


def test_key_gated_workers_disabled_without_key(db):
    assert FanartWorker(db).enabled is False
    assert OpenSubtitlesWorker(db).enabled is False
    db.set_setting("fanart_api_key", "KEY")
    assert FanartWorker(db).enabled is True


def test_nokey_workers_enabled_by_default_and_toggle(db):
    assert RydWorker(db).enabled is True
    db.set_setting("ryd_enabled", "0")
    assert RydWorker(db).enabled is False


def test_get_stats_shape_matches_matcher_worker(db):
    _seed_video(db)
    stats = RydWorker(db).get_stats()
    assert set(stats) == {"enabled", "running", "paused", "idle", "current_item",
                          "note", "cooldown", "stats", "progress", "breakdown"}
    assert set(stats["stats"]) == {"matched", "not_found", "errors", "pending"}


def test_build_backfill_workers_set(db):
    assert set(build_backfill_workers(db)) == {"ryd", "sponsorblock", "fanart", "opensubtitles"}


def test_backfill_module_imports_nothing_from_music():
    path = Path(__file__).resolve().parent.parent / "core" / "video" / "enrichment" / "backfill.py"
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            assert "music" not in s.lower(), f"music import leaked: {s!r}"
