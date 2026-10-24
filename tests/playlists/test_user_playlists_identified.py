"""your own playlists stay identified, so Sync never has to wait on Identify.

three legs: a song keeps the id its page knew when it was added (origin), a
song from your library borrows the id the enrichment workers found for it,
and every edit asks for a background identify of what's left.
"""

import json
import threading
import time

import pytest

from core.discovery import playlist as dp
from core.playlists import auto_identify
from core.playlists import user_playlists as up
from core.playlists.track_origin import clean_origin, library_ids, origin_ids
from database.music_database import MusicDatabase

from tests.discovery.test_direct_source_match import _Deezer, _Spotify
from tests.discovery.test_discovery_playlist import _build_deps, _playlist, _track
from tests.playlists.test_user_playlists import _client, _song


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _extra(db, playlist_id):
    return [json.loads(r["extra_data"]) if r["extra_data"] else None
            for r in db.get_mirrored_playlist_tracks(playlist_id)]


# ── origin: the id the page knew ────────────────────────────────────────


@pytest.mark.parametrize("source, sid, want", [
    ("spotify", "0flxRcIYyhr9teLBjouwQ8", {"source": "spotify", "id": "0flxRcIYyhr9teLBjouwQ8"}),
    ("Deezer", " 136340808 ", {"source": "deezer", "id": "136340808"}),
    ("spotify_public", "abc", {"source": "spotify", "id": "abc"}),
    ("myspace", "1", None),
    ("spotify", "", None),
    ("spotify", "x" * 101, None),
    ("deezer", "1; drop table", None),
])
def test_clean_origin(source, sid, want):
    assert clean_origin(source, sid) == want


def test_a_song_added_with_its_id_keeps_it_and_is_not_yet_discovered(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(pid),
                  [_song("One", source="deezer", source_id="136340808"), _song("Two")])
    one, two = _extra(mdb, pid)
    assert one == {"origin": {"source": "deezer", "id": "136340808"}}
    assert two is None
    # an origin alone is not a match: identify still has to run on it
    assert mdb.get_mirrored_playlist_discovery_counts(pid) == (0, 2)


def test_the_origin_survives_edits_and_the_match_that_follows(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One", source="deezer", source_id="1")])
    row = mdb.get_mirrored_playlist_tracks(pid)[0]
    mdb.update_mirrored_track_extra_data(row["id"], {"discovered": True, "matched_data": {"id": "1"}})
    up.add_tracks(mdb, pl, [_song("Two")])
    first = _extra(mdb, pid)[0]
    assert first["origin"] == {"source": "deezer", "id": "1"}
    assert first["discovered"] is True


# ── library: the id the workers found ───────────────────────────────────


def _library_track(db, title, artist, **ids):
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('lar', ?)", (artist,))
        conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('lal', 'lar', 'Alb')")
        cols = ", ".join(["album_id", "artist_id", "title", "file_path", *ids])
        marks = ", ".join("?" for _ in range(4 + len(ids)))
        conn.execute(f"INSERT INTO tracks ({cols}) VALUES ({marks})",
                     ("lal", "lar", title, f"/m/{title}.flac", *ids.values()))
        conn.commit()
    # the norm columns are filled the way a scan fills them
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET title_norm = ? WHERE title = ?",
                     (db._normalize_for_comparison(title), title))
        conn.execute("UPDATE artists SET name_norm = ? WHERE id = 'lar'",
                     (db._normalize_for_comparison(artist),))
        conn.commit()


def test_library_ids_finds_the_workers_ids_for_the_same_song(mdb):
    _library_track(mdb, "Not Like Us", "Kendrick Lamar",
                   spotify_track_id="6AI3ezQ4o3HUoP6Dhudph3", deezer_id="2841249222")
    assert library_ids(mdb, "not like us", "KENDRICK LAMAR") == {
        "spotify": "6AI3ezQ4o3HUoP6Dhudph3", "deezer": "2841249222",
    }
    assert library_ids(mdb, "Not Like Us", "Someone Else") == {}


def test_origin_ids_prefers_what_the_page_knew_then_fills_from_the_library(mdb):
    _library_track(mdb, "Not Like Us", "Kendrick Lamar",
                   spotify_track_id="lib-spotify", deezer_id="lib-deezer")
    track = {"track_name": "Not Like Us", "artist_name": "Kendrick Lamar",
             "extra_data": json.dumps({"origin": {"source": "deezer", "id": "page-deezer"}})}
    assert origin_ids(mdb, "soulsync", track) == {"deezer": "page-deezer", "spotify": "lib-spotify"}


def test_a_synced_mirror_is_only_ever_its_own_id(mdb):
    _library_track(mdb, "Not Like Us", "Kendrick Lamar", spotify_track_id="lib-spotify")
    track = {"track_name": "Not Like Us", "artist_name": "Kendrick Lamar", "source_track_id": "dz1"}
    assert origin_ids(mdb, "deezer", track) == {"deezer": "dz1"}
    assert origin_ids(mdb, "deezer", {"source_track_id": ""}) == {}


# ── the worker uses them ────────────────────────────────────────────────


def test_a_user_playlist_track_with_a_deezer_origin_is_fetched_not_searched():
    deezer = _Deezer()
    track = dict(_track(track_id=7, name="How Far I'll Go", artist="Auli'i Cravalho"),
                 extra_data=json.dumps({"origin": {"source": "deezer", "id": "136340808"}}))
    deps = _build_deps(tracks_by_playlist={"p1": [track]}, spotify_auth=False,
                       discovery_source="deezer", fallback_source="deezer")
    deps.get_metadata_fallback_client = lambda: deezer
    dp.run_playlist_discovery_worker([_playlist("p1", source="soulsync")], deps=deps)
    (_tid, extra), = deps._db.extra_data_writes
    assert extra["matched_data"]["id"] == "136340808"
    assert extra["confidence"] == 1.0
    assert deezer.search_calls == []


def test_an_origin_for_another_service_still_searches():
    spotify = _Spotify()
    track = dict(_track(track_id=7), extra_data=json.dumps({"origin": {"source": "deezer", "id": "1"}}))
    deps = _build_deps(tracks_by_playlist={"p1": [track]}, discovery_source="spotify")
    deps.spotify_client = spotify
    dp.run_playlist_discovery_worker([_playlist("p1", source="soulsync")], deps=deps)
    assert spotify.lookups == []


# ── every edit asks for a background identify ───────────────────────────


@pytest.fixture()
def identify_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(auto_identify, "request", lambda pid, **kw: calls.append(int(pid)) or True)
    return calls


def test_adding_creating_liking_and_copying_each_ask_for_an_identify(mdb, monkeypatch, identify_calls):
    client = _client(mdb, monkeypatch)
    made = client.post("/api/user-playlists", json={"name": "Mix", "tracks": [_song("One")]}).get_json()
    client.post(f"/api/user-playlists/{made['id']}/tracks", json={"tracks": [_song("Two")]})
    liked = client.post("/api/user-playlists/liked", json={"tracks": [_song("Three")]}).get_json()
    mirror = mdb.mirror_playlist(source="spotify", source_playlist_id="s", name="M",
                                 tracks=[_song("A", source_track_id="sp1")])
    copy = client.post(f"/api/user-playlists/from-mirror/{mirror}", json={}).get_json()
    assert identify_calls == [made["id"], made["id"], liked["id"], copy["id"]]


def test_an_add_that_added_nothing_asks_for_nothing(mdb, monkeypatch, identify_calls):
    client = _client(mdb, monkeypatch)
    pid = up.create_playlist(mdb, 1, "Mix")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(pid), [_song("One")])
    client.post(f"/api/user-playlists/{pid}/tracks", json={"tracks": [_song("One")]})
    assert identify_calls == []


def test_listing_catches_up_playlists_with_unidentified_songs(mdb, monkeypatch, identify_calls):
    client = _client(mdb, monkeypatch)
    behind = up.create_playlist(mdb, 1, "Behind")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(behind), [_song("One")])
    done = up.create_playlist(mdb, 1, "Done")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(done), [_song("Two")])
    row = mdb.get_mirrored_playlist_tracks(done)[0]
    mdb.update_mirrored_track_extra_data(row["id"], {"discovered": True})
    up.create_playlist(mdb, 1, "Empty")
    client.get("/api/user-playlists")
    assert identify_calls == [behind]


# ── the scheduler ───────────────────────────────────────────────────────


@pytest.fixture()
def scheduler(monkeypatch):
    runs = []
    gate = threading.Event()
    gate.set()

    def runner(pls):
        runs.append(pls[0]["id"])
        gate.wait(2)

    monkeypatch.setattr(auto_identify, "_runner", runner)
    monkeypatch.setattr(auto_identify, "_get_playlist", lambda pid: {"id": pid, "name": "p"})
    monkeypatch.setattr(auto_identify, "_pending", set())
    monkeypatch.setattr(auto_identify, "_running", set())
    monkeypatch.setattr(auto_identify, "_timers", {})
    return runs, gate


def _settle(runs, n, timeout=3.0):
    end = time.time() + timeout
    while len(runs) < n and time.time() < end:
        time.sleep(0.01)
    time.sleep(0.1)


def test_a_burst_of_edits_settles_into_one_run(scheduler):
    runs, _gate = scheduler
    for _ in range(5):
        auto_identify.request(1, delay=0.05)
    _settle(runs, 1)
    assert runs == [1]


def test_an_edit_during_a_run_gets_a_second_run(scheduler):
    runs, gate = scheduler
    gate.clear()
    auto_identify.request(1, delay=0.01)
    _settle(runs, 1)
    auto_identify.request(1, delay=0.01)  # lands while the first is still running
    gate.set()
    _settle(runs, 2)
    assert runs == [1, 1]


def test_without_a_runner_nothing_is_scheduled(monkeypatch):
    monkeypatch.setattr(auto_identify, "_runner", None)
    assert auto_identify.request(1) is False


# ── mirrors: identified after they are made and after every refresh ────


@pytest.fixture()
def asked(monkeypatch):
    calls = []
    monkeypatch.setattr(auto_identify, "request", lambda pid, **kw: calls.append(int(pid)) or True)
    return calls


@pytest.mark.parametrize("extra, wanted", [
    (None, True),                                              # never identified
    ({"origin": {"source": "spotify", "id": "x"}}, True),      # an origin is not a match
    ({"discovered": True, "wing_it_fallback": True}, False),   # a stub alone starts nothing
    ({"discovered": True, "manual_match": True}, False),
    ({"discovered": True, "matched_data": {"track_number": 3, "album": {"id": "a"}}}, False),
    ({"unmatched_by_user": True}, False),
])
def test_only_tracks_that_still_need_a_match_start_an_identify(asked, extra, wanted):
    track = {"extra_data": json.dumps(extra) if extra else None}
    assert auto_identify.request_if_unmatched(7, [track]) is wanted
    assert asked == ([7] if wanted else [])


def _refresh_deps(db, detail):
    from types import SimpleNamespace

    class _Adapter:
        def refresh_playlist(self, _id):
            return detail

    class _Registry:
        def get_source(self, _source):
            return _Adapter()

    return SimpleNamespace(
        get_database=lambda: db,
        playlist_source_registry=_Registry(),
        update_progress=lambda *a, **k: None,
        logger=__import__("logging").getLogger("test"),
        engine=None,
        config_manager=SimpleNamespace(get=lambda *a, **k: None),
        spotify_client=None,
    )


def _mirror(db):
    return db.mirror_playlist("deezer", "123", "Mirror", [
        {"track_name": "One", "artist_name": "A", "source_track_id": "1",
         "extra_data": {"discovered": True, "manual_match": True}},
    ], profile_id=1)


@pytest.mark.parametrize("skip_discovery, wanted", [(False, True), (True, False)])
def test_a_refresh_that_brings_new_tracks_asks_for_an_identify(mdb, asked, skip_discovery, wanted):
    from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
    from core.playlists.sources import NormalizedTrack, PlaylistDetail
    pid = _mirror(mdb)
    detail = PlaylistDetail(meta=None, tracks=[
        NormalizedTrack(position=1, track_name="One", artist_name="A", source_track_id="1"),
        NormalizedTrack(position=2, track_name="Two", artist_name="A", source_track_id="2"),
    ])
    result = auto_refresh_mirrored({"playlist_id": pid, "skip_discovery": skip_discovery},
                                   _refresh_deps(mdb, detail))
    assert result["refreshed"] == "1"
    assert asked == ([pid] if wanted else [])  # the pipeline identifies on its own


def test_a_refresh_with_nothing_new_asks_for_nothing(mdb, asked):
    from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
    from core.playlists.sources import NormalizedTrack, PlaylistDetail
    pid = _mirror(mdb)
    detail = PlaylistDetail(meta=None, tracks=[NormalizedTrack(position=1, track_name="One", artist_name="A", source_track_id="1")])
    auto_refresh_mirrored({"playlist_id": pid}, _refresh_deps(mdb, detail))
    assert asked == []


def test_background_identifies_never_run_more_than_two_at_once(monkeypatch):
    live, peak, gate = [0], [0], threading.Event()
    lock = threading.Lock()

    def runner(pls):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        gate.wait(0.3)
        with lock:
            live[0] -= 1

    monkeypatch.setattr(auto_identify, "_runner", runner)
    monkeypatch.setattr(auto_identify, "_get_playlist", lambda pid: {"id": pid, "name": "p"})
    monkeypatch.setattr(auto_identify, "_pending", set())
    monkeypatch.setattr(auto_identify, "_running", set())
    monkeypatch.setattr(auto_identify, "_timers", {})
    for pid in range(1, 6):
        auto_identify.request(pid, delay=0.01)
    time.sleep(1.2)
    assert peak[0] == auto_identify.MAX_CONCURRENT_RUNS
