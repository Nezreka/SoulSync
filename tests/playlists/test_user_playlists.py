"""user playlists: made in soulsync, stored as mirrored playlists (source 'soulsync').

the parts that would quietly break: an edit dropping a track's identify match,
another profile's playlist being reachable by id, refresh trying to pull a
playlist that has no upstream, and the pipeline refusing to run one.
"""

import io
import json
import os

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
    assert res["duplicates"] == [{
        "track_name": "one", "artist_name": "artist",
        "existing_track_name": "One", "existing_artist_name": "Artist",
    }]
    assert _titles(mdb, pid) == ["One", "Two"]
    res = up.add_tracks(mdb, pl, [_song("One")], allow_duplicates=True)
    assert res["added"] == 1
    assert _titles(mdb, pid) == ["One", "Two", "One"]


@pytest.mark.parametrize("have,adding", [
    (("Alright", "Kendrick Lamar"), ("Alright (Remastered 2015)", "Kendrick Lamar")),
    (("Alright", "Kendrick Lamar"), ("Alright - Radio Edit", "Kendrick Lamar")),
    (("Love.", "Kendrick Lamar"), ("LOVE (feat. Zacari)", "Kendrick Lamar, Zacari")),
    (("Beyoncé", "Señor"), ("Beyonce", "Senor")),
    (("Yellow", "The Coldplay"), ("Yellow", "Coldplay")),
    (("Dreams", "Fleetwood Mac"), ("Dreams [2004 Remaster]", "Fleetwood Mac & Friends")),
])
def test_likely_duplicates_are_caught_and_name_the_copy_already_there(mdb, have, adding):
    pl = mdb.get_mirrored_playlist(up.create_playlist(mdb, 1, "Mix"))
    up.add_tracks(mdb, pl, [_song(have[0], have[1])])
    res = up.add_tracks(mdb, pl, [_song(adding[0], adding[1])])
    assert res["added"] == 0
    assert res["duplicates"][0]["existing_track_name"] == have[0]


@pytest.mark.parametrize("a,b", [
    (("Alright", "Kendrick Lamar"), ("Alright", "Pharrell")),
    (("Alright", "Kendrick Lamar"), ("All Right", "Kendrick Lamar")),
    (("(Interlude)", "Band"), ("(Outro)", "Band")),
])
def test_different_songs_are_not_flagged(mdb, a, b):
    pl = mdb.get_mirrored_playlist(up.create_playlist(mdb, 1, "Mix"))
    up.add_tracks(mdb, pl, [_song(a[0], a[1])])
    assert up.add_tracks(mdb, pl, [_song(b[0], b[1])])["added"] == 1


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



def _set_tiles(mdb, pid, value):
    with mdb._get_connection() as conn:
        conn.execute("UPDATE mirrored_playlists SET cover_tiles = ? WHERE id = ?", (value, pid))
        conn.commit()


def test_list_carries_the_collage_the_playlists_page_draws(mdb):
    # no cover of its own: the page draws cover_tiles, so the picker must get them
    pid = up.create_playlist(mdb, 1, "Vibes")
    _set_tiles(mdb, pid, '["https://a.jpg", "https://b.jpg", "", 5]')
    [row] = up.list_playlists(mdb, 1)
    assert row["image_url"] is None
    assert row["cover_tiles"] == ["https://a.jpg", "https://b.jpg"]


@pytest.mark.parametrize("raw", ["[]", "not json", '{"a": 1}'])
def test_unusable_tiles_read_as_none(mdb, raw):
    pid = up.create_playlist(mdb, 1, "Vibes")
    _set_tiles(mdb, pid, raw)
    assert up.list_playlists(mdb, 1)[0]["cover_tiles"] == []


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


# ── cover / description / duplicate ──────────────────────────────────────


def _png(color=(200, 30, 30), size=(100, 80)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _owned(db, profile_id=1):
    pid = up.create_playlist(db, profile_id, "Mix")
    return db.get_mirrored_playlist(pid, profile_id=profile_id)


def _client_with_covers(db, monkeypatch, tmp_path, **kw):
    import types

    import api.user_playlists as mod

    c = _client(db, monkeypatch, **kw)
    monkeypatch.setattr(mod, "config_manager", types.SimpleNamespace(base_dir=str(tmp_path)))
    return c


def test_cover_url_round_trip(mdb):
    pl = _owned(mdb)
    url = up.set_cover_url(mdb, pl, "https://img/x.jpg")
    assert url == "https://img/x.jpg"
    assert mdb.get_mirrored_playlist(pl["id"])["image_url"] == url


def test_cover_url_rejects_non_http(mdb):
    pl = _owned(mdb)
    for bad in ("ftp://x/y.jpg", "javascript:alert(1)", "/etc/passwd", "", "   "):
        with pytest.raises(up.UserPlaylistError):
            up.set_cover_url(mdb, pl, bad)


def test_cover_from_track_borrows_art(mdb):
    pl = _owned(mdb)
    up.add_tracks(mdb, pl, [_song("One", image_url="https://img/one.jpg"), _song("Two")])
    art = up.set_cover_from_track(mdb, pl, 1)
    assert art == "https://img/one.jpg"
    assert mdb.get_mirrored_playlist(pl["id"])["image_url"] == art
    with pytest.raises(up.UserPlaylistError):  # track two has no art
        up.set_cover_from_track(mdb, pl, 2)
    with pytest.raises(up.UserPlaylistError):  # out of range
        up.set_cover_from_track(mdb, pl, 9)


def test_cover_survives_track_edits(mdb):
    pl = _owned(mdb)
    up.add_tracks(mdb, pl, [_song("One"), _song("Two")])
    up.set_cover_url(mdb, pl, "https://img/x.jpg")
    up.add_tracks(mdb, pl, [_song("Three")])
    up.reorder_tracks(mdb, pl, [3, 2, 1])
    up.remove_track(mdb, pl, 1)
    assert mdb.get_mirrored_playlist(pl["id"])["image_url"] == "https://img/x.jpg"


def test_clear_cover(mdb, tmp_path):
    pl = _owned(mdb)
    up.set_cover_url(mdb, pl, "https://img/x.jpg")
    up.clear_cover(mdb, pl, base_dir=str(tmp_path))
    assert mdb.get_mirrored_playlist(pl["id"])["image_url"] is None


def test_description_round_trip_and_clear(mdb):
    pl = _owned(mdb)
    assert up.set_description(mdb, pl, "  road trip, no skips  ") == "road trip, no skips"
    assert mdb.get_mirrored_playlist(pl["id"])["description"] == "road trip, no skips"
    assert up.set_description(mdb, pl, "   ") == ""
    assert mdb.get_mirrored_playlist(pl["id"])["description"] is None
    assert len(up.set_description(mdb, pl, "x" * 2000)) == up.MAX_DESCRIPTION_LENGTH


def test_upload_cover_reencodes_to_webp(mdb, tmp_path):
    from PIL import Image

    pl = _owned(mdb)
    url = up.set_cover_upload(mdb, pl, io.BytesIO(_png()), base_dir=str(tmp_path))
    with Image.open(up.cover_path(pl["id"], str(tmp_path))) as img:
        assert img.format == "WEBP"
        assert img.size == (up.COVER_SIDE, up.COVER_SIDE)
    assert mdb.get_mirrored_playlist(pl["id"])["image_url"] == url
    assert url.startswith(f"/api/user-playlists/{pl['id']}/cover?v=")


def test_upload_rejects_non_image_and_oversize(mdb, tmp_path):
    pl = _owned(mdb)
    with pytest.raises(up.UserPlaylistError):
        up.set_cover_upload(mdb, pl, io.BytesIO(b"not an image"), base_dir=str(tmp_path))
    with pytest.raises(up.UserPlaylistError):
        up.set_cover_upload(
            mdb, pl, io.BytesIO(b"x" * (up.COVER_MAX_BYTES + 1)), base_dir=str(tmp_path)
        )
    assert not os.path.exists(up.cover_path(pl["id"], str(tmp_path)))


def test_duplicate_copies_tracks_cover_and_description(mdb, tmp_path):
    pl = _owned(mdb)
    up.add_tracks(mdb, pl, [_song("One", image_url="https://img/one.jpg"), _song("Two")])
    up.set_cover_upload(mdb, pl, io.BytesIO(_png()), base_dir=str(tmp_path))
    up.set_description(mdb, pl, "no skips")
    pl = mdb.get_mirrored_playlist(pl["id"], profile_id=1)

    copy = up.duplicate_playlist(mdb, pl, base_dir=str(tmp_path))
    assert copy["name"] == "Mix (copy)"
    assert copy["id"] != pl["id"]
    assert _titles(mdb, copy["id"]) == ["One", "Two"]
    row = mdb.get_mirrored_playlist(copy["id"])
    assert row["description"] == "no skips"
    # the copy got its own cover file, not a pointer at the original's
    assert row["image_url"].startswith(f"/api/user-playlists/{copy['id']}/cover?v=")
    assert os.path.isfile(up.cover_path(copy["id"], str(tmp_path)))
    assert mdb.get_mirrored_playlist(pl["id"])["image_url"].startswith(
        f"/api/user-playlists/{pl['id']}/cover?v="
    )


def test_duplicate_copies_a_remote_cover_url(mdb):
    pl = _owned(mdb)
    up.set_cover_url(mdb, pl, "https://img/x.jpg")
    pl = mdb.get_mirrored_playlist(pl["id"], profile_id=1)
    copy = up.duplicate_playlist(mdb, pl, name="V2")
    assert copy["name"] == "V2"
    assert mdb.get_mirrored_playlist(copy["id"])["image_url"] == "https://img/x.jpg"


def test_duplicate_blank_name_is_refused(mdb):
    with pytest.raises(up.UserPlaylistError):
        up.duplicate_playlist(mdb, _owned(mdb), name="  ")


def test_description_patch_endpoint(mdb, monkeypatch):
    c = _client(mdb, monkeypatch)
    pid = c.post("/api/user-playlists", json={"name": "Mix"}).get_json()["id"]
    resp = c.patch(f"/api/user-playlists/{pid}", json={"description": "no skips"})
    assert resp.get_json()["description"] == "no skips"
    assert mdb.get_mirrored_playlist(pid)["description"] == "no skips"
    assert c.patch(f"/api/user-playlists/{pid}", json={}).status_code == 400


def test_cover_endpoints(mdb, monkeypatch, tmp_path):
    c = _client_with_covers(mdb, monkeypatch, tmp_path)
    pid = c.post("/api/user-playlists", json={"name": "Mix"}).get_json()["id"]

    r = c.put(f"/api/user-playlists/{pid}/cover", json={"image_url": "https://img/x.jpg"})
    assert r.get_json()["image_url"] == "https://img/x.jpg"

    assert c.delete(f"/api/user-playlists/{pid}/cover").status_code == 200
    assert mdb.get_mirrored_playlist(pid)["image_url"] is None

    assert c.put(f"/api/user-playlists/{pid}/cover", json={"image_url": "ftp://x"}).status_code == 400
    assert c.put(f"/api/user-playlists/{pid}/cover", json={"from_track": 1}).status_code == 400
    assert c.put(f"/api/user-playlists/{pid}/cover", json={}).status_code == 400

    r = c.post(
        f"/api/user-playlists/{pid}/cover",
        data={"file": (io.BytesIO(_png()), "cover.png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.data
    assert r.get_json()["image_url"].startswith(f"/api/user-playlists/{pid}/cover?v=")

    r = c.get(f"/api/user-playlists/{pid}/cover")
    assert r.status_code == 200
    assert r.content_type == "image/webp"

    r = c.post(
        f"/api/user-playlists/{pid}/cover",
        data={"file": (io.BytesIO(b"nope"), "x.txt")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_cover_from_track_endpoint(mdb, monkeypatch, tmp_path):
    c = _client_with_covers(mdb, monkeypatch, tmp_path)
    pid = c.post(
        "/api/user-playlists",
        json={"name": "Mix", "tracks": [_song("One", image_url="https://img/one.jpg")]},
    ).get_json()["id"]
    r = c.put(f"/api/user-playlists/{pid}/cover", json={"from_track": 1})
    assert r.get_json()["image_url"] == "https://img/one.jpg"


def test_duplicate_endpoint(mdb, monkeypatch, tmp_path):
    c = _client_with_covers(mdb, monkeypatch, tmp_path)
    pid = c.post(
        "/api/user-playlists", json={"name": "Mix", "tracks": [_song("One")]}
    ).get_json()["id"]
    c.patch(f"/api/user-playlists/{pid}", json={"description": "d"})
    body = c.post(f"/api/user-playlists/{pid}/duplicate", json={}).get_json()
    assert body["name"] == "Mix (copy)"
    assert _titles(mdb, body["id"]) == ["One"]
    assert mdb.get_mirrored_playlist(body["id"])["description"] == "d"
    listed = c.get("/api/user-playlists").get_json()["playlists"]
    assert {p["id"] for p in listed} == {pid, body["id"]}


def test_new_routes_are_404_for_foreign_playlists(mdb, monkeypatch, tmp_path):
    theirs = up.create_playlist(mdb, 3, "Theirs")
    c = _client_with_covers(mdb, monkeypatch, tmp_path, profile_id=1, is_admin=True)
    assert c.patch(f"/api/user-playlists/{theirs}", json={"description": "x"}).status_code == 404
    assert c.post(f"/api/user-playlists/{theirs}/duplicate", json={}).status_code == 404
    assert c.put(f"/api/user-playlists/{theirs}/cover", json={"image_url": "https://img/x.jpg"}).status_code == 404
    assert c.delete(f"/api/user-playlists/{theirs}/cover").status_code == 404
    assert c.get(f"/api/user-playlists/{theirs}/cover").status_code == 404
    assert (
        c.post(
            f"/api/user-playlists/{theirs}/cover",
            data={"file": (io.BytesIO(_png()), "c.png")},
            content_type="multipart/form-data",
        ).status_code
        == 404
    )
    assert mdb.get_mirrored_playlist(theirs)["image_url"] is None


def test_cover_get_is_not_profile_scoped(mdb, monkeypatch, tmp_path):
    # profile 3 uploads a cover; profile 1 — what the sync's cookieless fetch
    # resolves to — can still read it back for the media server.
    owner = _client_with_covers(mdb, monkeypatch, tmp_path, profile_id=3)
    pid = owner.post("/api/user-playlists", json={"name": "Theirs"}).get_json()["id"]
    r = owner.post(
        f"/api/user-playlists/{pid}/cover",
        data={"file": (io.BytesIO(_png()), "c.png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    reader = _client_with_covers(mdb, monkeypatch, tmp_path, profile_id=1, is_admin=True)
    r = reader.get(f"/api/user-playlists/{pid}/cover")
    assert r.status_code == 200
    assert r.content_type == "image/webp"
    # a non-user mirror still reads as missing
    mirror = mdb.mirror_playlist(source="spotify", source_playlist_id="sp", name="M",
                                 tracks=[], profile_id=1)
    assert reader.get(f"/api/user-playlists/{mirror}/cover").status_code == 404


def test_deleting_the_playlist_removes_its_cover_file(mdb, monkeypatch, tmp_path):
    # the cover upload lives under the user-playlist api; deletion rides the
    # mirrored route, like the ui does — the file must not be orphaned.
    import api.mirrored_playlists as mpl

    c = _client_with_covers(mdb, monkeypatch, tmp_path)
    pid = c.post("/api/user-playlists", json={"name": "Mix"}).get_json()["id"]
    r = c.post(
        f"/api/user-playlists/{pid}/cover",
        data={"file": (io.BytesIO(_png()), "c.png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert os.path.isfile(up.cover_path(pid, str(tmp_path)))

    monkeypatch.setattr(mpl, "get_database", lambda: mdb)
    monkeypatch.setattr(mpl, "_cover_base_dir", lambda: str(tmp_path))
    app = Flask(__name__)
    app.register_blueprint(mpl.create_blueprint())
    dc = app.test_client()
    assert dc.delete(f"/api/mirrored-playlists/{pid}").status_code == 200
    assert not os.path.exists(up.cover_path(pid, str(tmp_path)))
    assert mdb.get_mirrored_playlist(pid) is None
