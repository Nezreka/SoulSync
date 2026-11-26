"""a title's extras: its trailers and featurettes (core/video/extras.py).

read off the title's own folder by the layout plex and jellyfin read, listed
on the title's library tab, and played by a key the listing itself made, so
the stream can only ever serve a file that listing found.
"""

from __future__ import annotations

import os

import pytest
from flask import Flask

from core.video import extras
from database.video_database import VideoDatabase


def _touch(root, rel, size=100):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return str(p)


def test_extras_by_folder_and_by_suffix(tmp_path):
    m = tmp_path / "Aliens (1986)"
    _touch(m, "Aliens (1986).mkv", 900)
    _touch(m, "Aliens (1986)-trailer.mkv", 10)
    _touch(m, "Making of-behindthescenes.mp4", 20)
    _touch(m, "Featurettes/Superior Firepower.mkv", 30)
    _touch(m, "Deleted Scenes/Burke Cocooned.mkv", 40)
    _touch(m, "Behind The Scenes/On set.mkv", 50)
    _touch(m, "Featurettes/notes.txt", 5)
    _touch(m, "Subs/english.srt", 5)
    got = extras.list_extras(str(m))
    assert [(e["kind"], e["name"], e["size_bytes"]) for e in got] == [
        ("Trailer", "Aliens (1986)", 10),
        ("Featurette", "Superior Firepower", 30),
        ("Behind the Scenes", "Making of", 20),
        ("Behind the Scenes", "On set", 50),
        ("Deleted Scene", "Burke Cocooned", 40),
    ]
    # the feature itself is no extra, keys are distinct and stable
    assert len({e["key"] for e in got}) == 5
    assert [e["key"] for e in extras.list_extras(str(m))] == [e["key"] for e in got]
    assert extras.list_extras(str(tmp_path / "nope")) == []


def test_a_shows_extras_include_its_seasons_but_not_its_episodes(tmp_path):
    sh = tmp_path / "Silo"
    _touch(sh, "Season 01/Silo - S01E01.mkv")
    _touch(sh, "Season 01/Trailers/Season 1 Trailer.mkv")
    _touch(sh, "Season 01/Silo - S01E01-featurette.mkv")
    _touch(sh, "Interviews/Rebecca Ferguson.mkv")
    _touch(sh, "Season 01/Deep/Trailers/too deep.mkv")
    assert [(e["kind"], e["name"]) for e in extras.list_extras(str(sh), seasons=True)] == [
        ("Trailer", "Season 1 Trailer"), ("Featurette", "Silo - S01E01"), ("Interview", "Rebecca Ferguson")]
    assert [e["name"] for e in extras.list_extras(str(sh))] == ["Rebecca Ferguson"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    movies, tv = tmp_path / "Movies", tmp_path / "TV"
    db.set_setting("movies_path", str(movies))
    db.set_setting("tv_path", str(tv))
    feature = _touch(movies, "Aliens (1986)/Aliens (1986).mkv", 900)
    _touch(movies, "Aliens (1986)/Trailers/Teaser.mp4", 64)
    loose = _touch(movies, "Loose Movie.mkv", 500)
    _touch(movies, "Loose Movie-trailer.mkv", 10)
    ep = _touch(tv, "Silo/Season 01/Silo - S01E01.mkv", 700)
    _touch(tv, "Silo/Featurettes/The Silo.mkv", 33)
    mid = db.upsert_movie("plex", {"server_id": "1", "title": "Aliens", "content_rating": "R",
                                   "files": [{"relative_path": feature, "size_bytes": 900}]})
    lid = db.upsert_movie("plex", {"server_id": "2", "title": "Loose Movie",
                                   "files": [{"relative_path": loose, "size_bytes": 500}]})
    sid = db.upsert_show_tree("plex", {"server_id": "s", "title": "Silo", "seasons": [
        {"season_number": 1, "episodes": [{"server_id": "e", "episode_number": 1,
                                           "files": [{"relative_path": ep, "size_bytes": 700}]}]}]})
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    yield app.test_client(), db, mid, lid, sid, tmp_path
    videoapi._video_db = None


def test_the_library_tab_lists_them_without_paths(env):
    c, db, mid, lid, sid, tmp = env
    m = c.get(f"/api/video/detail/movie/{mid}/library").get_json()
    assert [(e["kind"], e["name"], e["size_bytes"]) for e in m["extras"]] == [("Trailer", "Teaser", 64)]
    assert all("path" not in e for e in m["extras"])
    s = c.get(f"/api/video/detail/show/{sid}/library").get_json()
    assert [(e["kind"], e["name"]) for e in s["extras"]] == [("Featurette", "The Silo")]
    # a movie loose in the library root has no folder of its own: the whole
    # library's loose trailers are not its extras
    assert c.get(f"/api/video/detail/movie/{lid}/library").get_json()["extras"] == []


def test_an_extra_plays_by_its_key_and_nothing_else_does(env):
    c, db, mid, lid, sid, tmp = env
    key = c.get(f"/api/video/detail/movie/{mid}/library").get_json()["extras"][0]["key"]
    r = c.get(f"/api/video/detail/movie/{mid}/extras/{key}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.data) == 10 and r.mimetype.startswith("video/")
    r.close()
    # another title's key, a made-up key, a path: none of them is a file
    assert c.get(f"/api/video/detail/show/{sid}/extras/{key}").status_code == 404
    assert c.get(f"/api/video/detail/movie/{mid}/extras/0000000000000000").status_code == 404
    assert c.get(f"/api/video/detail/movie/{mid}/extras/..%2F..%2Fvideo_library.db").status_code == 404
    assert c.get(f"/api/video/detail/movie/999/extras/{key}").status_code == 404
    # gone from the folder: gone from the route
    os.remove(tmp / "Movies" / "Aliens (1986)" / "Trailers" / "Teaser.mp4")
    assert c.get(f"/api/video/detail/movie/{mid}/extras/{key}").status_code == 404


def test_a_kids_profile_over_the_cap_gets_no_extra(env, monkeypatch):
    c, db, mid, lid, sid, tmp = env
    key = c.get(f"/api/video/detail/movie/{mid}/library").get_json()["extras"][0]["key"]
    from api.video.kids import VideoCap
    monkeypatch.setattr("api.video.kids.video_cap", lambda: VideoCap(movie=13))     # the movie is R
    assert c.get(f"/api/video/detail/movie/{mid}/extras/{key}").status_code in (403, 404)
    monkeypatch.setattr("api.video.kids.video_cap", lambda: VideoCap(movie=18))
    r = c.get(f"/api/video/detail/movie/{mid}/extras/{key}")
    assert r.status_code == 200
    r.close()


def test_an_unreachable_library_asks_only_a_few_files(tmp_path):
    asked = []

    class DB:
        def library_title_files(self, kind, item_id):
            return [{"relative_path": f"/gone/Show/Season 01/e{i}.mkv", "size_bytes": 1} for i in range(500)]

        def get_setting(self, k):
            return None

    assert extras.title_folders(DB(), "show", 1, resolve=lambda *a, **k: asked.append(1), base_dirs=[]) == []
    assert len(asked) <= 8
