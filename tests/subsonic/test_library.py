"""the library as music apps see it (core/subsonic/library.py): what a
profile sees, lists, search, paging, explicit filtering and own libraries."""

from __future__ import annotations

from contextlib import closing

import pytest

from core.library_scope import library_scope
from core.subsonic import library as L
from core.subsonic import response as R


def _lib(mdb, conn, *, scope="shared", hide=False, **kw):
    return L.Library(conn, db_key=str(mdb.database_path), scope=scope, hide_explicit=hide, **kw)


@pytest.fixture
def cat(seed):
    a = seed.artist("The Beatles", sort_name="Beatles, The")
    b = seed.artist("ABBA")
    c = seed.artist("Zappa")
    al1 = seed.album(a, "Abbey Road", year=1969, genres='["Rock","Pop"]', added_at="2024-01-03 00:00:00")
    al2 = seed.album(a, "Help!", year=1965, genres='["Rock"]', added_at="2024-01-01 00:00:00")
    al3 = seed.album(b, "Arrival", year=1976, genres='["Pop"]', added_at="2024-01-02 00:00:00")
    al4 = seed.album(c, "Only Wanted", year=1970)          # no files: a catalogue row
    t = {}
    t["come"] = seed.track(al1, "Come Together", n=1)
    t["something"] = seed.track(al1, "Something", n=2)
    t["help"] = seed.track(al2, "Help!", n=1, explicit=1)
    t["dq"] = seed.track(al3, "Dancing Queen", n=1)
    t["gone"] = seed.track(al3, "Missing One", n=2, file_state="missing_file")
    t["wanted"] = seed.track(al4, "Peaches", path=False)
    return {"artists": (a, b, c), "albums": (al1, al2, al3, al4), "tracks": t}


def test_ids_are_typed_and_a_bare_number_is_a_track():
    assert L.parse_id("ar-5", L.ARTIST) == ("ar", 5)
    assert L.parse_id("7", L.TRACK) == ("tr", 7)
    with pytest.raises(R.SubsonicError) as e:
        L.parse_id("al-5", L.ARTIST)
    assert e.value.code == R.NOT_FOUND
    with pytest.raises(R.SubsonicError):
        L.parse_id("7", L.ALBUM)
    with pytest.raises(R.SubsonicError):
        L.parse_id("tr-x", L.TRACK)


def test_only_albums_and_artists_with_a_live_file_are_seen(mdb, cat):
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        lib = _lib(mdb, conn)
        snap = lib.snap()
        al1, al2, al3, al4 = cat["albums"]
        assert set(snap.by_id) == {al1, al2, al3}
        assert set(snap.artists) == set(cat["artists"][:2])
        assert snap.by_id[al3].songs == 1            # the missing file doesn't count
        names = [a["name"] for g in lib.artist_index() for a in g["artist"]]
        assert names == ["ABBA", "The Beatles"]     # "The" is ignored for the index
        assert [g["name"] for g in lib.artist_index()] == ["A", "B"]


def test_hidden_explicit_leaves_explicit_songs_and_albums_out(mdb, cat, seed):
    al_x = seed.album(cat["artists"][1], "Explicit Album", explicit=1)
    seed.track(al_x, "Bad Words")
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        full = _lib(mdb, conn)
        assert full.song_rows([cat["tracks"]["help"]])
        kid = _lib(mdb, conn, hide=True)
        assert kid.song_rows([cat["tracks"]["help"]]) == []
        snap = kid.snap()
        assert al_x not in snap.by_id
        assert cat["albums"][1] not in snap.by_id    # Help! only had an explicit song
        assert kid.album_rows([al_x]) == []


def test_an_override_marks_a_clean_track_explicit(mdb, cat):
    with mdb._get_connection() as conn:
        conn.execute("INSERT INTO lib2_metadata_overrides (entity_type, entity_id, field_name, value_json) "
                     "VALUES ('track', ?, 'explicit', 'true')", (cat["tracks"]["dq"],))
        conn.commit()
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        assert _lib(mdb, conn, hide=True).song_rows([cat["tracks"]["dq"]]) == []
        assert _lib(mdb, conn).song_rows([cat["tracks"]["dq"]])[0]["is_explicit"]


def test_album_lists(mdb, cat):
    al1, al2, al3, _ = cat["albums"]
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        lib = _lib(mdb, conn)
        assert lib.album_list("alphabeticalByName", size=10, offset=0) == [al1, al3, al2]
        assert lib.album_list("alphabeticalByName", size=1, offset=1) == [al3]
        assert lib.album_list("alphabeticalByArtist", size=10, offset=0) == [al3, al2, al1]
        assert lib.album_list("newest", size=10, offset=0) == [al1, al3, al2]
        assert lib.album_list("byYear", size=10, offset=0, from_year=1960, to_year=1970) == [al2, al1]
        assert lib.album_list("byYear", size=10, offset=0, from_year=1970, to_year=1960) == [al1, al2]
        assert lib.album_list("byGenre", size=10, offset=0, genre="pop") == [al1, al3]
        assert sorted(lib.album_list("random", size=10, offset=0)) == sorted([al1, al2, al3])
        assert lib.album_list("frequent", size=10, offset=0, ranked=[al4 for al4 in (99, al2)]) == [al2]
        with pytest.raises(R.SubsonicError):
            lib.album_list("nonsense", size=10, offset=0)


def test_genres_count_songs_and_albums(mdb, cat):
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        g = {x["value"]: x for x in _lib(mdb, conn).genres()}
        assert g["Rock"] == {"value": "Rock", "songCount": 3, "albumCount": 2}
        assert g["Pop"]["albumCount"] == 2


def test_search_and_the_whole_library_paging(mdb, cat):
    t = cat["tracks"]
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        lib = _lib(mdb, conn)
        artists, albums, songs = lib.search("beat", artist_count=5, artist_offset=0, album_count=5,
                                            album_offset=0, song_count=5, song_offset=0)
        assert artists == [cat["artists"][0]]
        assert set(albums) == {cat["albums"][0], cat["albums"][1]}
        _, _, songs = lib.search("queen", artist_count=0, artist_offset=0, album_count=0, album_offset=0,
                                 song_count=5, song_offset=0)
        assert songs == [t["dq"]]
        # an empty query pages through every playable song, once each
        seen = []
        for off in range(0, 10, 2):
            seen += lib.search('""', artist_count=0, artist_offset=0, album_count=0, album_offset=0,
                               song_count=2, song_offset=off)[2]
        assert sorted(seen) == sorted([t["come"], t["something"], t["help"], t["dq"]])
        assert len(seen) == len(set(seen))


def test_song_shape(mdb, cat):
    t = cat["tracks"]
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        song = _lib(mdb, conn, starred={(L.TRACK, t["come"]): "2024-01-01T00:00:00.000Z"},
                    ratings={(L.TRACK, t["come"]): 4}).song(t["come"])
    assert song["id"] == "tr-%d" % t["come"] and song["parent"] == "al-%d" % cat["albums"][0]
    assert song["isDir"] is False and song["type"] == "music" and song["mediaType"] == "song"
    assert song["duration"] == 180 and song["bitRate"] == 320 and song["suffix"] == "mp3"
    assert song["contentType"] == "audio/mpeg" and song["artist"] == "The Beatles"
    assert song["starred"] and song["userRating"] == 4
    # a path inside the library, never the real one
    assert not song["path"].startswith("/music")


def test_own_library_sees_only_its_files(mdb, seed, monkeypatch):
    # someone keeps a library of their own (profiles.library_mode = 'own')
    monkeypatch.setattr("core.library_scope.any_own_library_exists", lambda: True)
    a = seed.artist("Shared Artist")
    al = seed.album(a, "Shared")
    shared = seed.track(al, "Shared Song")
    mine = seed.track(seed.album(a, "Mine"), "My Song", owner=5)
    with closing(mdb._get_connection()) as conn:
        with library_scope(5):
            lib = _lib(mdb, conn, scope=5)
            assert [r["id"] for r in lib.song_rows([shared, mine])] == [mine]
        with library_scope("shared"):
            lib = _lib(mdb, conn, scope="shared")
            assert [r["id"] for r in lib.song_rows([shared, mine])] == [shared]


def test_find_track_by_name(mdb, cat):
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        lib = _lib(mdb, conn)
        assert lib.find_track("Come Together", "The Beatles") == cat["tracks"]["come"]
        assert lib.find_track("come together", "Beatles") == cat["tracks"]["come"]
        assert lib.find_track("Come Together", "Someone Else") is None
        assert lib.find_track("Peaches", "Zappa") is None      # no file


def test_the_snapshot_is_rebuilt_when_a_file_changes(mdb, cat, seed):
    with closing(mdb._get_connection()) as conn, library_scope("shared"):
        lib = _lib(mdb, conn)
        first = lib.snap()
        assert lib.snap() is first
        seed.track(cat["albums"][3], "Now Here")
        assert cat["albums"][3] in lib.snap().by_id
