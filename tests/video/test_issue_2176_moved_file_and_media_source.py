"""#2176: a torrent import failed with "No such file or directory" on a path in
qbittorrent's incomplete folder, and the download card showed a different show.

1. path: qbittorrent reports progress 1.0 while it is still MOVING the finished
   torrent out of its incomplete/temp folder. content_path still named the
   temp copy, the import started from it, the move pulled the file away and the
   row went import_failed for good, pointing at a path that no longer exists.
   Retry grabbed another release; manual import said the file was gone.
2. wrong show: the show page's grabs sent the LIBRARY show id as media_id with
   no media_source (a library payload has no ``source``). The downloads card
   read a missing media_source as TMDB, so library show 37 showed TMDB's 37.
"""

from __future__ import annotations

import pytest
from flask import Flask

import core.video.client_download as cd
from database.video_database import VideoDatabase


class _St:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _done(content_path, **kw):
    return _St(state="seeding", progress=1.0, content_path=content_path, **kw)


# ── 1. the path ───────────────────────────────────────────────────────────────
def test_qbittorrent_moving_state_is_reported():
    from core.torrent_clients.qbittorrent import QBittorrentAdapter
    a = QBittorrentAdapter.__new__(QBittorrentAdapter)
    moving = a._parse_status({"hash": "h", "state": "moving", "progress": 1.0,
                              "content_path": "/data/incomplete/x.mkv"})
    assert moving.moving is True
    assert a._parse_status({"hash": "h", "state": "stalledUP", "progress": 1.0}).moving is False


def test_waits_while_the_client_is_moving_the_file(tmp_path):
    temp = tmp_path / "incomplete" / "show.s13e30.mkv"
    temp.parent.mkdir()
    temp.write_bytes(b"x" * 10)
    calls = []
    upd = cd.process_client_download(
        {"id": 1, "client_ref": "h", "source": "torrent"},
        get_status=lambda s, r: _St(state="queued", progress=1.0, moving=True,
                                    content_path=str(temp)),
        resolve_path=lambda p: p, find_video=cd.find_video_file,
        organizer=lambda dl, src: calls.append(src) or {"status": "completed"})
    assert calls == []                       # nothing imported from the temp copy
    assert upd == {"progress": 100.0}        # keep polling, not terminal


def test_file_moved_mid_import_keeps_polling(tmp_path):
    temp = tmp_path / "incomplete" / "show.s13e30.mkv"
    temp.parent.mkdir()
    temp.write_bytes(b"x" * 10)

    def organizer(dl, src):
        # the client finishes its move while we copy: the source vanishes
        import os
        os.remove(src)
        return {"status": "import_failed", "progress": 100.0, "dest_path": src,
                "error": "Import failed: [Errno 2] No such file or directory: " + src}

    upd = cd.process_client_download(
        {"id": 1, "client_ref": "h", "source": "torrent"},
        get_status=lambda s, r: _done(str(temp)), resolve_path=lambda p: p,
        find_video=cd.find_video_file, organizer=organizer)
    assert upd.get("status") is None                  # not import_failed
    assert upd["progress"] == 100.0
    assert upd["dest_path"] is None                   # planned dest dropped


def test_next_tick_imports_from_where_the_client_moved_it(tmp_path):
    final = tmp_path / "complete" / "show.s13e30.mkv"
    final.parent.mkdir()
    final.write_bytes(b"x" * 10)
    seen = []
    cd.process_client_download(
        {"id": 1, "client_ref": "h", "source": "torrent"},
        get_status=lambda s, r: _done(str(final)), resolve_path=lambda p: p,
        find_video=cd.find_video_file,
        organizer=lambda dl, src: seen.append(src) or {"status": "completed", "dest_path": "/lib/x"})
    assert seen == [str(final)]


def test_a_real_import_failure_on_a_present_file_stays_terminal(tmp_path):
    f = tmp_path / "show.s13e30.mkv"
    f.write_bytes(b"x" * 10)
    upd = cd.process_client_download(
        {"id": 1, "client_ref": "h", "source": "torrent"},
        get_status=lambda s, r: _done(str(f)), resolve_path=lambda p: p,
        find_video=cd.find_video_file,
        organizer=lambda dl, src: {"status": "import_failed", "dest_path": src, "error": "sample"})
    assert upd["status"] == "import_failed"


def test_current_job_file_asks_the_client_again(tmp_path):
    final = tmp_path / "complete" / "show.s13e30.mkv"
    final.parent.mkdir()
    final.write_bytes(b"x" * 10)
    dl = {"source": "torrent", "client_ref": "h"}
    assert cd.current_job_file(dl, get_status=lambda s, r: _done(str(final)),
                               resolve_path=lambda p: p) == str(final)
    # still moving, or the client forgot it: no answer
    assert cd.current_job_file(dl, get_status=lambda s, r: _done(str(final), moving=True),
                               resolve_path=lambda p: p) is None
    assert cd.current_job_file(dl, get_status=lambda s, r: None) is None
    # a soulseek row has no client to ask
    assert cd.current_job_file({"source": "soulseek", "client_ref": "h"},
                               get_status=lambda s, r: _done(str(final))) is None


@pytest.fixture()
def api(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g
        g.profile_id = 1
        g.is_admin = True
        g.can_download = True

    import core.video.download_monitor as mon
    monkeypatch.setattr(mon, "ensure_started", lambda *_a, **_k: None)
    final = tmp_path / "complete" / "Last.Week.Tonight.S13E30.mkv"
    final.parent.mkdir()
    final.write_bytes(b"x" * 10)
    monkeypatch.setattr(cd, "_get_status", lambda s, r: _done(str(final)))
    monkeypatch.setattr(cd, "_resolve_path", lambda p: p)
    dl_id = db.add_video_download({
        "kind": "show", "title": "Last Week Tonight with John Oliver",
        "release_title": "Last.Week.Tonight.S13E30", "source": "torrent",
        "username": "The Pirate Bay", "filename": "Last.Week.Tonight.S13E30",
        "client_ref": "h", "status": "import_failed", "media_id": "37",
        "media_source": "library",
        "search_ctx": '{"scope": "episode", "season": 13, "episode": 30}'})
    stale = str(tmp_path / "incomplete" / "Last.Week.Tonight.S13E30.mkv")
    db.update_video_download(dl_id, dest_path=stale,
                             error="Import failed: [Errno 2] No such file or directory")
    try:
        yield app.test_client(), db, dl_id, str(final), stale
    finally:
        videoapi._video_db = None


def test_retry_reimports_from_the_clients_current_path(api):
    http, db, dl_id, _final, _stale = api
    r = http.post("/api/video/downloads/retry", json={"id": dl_id}).get_json()
    assert r["ok"] and r["mode"] == "reimport"
    row = db.get_video_download(dl_id)
    assert row["status"] == "downloading" and row["dest_path"] is None and row["error"] is None


def test_retry_with_the_file_still_in_place_is_not_a_reimport(api, tmp_path):
    http, db, dl_id, final, _stale = api
    db.update_video_download(dl_id, dest_path=final)     # a real reject on a present file
    import core.video.download_monitor as mon
    called = []
    import api.video.downloads as dmod  # noqa: F401
    orig = mon.retry_another_release
    mon.retry_another_release = lambda db_, dl: called.append(dl["id"]) or {"status": "searching"}
    try:
        r = http.post("/api/video/downloads/retry", json={"id": dl_id}).get_json()
    finally:
        mon.retry_another_release = orig
    assert r["mode"] == "next" and called == [dl_id]


def test_manual_import_finds_the_moved_file(api):
    http, db, dl_id, final, _stale = api
    items = http.get("/api/video/import/failed").get_json()["items"]
    it = next(i for i in items if i["id"] == dl_id)
    assert it["file"] == final and it["file_exists"] is True
    assert db.get_video_download(dl_id)["dest_path"] == final


# ── 2. the wrong show ─────────────────────────────────────────────────────────
def test_a_grab_with_no_media_source_is_a_library_id():
    from api.video.downloads import _default_media_source
    body = {"media_id": 37}
    _default_media_source(body)
    assert body["media_source"] == "library"
    body = {"media_id": 60694, "media_source": "tmdb"}
    _default_media_source(body)
    assert body["media_source"] == "tmdb"
    body = {}
    _default_media_source(body)
    assert "media_source" not in body


def test_grab_stores_library_when_the_page_sent_no_source(api, monkeypatch):
    http, db, _dl_id, _final, _stale = api
    db.set_setting("tv_path", str(_final.rsplit("/", 2)[0]))
    import core.video.client_grab as cg
    monkeypatch.setattr(cg, "grab", lambda *a, **k: {"ok": True, "ref": "newhash"})
    from core.video import disk_guard
    monkeypatch.setattr(disk_guard, "check_room", lambda *a, **k: {"ok": True})
    r = http.post("/api/video/downloads/grab", json={
        "kind": "show", "title": "Last Week Tonight with John Oliver", "source": "torrent",
        "release_title": "Last.Week.Tonight.S13E30", "download_url": "magnet:?xt=urn:btih:abc",
        "media_id": 37, "search_ctx": {"scope": "episode", "season": 13, "episode": 30}})
    body = r.get_json()
    assert body["ok"], body
    row = db.get_video_download(body["id"])
    assert row["media_id"] == "37" and row["media_source"] == "library"


def test_title_lock_reads_a_sourceless_id_as_a_library_id():
    from core.video.importer import title_locked

    class _DB:
        def is_hands_off(self, kind, item):
            return (kind, item) == ("show", 37)

        def hands_off_tmdb_ids(self):
            return {"movie": set(), "show": set()}

    dl = {"kind": "show", "media_id": "37", "search_ctx": '{"scope": "episode"}'}
    assert title_locked(_DB(), dl) is True
    assert title_locked(_DB(), {**dl, "media_source": "tmdb"}) is False
