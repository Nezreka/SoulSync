"""Which release ended up downloading after one failed (SeadogsBooty).

the next-release retry grabs a new download row; the card that grabbed the
failed one kept saying Failed with no word of the replacement. the status
poll now returns the downloads that followed it for the same item.
"""

import json

import pytest

from core.video import download_monitor as monitor
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _row(db, release, *, episode=2, status="failed", media_id="1399", media_source="tmdb",
         scope="episode", season=1):
    ctx = {"scope": scope, "title": "Show", "season": season}
    if episode is not None:
        ctx["episode"] = episode
    dl_id = db.add_video_download({
        "kind": "episode" if scope == "episode" else "show", "title": "Show", "status": status,
        "source": "usenet", "username": "NZBgeek", "filename": release, "release_title": release,
        "media_id": media_id, "media_source": media_source, "search_ctx": json.dumps(ctx),
        "candidates": "[]", "tried_files": "[]", "tried_queries": "[]"})
    return db.get_video_download(dl_id)


def test_the_replacements_follow_the_failed_release_in_order(db):
    first = _row(db, "Show.S01E02.1080p.HEVC-MeGusta")
    _row(db, "Show.S01E03.1080p-MeGusta", episode=3)                 # another episode
    _row(db, "Show.S01E02.1080p-MeGusta.v2")                          # the retry
    _row(db, "Other.S01E02.1080p", media_id="555")                    # another show
    _row(db, "Show.S01E02.720p-MeGusta", status="completed")         # the one that worked
    chain = monitor.retry_chain(db, first)
    assert [c["release_title"] for c in chain] == [
        "Show.S01E02.1080p-MeGusta.v2", "Show.S01E02.720p-MeGusta"]
    assert chain[-1]["status"] == "completed"


def test_a_modal_grab_under_the_library_id_still_finds_its_retry(db, monkeypatch):
    # the modal grabbed under the library's show id, the retry under tmdb's
    monkeypatch.setattr(db, "show_tmdb_id", lambda library_id: 1399 if str(library_id) == "77" else None)
    first = _row(db, "Show.S01E02.1080p-MeGusta", media_id="77", media_source="library")
    _row(db, "Show.S01E02.720p-MeGusta", status="downloading")
    assert [c["release_title"] for c in monitor.retry_chain(db, first)] == ["Show.S01E02.720p-MeGusta"]


def test_a_season_pack_covering_the_episode_counts(db):
    first = _row(db, "Show.S01E02.1080p-MeGusta")
    _row(db, "Show.S01.1080p-MeGusta", episode=None, scope="season", status="downloading")
    _row(db, "Show.S02.1080p-MeGusta", episode=None, scope="season", season=2)
    assert [c["release_title"] for c in monitor.retry_chain(db, first)] == ["Show.S01.1080p-MeGusta"]


def test_the_status_poll_adds_the_chain_only_for_a_failed_row(db, monkeypatch):
    from flask import Flask

    import api.video as videoapi
    monkeypatch.setattr(monitor, "ensure_started", lambda *a, **k: None)
    monkeypatch.setattr(videoapi, "_video_db", db)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    first = _row(db, "Show.S01E02.1080p-MeGusta")
    _row(db, "Show.S01E02.720p-MeGusta", status="completed")
    live = _row(db, "Show.S01E04.1080p", episode=4, status="downloading")
    body = client.get("/api/video/downloads/status?chain=1&id=%d" % first["id"]).get_json()
    assert [c["release_title"] for c in body["chain"]] == ["Show.S01E02.720p-MeGusta"]
    assert "chain" not in client.get("/api/video/downloads/status?chain=1&id=%d" % live["id"]).get_json()
    assert "chain" not in client.get("/api/video/downloads/status?id=%d" % first["id"]).get_json()


def test_the_card_and_the_episode_row_follow_the_chain():
    # jsdom can't run this vanilla view; the wiring is what regressed before
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "webui" / "static" / "video"
          / "video-download-view.js").read_text(encoding="utf-8")
    poll = js[js.index("function _trackPoll"):js.index("function _trackPoll") + 3000]
    assert "status?id=" in poll and "'&chain=1'" in poll and "_renderChain(card, foot, chain)" in poll
    ep = js[js.index("function epTrack"):js.index("function epTrack") + 2000]
    assert "status?id=" in ep and "'&chain=1'" in ep and "chain[chain.length - 1]" in ep
