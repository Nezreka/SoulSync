"""Manual / failed-import resolution endpoints — list the unplaced queue, force-place
a file to a chosen identity, and dismiss. Uses real temp files (the place flow runs
the real importer against disk)."""

from __future__ import annotations

import os

import pytest
from flask import Flask

import api.video as videoapi
from core.video import organization
from database.video_database import VideoDatabase


@pytest.fixture()
def env(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    movies = tmp_path / "Movies"
    movies.mkdir()
    db.set_setting("movies_path", str(movies))
    db.set_setting("tv_path", str(tmp_path / "TV"))
    organization.save(db, {"verify_with_ffprobe": False})   # don't depend on ffprobe in CI

    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    src = dl_dir / "the.matrix.1999.1080p.bluray.x265.mkv"
    src.write_bytes(b"x" * 4096)

    dl_id = db.add_video_download({
        "kind": "movie", "title": "the matrix", "release_title": src.name,
        "source": "soulseek", "username": "neo", "filename": src.name,
        "size_bytes": 4096, "target_dir": str(movies), "status": "import_failed",
        "search_ctx": "{}",
    })
    db.update_video_download(dl_id, dest_path=str(src), error="Looks like a sample, not the feature")

    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        yield {"client": client, "db": db, "dl_id": dl_id, "src": src, "movies": movies}
    finally:
        videoapi._video_db = None


def test_failed_list_surfaces_unplaced_downloads(env):
    items = env["client"].get("/api/video/import/failed").get_json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["id"] == env["dl_id"]
    assert it["file"] == str(env["src"])               # points at the unplaced file
    assert "sample" in it["reason"].lower()


def test_place_force_imports_to_chosen_identity(env):
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"],
                           json={"scope": "movie", "title": "The Matrix", "year": 1999}).get_json()
    assert r["success"] and r["status"] == "completed"
    final = env["movies"] / "The Matrix (1999)" / "The Matrix (1999) Bluray-1080p.mkv"
    assert final.exists()                              # filed under the standard layout
    assert not env["src"].exists()                     # source reclaimed (copy mode, non-torrent)
    # the row is no longer in the failed queue
    assert env["client"].get("/api/video/import/failed").get_json()["items"] == []


def test_place_triggers_a_library_refresh(env):
    # a successful manual place fires the same batch-complete refresh the auto path
    # uses, so the title shows up without waiting for a scheduled scan.
    from core.video import download_events
    fired = []
    download_events.register_batch_complete_callback(lambda d: fired.append(d))
    try:
        env["client"].post("/api/video/import/%d/place" % env["dl_id"],
                           json={"scope": "movie", "title": "The Matrix", "year": 1999})
        assert fired and fired[-1].get("manual") is True
    finally:
        download_events._reset_for_tests()


def test_media_ids_resolves_tmdb_and_library_regrabs():
    from core.video.download_monitor import _media_ids
    # grabbed straight from TMDB → media_id is the tmdb id
    assert _media_ids(None, {"media_source": "tmdb", "media_id": "603"}) == (603, None)

    # owned re-grab → media_id is the LIBRARY id; resolve via the library row
    class _DB:
        def media_tmdb_id(self, kind, mid):
            assert kind == "movie" and mid == "5107"
            return (936075, "tt11378946")
    assert _media_ids(_DB(), {"media_source": "library", "media_id": "5107", "kind": "movie"}) \
        == (936075, "tt11378946")

    assert _media_ids(None, {}) == (None, None)            # unresolvable → no sidecars


def test_dismiss_does_not_trigger_a_refresh(env):
    from core.video import download_events
    fired = []
    download_events.register_batch_complete_callback(lambda d: fired.append(d))
    try:
        env["client"].post("/api/video/import/%d/dismiss" % env["dl_id"], json={})
        assert fired == []                             # nothing landed → no scan
    finally:
        download_events._reset_for_tests()


def test_place_rejects_bad_scope(env):
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json={"scope": "season"})
    assert r.status_code == 400


def test_dismiss_drops_row_and_can_delete_file(env):
    r = env["client"].post("/api/video/import/%d/dismiss" % env["dl_id"],
                           json={"delete_file": True}).get_json()
    assert r["success"]
    assert not env["src"].exists()                     # file removed
    assert env["client"].get("/api/video/import/failed").get_json()["items"] == []
