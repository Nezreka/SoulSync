"""the drive map: each of the media server's drives paired with where soulsync
reaches it, so every file on every drive can be found exactly. an unmapped
drive behaves the way it always did."""

import os

import pytest
from flask import Flask, g

from core.video import drive_map
from core.video.path_resolver import resolve_video_dir, resolve_video_file_path
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture(autouse=True)
def _fresh_cache():
    drive_map.forget()
    yield
    drive_map.forget()


def _movie(db, sid, path, size=1):
    return db.upsert_movie("plex", {"server_id": sid, "title": sid,
                                    "file": {"relative_path": path, "size_bytes": size}})


def test_a_path_maps_through_the_longest_matching_drive():
    entries = drive_map.normalize([
        {"server": "/mnt/easystore3/", "local": "\\\\nas\\easystore3"},
        {"server": "/mnt/easystore3/TV SHOWS", "local": "T:\\TV"},
        {"server": "/mnt/plex_20tb", "local": "/media/big"},
    ])
    assert drive_map.map_path(entries, "/mnt/easystore3/MOVIES/Heat (1995)/Heat.mkv") == \
        "\\\\nas\\easystore3\\MOVIES\\Heat (1995)\\Heat.mkv"
    assert drive_map.map_path(entries, "/mnt/easystore3/TV SHOWS/Lost/S01E01.mkv") == "T:\\TV\\Lost\\S01E01.mkv"
    assert drive_map.map_path(entries, "/mnt/plex_20tb/a.mkv") == "/media/big/a.mkv"
    # a drive whose name starts the same is another drive
    assert drive_map.map_path(entries, "/mnt/easystore30/a.mkv") is None
    assert drive_map.map_path(entries, "/elsewhere/a.mkv") is None


def test_a_bad_map_is_refused_and_a_blank_drive_is_simply_unmapped():
    with pytest.raises(ValueError, match="mapped twice"):
        drive_map.normalize([{"server": "/mnt/a", "local": "X:"}, {"server": "/mnt/a/", "local": "Y:"}])
    with pytest.raises(ValueError):
        drive_map.normalize("nope")
    assert drive_map.normalize([{"server": "/mnt/a", "local": "  "}]) == []


def test_the_drives_come_from_the_library_itself(db):
    for i in range(3):
        _movie(db, f"a{i}", f"/mnt/easystore3/MOVIES/A{i}.mkv")
    _movie(db, "b", "/mnt/easystore30/MOVIES/B.mkv")
    _movie(db, "c", "D:\\Movies\\C.mkv")
    drive_map.save(db, [{"server": "/mnt/easystore3", "local": "E:\\es3"}])
    drives = drive_map.server_drives(db)
    assert [(d["server"], d["files"], d["local"]) for d in drives] == [
        ("/mnt/easystore3", 3, "E:\\es3"), ("/mnt/easystore30", 1, ""), ("D:", 1, "")]


def test_the_resolver_takes_the_mapped_file_first_and_falls_back_otherwise(tmp_path):
    drive = tmp_path / "easystore3"
    (drive / "MOVIES" / "Heat (1995)").mkdir(parents=True)
    real = drive / "MOVIES" / "Heat (1995)" / "Heat.mkv"
    real.write_bytes(b"x" * 10)
    entries = [{"server": "/mnt/easystore3", "local": str(drive)}]
    stored = "/mnt/easystore3/MOVIES/Heat (1995)/Heat.mkv"
    # no base dirs at all: only the map can find it
    assert resolve_video_file_path(stored, [], size_bytes=10, drive_map=entries) == str(real)
    assert resolve_video_dir(stored, [], drive_map=entries) == str(drive / "MOVIES" / "Heat (1995)")
    # a different size is a different file, even through the map
    assert resolve_video_file_path(stored, [], size_bytes=11, drive_map=entries) is None
    # unmapped: today's re-root guess still works exactly as before
    assert resolve_video_file_path(stored, [str(drive)], size_bytes=10, drive_map=[]) == str(real)
    assert resolve_video_file_path(stored, [], size_bytes=10, drive_map=[]) is None


def test_the_check_finds_a_sample_of_the_drives_files(db, tmp_path):
    drive = tmp_path / "es3"
    (drive / "MOVIES").mkdir(parents=True)
    for i in range(5):
        _movie(db, f"m{i}", f"/mnt/easystore3/MOVIES/M{i}.mkv")
        if i < 4:
            (drive / "MOVIES" / f"M{i}.mkv").write_bytes(b"x")
    res = drive_map.check(db, "/mnt/easystore3", str(drive))
    assert (res["ok"], res["sampled"], res["found"]) == (True, 5, 4)
    assert "4 of 5" in res["message"]
    wrong = drive_map.check(db, "/mnt/easystore3", str(tmp_path))
    assert wrong["ok"] is False and wrong["found"] == 0
    assert drive_map.check(db, "/mnt/easystore3", str(tmp_path / "nope"))["ok"] is False


def _client(db, monkeypatch, *, is_admin=True):
    import api.video as videoapi
    monkeypatch.setattr(videoapi, "_video_db", db)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = is_admin
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_the_api_lists_saves_and_checks_and_is_admin_only(db, monkeypatch, tmp_path):
    _movie(db, "a", "/mnt/easystore3/MOVIES/A.mkv")
    c = _client(db, monkeypatch)
    assert c.get("/api/video/libraries/drives").get_json()["drives"][0]["server"] == "/mnt/easystore3"
    r = c.post("/api/video/libraries/drives", json={"map": [
        {"server": "/mnt/easystore3", "local": str(tmp_path)}]})
    assert r.status_code == 200 and r.get_json()["map"][0]["local"] == str(tmp_path)
    assert drive_map.load(db) == [{"server": "/mnt/easystore3", "local": str(tmp_path).rstrip(os.sep)}]
    assert c.post("/api/video/libraries/drives", json={"map": "x"}).status_code == 400
    assert c.post("/api/video/libraries/drives/check",
                  json={"server": "/mnt/easystore3", "local": str(tmp_path)}).status_code == 200
    guest = _client(db, monkeypatch, is_admin=False)
    assert guest.get("/api/video/libraries/drives").status_code == 403
    assert guest.post("/api/video/libraries/drives", json={"map": []}).status_code == 403
