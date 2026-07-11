"""TV ↔ YouTube episode-row parity (Boulder: "treat them the same").

Backend: POST /api/video/youtube/download — the row-level direct download (the
TV grab's YouTube equivalent, minus the search: the video IS the release).
Reuses the wishlist drain's enqueue (same row shape + per-channel overrides)
and its concurrency model. Frontend contracts: the yt row carries the same
.vd-ep-get action cluster (grab + wish, NO manual search), the season bar
appears for channels too, and the row wish toggle can actually UNWISH (the old
'vd-yt-wish--on' check matched no real button class, so ✓ re-added forever).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_DETAIL_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), db
    finally:
        videoapi._video_db = None


def test_direct_download_requires_a_youtube_folder(client):
    c, _db = client
    r = c.post("/api/video/youtube/download", json={"video_id": "v1"})
    assert r.status_code == 400
    assert "YouTube library folder" in r.get_json()["error"]


def test_direct_download_enqueues_like_the_wishlist_drain(client, monkeypatch):
    """Same row shape as the automation's enqueue: kind/source youtube, ctx with
    channel identity, target_dir = the youtube root — and the pump is kicked."""
    import core.video.youtube_download as ytd
    c, db = client
    db.set_setting("youtube_path", "/media/youtube")
    started = []
    monkeypatch.setattr(ytd, "start_next_queued", lambda provider: started.append(1) or 7)

    r = c.post("/api/video/youtube/download", json={
        "video_id": "vid42", "channel_id": "UC9", "channel_title": "Veritasium",
        "video_title": "Big Misconception", "published_at": "2026-07-01",
        "thumbnail_url": "https://yt/t.jpg"})
    out = r.get_json()
    assert out["success"] is True and out["started"] is True and started == [1]

    rows = db.list_video_downloads()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "youtube" and row["source"] == "youtube"
    assert row["media_id"] == "vid42" and row["target_dir"] == "/media/youtube"
    ctx = json.loads(row["search_ctx"])
    assert ctx["channel"] == "Veritasium" and ctx["channel_id"] == "UC9"
    assert ctx["video_title"] == "Big Misconception"

    # idempotent: the active row blocks a duplicate
    r2 = c.post("/api/video/youtube/download", json={"video_id": "vid42"})
    out2 = r2.get_json()
    assert out2["success"] is True and out2.get("already") is True
    assert len(db.list_video_downloads()) == 1


def test_yt_rows_carry_the_tv_action_cluster():
    yt_row = _DETAIL_JS.split("function ytEpisodeRow")[1].split("function ytWishBtn")[0]
    assert "vd-ep-get" in yt_row                       # same cluster wrapper as TV
    assert "data-vd-ep-dl" in yt_row                   # live download state span
    assert "data-vd-yt-grab" in yt_row                 # direct download (TV's ⭳)
    assert "data-vd-yt-wish" in yt_row                 # wish toggle in getbtn chrome
    assert "data-vd-ep-search" not in yt_row           # NO manual search — no release to pick


def test_owned_yt_rows_keep_the_download_button():
    """A server-side delete leaves the ownership ledger intact — the sanctioned
    way back is downloading again, so owned rows keep ⭳ next to the badge
    (Boulder). The wish toggle stays missing-rows-only."""
    yt_row = _DETAIL_JS.split("function ytEpisodeRow")[1].split("function ytWishBtn")[0]
    # the ACTION ternary (the first "ep.owned ?" is the row-class one)
    owned_branch = yt_row.split("ep.owned ? '<div")[1].split(": '<div")[0]
    assert "data-vd-yt-grab" in owned_branch           # re-download affordance
    assert "vd-ep-badge" in owned_branch               # badge stays
    assert "data-vd-yt-wish" not in owned_branch       # no wish toggle on owned rows
    assert "data-vd-ep-dl" in owned_branch             # live tracking on re-grabs too


def test_channel_season_bar_matches_tv_minus_manual_search():
    bar = _DETAIL_JS.split("Season-level acquisition bar")[1].split("host.innerHTML = seasonBar")[0]
    assert "isYt" in bar
    assert "Grab ' + (isYt ? 'year' : 'season')" in bar
    assert "Wishlist ' + (isYt ? 'year' : 'season')" in bar
    assert "(isYt ? '' :" in bar                       # manual search hidden for channels
    assert "ytFilter.q" in bar                         # a filtered view isn't "the season"


def test_yt_download_tracking_keys_by_video_id():
    assert "data.source === 'youtube') ? String(en)" in _DETAIL_JS   # synthetic keys
    poll = _DETAIL_JS.split("function pollDl")[1].split("function applyDlStates")[0]
    assert "dl.kind !== 'youtube'" in poll             # yt branch matches by media_id
    assert "String(dl.media_id)" in poll
    assert "_dlTrackable" in _DETAIL_JS                # channels track too (kind 'channel')
    assert "data.kind === 'show' || data.kind === 'channel'" in _DETAIL_JS


def test_row_wish_toggle_can_actually_unwish():
    """The regression this parity pass surfaced: 'on' was read from a class no
    real button carries, so the remove path never ran."""
    tog = _DETAIL_JS.split("function toggleYtWish")[1].split("function toggleYtPlaylistFollowHero")[0]
    assert "classList.contains('watching')" in tog
    assert "classList.contains('vd-ep-wish--done')" in tog
    assert "contains('vd-yt-wish--on')" not in tog
    # and the row-chrome buttons get painted on toggle (no icon/text children)
    assert "vd-ep-wish--done', val" in tog
