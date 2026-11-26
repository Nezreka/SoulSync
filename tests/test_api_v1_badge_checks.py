"""the batched badge checks the browser companion reads: POST /api/v1/library/check
(artists by exact name, their albums, watch state without a provider search)
and POST /api/v1/video/check (movies and shows by tmdb id). real databases in
a tmp path."""

import sys
import types
from unittest.mock import patch

import pytest


def _install_flask_limiter_stub():
    if "flask_limiter" in sys.modules:
        return
    stub = types.ModuleType("flask_limiter")

    class _Limiter:
        def __init__(self, *args, **kwargs):
            pass

        def limit(self, *args, **kwargs):
            return lambda target: target

        def init_app(self, app):
            pass

    stub.Limiter = _Limiter
    sys.modules["flask_limiter"] = stub
    util_stub = types.ModuleType("flask_limiter.util")
    util_stub.get_remote_address = lambda: "127.0.0.1"
    sys.modules["flask_limiter.util"] = util_stub


_install_flask_limiter_stub()

from flask import Blueprint, Flask  # noqa: E402

from api import library as library_mod  # noqa: E402
from api import library_check  # noqa: E402
from api import video_v1  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402


# ── music ─────────────────────────────────────────────────────

def _artist(cur, aid, name, *, files=True, spotify=None, name_key=True):
    from core.library2.importer import normalize_name
    cur.execute("INSERT INTO lib2_artists (id, name, name_key, spotify_id) VALUES (?, ?, ?, ?)",
                (aid, name, normalize_name(name) if name_key else None, spotify))
    if files:
        cur.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (?, ?, ?)",
                    (aid, aid, f"{name} Record"))
        cur.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (?, ?, ?)", (aid, aid, "Song"))
        cur.execute("INSERT INTO lib2_track_files (track_id, path, size, is_primary, file_state)"
                    " VALUES (?, ?, 1, 1, 'active')", (aid, f"/m/{aid}.flac"))


def _seed(db):
    conn = db._get_connection()
    cur = conn.cursor()
    # "Low" sorts after every artist whose name merely contains "low": the old
    # per-artist search (LIKE '%low%', first five a-z) never reached it
    for i, name in enumerate(["Afterglow", "Allow Me", "Barlow", "Below", "Blowfly", "Bellows"], start=10):
        _artist(cur, i, name)
    _artist(cur, 1, "Low", spotify="sp_low_lib")
    cur.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (100, 1, 'Things We Lost in the Fire')")
    cur.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (100, 100, 'Sunflower')")
    cur.execute("INSERT INTO lib2_track_files (track_id, path, size, is_primary, file_state)"
                " VALUES (100, '/m/100.flac', 1, 1, 'active')")
    # punctuation: asked as "ACDC", filed as "AC/DC" (no name key, so only the folded key finds it)
    _artist(cur, 2, "AC/DC", name_key=False)
    # in the catalogue but nothing of theirs on disk: not in the library
    _artist(cur, 3, "Ghost", files=False)
    # watched under the library artist's spotify id only (#1308 shape)
    _artist(cur, 4, "Bjork")
    cur.execute("UPDATE lib2_artists SET spotify_id = 'sp_bjork' WHERE id = 4")
    cur.executemany(
        "INSERT INTO watchlist_artists (spotify_artist_id, deezer_artist_id, artist_name, profile_id)"
        " VALUES (?, ?, ?, ?)",
        [("sp_low", None, "Low", 1),
         (None, "dz_9", "Somebody Else", 1),
         ("sp_bjork", None, "Björk Guðmundsdóttir", 1),
         ("sp_other", None, "Ghost", 2)])
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


@pytest.fixture
def client(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(library_mod, "get_database", lambda: db)
    monkeypatch.setattr(library_check, "get_database", lambda: db)
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(library_check, "require_api_key", lambda f: f):
        library_check.register_routes(bp)
    app.register_blueprint(bp)
    return app.test_client()


def _check(client, artists):
    r = client.post("/api/v1/library/check", json={"artists": artists})
    assert r.status_code == 200, r.get_json()
    return {a["name"]: a for a in r.get_json()["data"]["artists"]}


def test_exact_name_wins_over_names_that_contain_it(client):
    got = _check(client, ["Low", "low", "Lo"])
    assert got["Low"]["in_library"] is True
    assert got["Low"]["library_name"] == "Low"
    assert got["low"]["in_library"] is True and got["low"]["artist_id"] == got["Low"]["artist_id"]
    assert got["Lo"]["in_library"] is False and got["Lo"]["artist_id"] is None


def test_punctuation_and_case_still_match(client):
    got = _check(client, ["ACDC", "ac/dc"])
    assert got["ACDC"]["in_library"] is True
    assert got["ACDC"]["library_name"] == "AC/DC"
    assert got["ac/dc"]["artist_id"] == got["ACDC"]["artist_id"]


def test_a_catalogue_row_with_nothing_on_disk_is_not_in_the_library(client):
    assert _check(client, ["Ghost"])["Ghost"]["in_library"] is False


def test_albums_come_back_only_when_asked(client):
    got = _check(client, [{"name": "Low", "albums": True}, "Barlow"])
    titles = sorted(a["title"] for a in got["Low"]["albums"])
    assert titles == ["Low Record", "Things We Lost in the Fire"]
    assert all(a["id"] for a in got["Low"]["albums"])
    assert "albums" not in got["Barlow"]


def test_album_ids_open_the_v1_album_route(client):
    got = _check(client, [{"name": "Low", "albums": True}])
    artist_id = got["Low"]["artist_id"]
    # the artist id is the one the v1 artist routes answer under
    app = Flask("x")
    bp = Blueprint("v1b", __name__, url_prefix="/api/v1")
    with patch.object(library_mod, "require_api_key", lambda f: f):
        library_mod.register_routes(bp)
    app.register_blueprint(bp)
    albums = app.test_client().get(f"/api/v1/library/artists/{artist_id}/albums").get_json()
    assert sorted(a["id"] for a in albums["data"]["albums"]) == sorted(a["id"] for a in got["Low"]["albums"])


def test_watch_state_by_name_id_and_library_ids(client):
    got = _check(client, [
        "Low",                                              # watched by name
        {"name": "Whoever", "provider_id": "dz_9"},         # by the page's id
        "Bjork",                                            # by the library artist's spotify id
        "Barlow",                                           # not watched
        "Ghost",                                            # another profile's watch
    ])
    assert got["Low"]["watching"] is True and got["Low"]["watch_id"] == "sp_low"
    assert got["Whoever"]["watching"] is True and got["Whoever"]["watch_id"] == "dz_9"
    assert got["Bjork"]["watching"] is True and got["Bjork"]["watch_id"] == "sp_bjork"
    assert got["Barlow"]["watching"] is False and got["Barlow"]["watch_id"] is None
    assert got["Ghost"]["watching"] is False


def test_answers_in_the_order_asked_and_drops_junk(client):
    r = client.post("/api/v1/library/check", json={"artists": ["Barlow", 7, None, {"x": 1}, "", "Low", "Barlow"]})
    names = [a["name"] for a in r.get_json()["data"]["artists"]]
    assert names == ["Barlow", "Low"]


def test_empty_or_missing_list_is_a_400(client):
    for body in ({}, {"artists": []}, {"artists": "Low"}):
        r = client.post("/api/v1/library/check", json=body)
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "BAD_REQUEST"


def test_the_list_is_capped(client):
    r = client.post("/api/v1/library/check", json={"artists": [f"n{i}" for i in range(500)]})
    assert len(r.get_json()["data"]["artists"]) == library_check.MAX_ARTISTS


# ── video ─────────────────────────────────────────────────────

@pytest.fixture
def vclient(tmp_path, monkeypatch):
    import api.video as videoapi
    from database.video_database import VideoDatabase

    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = vdb
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda *a, **k: None)
    conn = vdb._get_connection()
    conn.execute("INSERT INTO movies (id, title, tmdb_id) VALUES (1, 'The Matrix', 603)")
    conn.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (1, 'Severance', 95396, 'Returning Series')")
    conn.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (2, 'Lost', 4607, 'Ended')")
    conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (1, 1, 1)")
    conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, has_file)"
                 " VALUES (1, 1, 1, 1, 1)")
    conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, has_file)"
                 " VALUES (1, 1, 1, 2, 0)")
    conn.commit()
    conn.close()
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")
    with patch.object(video_v1, "require_api_key", lambda f: f):
        video_v1.register_routes(v1)
    app.register_blueprint(v1)
    try:
        with app.test_client() as c:
            yield c
    finally:
        videoapi._video_db = None


def _vcheck(c, items):
    r = c.post("/api/v1/video/check", json={"items": items})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["items"]


def test_video_check_by_tmdb_id(vclient):
    c = vclient
    assert c.post("/api/v1/video/wishlist", json={"movie": {"tmdb_id": 27205, "title": "Inception"}}).status_code == 200
    assert c.post("/api/v1/video/wishlist", json={
        "show": {"tmdb_id": 1399, "title": "Game of Thrones"},
        "episodes": [{"season_number": 1, "episode_number": 1}]}).status_code == 200
    assert c.post("/api/v1/video/watchlist", json={"kind": "show", "tmdb_id": 1399, "title": "Game of Thrones"}).status_code == 200
    got = _vcheck(c, [
        {"kind": "movie", "tmdb_id": 603},
        {"kind": "movie", "tmdb_id": 27205},
        {"kind": "movie", "tmdb_id": 11},
        {"kind": "show", "tmdb_id": 1399},
        {"kind": "show", "tmdb_id": 95396},
        {"kind": "show", "tmdb_id": 4607},
    ])
    by = {(g["kind"], g["tmdb_id"]): g for g in got}
    assert by[("movie", 603)] == {"kind": "movie", "tmdb_id": 603, "in_library": True,
                                  "on_wishlist": False, "following": None}
    assert by[("movie", 27205)]["on_wishlist"] is True and by[("movie", 27205)]["in_library"] is False
    assert by[("movie", 11)]["in_library"] is False and by[("movie", 11)]["on_wishlist"] is False
    got_show = by[("show", 1399)]
    assert got_show["in_library"] is False and got_show["on_wishlist"] is True and got_show["following"] is True
    # an airing library show is on the watchlist by default; an ended one is not
    assert by[("show", 95396)]["in_library"] is True and by[("show", 95396)]["following"] is True
    assert by[("show", 4607)]["following"] is False


def test_video_check_episodes(vclient):
    c = vclient
    assert c.post("/api/v1/video/wishlist", json={
        "show": {"tmdb_id": 95396, "title": "Severance"},
        "episodes": [{"season_number": 1, "episode_number": 2}]}).status_code == 200
    got = _vcheck(vclient, [
        {"kind": "show", "tmdb_id": 95396, "season": 1, "episode": 1},
        {"kind": "show", "tmdb_id": 95396, "season": 1, "episode": 2},
        {"kind": "show", "tmdb_id": 95396, "season": 2, "episode": 1},
    ])
    assert [g["episode_in_library"] for g in got] == [True, False, False]
    assert [g["episode_on_wishlist"] for g in got] == [False, True, False]


def test_video_check_bad_bodies(vclient):
    for body in ({}, {"items": []}, {"items": [{"kind": "person", "tmdb_id": 1}, {"kind": "movie", "tmdb_id": "x"}]}):
        r = vclient.post("/api/v1/video/check", json=body)
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "BAD_REQUEST"


def test_video_check_without_the_video_side_says_so():
    app = Flask(__name__)
    v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")
    with patch.object(video_v1, "require_api_key", lambda f: f):
        video_v1.register_routes(v1)
    app.register_blueprint(v1)
    r = app.test_client().post("/api/v1/video/check", json={"items": [{"kind": "movie", "tmdb_id": 603}]})
    assert r.status_code == 503
    assert r.get_json()["error"]["code"] == "NOT_AVAILABLE"
