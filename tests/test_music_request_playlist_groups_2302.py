"""#2302: the songs a playlist sync asks for are one request, under the
playlist's name, that can be answered whole or song by song."""

from core.requests.music import describe, group_key, group_rows, narrow, playlist_of


def _row(tid, title, artist, album_id="al1", album="Album", source_type="album", info=None, when="2026-11-25 10:00:00"):
    return {"spotify_track_id": tid, "source_type": source_type, "date_added": when, "source_info": info or {},
            "spotify_data": {"id": tid, "name": title, "artists": [{"name": artist}],
                             "album": {"id": album_id, "name": album, "album_type": "album",
                                       "images": [{"url": f"http://img/{album_id}"}]}}}


ROAD = {"playlist_name": "Road Trip", "playlist_id": "pl42", "sync_type": "automatic_sync"}


def test_a_playlists_songs_are_one_request_whatever_albums_they_are_from():
    rows = [_row("t1", "One", "A", "al1", "First", "playlist", ROAD),
            _row("t2", "Two", "B", "al2", "Second", "playlist", ROAD),
            _row("t3", "Three", "A", "al1", "First", "playlist", ROAD),
            _row("t4", "Alone", "C", "al9", "Loose", "album")]
    groups = {g["key"]: g for g in group_rows(rows, 3, "Kid")}
    assert set(groups) == {"playlist:pl42", "album:al9"}
    road = groups["playlist:pl42"]
    assert road["kind"] == "playlist" and road["title"] == "Road Trip" and road["track_count"] == 3
    assert road["track_ids"] == ["t1", "t2", "t3"]
    assert [t["title"] for t in road["tracks"]] == ["One", "Two", "Three"]
    assert road["playlist"] == {"id": "pl42", "name": "Road Trip", "source": ""}
    assert road["artist"] == "" and road["requester_name"] == "Kid"
    assert describe(road) == "3 songs from Road Trip"
    assert groups["album:al9"]["kind"] == "album"


def test_two_playlists_and_a_playlist_with_no_id_stay_apart():
    other = {"playlist_name": "Gym", "playlist_id": "pl7"}
    by_name = {"playlist_name": "Named Only"}
    rows = [_row("t1", "One", "A", source_type="playlist", info=ROAD),
            _row("t2", "Two", "A", source_type="playlist", info=other),
            _row("t3", "Three", "A", source_type="playlist", info=by_name),
            _row("t4", "Four", "A", source_type="playlist", info=dict(by_name))]
    assert sorted(g["key"] for g in group_rows(rows, 3)) == ["playlist:named only", "playlist:pl42", "playlist:pl7"]


def test_only_a_playlist_sync_makes_a_playlist_request():
    # an album ask, a row with no origin recorded, and junk in the origin: as before
    assert group_key(_row("t1", "One", "A")) == "album:al1"
    assert group_key(_row("t1", "One", "A", source_type="playlist", info={})) == "album:al1"
    assert group_key(_row("t1", "One", "A", source_type="playlist", info="not json")) == "album:al1"
    assert playlist_of(_row("t1", "One", "A", source_type="album", info=ROAD)) is None
    # the origin as the json text it is stored as
    assert group_key(_row("t1", "One", "A", source_type="playlist",
                          info='{"playlist_name": "Road Trip", "playlist_id": "pl42"}')) == "playlist:pl42"


def test_part_of_a_playlist_can_be_answered_on_its_own():
    rows = [_row(f"t{i}", f"Song {i}", "A", source_type="playlist", info=ROAD) for i in range(1, 6)]
    road = group_rows(rows, 3)[0]
    some = narrow(road, ["t2", "t4", "not-in-it"])
    assert some["track_ids"] == ["t2", "t4"] and some["track_count"] == 2 and some["part_of"] == 5
    assert [t["title"] for t in some["tracks"]] == ["Song 2", "Song 4"]
    assert some["key"] != road["key"] and some["key"].startswith("playlist:pl42#")
    assert describe(some) == "2 songs from Road Trip"
    assert narrow(road, ["t1", "t2", "t3", "t4", "t5"]) is road        # all of it is the request itself
    assert narrow(road, ["nope"]) is None and narrow(road, []) is None
    assert road["track_count"] == 5, "the request itself is untouched"
