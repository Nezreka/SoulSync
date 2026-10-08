"""user playlists: made in soulsync, stored as mirrored playlists (source 'soulsync').

the parts that would quietly break: an edit dropping a track's identify match,
another profile's playlist being reachable by id, refresh trying to pull a
playlist that has no upstream, and the pipeline refusing to run one.
"""

import json

import pytest
from flask import Flask, g

from core.playlists import user_playlists as up
from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _client(db, monkeypatch, *, profile_id=1, is_admin=True):
    # monkeypatch, never configure(): configure() rebinds the module global for
    # the rest of the pytest worker
    import api.user_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    monkeypatch.setattr(mod, "get_current_profile_id", lambda: profile_id)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = is_admin

    app.register_blueprint(mod.create_blueprint())
    return app.test_client()


def _titles(db, playlist_id):
    return [t["track_name"] for t in db.get_mirrored_playlist_tracks(playlist_id)]


def _song(title, artist="Artist", **extra):
    return {"track_name": title, "artist_name": artist, **extra}


# ── core ────────────────────────────────────────────────────────────────


def test_create_makes_an_empty_soulsync_mirror_owned_by_the_profile(mdb):
    pid = up.create_playlist(mdb, 3, "  Late   night  ")
    pl = mdb.get_mirrored_playlist(pid)
    assert pl["source"] == "soulsync"
    assert pl["name"] == "Late night"
    assert int(pl["profile_id"]) == 3
    assert mdb.get_mirrored_playlist_tracks(pid) == []


def test_two_playlists_with_one_name_are_two_playlists(mdb):
    a = up.create_playlist(mdb, 1, "Chill")
    b = up.create_playlist(mdb, 1, "Chill")
    assert a != b


def test_blank_name_is_refused(mdb):
    with pytest.raises(up.UserPlaylistError):
        up.create_playlist(mdb, 1, "   ")


def test_add_appends_in_order(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One")])
    up.add_tracks(mdb, pl, [_song("Two"), _song("Three")])
    assert _titles(mdb, pid) == ["One", "Two", "Three"]
    assert mdb.get_mirrored_playlist(pid)["track_count"] == 3


def test_tracks_without_artist_or_title_are_dropped(mdb):
    pl = mdb.get_mirrored_playlist(up.create_playlist(mdb, 1, "Mix"))
    with pytest.raises(up.UserPlaylistError):
        up.add_tracks(mdb, pl, [{"track_name": "no artist"}, {"artist_name": "no title"}])


def test_accepts_the_shapes_pages_hand_over(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [
        {"title": "A", "artist": "X", "album": "Al", "duration_ms": 1000},
        {"name": "B", "artist": "Y"},
    ])
    rows = mdb.get_mirrored_playlist_tracks(pid)
    assert [(r["track_name"], r["artist_name"]) for r in rows] == [("A", "X"), ("B", "Y")]
    assert rows[0]["album_name"] == "Al"
    assert rows[0]["duration_ms"] == 1000


def test_duplicate_is_skipped_and_reported_unless_allowed(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One")])
    res = up.add_tracks(mdb, pl, [_song("one", "artist"), _song("Two")])
    assert res["added"] == 1
    assert res["duplicates"] == [{"track_name": "one", "artist_name": "artist"}]
    assert _titles(mdb, pid) == ["One", "Two"]
    res = up.add_tracks(mdb, pl, [_song("One")], allow_duplicates=True)
    assert res["added"] == 1
    assert _titles(mdb, pid) == ["One", "Two", "One"]


def test_edits_keep_each_tracks_identify_match(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One"), _song("Two"), _song("Three")])
    two = next(t for t in mdb.get_mirrored_playlist_tracks(pid) if t["track_name"] == "Two")
    mdb.update_mirrored_track_extra_data(two["id"], {"discovered": True, "matched_data": {"id": "sp2"}})

    up.add_tracks(mdb, pl, [_song("Four")])
    up.remove_track(mdb, pl, 1)
    up.reorder_tracks(mdb, pl, [3, 1, 2])

    rows = mdb.get_mirrored_playlist_tracks(pid)
    assert [r["track_name"] for r in rows] == ["Four", "Two", "Three"]
    extra = json.loads(rows[1]["extra_data"])
    assert extra["matched_data"]["id"] == "sp2"
    assert rows[0]["extra_data"] is None


def test_remove_out_of_range_is_refused(mdb):
    pl = mdb.get_mirrored_playlist(up.create_playlist(mdb, 1, "Mix"))
    up.add_tracks(mdb, pl, [_song("One")])
    with pytest.raises(up.UserPlaylistError):
        up.remove_track(mdb, pl, 2)


def test_removing_the_last_track_leaves_an_empty_playlist(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One")])
    up.remove_track(mdb, pl, 1)
    assert mdb.get_mirrored_playlist(pid) is not None
    assert _titles(mdb, pid) == []


@pytest.mark.parametrize("order", [[1, 2], [1, 1, 2], [1, 2, 4], ["x", 1, 2]])
def test_stale_or_bad_order_is_refused_and_nothing_moves(mdb, order):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One"), _song("Two"), _song("Three")])
    with pytest.raises(up.UserPlaylistError):
        up.reorder_tracks(mdb, pl, order)
    assert _titles(mdb, pid) == ["One", "Two", "Three"]


def test_list_is_only_this_profiles_user_playlists(mdb):
    mine = up.create_playlist(mdb, 2, "Mine")
    up.create_playlist(mdb, 3, "Theirs")
    mdb.mirror_playlist(source="spotify", source_playlist_id="sp", name="Mirror",
                        tracks=[_song("x")], profile_id=2)
    assert [p["id"] for p in up.list_playlists(mdb, 2)] == [mine]


# ── refresh / pipeline ──────────────────────────────────────────────────


def test_refresh_never_tries_to_pull_a_user_playlist(mdb):
    from core.automation.handlers import refresh_mirrored as rm

    pid = up.create_playlist(mdb, 1, "Mix")

    class _Deps:
        def get_database(self):
            return mdb

        def update_progress(self, *a, **k):
            pass

    called = []
    original = rm._fetch_detail
    rm_fetch = lambda *a, **k: called.append(a) or original(*a, **k)  # noqa: E731
    import unittest.mock as mock
    with mock.patch.object(rm, "_fetch_detail", rm_fetch):
        result = rm.auto_refresh_mirrored({"playlist_id": pid}, _Deps())
    assert called == []
    assert result["errors"] == "0"


def test_pipeline_keeps_user_playlists_but_drops_file_and_beatport():
    from core.playlists.pipeline import _filter_refreshable_playlists
    kept = _filter_refreshable_playlists([
        {"source": "soulsync"}, {"source": "file"}, {"source": "beatport"}, {"source": "spotify"},
    ])
    assert [p["source"] for p in kept] == ["soulsync", "spotify"]


def test_pipeline_endpoint_does_not_reject_a_user_playlist(mdb, monkeypatch):
    import api.mirrored_playlists as mod
    pid = up.create_playlist(mdb, 1, "Mix")
    monkeypatch.setattr(mod, "get_database", lambda: mdb)
    # no automation deps: a refused source answers 400, an accepted one 503
    monkeypatch.setattr(mod, "_get_automation_deps", lambda: None)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = True

    app.register_blueprint(mod.create_blueprint())
    resp = app.test_client().post(f"/api/mirrored-playlists/{pid}/pipeline/run", json={})
    assert resp.status_code == 503, resp.data


# ── endpoints ───────────────────────────────────────────────────────────


def test_create_with_a_track_makes_it_and_adds_it(mdb, monkeypatch):
    c = _client(mdb, monkeypatch, profile_id=2)
    resp = c.post("/api/user-playlists", json={"name": "Road trip", "tracks": [_song("One")]})
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["added"] == 1
    assert _titles(mdb, body["id"]) == ["One"]
    assert int(mdb.get_mirrored_playlist(body["id"])["profile_id"]) == 2


def test_blank_name_is_a_400(mdb, monkeypatch):
    c = _client(mdb, monkeypatch)
    assert c.post("/api/user-playlists", json={"name": ""}).status_code == 400


def test_full_edit_round_trip(mdb, monkeypatch):
    c = _client(mdb, monkeypatch)
    pid = c.post("/api/user-playlists", json={"name": "Mix"}).get_json()["id"]
    resp = c.post(f"/api/user-playlists/{pid}/tracks", json={"tracks": [_song("One"), _song("Two")]})
    assert resp.get_json()["added"] == 2
    dup = c.post(f"/api/user-playlists/{pid}/tracks", json={"tracks": [_song("One")]}).get_json()
    assert dup["added"] == 0 and len(dup["duplicates"]) == 1
    assert c.put(f"/api/user-playlists/{pid}/order", json={"order": [2, 1]}).status_code == 200
    assert _titles(mdb, pid) == ["Two", "One"]
    assert c.delete(f"/api/user-playlists/{pid}/tracks/1").get_json()["track_count"] == 1
    assert _titles(mdb, pid) == ["One"]
    listed = c.get("/api/user-playlists").get_json()["playlists"]
    assert [(p["id"], p["track_count"]) for p in listed] == [(pid, 1)]


def test_another_profiles_playlist_is_a_404_even_for_admin(mdb, monkeypatch):
    theirs = up.create_playlist(mdb, 3, "Theirs")
    c = _client(mdb, monkeypatch, profile_id=1, is_admin=True)
    assert c.post(f"/api/user-playlists/{theirs}/tracks", json={"tracks": [_song("x")]}).status_code == 404
    assert c.delete(f"/api/user-playlists/{theirs}/tracks/1").status_code == 404
    assert c.put(f"/api/user-playlists/{theirs}/order", json={"order": []}).status_code == 404
    assert _titles(mdb, theirs) == []


def test_a_synced_mirror_cannot_be_edited_as_a_user_playlist(mdb, monkeypatch):
    mirror = mdb.mirror_playlist(source="spotify", source_playlist_id="sp", name="Mirror",
                                 tracks=[_song("x")], profile_id=1)
    c = _client(mdb, monkeypatch)
    assert c.post(f"/api/user-playlists/{mirror}/tracks", json={"tracks": [_song("y")]}).status_code == 404
    assert _titles(mdb, mirror) == ["x"]
