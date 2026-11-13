"""#2070 (wolf39us): take the Library's metadata from the files' own tags.

Two things he showed. An album the Library dates 2021 whose tags say 2005. And
"Pink", filed under a 2022 best-of, whose tag says it is on Nine Lives.
"""

from __future__ import annotations

import pytest

from core.library2 import tags_to_library
from core.library2.metadata_overrides import get_field_overrides


class _DB:
    """The module opens its own connections; wishlist calls answer nothing."""

    def __init__(self, shim):
        self._shim = shim

    def _get_connection(self):
        return self._shim._get_connection()

    def remove_from_wishlist(self, *_a, **_k):
        return False

    def add_to_wishlist(self, *_a, **_k):
        return False


@pytest.fixture
def lib(imported_conn, legacy_db):
    conn = imported_conn
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Aerosmith')")
    artist = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type, year) "
                "VALUES(?, 'The Very Best of Aerosmith', 'album', 2022)", (artist,))
    best_of = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (best_of, artist))
    tracks = {}
    for number, title in ((1, "Pink"), (2, "Dream On"), (3, "Crazy")):
        cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
                    "VALUES(?,?,?,1,1)", (best_of, title, number))
        tracks[title] = cur.lastrowid
        cur.execute("INSERT INTO lib2_track_files(track_id, path, format) VALUES(?,?, 'flac')",
                    (tracks[title], f"/m/{title}.flac"))
    conn.commit()
    return {"db": _DB(legacy_db), "conn": conn, "artist": artist, "album": best_of, "tracks": tracks}


def _reader(by_name, monkeypatch):
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda p: p)
    return lambda path: by_name.get(path.rsplit("/", 1)[-1].removesuffix(".flac"), {})


def _best_of(**over):
    return {"title": None, "album": "The Very Best of Aerosmith", "year": "2022",
            "track_number": None, "disc_number": 1, "genre": None, **over}


def test_an_albums_year_and_genre_come_from_what_most_files_say(lib, monkeypatch):
    read = _reader({
        "Pink": _best_of(title="Pink", year="2005-04-12", genre="Hard Rock; Rock", track_number="1"),
        "Dream On": _best_of(title="Dream On", year="2005-04-12", genre="Hard Rock; Rock", track_number="2"),
        "Crazy": _best_of(title="Crazy", year="1994", genre="Rock", track_number="3"),
    }, monkeypatch)
    ids = list(lib["tracks"].values())

    plan = tags_to_library.plan(lib["db"], ids, read_tags=read)

    album = plan["albums"][0]
    assert album["rename"] is None
    assert {c["field"]: (c["library"], c["file"]) for c in album["changes"]} == {
        "year": (2022, 2005), "release_date": (None, "2005-04-12"),
        "genres": ([], ["Hard Rock", "Rock"])}
    assert plan["counts"] == {"tracks": 3, "changed_tracks": 0, "moves": 0, "albums": 1,
                              "unreadable": 0, "held": 0}

    done = tags_to_library.apply(lib["db"], ids, read_tags=read)

    assert done["albums_changed"] == 1 and done["moved"] == 0
    saved = get_field_overrides(lib["conn"], entity_type="release_group", entity_id=lib["album"])
    assert saved["year"].value == 2005 and saved["release_date"].value == "2005-04-12"
    assert saved["genres"].value == ["Hard Rock", "Rock"]
    # a second look finds nothing left to do
    again = tags_to_library.plan(lib["db"], ids, read_tags=read)
    assert again["albums"] == [] and again["counts"]["changed_tracks"] == 0


def test_a_track_tagged_with_another_album_moves_there(lib, monkeypatch):
    read = _reader({
        "Pink": _best_of(title="Pink", album="Nine Lives", year="1997", track_number="7"),
        "Dream On": _best_of(title="Dream On", track_number="2"),
        "Crazy": _best_of(title="Crazy", track_number="3"),
    }, monkeypatch)
    ids = list(lib["tracks"].values())

    plan = tags_to_library.plan(lib["db"], ids, read_tags=read)

    pink = next(t for t in plan["tracks"] if t["title"] == "Pink")
    assert pink["move"]["album_title"] == "Nine Lives"
    assert {c["field"]: c["file"] for c in pink["move"]["album_changes"]} == {"year": 1997}
    assert plan["counts"]["moves"] == 1
    # the album the other two stay on is not renamed and keeps its year
    assert plan["albums"] == []

    done = tags_to_library.apply(lib["db"], ids, read_tags=read)

    assert done["moved"] == 1 and done["albums_created"] == 1 and done["failed"] == []
    conn = lib["conn"]
    row = conn.execute(
        "SELECT a.id, a.title, a.primary_artist_id, t.id AS track_id FROM lib2_track_files f "
        "JOIN lib2_tracks t ON t.id = f.track_id JOIN lib2_albums a ON a.id = t.album_id "
        "WHERE f.path = '/m/Pink.flac'").fetchone()
    assert (row["title"], row["primary_artist_id"]) == ("Nine Lives", lib["artist"])
    assert row["track_id"] != lib["tracks"]["Pink"]
    assert get_field_overrides(conn, entity_type="release_group", entity_id=row["id"])["year"].value == 1997
    # the best-of keeps its other two files and its own year
    kept = conn.execute(
        "SELECT COUNT(*) FROM lib2_track_files f JOIN lib2_tracks t ON t.id = f.track_id "
        "WHERE t.album_id = ?", (lib["album"],)).fetchone()[0]
    assert kept == 2
    assert "year" not in get_field_overrides(conn, entity_type="release_group", entity_id=lib["album"])


def test_every_file_naming_the_same_other_album_is_a_rename(lib, monkeypatch):
    read = _reader({name: _best_of(title=name, album="Greatest Hits 1973-1988")
                    for name in lib["tracks"]}, monkeypatch)
    ids = list(lib["tracks"].values())

    plan = tags_to_library.plan(lib["db"], ids, read_tags=read)

    assert plan["albums"][0]["rename"] == "Greatest Hits 1973-1988"
    assert plan["counts"]["moves"] == 0

    done = tags_to_library.apply(lib["db"], ids, read_tags=read)
    assert done["albums_renamed"] == 1 and done["moved"] == 0
    assert get_field_overrides(lib["conn"], entity_type="release_group",
                               entity_id=lib["album"])["title"].value == "Greatest Hits 1973-1988"


def test_one_track_of_an_album_is_never_a_rename(lib, monkeypatch):
    # only Pink was asked for: its tag naming another album cannot rename the
    # album its two neighbours are still on
    read = _reader({"Pink": _best_of(title="Pink", album="Nine Lives")}, monkeypatch)

    plan = tags_to_library.plan(lib["db"], [lib["tracks"]["Pink"]], read_tags=read)

    assert plan["albums"] == [] and plan["tracks"][0]["move"]["album_title"] == "Nine Lives"


def test_track_fields_are_saved_as_manual_edits(lib, monkeypatch):
    read = _reader({"Pink": _best_of(title="Pink (Album Version)", track_number="07/13", disc_number="2")},
                   monkeypatch)
    track = lib["tracks"]["Pink"]

    plan = tags_to_library.plan(lib["db"], [track], read_tags=read)
    assert {c["field"]: (c["library"], c["file"]) for c in plan["tracks"][0]["changes"]} == {
        "title": ("Pink", "Pink (Album Version)"), "track_number": (1, 7), "disc_number": (1, 2)}

    tags_to_library.apply(lib["db"], [track], read_tags=read)
    saved = get_field_overrides(lib["conn"], entity_type="track", entity_id=track)
    assert (saved["title"].value, saved["track_number"].value, saved["disc_number"].value) == (
        "Pink (Album Version)", 7, 2)


def test_an_empty_tag_never_blanks_the_library(lib, monkeypatch):
    read = _reader({"Pink": {"title": "", "album": "", "year": None, "track_number": None,
                             "disc_number": None, "genre": ""}}, monkeypatch)

    plan = tags_to_library.plan(lib["db"], [lib["tracks"]["Pink"]], read_tags=read)

    assert plan["tracks"][0]["changes"] == [] and plan["tracks"][0]["move"] is None
    assert plan["albums"] == []


def test_a_file_that_cannot_be_read_is_reported_and_left_alone(lib, monkeypatch):
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda p: p)

    def read(path):
        if path.endswith("Pink.flac"):
            return {"error": "not an audio file"}
        return _best_of(title=path.rsplit("/", 1)[-1].removesuffix(".flac"))

    plan = tags_to_library.plan(lib["db"], list(lib["tracks"].values()), read_tags=read)

    assert plan["counts"]["unreadable"] == 1
    pink = next(t for t in plan["tracks"] if t["title"] == "Pink")
    assert pink.get("unreadable") is True and pink["changes"] == []
    # two readable files out of three is not the whole album, so no rename
    assert plan["albums"] == []


def test_a_different_artist_in_the_tag_is_said_not_done(lib, monkeypatch):
    read = _reader({"Pink": _best_of(title="Pink", artist="Aerosmith feat. Someone",
                                     album_artist="Various Artists")}, monkeypatch)

    plan = tags_to_library.plan(lib["db"], [lib["tracks"]["Pink"]], read_tags=read)

    notes = plan["tracks"][0]["notes"]
    assert any("album artist is Various Artists" in n for n in notes)
    assert plan["tracks"][0]["changes"] == []


# --- #2084: more changes after the Library took its values from the files ----

def test_files_naming_an_album_the_artist_already_has_move_onto_it(lib, monkeypatch):
    # every file of the best-of names Nine Lives, and Nine Lives is in the
    # Library: a rename would have made a second Nine Lives
    conn = lib["conn"]
    nine = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                        "VALUES(?, 'Nine Lives', 'album')", (lib["artist"],)).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (nine, lib["artist"]))
    conn.commit()
    read = _reader({name: _best_of(title=name, album="nine lives") for name in lib["tracks"]},
                   monkeypatch)
    ids = list(lib["tracks"].values())

    plan = tags_to_library.plan(lib["db"], ids, read_tags=read)
    assert plan["albums"] == [] and plan["counts"]["moves"] == 3

    done = tags_to_library.apply(lib["db"], ids, read_tags=read)
    assert done["moved"] == 3 and done["albums_renamed"] == 0 and done["albums_created"] == 0
    assert "title" not in get_field_overrides(conn, entity_type="release_group", entity_id=lib["album"])
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_track_files f JOIN lib2_tracks t ON t.id = f.track_id "
        "WHERE t.album_id=?", (nine,)).fetchone()[0] == 3


def test_a_caller_that_knows_the_tracks_are_leaving_never_gets_a_rename(lib, monkeypatch):
    read = _reader({name: _best_of(title=name, album="Greatest Hits 1973-1988")
                    for name in lib["tracks"]}, monkeypatch)
    ids = list(lib["tracks"].values())

    plan = tags_to_library.plan(lib["db"], ids, read_tags=read, allow_rename=False)
    assert plan["albums"] == [] and plan["counts"]["moves"] == 3

    done = tags_to_library.apply(lib["db"], ids, read_tags=read, allow_rename=False)
    assert done["moved"] == 3 and done["albums_renamed"] == 0


def _take_from_files(lib, monkeypatch):
    read = _reader({
        "Pink": _best_of(title="Pink!", year="2005", genre="Rock", track_number="4"),
        "Dream On": _best_of(title="Dream On", year="2005", genre="Rock", track_number="2"),
        "Crazy": _best_of(title="Crazy", year="2005", genre="Rock", track_number="3"),
    }, monkeypatch)
    ids = list(lib["tracks"].values())
    tags_to_library.apply(lib["db"], ids, read_tags=read)
    return read, ids


def test_the_plan_counts_the_values_already_taken_from_files(lib, monkeypatch):
    read, ids = _take_from_files(lib, monkeypatch)
    # Pink's title and number, the album's year and genre
    assert tags_to_library.plan(lib["db"], ids, read_tags=read)["counts"]["held"] == 4
    assert tags_to_library.plan(lib["db"], [lib["tracks"]["Crazy"]], read_tags=read)["counts"]["held"] == 2
    assert tags_to_library.held_values(lib["conn"]) == 4


def test_release_lets_go_of_file_tag_values_and_keeps_hand_edits(lib, monkeypatch):
    from core.library2.metadata_overrides import set_field_override

    _read, ids = _take_from_files(lib, monkeypatch)
    conn = lib["conn"]
    set_field_override(conn, entity_type="track", entity_id=lib["tracks"]["Crazy"],
                       field_name="title", value="Crazy (my edit)", reason=None)
    set_field_override(conn, entity_type="release_group", entity_id=lib["album"],
                       field_name="release_date", value="2005-01-01", reason="edit dialog")
    conn.commit()

    # one track: its own values and its album's
    assert tags_to_library.release(lib["db"], [lib["tracks"]["Dream On"]]) == {"released": 2}
    assert set(get_field_overrides(conn, entity_type="release_group", entity_id=lib["album"])) == {
        "release_date"}
    assert set(get_field_overrides(conn, entity_type="track", entity_id=lib["tracks"]["Pink"])) == {
        "title", "track_number"}

    # the whole Library
    assert tags_to_library.release(lib["db"]) == {"released": 2}
    assert get_field_overrides(conn, entity_type="track", entity_id=lib["tracks"]["Pink"]) == {}
    assert get_field_overrides(conn, entity_type="track", entity_id=lib["tracks"]["Crazy"])[
        "title"].value == "Crazy (my edit)"
    assert tags_to_library.release(lib["db"]) == {"released": 0}
    assert tags_to_library.held_values(conn) == 0


def test_release_is_the_admins_to_do(lib, monkeypatch):
    from core.library2.metadata_overrides import MetadataOverrideError

    _take_from_files(lib, monkeypatch)
    monkeypatch.setattr("core.acquisition.requests.actor_is_admin", lambda conn, profile_id: False)
    with pytest.raises(MetadataOverrideError):
        tags_to_library.release(lib["db"], profile_id=7)
    assert tags_to_library.held_values(lib["conn"]) == 4
