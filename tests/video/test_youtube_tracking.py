"""YouTube library tracking: the Channels tab query (real ownership from the
permanent download history) and the channel detail's true downloaded flag —
'owned' means ON DISK, not merely wished."""

from __future__ import annotations

import json

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _follow(db, cid, title, avatar="https://yt/av.jpg"):
    assert db.add_channel_to_watchlist({"youtube_id": cid, "title": title,
                                        "avatar_url": avatar})


def _downloaded(db, cid, vid, dl_id):
    db.record_download_history({
        "id": dl_id, "kind": "youtube", "source": "youtube", "media_source": "youtube",
        "media_id": vid, "title": "T", "status": "completed", "dest_path": f"/yt/{vid}.mp4",
        "search_ctx": json.dumps({"channel_id": cid})})


# ── the Channels tab query ───────────────────────────────────────────────────
def test_query_channel_library_counts_and_search(db):
    _follow(db, "UC1", "Kurzgesagt")
    _follow(db, "UC2", "Veritasium")
    db.cache_channel_videos("UC1", [{"youtube_id": "v%d" % i, "title": "V%d" % i}
                                    for i in range(1, 5)])
    _downloaded(db, "UC1", "v1", 1)
    _downloaded(db, "UC1", "v2", 2)
    got = db.query_channel_library()
    assert got["pagination"]["total_count"] == 2
    by = {c["id"]: c for c in got["items"]}
    assert by["UC1"]["owned_count"] == 2 and by["UC1"]["video_count"] == 4
    assert by["UC2"]["owned_count"] == 0
    assert by["UC1"]["kind"] == "channel" and by["UC1"]["poster_url"]
    got = db.query_channel_library(search="kurz")
    assert [c["id"] for c in got["items"]] == ["UC1"]
    got = db.query_channel_library(letter="V")
    assert [c["id"] for c in got["items"]] == ["UC2"]
    assert db.query_channel_library(page=2, limit=1)["pagination"]["has_prev"]


def test_query_channel_library_includes_unfollowed_with_downloads(db):
    """A library shows what you OWN: a one-off download from an unfollowed
    channel appears (title/avatar from the meta cache), marked not-followed.
    Muted follows with no downloads stay hidden."""
    _follow(db, "UC1", "Followed")
    conn = db._get_connection()
    conn.execute("UPDATE video_watchlist SET state='mute' WHERE source_id='UC1'")
    conn.commit(); conn.close()
    assert db.query_channel_library()["pagination"]["total_count"] == 0
    _downloaded(db, "UCslomo", "v1", 1)
    db.cache_channel_meta("UCslomo", {"title": "The Slow Mo Guys",
                                      "avatar_url": "https://yt/slomo.jpg"})
    got = db.query_channel_library()
    assert got["pagination"]["total_count"] == 1
    c = got["items"][0]
    assert c["id"] == "UCslomo" and c["title"] == "The Slow Mo Guys"
    assert c["poster_url"] == "https://yt/slomo.jpg"
    assert c["followed"] is False and c["owned_count"] == 1
    # No meta cached → the id stands in for the title rather than hiding the row.
    _downloaded(db, "UCmystery", "v2", 2)
    titles = {c["id"]: c["title"] for c in db.query_channel_library()["items"]}
    assert titles["UCmystery"] == "UCmystery"


# ── API: /api/video/library?kind=channels ────────────────────────────────────
def test_library_api_channels_kind(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db = videoapi._video_db
    _follow(db, "UC1", "Kurzgesagt")
    _downloaded(db, "UC1", "v1", 1)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        d = app.test_client().get("/api/video/library?kind=channels").get_json()
        assert d["pagination"]["total_count"] == 1
        assert d["items"][0] == {"kind": "channel", "id": "UC1", "title": "Kurzgesagt",
                                 "poster_url": "https://yt/av.jpg", "followed": True,
                                 "video_count": 0, "owned_count": 1}
    finally:
        videoapi._video_db = None


# ── manual wish of a downloaded video (the dead-button bug) ──────────────────
def test_manual_wish_readds_a_downloaded_video(tmp_path):
    """add_videos_to_wishlist silently skips downloaded videos (right for bulk
    re-follows, a dead button for a deliberate click). The manual endpoint now
    passes allow_downloaded=True and reports honest success."""
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db = videoapi._video_db
    _downloaded(db, "UC1", "vGot", 1)
    ch = {"youtube_id": "UC1", "title": "Kurzgesagt"}
    vid = [{"youtube_id": "vGot", "title": "Get it again"}]
    # Bulk semantics unchanged: the default still refuses a downloaded video.
    assert db.add_videos_to_wishlist(ch, vid) == 0
    assert db.add_videos_to_wishlist(ch, vid, allow_downloaded=True) == 1
    db.remove_youtube_from_wishlist("video", "vGot")
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().post("/api/video/youtube/wishlist/add",
                                   json={"channel": ch, "videos": vid})
        body = r.get_json()
        assert body["success"] is True and body["added"] == 1
        # And a genuinely bad payload reports failure instead of a silent 200.
        r = app.test_client().post("/api/video/youtube/wishlist/add",
                                   json={"channel": ch, "videos": [{"title": "no id"}]})
        assert r.get_json()["success"] is False
    finally:
        videoapi._video_db = None


# ── channel detail: true downloaded flags (cache-hit path, no network) ───────
def test_channel_detail_marks_downloaded_vs_wished(tmp_path, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db = videoapi._video_db
    db.cache_channel_meta("UC1", {"title": "Kurzgesagt", "avatar_url": "https://yt/av.jpg"})
    db.cache_channel_videos("UC1", [
        {"youtube_id": "vGot", "title": "Downloaded one"},
        {"youtube_id": "vWish", "title": "Wished one"},
        {"youtube_id": "vNew", "title": "Neither"}])
    _downloaded(db, "UC1", "vGot", 1)
    db.add_videos_to_wishlist({"youtube_id": "UC1", "title": "Kurzgesagt"},
                              [{"youtube_id": "vWish", "title": "Wished one"}])

    class _StubEnricher:
        def enqueue(self, *a, **k):
            pass

    monkeypatch.setattr("core.video.youtube_enrichment.get_youtube_date_enricher",
                        lambda: _StubEnricher())
    # Belt AND suspenders: this test is about the cache-first path, so a cache
    # miss is a bug — fail loudly on the flag instead of KeyError'ing on the 404
    # body, and make sure even that bug path can never reach the real YouTube.
    monkeypatch.setattr("core.video.youtube.resolve_channel", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        d = app.test_client().get("/api/video/youtube/channel/UC1").get_json()
        assert d.get("from_cache") is True, f"expected cache-first hit, got: {d}"
        vids = {v["youtube_id"]: v for v in d["channel"]["videos"]}
        assert vids["vGot"]["downloaded"] is True and vids["vGot"]["wished"] is False
        assert vids["vWish"]["downloaded"] is False and vids["vWish"]["wished"] is True
        assert vids["vNew"]["downloaded"] is False and vids["vNew"]["wished"] is False
    finally:
        videoapi._video_db = None
