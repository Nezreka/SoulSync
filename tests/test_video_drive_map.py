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
def _fresh_cache(monkeypatch):
    drive_map.forget()
    drive_map._folders_cache.update(at=0.0, folders=None)
    drive_map._counts_cache.clear()
    # never ask a real server: the server's folder list is injected per test
    monkeypatch.setattr("core.video.sources.server_library_folders", lambda: None)
    yield
    drive_map.forget()
    drive_map._folders_cache.update(at=0.0, folders=None)
    drive_map._counts_cache.clear()


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
    # the server can't be asked: the drives come from the library's own paths
    drives = drive_map.server_folders(db, fetch=lambda: None)
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
    assert c.get("/api/video/libraries/drives").get_json()["folders"][0]["server"] == "/mnt/easystore3"
    r = c.post("/api/video/libraries/drives", json={"map": [
        {"server": "/mnt/easystore3", "local": str(tmp_path)}]})
    assert r.status_code == 200 and r.get_json()["map"][0]["local"] == str(tmp_path)
    assert drive_map.load(db) == [{"server": "/mnt/easystore3", "local": str(tmp_path).rstrip(os.sep),
                                   "kind": None}]
    assert c.post("/api/video/libraries/drives", json={"map": "x"}).status_code == 400
    assert c.post("/api/video/libraries/drives/check",
                  json={"server": "/mnt/easystore3", "local": str(tmp_path)}).status_code == 200
    guest = _client(db, monkeypatch, is_admin=False)
    assert guest.get("/api/video/libraries/drives").status_code == 403
    assert guest.post("/api/video/libraries/drives", json={"map": []}).status_code == 403


def test_a_delete_on_a_mapped_drive_goes_to_that_drives_recycle_bin(db, tmp_path):
    # under no library folder it used to be deleted for good
    from core.video import recycle
    drive = tmp_path / "easystore3"
    (drive / "MOVIES").mkdir(parents=True)
    f = drive / "MOVIES" / "Heat.mkv"
    f.write_bytes(b"x")
    drive_map.save(db, [{"server": "/mnt/easystore3", "local": str(drive)}])
    res = recycle.discard(str(f), {"recycle_deletes": True}, db, reason="test")
    assert res["ok"] and res["recycled"]
    assert res["trash_path"].startswith(str(drive / recycle.TRASH_DIRNAME))
    assert os.path.exists(res["trash_path"]) and not f.exists()


def test_mass_rename_leaves_a_file_on_a_mapped_drive_outside_a_library_folder(db, tmp_path, monkeypatch):
    # renaming from the drive root would pull it out of its MOVIES folder
    from api import video as videoapi
    from core.video import mass_rename, organization
    drive = tmp_path / "easystore3"
    (drive / "MOVIES" / "Heat (1995)").mkdir(parents=True)
    f = drive / "MOVIES" / "Heat (1995)" / "heat.1995.1080p.mkv"
    f.write_bytes(b"x" * 9)
    library = tmp_path / "Movies"
    (library / "Ronin (1998)").mkdir(parents=True)
    g_ = library / "Ronin (1998)" / "ronin.mkv"
    g_.write_bytes(b"y" * 7)
    db.set_setting("movies_path", str(library))
    organization.save(db, {"movie_template": "$title ($year)/$title ($year)"})
    db.upsert_movie("plex", {"server_id": "h", "title": "Heat", "year": 1995, "file": {
        "relative_path": "/mnt/easystore3/MOVIES/Heat (1995)/heat.1995.1080p.mkv", "size_bytes": 9}})
    db.upsert_movie("plex", {"server_id": "r", "title": "Ronin", "year": 1998, "file": {
        "relative_path": str(g_), "size_bytes": 7}})
    drive_map.save(db, [{"server": "/mnt/easystore3", "local": str(drive)}])
    monkeypatch.setattr(videoapi, "_video_db", db)
    proposed = [os.path.basename(e["proposed"]) for e in mass_rename.preview()["entries"]]
    # the library folder's file is renamed as before; the mapped one is left alone
    assert proposed == ["Ronin (1998).mkv"]


SERVER_FOLDERS = [
    {"server": "/mnt/plex_20tb/PLEX/MOVIES", "kind": "movie", "library": "Movies"},
    {"server": "/mnt/easystore3/MOVIES", "kind": "movie", "library": "Movies"},
    {"server": "/mnt/easystore3/TV SHOWS", "kind": "episode", "library": "TV Shows"},
]


def test_the_servers_folders_come_with_their_kind_and_the_output_folder_is_ticked(db, tmp_path):
    # the movies output folder is the server's /mnt/plex_20tb/PLEX/MOVIES:
    # recognised from its files, never typed in again
    out = tmp_path / "share" / "PLEX" / "MOVIES"
    (out / "Heat (1995)").mkdir(parents=True)
    (out / "Heat (1995)" / "Heat.mkv").write_bytes(b"x" * 5)
    db.set_setting("movies_path", str(out))
    _movie(db, "h", "/mnt/plex_20tb/PLEX/MOVIES/Heat (1995)/Heat.mkv", 5)
    _movie(db, "a", "/mnt/easystore3/MOVIES/Akira (1988)/Akira.mkv")
    _movie(db, "b", "/mnt/easystore3/MOVIES/Ran (1985)/Ran.mkv")
    folders = {f["server"]: f for f in drive_map.server_folders(db, fetch=lambda: SERVER_FOLDERS)}
    out_row = folders["/mnt/plex_20tb/PLEX/MOVIES"]
    assert out_row["auto"] and out_row["local"] == str(out) and out_row["role"] == "your Movies folder"
    es3 = folders["/mnt/easystore3/MOVIES"]
    assert (es3["kind"], es3["files"], es3["local"], es3["auto"]) == ("movie", 2, "", False)
    assert folders["/mnt/easystore3/TV SHOWS"]["kind"] == "episode"


def test_a_reached_movies_folder_is_a_library_folder_names_and_all(db, tmp_path, monkeypatch):
    from api import video as videoapi
    from core.video import mass_rename, organization
    from core.video.library_roots import library_roots
    folder = tmp_path / "es3" / "MOVIES"
    (folder / "Akira (1988)").mkdir(parents=True)
    f = folder / "Akira (1988)" / "akira.1988.mkv"
    f.write_bytes(b"z" * 4)
    organization.save(db, {"movie_template": "$title ($year)/$title ($year)"})
    _movie(db, "a", "/mnt/easystore3/MOVIES/Akira (1988)/akira.1988.mkv", 4)
    db.upsert_movie("plex", {"server_id": "a", "title": "Akira", "year": 1988, "file": {
        "relative_path": "/mnt/easystore3/MOVIES/Akira (1988)/akira.1988.mkv", "size_bytes": 4}})
    drive_map.save(db, [{"server": "/mnt/easystore3/MOVIES", "local": str(folder), "kind": "movie"}])
    assert str(folder) in library_roots(db, "movie")
    assert str(folder) not in library_roots(db, "episode")
    monkeypatch.setattr(videoapi, "_video_db", db)
    proposed = [e["proposed"] for e in mass_rename.preview()["entries"]]
    # named inside its own MOVIES folder, never pulled out of it
    assert proposed == [str(folder / "Akira (1988)" / "Akira (1988).mkv")]


def test_the_folder_counts_are_read_once_not_on_every_open_and_save(db, monkeypatch):
    # 100k+ library paths: a save used to take minutes rebuilding the list
    for i in range(3):
        _movie(db, f"m{i}", f"/mnt/easystore3/MOVIES/M{i}.mkv")
    calls = []
    real = db.media_file_paths
    monkeypatch.setattr(db, "media_file_paths", lambda *a, **k: calls.append(1) or real(*a, **k))
    folders = [{"server": "/mnt/easystore3/MOVIES", "kind": "movie", "library": "Movies"}]
    first = drive_map.server_folders(db, fetch=lambda: folders)
    again = drive_map.server_folders(db, fetch=lambda: folders)
    assert first[0]["files"] == again[0]["files"] == 3
    assert len(calls) == 1
