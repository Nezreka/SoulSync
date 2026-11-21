"""music apps through the real app (api/subsonic.py behind web_server's
device gate): signing in every way and every refusal, the setup route,
each method's shape in json and xml, one profile never seeing another's
things, stream with Range, stars, plays and the media server bridge."""

from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

web_server = pytest.importorskip("web_server")

from api import subsonic as API  # noqa: E402
from core.connect import device_tokens as T  # noqa: E402
from core.subsonic import library as L  # noqa: E402
from core.subsonic import store as S  # noqa: E402

NS = "{http://subsonic.org/restapi}"


@pytest.fixture
def db():
    return web_server.get_database()


def _client(pid=None):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        if pid is not None:
            s["profile_id"] = pid
    return c


def _template(db, slug):
    return next(t for t in db.list_permission_templates() if t.get("slug") == slug)


def _set_template(db, pid, slug, overrides=None):
    assert db.set_profile_permission_layers(pid, _template(db, slug)["id"], json.dumps(overrides or {}))
    from core import permission_store
    permission_store.recompute(db, [pid])


class Env:
    def __init__(self, db, tmp):
        self.db = db
        self.tmp = tmp
        self.tag = uuid4().hex[:8]
        self.profiles = []
        self.lib2 = {"lib2_artists": [], "lib2_albums": [], "lib2_tracks": []}

    def profile(self, slug="adult", overrides=None, password=None):
        name = "ss_%s_%s" % (slug, uuid4().hex[:6])
        pid = self.db.create_profile(name, "#123456",
                                     generate_password_hash(password, method="pbkdf2:sha256") if password else None,
                                     is_admin=False)
        _set_template(self.db, pid, slug, overrides)
        self.profiles.append(pid)
        return pid, name

    def app(self, pid):
        r = _client(1).post("/api/devices/music-apps", json={"profile_id": pid, "name": "Phone"})
        assert r.status_code == 201, r.get_json()
        return r.get_json()

    def _x(self, sql, params=()):
        with self.db._get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid

    def track(self, title, *, album=None, explicit=None, data=b"ID3" + b"x" * 5000, path=None, source=None):
        if album is None:
            ar = self._x("INSERT INTO lib2_artists (name) VALUES (?)", ("Artist %s" % self.tag,))
            self.lib2["lib2_artists"].append(ar)
            album = self._x("INSERT INTO lib2_albums (primary_artist_id, title, year, genres) VALUES (?, ?, 2001, ?)",
                            (ar, "Album %s" % self.tag, '["Genre%s"]' % self.tag))
            self.lib2["lib2_albums"].append(album)
        tid = self._x("INSERT INTO lib2_tracks (album_id, title, track_number, duration, explicit) "
                      "VALUES (?, ?, 1, 200000, ?)", (album, title, explicit))
        self.lib2["lib2_tracks"].append(tid)
        if path is None:
            path = str(self.tmp / ("%s_%d.mp3" % (self.tag, tid)))
            with open(path, "wb") as fh:
                fh.write(data)
        if path is not False:
            self._x("INSERT INTO lib2_track_files (track_id, path, size, bitrate, format, is_primary, file_state, "
                    "server_source) VALUES (?, ?, ?, 320, 'mp3', 1, 'active', ?)",
                    (tid, path, len(data), source))
        return tid, album

    def cleanup(self):
        with self.db._get_connection() as conn:
            for tid in self.lib2["lib2_tracks"]:
                conn.execute("DELETE FROM lib2_track_files WHERE track_id = ?", (tid,))
                conn.execute("DELETE FROM lib2_media_server_mappings WHERE entity_type = 'track' AND entity_id = ?",
                             (tid,))
            for table in ("lib2_tracks", "lib2_albums", "lib2_artists"):
                for i in self.lib2[table]:
                    conn.execute(f"DELETE FROM {table} WHERE id = ?", (i,))  # noqa: S608 - fixed names
            conn.commit()
        for pid in self.profiles:
            self.db.delete_profile(pid)


@pytest.fixture
def env(db, tmp_path):
    L.forget_snapshots()
    API._auth_limiter.reset()
    API._liked_cache.clear()
    T._reset_for_tests()
    db.set_permission_policy_raw("{}")
    e = Env(db, tmp_path)
    yield e
    e.cleanup()
    L.forget_snapshots()
    API._auth_limiter.reset()


def call(method, creds, *, fmt="json", post=False, **params):
    q = dict(creds or {}, f=fmt, v="1.16.1", c="test", **params)
    c = _client()
    if post:
        return c.post("/rest/%s" % method, data=q)
    return c.get("/rest/%s.view" % method, query_string=q)


def body(r):
    assert r.status_code == 200
    return json.loads(r.get_data(as_text=True))["subsonic-response"]


def key(app):
    return {"apiKey": app["api_key"]}


def classic(app, how="token"):
    pw = app["password"]
    if how == "token":
        return {"u": app["username"], "t": hashlib.md5((pw + "s4lt").encode()).hexdigest(), "s": "s4lt"}
    if how == "enc":
        return {"u": app["username"], "p": "enc:" + pw.encode().hex()}
    return {"u": app["username"], "p": pw}


# ── setting up and signing in ────────────────────────────────────────────

def test_setting_up_a_music_app_shows_everything_once_and_keeps_only_a_hash_and_a_cipher(env, db):
    pid, name = env.profile()
    app = env.app(pid)
    assert app["username"] == name and len(app["password"]) == S.APP_PASSWORD_LEN
    assert app["api_key"].startswith("sdt_") and app["server"].startswith("http")
    qr = json.loads(app["qr_text"])
    assert qr["password"] == app["password"] and qr["apiKey"] == app["api_key"]
    row = T.get(db, app["device_id"])
    assert row["profile_id"] == pid and row["kind"] == "music"
    with db._get_connection() as conn:
        dump = "\n".join(str(tuple(r)) for r in conn.execute("SELECT * FROM subsonic_app_passwords"))
    assert app["password"] not in dump and app["api_key"] not in dump
    # members can't set one up
    assert _client(pid).post("/api/devices/music-apps", json={"profile_id": pid}).status_code == 403


def test_a_profile_without_music_apps_cant_get_one(env):
    kid, _ = env.profile("kids")
    r = _client(1).post("/api/devices/music-apps", json={"profile_id": kid})
    assert r.status_code == 400


@pytest.mark.parametrize("how", ["key", "token", "plain", "enc"])
def test_every_way_of_signing_in(env, how):
    pid, _ = env.profile()
    app = env.app(pid)
    creds = key(app) if how == "key" else classic(app, how)
    out = body(call("ping", creds))
    assert out["status"] == "ok" and out["openSubsonic"] is True
    assert body(call("getUser", creds))["user"]["username"] == app["username"]
    assert body(call("tokenInfo", creds))["tokenInfo"]["username"] == app["username"]


def test_post_form_works_like_get(env):
    pid, _ = env.profile()
    app = env.app(pid)
    assert body(call("ping", key(app), post=True))["status"] == "ok"


def test_wrong_credentials(env, db):
    pid, name = env.profile(password="realpass1")
    app = env.app(pid)
    assert body(call("ping", {"u": name, "p": "nope"}))["error"]["code"] == 40
    # the profile's own password is never a music app password
    assert body(call("ping", {"u": name, "p": "realpass1"}))["error"]["code"] == 40
    # nor is an api key (sk_) or a made-up token
    assert body(call("ping", {"apiKey": "sk_" + "a" * 40}))["error"]["code"] == 44
    assert body(call("ping", {"apiKey": app["api_key"] + "x"}))["error"]["code"] == 44
    assert body(call("ping", dict(key(app), u=name)))["error"]["code"] == 43
    assert body(call("ping", {}))["error"]["code"] == 10
    # another profile's name with this password
    other, other_name = env.profile()
    assert body(call("ping", {"u": other_name, "p": app["password"]}))["error"]["code"] == 40


def test_too_many_wrong_passwords_lock_the_address(env):
    pid, name = env.profile()
    app = env.app(pid)
    for _ in range(10):
        call("ping", {"u": name, "p": "wrong"})
    out = body(call("ping", classic(app, "plain")))
    assert out["error"]["code"] == 40 and "Too many" in out["error"]["message"]


def test_revoked_apps_are_refused_both_ways(env, db):
    pid, _ = env.profile()
    app = env.app(pid)
    assert _client(1).delete("/api/devices/%d" % app["device_id"]).status_code == 200
    assert body(call("ping", key(app)))["error"]["code"] == 44
    assert body(call("ping", classic(app)))["error"]["code"] == 40
    with db._get_connection() as conn:
        assert conn.execute("SELECT 1 FROM subsonic_app_passwords WHERE device_id = ?",
                            (app["device_id"],)).fetchone() is None
    # revoked any other way (sign out everywhere), the password row may still
    # be there: it matches nothing
    second = env.app(pid)
    T.revoke(db, second["device_id"])
    assert body(call("ping", classic(second)))["error"]["code"] == 40


def test_an_unprofiled_device_and_a_profile_without_music_apps(env, db):
    token, did = T.create(db, name="waiting")
    assert body(call("ping", {"apiKey": token}))["error"]["code"] == 50
    T.revoke(db, did)
    pid, _ = env.profile()
    app = env.app(pid)
    assert body(call("ping", key(app)))["status"] == "ok"
    # the admin turns music apps off for this profile (the kids template)
    _set_template(db, pid, "kids")
    assert body(call("ping", key(app)))["error"]["code"] == 50
    assert body(call("ping", classic(app)))["error"]["code"] == 50


def test_extensions_answer_without_signing_in(env):
    out = body(call("getOpenSubsonicExtensions", {}))
    names = {e["name"] for e in out["openSubsonicExtensions"]}
    assert {"apiKeyAuthentication", "songLyrics", "formPost", "transcodeOffset"} <= names
    # and nothing else does
    assert body(call("getArtists", {}))["error"]["code"] == 10


def test_a_header_token_on_rest_still_works(env):
    pid, _ = env.profile()
    app = env.app(pid)
    r = _client().get("/rest/ping.view", query_string={"f": "json"}, headers={"X-Device-Token": app["api_key"]})
    assert body(r)["status"] == "ok"


# ── every method's shape ─────────────────────────────────────────────────

@pytest.fixture
def lib(env):
    pid, _ = env.profile()
    app = env.app(pid)
    t1, album = env.track("Song One %s" % env.tag)
    t2, _ = env.track("Song Two %s" % env.tag, album=album)
    with env.db._get_connection() as conn:
        artist = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id = ?", (album,)).fetchone()[0]
    return {"pid": pid, "app": app, "t1": t1, "t2": t2, "album": album, "artist": artist}


def _calls(lib, tag):
    tr, al, ar = "tr-%d" % lib["t1"], "al-%d" % lib["album"], "ar-%d" % lib["artist"]
    return [
        ("ping", {}, None), ("getLicense", {}, "license"), ("getMusicFolders", {}, "musicFolders"),
        ("getIndexes", {}, "indexes"), ("getArtists", {}, "artists"), ("getArtist", {"id": ar}, "artist"),
        ("getAlbum", {"id": al}, "album"), ("getSong", {"id": tr}, "song"),
        ("getMusicDirectory", {"id": ar}, "directory"), ("getMusicDirectory", {"id": al}, "directory"),
        ("getGenres", {}, "genres"), ("getRandomSongs", {"size": 5}, "randomSongs"),
        ("getSongsByGenre", {"genre": "Genre%s" % tag}, "songsByGenre"),
        ("search2", {"query": tag}, "searchResult2"), ("search3", {"query": tag}, "searchResult3"),
        ("getStarred", {}, "starred"), ("getStarred2", {}, "starred2"), ("getNowPlaying", {}, "nowPlaying"),
        ("getPlaylists", {}, "playlists"), ("getUser", {}, "user"), ("getScanStatus", {}, "scanStatus"),
        ("getArtistInfo", {"id": ar}, "artistInfo"), ("getArtistInfo2", {"id": ar}, "artistInfo2"),
        ("getAlbumInfo", {"id": al}, "albumInfo"), ("getAlbumInfo2", {"id": al}, "albumInfo"),
        ("getSimilarSongs", {"id": tr}, "similarSongs"), ("getSimilarSongs2", {"id": tr}, "similarSongs2"),
        ("getTopSongs", {"artist": "Artist %s" % tag}, "topSongs"),
        ("getLyrics", {"artist": "nobody", "title": ""}, "lyrics"),
        ("getOpenSubsonicExtensions", {}, "openSubsonicExtensions"), ("getBookmarks", {}, "bookmarks"),
        ("getPodcasts", {}, "podcasts"), ("getInternetRadioStations", {}, "internetRadioStations"),
        ("getShares", {}, "shares"),
    ] + [("getAlbumList", {"type": t, "size": 5, "fromYear": 2000, "toYear": 2002, "genre": "Genre%s" % tag},
          "albumList") for t in ("random", "newest", "highest", "frequent", "recent", "alphabeticalByName",
                                 "alphabeticalByArtist", "starred", "byYear", "byGenre")] + [
        ("getAlbumList2", {"type": "alphabeticalByName", "size": 500}, "albumList2")]


def test_every_method_answers_in_json_and_xml(lib, env):
    creds = key(lib["app"])
    for method, params, want in _calls(lib, env.tag):
        out = body(call(method, creds, **params))
        assert out["status"] == "ok", (method, out)
        if want:
            assert want in out, (method, out)
        r = call(method, creds, fmt="xml", **params)
        root = ET.fromstring(r.get_data())
        assert root.get("status") == "ok", (method, r.get_data()[:300])
        if want:
            assert root.find(NS + want) is not None, method


def test_shapes(lib, env):
    creds = key(lib["app"])
    album = body(call("getAlbum", creds, id="al-%d" % lib["album"]))["album"]
    assert album["songCount"] == 2 and album["artistId"] == "ar-%d" % lib["artist"]
    assert [s["id"] for s in album["song"]] == ["tr-%d" % lib["t1"], "tr-%d" % lib["t2"]]
    song = album["song"][0]
    for k in ("id", "parent", "title", "album", "artist", "isDir", "coverArt", "duration", "suffix",
              "contentType", "albumId", "artistId", "type", "mediaType"):
        assert k in song, k
    genres = body(call("getGenres", creds))["genres"]["genre"]
    mine = [g for g in genres if g["value"] == "Genre%s" % env.tag]
    assert mine == [{"value": "Genre%s" % env.tag, "songCount": 2, "albumCount": 1}]
    found = body(call("search3", creds, query=env.tag))["searchResult3"]
    assert {s["id"] for s in found["song"]} == {"tr-%d" % lib["t1"], "tr-%d" % lib["t2"]}
    assert [a["id"] for a in found["album"]] == ["al-%d" % lib["album"]]
    assert body(call("getSong", creds, id="tr-999999999"))["error"]["code"] == 70
    assert body(call("getSong", creds))["error"]["code"] == 10
    assert body(call("getAlbumList2", creds))["error"]["code"] == 10
    assert body(call("noSuchMethod", creds))["status"] == "failed"


# ── stream, download, art ────────────────────────────────────────────────

def test_stream_raw_with_range(lib, env):
    creds = key(lib["app"])
    r = call("stream", creds, id="tr-%d" % lib["t1"])
    assert r.status_code == 200 and r.data.startswith(b"ID3")
    q = dict(creds, id="tr-%d" % lib["t1"], f="json")
    r = _client().get("/rest/stream.view", query_string=q, headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.data) == 10 and r.headers["Content-Range"].startswith("bytes 0-9/")
    r = call("download", creds, id="tr-%d" % lib["t1"])
    assert r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "")
    assert body(call("stream", creds, id="tr-999999999"))["error"]["code"] == 70


def test_a_file_soulsync_cant_read_without_the_bridge_is_a_plain_error(env, monkeypatch):
    pid, _ = env.profile()
    app = env.app(pid)
    tid, _ = env.track("Far Away %s" % env.tag, path="/nowhere/%s.flac" % env.tag, source="jellyfin")
    monkeypatch.setitem(API._hooks, "resolve_path", lambda p: p if os.path.exists(p) else None)
    out = body(call("stream", key(app), id="tr-%d" % tid))
    assert out["error"]["code"] == 70 and "can't read" in out["error"]["message"]
    assert "Jellyfin" in out["error"]["message"]


def test_the_bridge_proxies_the_media_servers_stream_when_on(env, db, monkeypatch):
    from flask import Response

    from core.subsonic import bridge as B
    pid, _ = env.profile()
    app = env.app(pid)
    tid, _ = env.track("Far Away %s" % env.tag, path="/nowhere/%s.flac" % env.tag, source="jellyfin")
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_media_server_mappings (entity_type, entity_id, server_source, server_id, "
                     "server_path) VALUES ('track', ?, 'jellyfin', 'jf-123', '/srv/x.flac')", (tid,))
        conn.commit()

    class Client:
        base_url, api_key = "http://jf:8096", "KEY"

        def ensure_connection(self):
            return True

    class Engine:
        def client(self, name):
            return Client() if name == "jellyfin" else None

    proxied = []
    monkeypatch.setitem(API._hooks, "resolve_path", lambda p: p if os.path.exists(p) else None)
    monkeypatch.setitem(API._hooks, "media_engine", lambda: Engine())
    monkeypatch.setitem(API._hooks, "proxy_url", lambda url: proxied.append(url) or Response(b"from jellyfin"))
    cfg = dict()
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, "get",
                        lambda k, d=None: cfg[k] if k in cfg else real_get(k, d))
    # off: refused, and the song carries no marker
    assert body(call("stream", key(app), id="tr-%d" % tid))["error"]["code"] == 70
    assert B.MARKER_FIELD not in body(call("getSong", key(app), id="tr-%d" % tid))["song"]
    cfg[B.setting_key("jellyfin")] = True
    r = call("stream", key(app), id="tr-%d" % tid)
    assert r.data == b"from jellyfin" and r.headers[B.MARKER_FIELD] == "jellyfin"
    assert proxied == ["http://jf:8096/Audio/jf-123/stream?static=true&api_key=KEY"]
    assert body(call("getSong", key(app), id="tr-%d" % tid))["song"][B.MARKER_FIELD] == "jellyfin"
    # another server's switch does nothing for it
    cfg[B.setting_key("jellyfin")] = False
    cfg[B.setting_key("plex")] = True
    assert body(call("stream", key(app), id="tr-%d" % tid))["error"]["code"] == 70
    assert B.MARKER_FIELD not in body(call("getSong", key(app), id="tr-%d" % tid))["song"]


def test_cover_art_for_unknown_ids_is_70(lib, env):
    creds = key(lib["app"])
    assert body(call("getCoverArt", creds, id="al-999999999"))["error"]["code"] == 70
    assert body(call("getCoverArt", creds, id="nonsense"))["error"]["code"] == 70


# ── one profile never sees another's things ──────────────────────────────

def test_playlists_stars_ratings_and_queues_are_per_profile(lib, env):
    a = key(lib["app"])
    other, _ = env.profile()
    b = key(env.app(other))
    tr = "tr-%d" % lib["t1"]
    made = body(call("createPlaylist", a, name="Mine %s" % env.tag, songId=[tr, "tr-%d" % lib["t2"]]))["playlist"]
    assert made["songCount"] == 2 and [e["id"] for e in made["entry"]] == [tr, "tr-%d" % lib["t2"]]
    pl = made["id"]
    assert pl in {p["id"] for p in body(call("getPlaylists", a))["playlists"]["playlist"]}
    assert pl not in {p["id"] for p in body(call("getPlaylists", b))["playlists"].get("playlist", [])}
    assert body(call("getPlaylist", b, id=pl))["error"]["code"] == 70
    assert body(call("deletePlaylist", b, id=pl))["error"]["code"] == 70
    assert body(call("updatePlaylist", b, playlistId=pl, name="x"))["error"]["code"] == 70
    # edits
    assert body(call("updatePlaylist", a, playlistId=pl, name="Renamed", songIndexToRemove=0))["status"] == "ok"
    got = body(call("getPlaylist", a, id=pl))["playlist"]
    assert got["name"] == "Renamed" and [e["id"] for e in got["entry"]] == ["tr-%d" % lib["t2"]]
    assert body(call("updatePlaylist", a, playlistId=pl, songIdToAdd=tr))["status"] == "ok"
    assert len(body(call("getPlaylist", a, id=pl))["playlist"]["entry"]) == 2
    # stars and ratings
    body(call("star", a, id=tr, albumId="al-%d" % lib["album"]))
    body(call("setRating", a, id="al-%d" % lib["album"], rating=5))
    st = body(call("getStarred2", a))["starred2"]
    assert tr in {s["id"] for s in st["song"]} and "al-%d" % lib["album"] in {x["id"] for x in st["album"]}
    st_b = body(call("getStarred2", b))["starred2"]
    assert not st_b.get("song") and not st_b.get("album")
    assert body(call("getAlbumList2", a, type="highest"))["albumList2"]["album"][0]["userRating"] == 5
    assert not body(call("getAlbumList2", b, type="highest"))["albumList2"].get("album")
    body(call("unstar", a, id=tr))
    assert tr not in {s["id"] for s in body(call("getStarred2", a))["starred2"].get("song", [])}
    # play queue
    body(call("savePlayQueue", a, id=[tr], current=tr, position=4200))
    q = body(call("getPlayQueue", a))["playQueue"]
    assert q["current"] == tr and q["position"] == 4200 and q["entry"][0]["id"] == tr
    assert "playQueue" not in body(call("getPlayQueue", b))
    # the playlist goes
    assert body(call("deletePlaylist", a, id=pl))["status"] == "ok"
    assert body(call("getPlaylist", a, id=pl))["error"]["code"] == 70


def test_a_starred_song_is_a_liked_song(lib, env, db):
    a = key(lib["app"])
    body(call("star", a, id="tr-%d" % lib["t1"]))
    from core.playlists.user_playlists import liked_playlist
    p = liked_playlist(db, lib["pid"])
    rows = db.get_mirrored_playlist_tracks(p["id"], profile_id=lib["pid"])
    assert [r["track_name"] for r in rows] == ["Song One %s" % env.tag]


def test_hidden_explicit_for_a_kid_with_music_apps(env):
    kid, _ = env.profile("kids", overrides={"music.apps": True})
    app = env.app(kid)
    clean, album = env.track("Clean %s" % env.tag)
    dirty, _ = env.track("Dirty %s" % env.tag, album=album, explicit=1)
    k = key(app)
    ids = {s["id"] for s in body(call("getAlbum", k, id="al-%d" % album))["album"]["song"]}
    assert ids == {"tr-%d" % clean}
    assert body(call("stream", k, id="tr-%d" % dirty))["error"]["code"] == 70
    assert body(call("getSong", k, id="tr-%d" % dirty))["error"]["code"] == 70


# ── star on a song without a file: wishlist or request ───────────────────

def _no_file_track(env):
    tid, _ = env.track("Not Here %s" % env.tag, path=False)
    return tid


def _wishlist_rows(db, pid):
    with db._get_connection() as conn:
        return conn.execute("SELECT * FROM wishlist_tracks WHERE profile_id = ?", (pid,)).fetchall()


@pytest.mark.parametrize("mode,want", [("download", "ok"), ("ask", "ok"), ("none", 50)])
def test_starring_a_song_you_dont_have(env, db, mode, want):
    pid, _ = env.profile(overrides={"music.get": mode})
    app = env.app(pid)
    tid = _no_file_track(env)
    out = body(call("star", key(app), id="tr-%d" % tid))
    if want == "ok":
        assert out["status"] == "ok"
        assert len(_wishlist_rows(db, pid)) == 1
    else:
        assert out["error"]["code"] == 50
        assert not _wishlist_rows(db, pid)


# ── plays ────────────────────────────────────────────────────────────────

def _history(db, tid):
    with db._get_connection() as conn:
        return conn.execute("SELECT * FROM listening_history WHERE lib2_track_id = ?", (tid,)).fetchall()


def test_scrobble_records_the_play_and_now_playing_shows_it(lib, env, db):
    a = key(lib["app"])
    tr = "tr-%d" % lib["t1"]
    body(call("scrobble", a, id=tr, submission="false"))
    np = body(call("getNowPlaying", a))["nowPlaying"]["entry"]
    assert np[0]["id"] == tr and np[0]["username"] == lib["app"]["username"]
    other, _ = env.profile()
    assert not body(call("getNowPlaying", key(env.app(other))))["nowPlaying"].get("entry")
    assert not _history(db, lib["t1"])
    body(call("scrobble", a, id=tr, time=1700000000000))
    rows = _history(db, lib["t1"])
    assert len(rows) == 1 and rows[0]["played_at"] == "2023-11-14 22:13:20"
    assert rows[0]["scrobble_eligible"] == 1


def test_a_private_session_keeps_the_play_out(lib, env, db):
    from core.personalized.listening_privacy import set_private_session
    set_private_session(db, lib["pid"], True)
    body(call("scrobble", key(lib["app"]), id="tr-%d" % lib["t1"]))
    assert not _history(db, lib["t1"])


# ── conversion through the route (needs ffmpeg) ──────────────────────────

def test_stream_converts_when_asked_for_less(env, tmp_path):
    import subprocess

    from core.subsonic import transcode as X
    X._reset_for_tests()
    binary = X.ffmpeg()
    if not binary:
        pytest.skip("no ffmpeg here")
    wav = tmp_path / "tone.wav"
    subprocess.run([binary, "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=4", str(wav)], check=True)
    pid, _ = env.profile()
    app = env.app(pid)
    tid, _ = env.track("Tone %s" % env.tag, path=str(wav), data=wav.read_bytes())
    r = call("stream", key(app), id="tr-%d" % tid, format="mp3", maxBitRate=64, estimateContentLength="true")
    assert r.status_code == 200 and r.mimetype == "audio/mpeg"
    assert int(r.headers["Content-Length"]) == X.estimate_length(200, 64)
    data = r.get_data()
    assert data and data[:4] != b"RIFF"
    raw = call("stream", key(app), id="tr-%d" % tid, format="raw", maxBitRate=64)
    assert raw.get_data()[:4] == b"RIFF"


# ── daily mixes as read-only playlists ───────────────────────────────────

def test_daily_mixes_are_read_only_playlists(lib, env, db, monkeypatch):
    mixes = {"mixes": [{"name": "Daily Mix 1", "subtitle": "x", "tracks": [
        {"name": "Song Two %s" % env.tag, "artists": [{"name": "Artist %s" % env.tag}], "owned": True,
         "lib2_track_id": lib["t2"]},
        {"name": "Song One %s" % env.tag, "artists": [{"name": "Artist %s" % env.tag}], "owned": True},
        {"name": "Not owned", "artists": [{"name": "x"}], "owned": False}]}]}
    real = db.get_curated_playlist
    monkeypatch.setattr(db, "get_curated_playlist",
                        lambda key, pid=1: mixes if pid == lib["pid"] else real(key, pid))
    a = key(lib["app"])
    lists = body(call("getPlaylists", a))["playlists"]["playlist"]
    mix = next(p for p in lists if p["id"] == "mx-1")
    assert mix["readonly"] is True and mix["songCount"] == 2
    got = body(call("getPlaylist", a, id="mx-1"))["playlist"]
    assert [e["id"] for e in got["entry"]] == ["tr-%d" % lib["t2"], "tr-%d" % lib["t1"]]
    assert body(call("deletePlaylist", a, id="mx-1"))["error"]["code"] == 70
    assert body(call("getPlaylist", a, id="mx-9"))["error"]["code"] == 70


def test_liked_songs_and_mirrored_playlists_are_read_only(lib, env, db):
    a = key(lib["app"])
    body(call("star", a, id="tr-%d" % lib["t1"]))
    liked = next(p for p in body(call("getPlaylists", a))["playlists"]["playlist"] if p["name"] == "Liked Songs")
    assert liked["readonly"] is True and liked["songCount"] == 1
    assert body(call("updatePlaylist", a, playlistId=liked["id"], name="x"))["error"]["code"] == 50
    assert body(call("deletePlaylist", a, id=liked["id"]))["error"]["code"] == 50
    mirrored = db.mirror_playlist(source="spotify", source_playlist_id="sp_%s" % env.tag, name="Mirror %s" % env.tag,
                                  tracks=[{"track_name": "Song Two %s" % env.tag, "artist_name": "Artist %s" % env.tag,
                                           "album_name": "", "duration_ms": 0}], profile_id=lib["pid"])
    got = body(call("getPlaylist", a, id="pl-%d" % mirrored))["playlist"]
    assert got["readonly"] is True and [e["id"] for e in got["entry"]] == ["tr-%d" % lib["t2"]]
    assert body(call("updatePlaylist", a, playlistId="pl-%d" % mirrored, songIdToAdd="tr-%d" % lib["t1"]))[
        "error"]["code"] == 50
    assert body(call("getUser", a, username="admin"))["error"]["code"] == 50


def test_two_credentials_and_other_profiles_are_refused(lib, env):
    a = key(lib["app"])
    r = _client().get("/rest/ping.view", query_string=dict(a, f="json", api_key="sk_x"))
    assert body(r)["error"]["code"] == 43
    assert body(call("getPlaylists", a, username="someone_else"))["error"]["code"] == 50


def test_a_song_this_profile_cant_play_is_never_scrobbled(env, db):
    kid, _ = env.profile("kids", overrides={"music.apps": True})
    app = env.app(kid)
    dirty, _ = env.track("Dirty %s" % env.tag, explicit=1)
    gone, _ = env.track("Gone %s" % env.tag, path=False)
    for tid in (dirty, gone):
        assert body(call("scrobble", key(app), id="tr-%d" % tid))["error"]["code"] == 70
        assert not _history(db, tid)


# ── every transcoder busy ────────────────────────────────────────────────

def test_a_stream_that_waited_out_every_busy_transcoder_is_an_error_never_the_bigger_file(env, monkeypatch):
    from core.subsonic import transcode as X
    pid, _ = env.profile()
    app = env.app(pid)
    tid, _ = env.track("Busy %s" % env.tag)
    seen = []

    def busy(path, plan, offset=0, **kw):
        seen.append(kw)
        raise X.Busy()
    monkeypatch.setattr(X, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(X, "open_stream", busy)
    r = call("stream", key(app), id="tr-%d" % tid, maxBitRate=64)
    assert not r.data.startswith(b"ID3")
    out = body(r)
    assert out["status"] == "failed" and out["error"]["code"] == 0
    assert "busy converting" in out["error"]["message"]
    assert seen and seen[0]["max_jobs"] == X.MAX_JOBS
    # asking for the file, or for no less than it is, never needs a slot
    assert call("stream", key(app), id="tr-%d" % tid, format="raw", maxBitRate=64).data.startswith(b"ID3")
    assert call("stream", key(app), id="tr-%d" % tid, maxBitRate=320).data.startswith(b"ID3")
    assert len(seen) == 1


def test_the_slot_setting_saves_and_reaches_the_stream(env, monkeypatch):
    from core.subsonic import transcode as X
    cfg = {}
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, "get", lambda k, d=None: cfg[k] if k in cfg else real_get(k, d))
    monkeypatch.setattr(web_server.config_manager, "set", lambda k, v: cfg.__setitem__(k, v))
    c = _client(1)
    got = c.get("/api/devices/music-apps").get_json()
    assert got["transcode_slots"] == X.MAX_JOBS and got["slots_range"] == [X.MIN_JOBS, X.MOST_JOBS]
    r = c.put("/api/devices/music-apps/settings", json={"transcode_slots": 2})
    assert r.status_code == 200 and r.get_json()["transcode_slots"] == 2 and cfg["music_apps.transcode_slots"] == 2
    for bad in (0, 17, "3", True, 2.5):
        assert c.put("/api/devices/music-apps/settings", json={"transcode_slots": bad}).status_code == 400
    assert cfg["music_apps.transcode_slots"] == 2
    pid, _ = env.profile()
    app = env.app(pid)
    tid, _ = env.track("Slots %s" % env.tag)
    seen = []
    monkeypatch.setattr(X, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(X, "open_stream", lambda path, plan, offset=0, **kw: seen.append(kw) or iter([b"converted"]))
    assert call("stream", key(app), id="tr-%d" % tid, maxBitRate=64).data == b"converted"
    assert seen[0]["max_jobs"] == 2
