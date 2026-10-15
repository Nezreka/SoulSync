"""user playlists, round two: date added, the picker's ticks and untick,
Liked Songs, and making a mirrored playlist your own.

the parts that would quietly break: an edit resetting every track's date
added, a tick lying about what a playlist holds, a second Liked Songs
appearing, and a copied mirror losing its identify matches.
"""

import pytest

from core.playlists import user_playlists as up
from database.music_database import MusicDatabase

from tests.playlists.test_user_playlists import _client, _song, _titles


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _added(db, playlist_id):
    return {t["track_name"]: t["added_at"] for t in db.get_mirrored_playlist_tracks(playlist_id)}


def _backdate(db, playlist_id, when):
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE mirrored_playlist_tracks SET added_at = ? WHERE playlist_id = ?", (when, playlist_id)
        )
        conn.commit()


# ── date added ──────────────────────────────────────────────────────────


def test_every_track_gets_a_date_added(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(pid), [_song("One")])
    assert _added(mdb, pid)["One"]


@pytest.mark.parametrize("edit", ["add", "remove", "reorder"])
def test_an_edit_never_resets_a_tracks_date_added(mdb, edit):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One"), _song("Two"), _song("Three")])
    _backdate(mdb, pid, "2020-01-01 00:00:00")
    if edit == "add":
        up.add_tracks(mdb, pl, [_song("Four")])
    elif edit == "remove":
        up.remove_track(mdb, pl, 3)
    else:
        up.reorder_tracks(mdb, pl, [3, 1, 2])
    dates = _added(mdb, pid)
    assert dates["One"] == dates["Two"] == "2020-01-01 00:00:00"
    if edit == "add":
        assert dates["Four"] != "2020-01-01 00:00:00"


def test_a_re_mirror_keeps_dates_for_tracks_it_already_had(mdb):
    tracks = [_song("A", source_track_id="sp1"), _song("B", source_track_id="sp2")]
    pid = mdb.mirror_playlist(source="spotify", source_playlist_id="x", name="M", tracks=tracks)
    _backdate(mdb, pid, "2020-01-01 00:00:00")
    mdb.mirror_playlist(source="spotify", source_playlist_id="x", name="M",
                        tracks=tracks + [_song("C", source_track_id="sp3")])
    dates = _added(mdb, pid)
    assert dates["A"] == dates["B"] == "2020-01-01 00:00:00"
    assert dates["C"] != "2020-01-01 00:00:00"


# ── the picker's ticks and untick ───────────────────────────────────────


def test_membership_counts_what_each_playlist_already_has(mdb):
    a = up.create_playlist(mdb, 1, "A")
    b = up.create_playlist(mdb, 1, "B")
    up.create_playlist(mdb, 1, "Empty")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(a), [_song("One"), _song("Two")])
    # a different spelling of One still counts, same as the duplicate prompt
    up.add_tracks(mdb, mdb.get_mirrored_playlist(b), [_song("One (Remastered)")])
    result = up.membership(mdb, 1, [_song("One"), _song("Two")])
    assert result == {"total": 2, "playlists": {a: 2, b: 1}}


def test_membership_never_reads_another_profiles_playlists(mdb):
    theirs = up.create_playlist(mdb, 2, "Theirs")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(theirs), [_song("One")])
    assert up.membership(mdb, 1, [_song("One")])["playlists"] == {}


def test_untick_removes_every_copy_and_nothing_else(mdb):
    pid = up.create_playlist(mdb, 1, "Mix")
    pl = mdb.get_mirrored_playlist(pid)
    up.add_tracks(mdb, pl, [_song("One"), _song("Two")])
    up.add_tracks(mdb, pl, [_song("One")], allow_duplicates=True)
    assert up.remove_matching(mdb, pl, [_song("One")]) == {"removed": 2, "track_count": 1}
    assert _titles(mdb, pid) == ["Two"]


def test_untick_routes(mdb, monkeypatch):
    client = _client(mdb, monkeypatch)
    pid = up.create_playlist(mdb, 1, "Mix")
    up.add_tracks(mdb, mdb.get_mirrored_playlist(pid), [_song("One")])
    resp = client.post("/api/user-playlists/membership", json={"tracks": [_song("One")]})
    assert resp.get_json()["playlists"] == {str(pid): 1}
    resp = client.post(f"/api/user-playlists/{pid}/tracks/remove", json={"tracks": [_song("One")]})
    assert resp.get_json()["removed"] == 1
    # another profile's playlist reads as missing
    other = up.create_playlist(mdb, 2, "Theirs")
    resp = client.post(f"/api/user-playlists/{other}/tracks/remove", json={"tracks": [_song("One")]})
    assert resp.status_code == 404


# ── Liked Songs ─────────────────────────────────────────────────────────


def test_liking_makes_one_liked_songs_and_puts_the_newest_first(mdb):
    up.like(mdb, 1, [_song("Old")])
    result = up.like(mdb, 1, [_song("New")])
    liked = [p for p in mdb.get_mirrored_playlists(profile_id=1) if up.is_liked_playlist(p)]
    assert len(liked) == 1
    assert _titles(mdb, result["id"]) == ["New", "Old"]


def test_liking_twice_keeps_one_copy_and_unliking_takes_it_out(mdb):
    up.like(mdb, 1, [_song("One")])
    again = up.like(mdb, 1, [_song("One")])
    assert again["added"] == 0
    assert up.unlike(mdb, 1, [_song("One")])["removed"] == 1
    assert _titles(mdb, again["id"]) == []


def test_unliking_with_no_liked_songs_makes_nothing(mdb):
    assert up.unlike(mdb, 1, [_song("One")])["removed"] == 0
    assert up.liked_playlist(mdb, 1) is None


def test_each_profile_has_its_own_liked_songs(mdb):
    mine = up.like(mdb, 1, [_song("Mine")])["id"]
    theirs = up.like(mdb, 2, [_song("Theirs")])["id"]
    assert mine != theirs
    assert _titles(mdb, mine) == ["Mine"]


def test_liked_songs_is_pinned_first_in_the_list(mdb):
    up.like(mdb, 1, [_song("One")])
    up.create_playlist(mdb, 1, "Newer")
    rows = up.list_playlists(mdb, 1)
    assert rows[0]["name"] == "Liked Songs" and rows[0]["liked"] is True
    assert rows[1]["liked"] is False


def test_like_routes(mdb, monkeypatch):
    client = _client(mdb, monkeypatch)
    resp = client.post("/api/user-playlists/liked", json={"tracks": [_song("One")]})
    assert resp.get_json()["added"] == 1
    resp = client.post("/api/user-playlists/liked/remove", json={"tracks": [_song("One")]})
    assert resp.get_json()["removed"] == 1
    assert client.post("/api/user-playlists/liked", json={}).status_code == 400


# ── make a mirror yours ─────────────────────────────────────────────────


def _discovered_mirror(db, profile_id=1):
    pid = db.mirror_playlist(
        source="spotify", source_playlist_id="sp-x", name="Discover Weekly",
        tracks=[_song("A", source_track_id="sp1"), _song("B", source_track_id="sp2")],
        profile_id=profile_id, image_url="https://img/cover.jpg", description="weekly",
    )
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE mirrored_playlist_tracks SET extra_data = ?, added_at = '2020-01-01 00:00:00'"
            " WHERE playlist_id = ? AND track_name = 'A'",
            ('{"discovered": true, "matched_data": {"id": "m1"}}', pid),
        )
        conn.commit()
    return pid


def test_a_copied_mirror_keeps_tracks_matches_dates_cover_and_blurb(mdb):
    mirror = _discovered_mirror(mdb)
    result = up.copy_mirror(mdb, mdb.get_mirrored_playlist(mirror), 1)
    copy = mdb.get_mirrored_playlist(result["id"])
    assert up.is_user_playlist(copy)
    assert result["name"] == "Discover Weekly"
    assert copy["image_url"] == "https://img/cover.jpg"
    assert copy["description"] == "weekly"
    rows = mdb.get_mirrored_playlist_tracks(result["id"])
    assert [r["track_name"] for r in rows] == ["A", "B"]
    assert '"m1"' in rows[0]["extra_data"]
    assert rows[0]["added_at"] == "2020-01-01 00:00:00"
    # the mirror itself is left as it was
    assert _titles(mdb, mirror) == ["A", "B"]
    assert not up.is_user_playlist(mdb.get_mirrored_playlist(mirror))


def test_a_media_server_cover_is_not_copied_as_a_poster(mdb):
    mirror = _discovered_mirror(mdb)
    row = dict(mdb.get_mirrored_playlist(mirror), image_url="/library/metadata/1/thumb")
    copy = mdb.get_mirrored_playlist(up.copy_mirror(mdb, row, 1)["id"])
    assert not copy["image_url"]


def test_copy_route_only_takes_your_own_mirror(mdb, monkeypatch):
    client = _client(mdb, monkeypatch)
    mine = _discovered_mirror(mdb)
    resp = client.post(f"/api/user-playlists/from-mirror/{mine}", json={"name": "Mine now"})
    assert resp.get_json()["name"] == "Mine now"
    theirs = _discovered_mirror(mdb, profile_id=2)
    assert client.post(f"/api/user-playlists/from-mirror/{theirs}", json={}).status_code == 404
    # a user playlist is already yours, there is nothing to convert
    own = up.create_playlist(mdb, 1, "Own")
    assert client.post(f"/api/user-playlists/from-mirror/{own}", json={}).status_code == 404
