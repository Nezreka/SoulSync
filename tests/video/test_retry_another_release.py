"""'Block release and retry with another' actually retries with ANOTHER
(Boulder's report: blocklist POST 200, then /downloads/retry 502, no retry).

Root cause: the retry endpoint only knew ONE mode — re-grab the SAME release
via slskd — so the block-and-retry flow asked it to re-download the exact
release just blocked, and torrent grabs 502'd outright (no slskd peer behind
them). Now `next: true` (or any non-soulseek source, automatically) routes
through retry_another_release: still-active rows walk the ranked-candidate /
requery failure path; already-failed rows (the button's normal case — auto
retry ran dry) go straight to a fresh wishlist indexer search, where the
blocklist filters the just-blocked release and the next best gets grabbed.
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g
        g.profile_id = 1
        g.is_admin = True
        g.can_download = True

    searches = []
    import core.video.wishlist_search as ws
    monkeypatch.setattr(ws, "manual_search",
                        lambda scope, tmdb_id, season_number=None, episode_number=None:
                        searches.append((scope, tmdb_id, season_number, episode_number))
                        or {"queued": 1, "skipped": 0, "total": 1})
    # the monitor thread must not actually start in tests
    import core.video.download_monitor as mon
    monkeypatch.setattr(mon, "ensure_started", lambda *_a, **_k: None)
    import api.video.downloads  # noqa: F401 - route module resolves mon lazily

    try:
        yield app.test_client(), videoapi._video_db, searches
    finally:
        videoapi._video_db = None


def _failed_torrent_movie(db, tmdb_id=603):
    conn = db._get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO video_downloads (kind, title, status, source, username, filename, "
            " media_id, media_source, search_ctx, error) "
            "VALUES ('movie', 'The Matrix', 'failed', 'torrent', 'indexer', "
            " 'The.Matrix.1999.720p.x264-BAD.mkv', ?, 'tmdb', "
            " '{\"scope\": \"movie\", \"title\": \"The Matrix\", \"year\": 1999}', 'stalled')",
            (str(tmdb_id),))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_block_and_retry_kicks_a_fresh_search(client):
    http, db, searches = client
    dl_id = _failed_torrent_movie(db)
    res = http.post("/api/video/downloads/retry", json={"id": dl_id, "next": True})
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is True and body["mode"] == "next"
    assert body.get("wishlist_search") is True
    assert searches == [("movie", 603, None, None)]
    # the item is back on the wishlist (not silently lost)
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT title FROM video_wishlist WHERE kind='movie' "
                           "AND tmdb_id=603").fetchone()
    finally:
        conn.close()
    assert row is not None and row["title"] == "The Matrix"


def test_plain_retry_on_torrent_routes_to_next_not_502(client):
    """The old code 502'd (slskd can't re-grab a torrent release). Plain Retry
    on a non-soulseek source now routes to next-release automatically."""
    http, db, searches = client
    dl_id = _failed_torrent_movie(db, tmdb_id=604)
    res = http.post("/api/video/downloads/retry", json={"id": dl_id})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["mode"] == "next"
    assert searches and searches[0][1] == 604


def test_unknown_download_404(client):
    http, _db, _s = client
    assert http.post("/api/video/downloads/retry", json={"id": 9999}).status_code == 404
