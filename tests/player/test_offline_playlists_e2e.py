"""player downloads of a playlist, end to end: the real plan and prepare
routes over a real music library, for every kind of playlist the player
shows (yours, liked songs, mirrored, a discovery mix), as a member and as
an admin, from a browser and from a paired app."""

from __future__ import annotations

import pytest
from flask import Flask, g

import api.player_offline as api
from core.player import discovery as D
from core.player import offline_jobs as J
from core.player import playlists as P
from core.playlists.user_playlists import add_tracks, create_playlist, liked_playlist
from core.subsonic import library as L

MEMBER_PERMS = {"music.access": True, "music.discover": True}


@pytest.fixture
def mdb(tmp_path, monkeypatch):
    from core.library_scope import invalidate_library_scope_cache
    from database.music_database import MusicDatabase
    d = MusicDatabase(str(tmp_path / "library.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: d)
    invalidate_library_scope_cache()
    L.forget_snapshots()
    P._forget_matches()
    yield d
    P._forget_matches()
    L.forget_snapshots()
    invalidate_library_scope_cache()


@pytest.fixture
def pdb(tmp_path, monkeypatch):
    from database.player_database import PlayerDatabase
    J._reset_for_tests()
    monkeypatch.setattr(J, "cache_dir", lambda: tmp_path / "offline")
    d = PlayerDatabase(str(tmp_path / "player.db"))
    monkeypatch.setattr(api, "player_db", lambda: d)
    yield d
    J._reset_for_tests()


def _x(db, sql, params=()):
    with db._get_connection() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


@pytest.fixture
def lib(mdb, tmp_path):
    ar = _x(mdb, "INSERT INTO lib2_artists (name) VALUES ('Mitski')")
    al = _x(mdb, "INSERT INTO lib2_albums (primary_artist_id, title, year) VALUES (?, 'Be the Cowboy', 2018)", (ar,))
    out = {"album": al}
    for n, title in ((1, "Geyser"), (2, "Nobody"), (3, "Washing Machine Heart")):
        tid = _x(mdb, "INSERT INTO lib2_tracks (album_id, title, track_number, duration) "
                      "VALUES (?, ?, ?, 180000)", (al, title, n))
        f = tmp_path / f"{tid}.flac"
        f.write_bytes(b"fLaC" + b"\0" * 64)
        _x(mdb, "INSERT INTO lib2_track_files (track_id, path, format, size, is_primary, file_state) "
                "VALUES (?, ?, 'flac', 68, 1, 'active')", (tid, str(f)))
        out[title] = tid
    return out


def _songs(lib):
    return [{"title": t, "artist": "Mitski", "lib2_track_id": lib[t]} for t in ("Geyser", "Nobody")]


@pytest.fixture
def lists(mdb, lib):
    """every kind of playlist profile 2 sees in the player"""
    mine = create_playlist(mdb, 2, "Road trip")
    add_tracks(mdb, mdb.get_mirrored_playlist(mine, profile_id=2), _songs(lib))
    liked = liked_playlist(mdb, 2, create=True)
    liked_id = liked["id"] if isinstance(liked, dict) else int(liked)
    add_tracks(mdb, mdb.get_mirrored_playlist(liked_id, profile_id=2), _songs(lib)[:1])
    mirror = mdb.mirror_playlist(source="spotify", source_playlist_id="sp1", name="Discover Weekly",
                                 tracks=[{"track_name": "Geyser", "artist_name": "Mitski",
                                          "album_name": "Be the Cowboy", "duration_ms": 180000},
                                         {"track_name": "Nobody", "artist_name": "Mitski",
                                          "album_name": "Be the Cowboy", "duration_ms": 180000}],
                                 profile_id=2)
    # a playlist the media server (plex, navidrome) has, mirrored in
    server = mdb.mirror_playlist(source="plex", source_playlist_id="px1", name="Plex mix",
                                 tracks=[{"track_name": "Washing Machine Heart", "artist_name": "Mitski",
                                          "album_name": "Be the Cowboy", "duration_ms": 180000}],
                                 profile_id=2)
    return {"mine": mine, "liked": liked_id, "mirror": mirror, "server": server}


def _client(*, profile_id=2, admin=False, paired=None):
    app = Flask(__name__)
    app.secret_key = "test"

    @app.before_request
    def _stamp():
        g.profile_id = profile_id
        g.is_admin = admin
        g.perms = None if admin else dict(MEMBER_PERMS)
        g.perms_source = "admin" if admin else "profile"
        if paired is not None:
            g.device_auth = True
            # a paired app: its token's row id
            g.device = {"id": 7, "kind": "player"}

    app.register_blueprint(api.create_blueprint())
    return app.test_client()


def _key(mdb, pid, playlist_id):
    """the key the player's page sends, from the kind the player api says"""
    from core.player.playlists import list_for
    shelf = list_for(mdb, pid)
    row = next(p for p in shelf["yours"] + shelf["mirrored"] if p["id"] == playlist_id)
    return f"{'user' if row['kind'] == 'yours' else 'mirror'}:{playlist_id}"


@pytest.mark.parametrize("who", ["member", "admin", "app"])
@pytest.mark.parametrize("which", ["mine", "liked", "mirror", "server"])
def test_every_playlist_plans_and_prepares(mdb, pdb, lib, lists, who, which):
    from core.player import downloads
    downloads.set_permission(pdb, 2, True, 0)
    c = _client(admin=who == "admin", paired="pixel" if who == "app" else None)
    key = _key(mdb, 2, lists[which])
    r = c.get(f"/api/player/offline/plan?type=playlist&id={key}")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["kind"] == "audio" and body["items"], body
    assert all(i["item_type"] == "track" for i in body["items"])
    assert body["qualities"][0]["id"] == "original"
    for item in body["items"]:
        p = c.post("/api/player/offline/prepare", json={"item_type": "track", "item_id": item["item_id"],
                                                         "quality": "original"})
        assert p.status_code == 200, p.get_json()
        assert p.get_json()["status"] == "ready"


def test_a_discovery_mix_plans_and_prepares(mdb, pdb, lib, monkeypatch):
    from core.player import downloads
    downloads.set_permission(pdb, 2, True, 0)
    card = D._card("daily-mix-1", "Daily Mix 1", "", "made",
                   [{"title": "Geyser", "artist": "Mitski", "album": "Be the Cowboy", "duration_ms": 180000,
                     "owned": True, "lib2_track_id": lib["Geyser"]}], owned=True)
    monkeypatch.setattr(D, "_find", lambda db, pid, key: card if key == "daily-mix-1" else None)
    for c in (_client(), _client(admin=True), _client(paired="pixel")):
        r = c.get("/api/player/offline/plan?type=playlist&id=discover:daily-mix-1")
        assert r.status_code == 200, r.get_json()
        assert [i["item_id"] for i in r.get_json()["items"]] == [str(lib["Geyser"])]
        p = c.post("/api/player/offline/prepare", json={"item_type": "track", "item_id": str(lib["Geyser"]),
                                                         "quality": "original"})
        assert p.status_code == 200, p.get_json()


def _report(c, device="pixel"):
    r = c.post("/api/player/offline/devices/report", json={
        "device_id": device, "device_name": "Pixel 8", "platform": "android", "free_bytes": 6_000_000_000,
        "limit_bytes": 25_000_000_000, "used_bytes": 0, "wifi_only": False, "items": []})
    assert r.status_code == 200, r.get_json()


@pytest.mark.parametrize("who", ["member", "admin"])
@pytest.mark.parametrize("which", ["mine", "mirror", "server"])
def test_a_playlist_sent_to_a_phone_reaches_its_wanted_list(mdb, pdb, lib, lists, who, which):
    from core.player import downloads
    downloads.set_permission(pdb, 2, True, 0)
    admin = who == "admin"
    phone = _client(admin=admin, paired="pixel")
    _report(phone)
    web = _client(admin=admin)
    key = _key(mdb, 2, lists[which])
    w = web.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "add", "type": "playlist",
                                                     "id": key, "quality": "original"})
    assert w.status_code == 200, w.get_json()
    r = web.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "playlist", "id": key,
                                                    "quality": "original", "follow": True})
    assert r.status_code == 200, r.get_json()
    wanted = phone.get("/api/player/offline/devices/pixel/wanted")
    assert wanted.status_code == 200, wanted.get_json()
    adds = wanted.get_json()["add"]
    assert adds and {a["item_type"] for a in adds} == {"track"}, wanted.get_json()


def test_a_bare_playlist_id_is_the_same_playlist(mdb, pdb, lib, lists):
    c = _client()
    a = c.get(f"/api/player/offline/plan?type=playlist&id={lists['mirror']}").get_json()
    b = c.get(f"/api/player/offline/plan?type=playlist&id=mirror:{lists['mirror']}").get_json()
    assert [i["item_id"] for i in a["items"]] == [i["item_id"] for i in b["items"]] != []


def test_a_download_keeps_its_time_across_reports(pdb, monkeypatch):
    import time as T

    from core.player import offline_catalog as C
    from core.player import offline_store as S
    monkeypatch.setattr(C, "describe", lambda keys: {})
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(S.time, "time", lambda: clock["now"])
    phone = _client(paired="pixel")

    def report(items):
        r = phone.post("/api/player/offline/devices/report", json={
            "device_id": "pixel", "device_name": "Pixel 8", "platform": "android", "items": items})
        assert r.status_code == 200, r.get_json()

    def times():
        dev = phone.get("/api/player/offline/devices").get_json()["devices"][0]
        return {i["item_id"]: i["downloaded_at"] for i in dev["items"]}

    report([{"item_type": "movie", "item_id": "5", "state": "downloading", "progress": 0.5}])
    clock["now"] += 60
    report([{"item_type": "movie", "item_id": "5", "state": "downloaded"}])
    finished = times()["5"]
    clock["now"] += 3600
    # the next report says the same thing: the time stays when it finished
    report([{"item_type": "movie", "item_id": "5", "state": "downloaded"},
            {"item_type": "track", "item_id": "9", "state": "downloaded",
             "downloaded_at": int((clock["now"] - 7200) * 1000)}])
    after = times()
    assert after["5"] == finished == T.strftime("%Y-%m-%dT%H:%M:%SZ", T.gmtime(1_800_000_060))
    # the app says when a song finished (ms): that wins
    assert after["9"] == T.strftime("%Y-%m-%dT%H:%M:%SZ", T.gmtime(clock["now"] - 7200))
