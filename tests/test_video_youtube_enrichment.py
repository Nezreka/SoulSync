"""Seam tests for the background YouTube date enricher."""

from pathlib import Path

import pytest

from core.video.youtube_enrichment import YoutubeDateEnricher
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_enrich_caches_bulk_dates_and_marks_done(db, monkeypatch):
    import core.video.youtube as yt
    # InnerTube (primary) covers everything → per-video fallback must NOT run
    monkeypatch.setattr(yt, "innertube_channel_dates",
                        lambda cid, *a, **k: {"v1": "2024-06-01", "v2": "2023-02-02"})
    monkeypatch.setattr(yt, "video_detail", lambda vid: (_ for _ in ()).throw(AssertionError("no fallback")))
    db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "X"}, [{"youtube_id": "v1", "title": "A"}])

    e = YoutubeDateEnricher(db_factory=lambda: db)
    e._enrich("UCx")
    assert db.get_video_dates(["v1", "v2"]) == {"v1": "2024-06-01", "v2": "2023-02-02"}
    assert db.channel_dates_enriched_recently("UCx") is True
    # already enriched → second pass is a no-op (no fetch)
    monkeypatch.setattr(yt, "innertube_channel_dates", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-swept")))
    e._enrich("UCx")


def test_enrich_falls_back_to_per_video_when_bulk_empty(db, monkeypatch):
    import core.video.youtube as yt
    monkeypatch.setattr(yt, "innertube_channel_dates", lambda cid, *a, **k: {})   # InnerTube empty
    monkeypatch.setattr(yt, "proxy_channel_dates", lambda cid, *a, **k: {})       # no proxy either
    # flat resolve adds a recent upload r1 to the date-fallback set (besides wished w1/w2)
    monkeypatch.setattr(yt, "resolve_channel",
                        lambda url, **k: {"videos": [{"youtube_id": "r1", "title": "Recent"}]})
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
