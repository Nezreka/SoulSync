"""Seam tests for the background YouTube date enricher."""

from pathlib import Path

import pytest

from core.video.youtube_enrichment import YoutubeDateEnricher
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_enrich_remembers_catalog_meta_and_dates(db, monkeypatch):
    import core.video.youtube as yt
    # InnerTube catalog (primary) covers everything → per-video fallback must NOT run
    monkeypatch.setattr(yt, "innertube_channel_catalog", lambda cid, *a, **k: [
        {"youtube_id": "v1", "title": "A", "thumbnail_url": "t1", "published_at": "2024-06-01"},
        {"youtube_id": "v2", "title": "B", "thumbnail_url": "t2", "published_at": "2023-02-02"}])
    monkeypatch.setattr(yt, "resolve_channel", lambda url, **k: {"youtube_id": "UCx", "title": "X", "avatar_url": "a.jpg"})
    monkeypatch.setattr(yt, "video_detail", lambda vid: (_ for _ in ()).throw(AssertionError("no fallback")))
    db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "X"}, [{"youtube_id": "v1", "title": "A"}])

    e = YoutubeDateEnricher(db_factory=lambda: db)
    e._enrich("UCx")
    assert db.get_video_dates(["v1", "v2"]) == {"v1": "2024-06-01", "v2": "2023-02-02"}
    # the LIST + METADATA got remembered, not just the dates
    remembered = db.get_channel_videos("UCx")
    assert {v["youtube_id"] for v in remembered} == {"v1", "v2"}
    assert db.get_channel_meta("UCx")["avatar_url"] == "a.jpg"
    assert db.channel_dates_enriched_recently("UCx") is True
    # already enriched → second pass is a no-op (no fetch)
    monkeypatch.setattr(yt, "innertube_channel_catalog", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-swept")))
    e._enrich("UCx")


def test_enrich_falls_back_to_per_video_when_bulk_empty(db, monkeypatch):
    import core.video.youtube as yt
    monkeypatch.setattr(yt, "innertube_channel_catalog", lambda cid, *a, **k: [])  # InnerTube empty
    monkeypatch.setattr(yt, "proxy_channel_dates", lambda cid, *a, **k: {})        # no proxy either
    # flat resolve adds a recent upload r1 to the date-fallback set (besides wished w1/w2)
    monkeypatch.setattr(yt, "resolve_channel",
                        lambda url, **k: {"youtube_id": "UCx", "videos": [{"youtube_id": "r1", "title": "Recent"}]})
    monkeypatch.setattr(yt, "video_detail",
                        lambda vid: {"youtube_id": vid, "published_at": "2022-03-03"} if vid in ("w1", "r1") else None)
    import core.video.youtube_enrichment as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)   # no real throttle delay in tests
    db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "X"},
                              [{"youtube_id": "w1", "title": "A"}, {"youtube_id": "w2", "title": "B"}])

    e = YoutubeDateEnricher(db_factory=lambda: db)
    e._enrich("UCx")
    # w1 (wished) + r1 (recent upload from flat resolve) got dates; w2 had none
    assert db.get_video_dates(["w1", "w2", "r1"]) == {"w1": "2022-03-03", "r1": "2022-03-03"}
    assert db.channel_dates_enriched_recently("UCx") is True


def test_enricher_imports_nothing_from_music():
    src = Path("core/video/youtube_enrichment.py").read_text(encoding="utf-8")
    assert "database.music_database" not in src and "from database import" not in src


def test_enricher_stats_shape_and_pause():
    e = YoutubeDateEnricher(db_factory=lambda: None)
    s = e.stats()
    assert s["enabled"] is True and s["running"] is False and s["paused"] is False
    assert s["current_item"] is None and "progress" in s
    e.pause(); assert e.stats()["paused"] is True
    e.resume(); assert e.stats()["paused"] is False


def test_proxy_instances_setting_parsing(db):
    e = YoutubeDateEnricher(db_factory=lambda: db)
    assert e._proxy_instances(db) == []                       # unset → proxy off by default
    db.set_setting("youtube_proxy_instances",
                   "piped|https://a.test, https://invidious.b.test/, junk, https://c.test")
    got = e._proxy_instances(db)
    assert ("piped", "https://a.test") in got
    assert ("invidious", "https://invidious.b.test") in got   # kind inferred + trailing / stripped
    assert ("piped", "https://c.test") in got
    assert all(u.startswith("http") for _, u in got)          # 'junk' dropped


def test_dearrow_retry_requeues_failed_youtube_videos(db):
    # Regression: enrichment_retry only handled ryd/sponsorblock, so DeArrow's
    # Retry button was a silent no-op. It must re-queue failed dearrow rows.
    conn = db._get_connection()
    conn.execute("INSERT INTO youtube_video_stats (youtube_id, dearrow_status) VALUES ('a', 'not_found')")
    conn.execute("INSERT INTO youtube_video_stats (youtube_id, dearrow_status) VALUES ('b', 'error')")
    conn.execute("INSERT INTO youtube_video_stats (youtube_id, dearrow_status) VALUES ('c', 'ok')")
    conn.commit(); conn.close()

    n = db.enrichment_retry("dearrow", "video", scope="failed")
    assert n == 2                                      # the not_found + error rows
    conn = db._get_connection()
    rows = dict(conn.execute("SELECT youtube_id, dearrow_status FROM youtube_video_stats").fetchall())
    conn.close()
    assert rows["a"] is None and rows["b"] is None     # re-queued
    assert rows["c"] == "ok"                           # matched left untouched
