"""the player's one play check (core/player/playable.py) and its routes:
the order of the checks, what a client is told, and each kind's lookup."""

from __future__ import annotations

import json
import os

import pytest
from flask import Flask, g

from core.player import playable as P
from core.player.playable import Refused, Source


@pytest.fixture
def ctx(monkeypatch):
    """a request with a profile that may do anything unless a test says otherwise"""
    app = Flask(__name__)
    state = {"pid": 3, "perms": set(P.KINDS["track"].permissions) | {"movies.access", "tv.access",
                                                                     "books.access"}}
    monkeypatch.setattr(P, "current_profile_id", lambda: state["pid"])
    monkeypatch.setattr(P, "_may", lambda perm: perm in state["perms"])
    with app.test_request_context("/"):
        yield state


def test_checks_run_in_order(ctx):
    with pytest.raises(Refused) as e:
        P.check("hologram")
    assert e.value.status == 404
    ctx["pid"] = None
    with pytest.raises(Refused) as e:
        P.check("track")
    assert e.value.status == 401
    ctx["pid"] = 3
    ctx["perms"] = {"movies.access"}
    with pytest.raises(Refused) as e:
        P.check("track")
    assert (e.value.status, e.value.code) == (403, "not_allowed")
    assert P.check("movie") is P.KINDS["movie"]


def test_a_paired_app_plays_only_what_its_kind_is_for(ctx):
    g.device_auth = True
    g.device = {"id": 1, "kind": "music", "profile_id": 3}
    assert P.check("track") is P.KINDS["track"]
    with pytest.raises(Refused) as e:
        P.check("movie")
    assert e.value.code == "not_for_this_app"
    g.device = {"id": 2, "kind": "fridge", "profile_id": 3}
    with pytest.raises(Refused):
        P.check("track")


def test_resolve_hands_the_id_to_the_kind(ctx, monkeypatch):
    seen = []
    monkeypatch.setitem(P.KINDS, "track", P.Kind(("music.access",),
                                                 lambda i, part=None: seen.append((i, part)) or Source("file")))
    P.resolve("track", " 42 ", "1")
    assert seen == [("42", "1")]
    with pytest.raises(Refused) as e:
        P.resolve("track", "")
    assert e.value.status == 400


def test_a_client_never_sees_where_the_bytes_are():
    relayed = Source("relay", "video/mp4", url="http://plex:32400/x?X-Plex-Token=secret", duration=60.0)
    assert relayed.public() == {"mode": "stream", "mime": "video/mp4", "duration": 60.0}
    local = Source("file", "audio/flac", path="/music/a.flac")
    assert "path" not in json.dumps(local.public()) and "/music" not in json.dumps(local.public())
    podcast = Source("external", "audio/mpeg", url="/api/podcasts/audio-proxy?url=x")
    assert podcast.public()["url"] == "/api/podcasts/audio-proxy?url=x"


# ── audiobooks ───────────────────────────────────────────────────────────

def test_an_audiobook_is_a_timeline_of_parts_and_chapters(monkeypatch, tmp_path):
    from core.player.sources import audiobook as A
    files = []
    for name in ("02 - Part 2.mp3", "01 - Part 1.m4b"):
        f = tmp_path / name
        f.write_bytes(b"x")
        files.append(str(f))
    facts = {"01 - Part 1.m4b": (100.0, ((0.0, "Opening"), (40.0, ""))),
             "02 - Part 2.mp3": (50.0, ())}
    monkeypatch.setattr(A, "_file_facts", lambda p: facts[os.path.basename(p)])

    class Db:
        def get_library_entry(self, key):
            return {"file_paths": files} if key == "B0X" else None

    import core.audiobook_database as adb
    monkeypatch.setattr(adb, "get_audiobook_db", lambda: Db())
    src = A.locate("B0X", "1")
    assert src.duration == 150.0 and src.extra["part"] == 1
    assert src.path.endswith("02 - Part 2.mp3")
    assert [p["start"] for p in src.parts] == [0.0, 100.0]
    assert [(c["start"], c["title"], c["part"]) for c in src.chapters] == [
        (0.0, "Opening", 0), (40.0, "Chapter 2", 0), (100.0, "02 - Part 2", 1)]
    with pytest.raises(Refused):
        A.locate("B0X", "7")
    with pytest.raises(Refused) as e:
        A.locate("nope")
    assert e.value.status == 404


# ── video ────────────────────────────────────────────────────────────────

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "..", "database", "video_schema.sql")


@pytest.fixture
def vdb(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "video.db"
    c = sqlite3.connect(path)
    c.executescript(open(SCHEMA, encoding="utf-8").read())
    from database.video_database import _COLUMN_MIGRATIONS
    for table, column, decl in _COLUMN_MIGRATIONS:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass
    c.execute("INSERT INTO movies (id, title, tmdb_id, content_rating) VALUES (1, 'Alien', 348, 'R')")
    c.execute("INSERT INTO media_files (id, movie_id, relative_path, size_bytes, video_codec, audio_codec) "
              "VALUES (10, 1, '/srv/Alien.mkv', 100, 'hevc', 'ac3'), (11, 1, '/srv/Alien.mp4', 50, 'h264', 'aac')")
    c.commit()
    c.close()

    class Db:
        def _get_connection(self):
            return sqlite3.connect(path)

    import api.video as video_api
    import api.video.kids as kids
    import core.video.path_resolver as pr
    monkeypatch.setattr(video_api, "get_video_db", lambda: Db())
    monkeypatch.setattr(kids, "video_cap", lambda: None)
    monkeypatch.setattr(pr, "video_base_dirs", lambda db: [])
    monkeypatch.setattr(pr, "resolve_video_file_path", lambda p, dirs, size_bytes=None: "/local" + p)
    return kids


def test_a_movie_plays_its_biggest_file_or_the_one_picked(vdb):
    from core.player.sources import video
    best = video.locate("movie", "1")
    assert best.path == "/local/srv/Alien.mkv" and best.extra["file_id"] == 10
    assert best.verdict["verdict"] == "no"          # hevc + ac3: a browser can't
    picked = video.locate("movie", "1", "11")
    assert picked.path == "/local/srv/Alien.mp4" and picked.verdict["verdict"] == "yes"
    with pytest.raises(Refused):
        video.locate("movie", "1", "999")


def test_over_the_cap_is_refused_before_its_files_are_read(vdb, monkeypatch):
    from api.video.kids import VideoCap
    from core.player.sources import video
    monkeypatch.setattr(vdb, "video_cap", lambda: VideoCap(movie=13))
    with pytest.raises(Refused) as e:
        video.locate("movie", "1")
    assert (e.value.status, e.value.code) == (403, "restricted")


# ── routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import api.player_stream as S
    f = tmp_path / "song.flac"
    f.write_bytes(bytes(range(256)) * 40)
    sources = {"file": Source("file", "audio/flac", path=str(f), duration=200.0),
               "relay": Source("relay", "video/mp4", url="http://plex/secret", duration=60.0)}

    def fake(item_type, item_id, part=None):
        if item_id == "nope":
            raise Refused(403, "restricted", "Not on this profile.")
        return sources[item_id]

    monkeypatch.setattr(P, "resolve", fake)
    app = Flask(__name__)
    app.register_blueprint(S.create_blueprint())
    return app.test_client()


def test_playable_says_how_and_never_where(client):
    body = client.get("/api/player/playable/track/file").get_json()
    assert body == {"success": True, "mode": "stream", "mime": "audio/flac", "duration": 200.0,
                    "url": "/api/player/stream/track/file"}
    relayed = client.get("/api/player/playable/movie/relay?part=11").get_json()
    assert relayed["url"] == "/api/player/stream/movie/relay?part=11"
    assert "secret" not in json.dumps(relayed)
    refused = client.get("/api/player/playable/track/nope")
    assert refused.status_code == 403 and refused.get_json()["error"] == "restricted"


def test_stream_answers_a_range_with_206(client):
    res = client.get("/api/player/stream/track/file", headers={"Range": "bytes=10-19"})
    assert res.status_code == 206 and res.data == bytes(range(10, 20))
    assert res.headers["Content-Type"].startswith("audio/flac")
    assert client.get("/api/player/stream/track/nope").status_code == 403


def test_the_routes_declare_their_access():
    import api.player_stream as S
    from core.connect.device_access import PLAYBACK, device_access
    from core.permissions import declared_access
    for view in (S.playable, S.stream):
        assert device_access(view) == PLAYBACK
        assert declared_access(view)[0] == "perm_any"


# ── music ────────────────────────────────────────────────────────────────

@pytest.fixture
def mdb(tmp_path, monkeypatch):
    from core.library2.schema import ensure_library_v2_schema
    from tests import lib2_seed
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    ensure_library_v2_schema(conn)
    ids = {
        "clean": lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "So What", duration=540_000,
                                 path="/srv/So What.flac"),
        "explicit": lib2_seed.track(conn, "Run DMC", "Raising Hell", "Peter Piper", explicit=1,
                                    path="/srv/Peter Piper.mp3"),
        "wanted": lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "Blue in Green", owned=False),
    }
    conn.commit()

    class Db:
        def _get_connection(self):
            return conn

    import database.music_database as mdbmod
    from core.player.sources import music
    monkeypatch.setattr(mdbmod, "get_database", lambda: Db())
    monkeypatch.setattr(music, "_on_disk", lambda stored: "/local" + stored)
    monkeypatch.setattr(music, "hides_explicit", lambda: False)
    monkeypatch.setattr(music, "_scope", lambda: "shared")
    return ids, music


def test_an_owned_track_plays_its_file(mdb):
    ids, music = mdb
    src = music.locate(str(ids["clean"]))
    assert src.mode == "file" and src.path == "/local/srv/So What.flac"
    assert src.mime == "audio/flac" and src.duration == 540.0


def test_a_track_not_on_disk_or_not_owned_is_refused(mdb, monkeypatch):
    ids, music = mdb
    with pytest.raises(Refused) as e:
        music.locate(str(ids["wanted"]))
    assert e.value.status == 404
    monkeypatch.setattr(music, "_on_disk", lambda stored: None)
    monkeypatch.setattr(music, "_server_stream", lambda conn, tid: None)
    with pytest.raises(Refused) as e:
        music.locate(str(ids["clean"]))
    assert e.value.code == "no_file"


def test_a_kids_profile_cant_play_explicit_music(mdb, monkeypatch):
    ids, music = mdb
    assert music.locate(str(ids["explicit"])).mode == "file"
    monkeypatch.setattr(music, "hides_explicit", lambda: True)
    with pytest.raises(Refused) as e:
        music.locate(str(ids["explicit"]))
    assert e.value.code == "restricted"
    assert music.locate(str(ids["clean"])).mode == "file"
