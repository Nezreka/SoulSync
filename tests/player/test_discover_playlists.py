"""discovery playlists in the player: what discover made for a profile,
read from stored rows, each song matched to the library track it plays"""

import json
import socket
from datetime import datetime, timezone

import pytest
from flask import Flask, g

import api.player as player_api
from core.player import discovery as D
from core.player import playlists as P
from core.subsonic import library as L


@pytest.fixture
def mdb(tmp_path, monkeypatch):
    from core.library_scope import invalidate_library_scope_cache
    from database.music_database import MusicDatabase
    d = MusicDatabase(str(tmp_path / "library.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: d)
    invalidate_library_scope_cache()
    L.forget_snapshots()
    P._forget_matches()
    D._forget_for_you()
    yield d
    D._forget_for_you()
    P._forget_matches()
    L.forget_snapshots()
    invalidate_library_scope_cache()


def _x(db, sql, params=()):
    with db._get_connection() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


@pytest.fixture
def lib(mdb):
    ar = _x(mdb, "INSERT INTO lib2_artists (name) VALUES ('Mitski')")
    al = _x(mdb, "INSERT INTO lib2_albums (primary_artist_id, title, year) VALUES (?, 'Be the Cowboy', 2018)", (ar,))
    out = {"artist": ar, "album": al}
    for n, title, explicit in ((1, "Geyser", 0), (2, "Nobody", 1)):
        tid = _x(mdb, "INSERT INTO lib2_tracks (album_id, title, track_number, duration, explicit) "
                      "VALUES (?, ?, ?, 180000, ?)", (al, title, n, explicit))
        _x(mdb, "INSERT INTO lib2_track_files (track_id, path, format, is_primary, file_state) "
                "VALUES (?, ?, 'flac', 1, 'active')", (tid, f"/m/{tid}.flac"))
        out[title] = tid
    # a song the catalogue knows with no file: not playable
    out["Gone"] = _x(mdb, "INSERT INTO lib2_tracks (album_id, title, track_number, duration) "
                          "VALUES (?, 'Gone', 3, 1000)", (al,))
    return out


def _curate(db, kind, payload, profile_id=1, when="2026-11-20 10:00:00"):
    _x(db, "INSERT INTO discovery_curated_playlists (playlist_type, track_ids_json, curated_date, profile_id) "
           "VALUES (?, ?, ?, ?)", (kind, json.dumps(payload), when, profile_id))


def _meta(title, artist, sid, album="", cover=None):
    """a metadata track as fresh tape stores it (curated_full shape)"""
    return {"track_id": sid, "spotify_track_id": sid, "itunes_track_id": None, "deezer_track_id": None,
            "track_name": title, "artist_name": artist, "album_name": album, "album_cover_url": cover,
            "duration_ms": 200000, "track_data_json": None, "source": "spotify"}


def _owned(title, artist, lib2=None, cover=None):
    t = {"name": title, "artists": [{"name": artist}],
         "album": {"name": "Be the Cowboy", "images": [{"url": cover}] if cover else []},
         "duration_ms": 180000, "owned": True}
    if lib2:
        t["lib2_track_id"] = lib2
    return t


def _daily(db, mixes, profile_id=1):
    _curate(db, "daily_mixes_v2", {"mixes": mixes, "generated_at": "2026-11-21T06:00:00+00:00", "v": 5},
            profile_id)


def _no_generators(monkeypatch):
    """anything that builds a mix, a pick or a fetch: listing must not touch it"""
    def boom(*a, **k):
        raise AssertionError("listing ran a generator")
    monkeypatch.setattr("core.personalized.daily_mixes.generate_daily_mixes", boom)
    monkeypatch.setattr("core.personalized.daily_mixes.get_or_build_daily_mixes", boom)
    monkeypatch.setattr("core.discovery.moods.generate_mood_mixes", boom)
    monkeypatch.setattr("core.discovery.moods.get_or_build_mood_mixes", boom)
    for name in ("build_for_you", "build_flow", "flow", "on_repeat", "repeat_rewind", "blend"):
        monkeypatch.setattr(f"core.personalized.for_you.{name}", boom)
    from core.personalized.manager import PersonalizedPlaylistManager
    monkeypatch.setattr(PersonalizedPlaylistManager, "ensure_playlist", boom)
    monkeypatch.setattr(PersonalizedPlaylistManager, "refresh_playlist", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


def _seasonal(db, monkeypatch):
    with db._get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS curated_seasonal_playlists (season_key TEXT PRIMARY KEY, "
                     "track_ids TEXT, curated_at TIMESTAMP, track_count INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS seasonal_tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "season_key TEXT, spotify_track_id TEXT, track_name TEXT, artist_name TEXT, album_name TEXT, "
                     "album_cover_url TEXT, duration_ms INTEGER, popularity INTEGER, track_data_json TEXT, "
                     "added_at TIMESTAMP, source TEXT NOT NULL DEFAULT 'spotify')")
        conn.execute("INSERT INTO curated_seasonal_playlists VALUES ('christmas_deezer', '[\"dz1\"]', "
                     "'2026-11-19', 1)")
        conn.execute("INSERT INTO seasonal_tracks (season_key, spotify_track_id, track_name, artist_name, "
                     "album_name, source) VALUES ('christmas', 'dz1', 'Last Christmas', 'Wham!', 'Music', 'deezer')")
        conn.commit()
    monkeypatch.setattr(D, "_season_now", lambda db: "christmas")


def _personalized(db, kind, name, tracks, profile_id=1, variant=""):
    pid = _x(db, "INSERT INTO personalized_playlists (profile_id, kind, variant, name, track_count, "
                 "last_generated_at) VALUES (?, ?, ?, ?, ?, '2026-11-20')",
             (profile_id, kind, variant, name, len(tracks)))
    for n, (title, artist, sid) in enumerate(tracks):
        _x(db, "INSERT INTO personalized_playlist_tracks (playlist_id, position, spotify_track_id, track_name, "
               "artist_name, album_name) VALUES (?, ?, ?, ?, ?, '')", (pid, n, sid, title, artist))
    return pid


def _lb(db, title, tracks, profile_id=1, kind="created_for"):
    lid = _x(db, "INSERT INTO listenbrainz_playlists (playlist_mbid, title, creator, playlist_type, track_count, "
                 "profile_id) VALUES (?, ?, 'listenbrainz', ?, ?, ?)",
             (f"mbid-{title}-{profile_id}", title, kind, len(tracks), profile_id))
    for n, (name, artist, mbid) in enumerate(tracks):
        _x(db, "INSERT INTO listenbrainz_tracks (playlist_id, position, track_name, artist_name, album_name, "
               "recording_mbid) VALUES (?, ?, ?, ?, '', ?)", (lid, n, name, artist, mbid))
    return lid


def _play(db, tid, artist="Mitski", profile_id=1):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _x(db, "INSERT INTO listening_history (title, artist, played_at, lib2_track_id, profile_id) "
           "VALUES ('x', ?, ?, ?, ?)", (artist, now, tid, profile_id))


# ── the shelf ────────────────────────────────────────────────────────────

def test_the_shelf_reads_stored_rows_in_section_order(mdb, lib, monkeypatch):
    _daily(mdb, [{"key": "daily_mix_2", "name": "Daily Mix 2", "subtitle": "Mitski and more",
                  "tracks": [_owned("Geyser", "Mitski", lib["Geyser"], cover="/a.jpg")]}])
    _curate(mdb, "mood_mixes_v1", {"mixes": [{"key": "mood_chill", "name": "Chill", "subtitle": "Downtempo",
                                              "tracks": [_owned("Geyser", "Mitski")]}],
                                   "generated_at": "2026-11-21T00:00:00+00:00", "v": 1})
    _curate(mdb, "release_radar_spotify_full", [_meta("Geyser", "Mitski", "sp1"), _meta("New One", "Mitski", "sp2")])
    # the archives from before snapshots: ids, looked up in the pool
    _x(mdb, "INSERT INTO discovery_pool (spotify_track_id, source, track_name, artist_name, album_name, "
            "track_data_json, profile_id) VALUES ('sp9', 'spotify', 'Old Gem', 'Someone', 'LP', '{}', 1)")
    _curate(mdb, "discovery_weekly_spotify", ["sp9"])
    mdb.set_metadata("listening_recs_tracks_full:1", json.dumps([_meta("Recs", "Phoebe", "sp3")]))
    _seasonal(mdb, monkeypatch)
    gems = _personalized(mdb, "hidden_gems", "Hidden Gems", [("Gem", "Someone", "sp4")])
    _personalized(mdb, "fresh_tape", "Fresh Tape", [("Dup", "Someone", "sp5")])  # shown from its own store
    lb = _lb(mdb, "Weekly Jams", [("Geyser", "Mitski", "mb1")])
    _play(mdb, lib["Geyser"])
    _no_generators(monkeypatch)

    sections = D.list_for(mdb, 1)
    assert [s["id"] for s in sections] == ["made", "fresh", "picks", "listenbrainz"]
    keys = {s["id"]: [p["key"] for p in s["playlists"]] for s in sections}
    assert keys["made"] == ["flow", "daily-2", "mood-chill"]
    assert keys["fresh"] == ["curated-release_radar", "curated-discovery_weekly", "listening-mix",
                             "seasonal-christmas"]
    assert keys["picks"] == [f"picks-{gems}"]
    assert keys["listenbrainz"] == [f"lb-{lb}"]
    cards = {p["key"]: p for s in sections for p in s["playlists"]}
    assert cards["flow"]["track_count"] is None and cards["flow"]["owned"] is True
    assert cards["daily-2"]["cover_tiles"] == ["/a.jpg"] and cards["daily-2"]["owned"] is True
    assert cards["curated-release_radar"]["track_count"] == 2 and cards["curated-release_radar"]["owned"] is False
    assert cards["curated-discovery_weekly"]["track_count"] == 1
    assert cards["seasonal-christmas"]["name"].endswith("Christmas Classics Mix")
    assert set(cards["daily-2"]) == {"key", "name", "blurb", "section", "owned", "track_count",
                                     "cover_tiles", "updated_at"}


def test_another_profiles_rows_never_show(mdb, lib):
    _daily(mdb, [{"key": "daily_mix_1", "tracks": [_owned("Geyser", "Mitski", lib["Geyser"])]}], profile_id=2)
    _curate(mdb, "release_radar_spotify_full", [_meta("Geyser", "Mitski", "sp1")], profile_id=2)
    _personalized(mdb, "hidden_gems", "Hidden Gems", [("Gem", "Someone", "sp4")], profile_id=2)
    theirs = _lb(mdb, "Theirs", [("Geyser", "Mitski", "mb1")], profile_id=2)
    _play(mdb, lib["Geyser"], profile_id=2)
    assert D.list_for(mdb, 1) == []
    for key in ("daily-1", "curated-release_radar", f"lb-{theirs}", "flow"):
        assert D.detail(mdb, 1, key, scope="shared", hide_explicit=False) is None, key


def test_blocked_artists_are_dropped(mdb, lib):
    _curate(mdb, "release_radar_spotify_full", [_meta("Geyser", "Mitski", "sp1"), _meta("Bad", "Blocked Guy", "sp2")])
    _curate(mdb, "discovery_weekly_spotify_full", [_meta("Worse", "Blocked Guy", "sp3")])
    mdb.add_blocklist_entry(1, "artist", "Blocked Guy")
    sections = D.list_for(mdb, 1)
    cards = {p["key"]: p for s in sections for p in s["playlists"]}
    # a playlist whose songs were all blocked goes too
    assert "curated-discovery_weekly" not in cards
    assert cards["curated-release_radar"]["track_count"] == 1
    out = D.detail(mdb, 1, "curated-release_radar", scope="shared", hide_explicit=False)
    assert [s["title"] for s in out["songs"]] == ["Geyser"]


# ── opening one ──────────────────────────────────────────────────────────

def test_a_daily_mix_plays_the_library_tracks_it_carries(mdb, lib):
    _daily(mdb, [{"key": "daily_mix_1", "name": "Daily Mix 1", "tracks": [
        # the stamp wins over the name
        _owned("Geyser (2018 Remaster)", "Mitski", lib["Geyser"]),
        _owned("Gone", "Mitski", lib["Gone"]),          # in the catalogue, no file
    ]}])
    out = D.detail(mdb, 1, "daily-1", scope="shared", hide_explicit=False)
    assert [s["track_id"] for s in out["songs"]] == [lib["Geyser"], None]
    assert [s["unplayable"] for s in out["songs"]] == [None, "missing"]
    assert out["songs"][0]["title"] == "Geyser" and out["songs"][0]["album_id"] == lib["album"]
    assert out["playable_count"] == 1 and out["songs"][0]["position"] == 1


def test_a_metadata_playlist_matches_owned_songs_by_name(mdb, lib):
    _curate(mdb, "release_radar_spotify_full", [_meta("Geyser", "Mitski", "sp1", cover="/c.jpg"),
                                                _meta("Not Here", "Nobody Knows", "sp2")])
    out = D.detail(mdb, 1, "curated-release_radar", scope="shared", hide_explicit=False)
    assert [s["track_id"] for s in out["songs"]] == [lib["Geyser"], None]
    assert [s["unplayable"] for s in out["songs"]] == [None, "missing"]
    assert out["songs"][0]["image_url"] == "/c.jpg"
    assert out["songs"][1]["title"] == "Not Here" and out["songs"][1]["wanted"] is False


def test_hidden_explicit_songs_say_so(mdb, lib):
    _curate(mdb, "release_radar_spotify_full", [_meta("Nobody", "Mitski", "sp1")])
    out = D.detail(mdb, 1, "curated-release_radar", scope="shared", hide_explicit=True)
    assert out["songs"][0]["track_id"] is None
    assert out["songs"][0]["unplayable"] == "explicit"
    assert out["hides_explicit"] is True


def test_a_song_already_wished_for_says_so(mdb, lib):
    _curate(mdb, "release_radar_spotify_full", [_meta("Not Here", "Nobody Knows", "sp2")])
    _x(mdb, "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) VALUES ('sp2::alb', '{}', 1)")
    out = D.detail(mdb, 1, "curated-release_radar", scope="shared", hide_explicit=False)
    assert out["songs"][0]["wanted"] is True


def test_flow_is_made_on_open_without_the_network(mdb, lib, monkeypatch):
    _play(mdb, lib["Geyser"])
    _play(mdb, lib["Geyser"])

    def no_network(*a, **k):
        raise AssertionError("flow reached the network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    out = D.detail(mdb, 1, "flow", scope="shared", hide_explicit=False)
    assert out is not None and out["key"] == "flow"
    assert lib["Geyser"] in [s["track_id"] for s in out["songs"]]


def test_on_repeat_opens_and_then_shows_on_the_shelf(mdb, lib, monkeypatch):
    monkeypatch.setattr("core.personalized.for_you.MIN_TRACKS", 1)
    for _ in range(3):
        _play(mdb, lib["Geyser"])
    out = D.detail(mdb, 1, "on-repeat", scope="shared", hide_explicit=False)
    assert [s["track_id"] for s in out["songs"]] == [lib["Geyser"]]
    made = [p["key"] for p in D.list_for(mdb, 1)[0]["playlists"]]
    assert made == ["flow", "on-repeat"]


def test_a_blend_needs_both_sides_to_share(mdb, lib):
    _play(mdb, lib["Geyser"])
    assert D.detail(mdb, 1, "blend-2", scope="shared", hide_explicit=False) is None


# ── routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(mdb, monkeypatch):
    monkeypatch.setattr(player_api, "get_database", lambda: mdb)
    monkeypatch.setattr("core.player.sources.music._scope", lambda: "shared")
    monkeypatch.setattr("core.player.sources.music.hides_explicit", lambda: False)
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["profile_id"] = 1
    c.app = app
    return c


@pytest.fixture
def wished(monkeypatch):
    import api.wishlist_routes as W
    calls = []

    def add(runtime, *, track, artist, album, source_type="album", source_context=None, quality_profile_id=None):
        calls.append({"runtime": runtime, "track": track, "artist": artist, "album": album,
                      "source_type": source_type, "context": source_context})
        return {"success": True, "message": "added"}, 200
    monkeypatch.setattr("core.wishlist.routes.add_album_track_to_wishlist", add)
    monkeypatch.setattr(W, "_build_wishlist_route_runtime", lambda: "runtime")
    return calls


def test_the_routes_answer_the_contract(client, mdb, lib):
    _curate(mdb, "release_radar_spotify_full", [_meta("Geyser", "Mitski", "sp1")])
    res = client.get("/api/player/discover-playlists").get_json()
    assert res["success"] is True and res["sections"][0]["id"] == "fresh"
    one = client.get("/api/player/discover-playlists/curated-release_radar").get_json()
    assert one["playlist"]["songs"][0]["track_id"] == lib["Geyser"]
    assert one["playlist"]["playable_count"] == 1


def test_an_unknown_key_is_not_found(client, mdb):
    for key in ("nope", "daily-9", "lb-12345", "picks-x", "BAD%20KEY"):
        res = client.get(f"/api/player/discover-playlists/{key}")
        assert res.status_code == 404, key
        assert res.get_json() == {"success": False, "error": "not_found", "message": "No such playlist."}
    assert client.post("/api/player/discover-playlists/nope/get", json={}).status_code == 404


def test_get_wishlists_only_the_missing_songs(client, mdb, lib, wished):
    _curate(mdb, "release_radar_spotify_full", [
        _meta("Geyser", "Mitski", "sp1"),           # owned: stays put
        _meta("Not Here", "Nobody Knows", "sp2"),
        _meta("Also Gone", "Nobody Knows", "sp3"),
        _meta("Wished", "Nobody Knows", "sp4"),
    ])
    _x(mdb, "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) VALUES ('sp4', '{}', 1)")
    res = client.post("/api/player/discover-playlists/curated-release_radar/get", json={}).get_json()
    assert res == {"success": True, "added": 2, "already": 1, "skipped": 0}
    assert [c["track"]["id"] for c in wished] == ["sp2", "sp3"]
    assert wished[0]["runtime"] == "runtime" and wished[0]["artist"] == {"name": "Nobody Knows"}
    assert wished[0]["context"]["metadata_source"] == "spotify"
    # just one of them, by position
    wished.clear()
    res = client.post("/api/player/discover-playlists/curated-release_radar/get",
                      json={"positions": [1, 3]}).get_json()
    assert res["added"] == 1 and [c["track"]["id"] for c in wished] == ["sp3"]


def test_get_keys_listenbrainz_songs_by_their_recording(client, mdb, lib, wished):
    lid = _lb(mdb, "Weekly Jams", [("Geyser", "Mitski", "mb1"), ("Elsewhere", "Someone", "mb2")])
    res = client.post(f"/api/player/discover-playlists/lb-{lid}/get", json={}).get_json()
    assert res["added"] == 1
    assert wished[0]["track"]["id"] == "mb2" and wished[0]["context"]["metadata_source"] == "musicbrainz"


def test_get_refuses_junk_positions(client, mdb, wished):
    _curate(mdb, "release_radar_spotify_full", [_meta("Not Here", "Nobody Knows", "sp2")])
    for bad in ("1", [0], [True], ["2"]):
        res = client.post("/api/player/discover-playlists/curated-release_radar/get", json={"positions": bad})
        assert res.status_code == 400, bad
    assert wished == []


def test_a_profile_that_cannot_request_music_is_told(client, mdb, wished):
    _curate(mdb, "release_radar_spotify_full", [_meta("Not Here", "Nobody Knows", "sp2")])

    @client.app.before_request
    def _perms():
        g.profile_id = 1
        g.perms = {"music.access": True, "music.discover": True, "music.get": "none"}
    res = client.post("/api/player/discover-playlists/curated-release_radar/get", json={})
    assert res.status_code == 403
    assert res.get_json()["permission"] == "music.get"
    assert wished == []
    # the list still opens: listening isn't requesting
    assert client.get("/api/player/discover-playlists/curated-release_radar").status_code == 200


def test_a_profile_without_discover_sees_none(client, mdb):
    """the manager's discover gate holds here too"""
    @client.app.before_request
    def _perms():
        g.profile_id = 1
        g.perms = {"music.access": True, "music.discover": False}
    assert client.get("/api/player/discover-playlists").status_code == 403
    assert client.get("/api/player/discover-playlists/flow").status_code == 403
