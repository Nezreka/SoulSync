"""A moved file's old record is settled when the scan sees it, not listed as a
second copy of the song.

wolf39us, Nov 13 2026: "Lost Stars" twice in Songs. One file on disk
(`2014 - Lost Stars/01 - Lost Stars.flac`, 32,170,087 bytes), two records: the
real one on a Maroon 5 track, and a record of `2015 - V (Deluxe)/01 - Lost
Stars.flac`, a folder that no longer existed, still marked as a live file on
the Adam Levine single. He ended up deleting rows from the database by hand."""

from __future__ import annotations

import os
from contextlib import closing

import pytest

from core.library2 import moved_file_records as moved
from core.library2.track_file_move import MoveError, move_track_file
from database.music_database import MusicDatabase

SIZE = 32170087


class _NoWishlist:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def remove_from_wishlist(self, *_a, **_k):
        return False

    def add_to_wishlist(self, *_a, **_k):
        return False


@pytest.fixture
def library(tmp_path):
    """wolf's library: the single and the album, one file on disk."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    music = tmp_path / "music" / "Adam Levine"
    real = music / "2014 - Lost Stars" / "01 - Lost Stars.flac"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"audio")
    gone = music / "2015 - V (Deluxe)" / "01 - Lost Stars.flac"   # never created
    with closing(db._get_connection()) as conn:
        adam = conn.execute("INSERT INTO lib2_artists(name) VALUES('Adam Levine')").lastrowid
        maroon = conn.execute("INSERT INTO lib2_artists(name) VALUES('Maroon 5')").lastrowid
        single_album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
            "VALUES(?, 'Lost Stars', 'single')", (adam,)).lastrowid
        v_album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
            "VALUES(?, 'V', 'album')", (maroon,)).lastrowid
        single = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, duration, monitored) "
            "VALUES(?, 'Lost Stars', 1, 267000, 1)", (single_album,)).lastrowid
        album_track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, duration) "
            "VALUES(?, 'Lost Stars', 15, 267000)", (v_album,)).lastrowid
        old_record = conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
            (single, str(gone), SIZE)).lastrowid
        real_record = conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
            (album_track, str(real), SIZE)).lastrowid
        conn.commit()
    return {"db": db, "single": single, "album_track": album_track,
            "old_record": old_record, "real_record": real_record,
            "real": str(real), "gone": str(gone), "adam": adam}


def _rows(db, *file_ids):
    with closing(db._get_connection()) as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT id, track_id, path, size, file_state, missing_scan_count FROM lib2_track_files "
            f"WHERE id IN ({','.join('?' * len(file_ids))})", file_ids)]


def _state(db, file_id):
    with closing(db._get_connection()) as conn:
        return conn.execute("SELECT COALESCE(file_state,'active') FROM lib2_track_files WHERE id=?",
                            (file_id,)).fetchone()[0]


def _canonical(db, track_id):
    with closing(db._get_connection()) as conn:
        return conn.execute("SELECT canonical_track_id FROM lib2_tracks WHERE id=?",
                            (track_id,)).fetchone()[0]


def _settle(lib, *file_ids, **kwargs):
    kwargs.setdefault("exists", os.path.exists)
    kwargs.setdefault("root_healthy", lambda path: True)
    return moved.settle_absent_rows(lib["db"], _rows(lib["db"], *(file_ids or (lib["old_record"],))),
                                    **kwargs)


def _live_songs(db):
    """the tracks Songs lists: one row per track with an active file"""
    with closing(db._get_connection()) as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT track_id FROM lib2_track_files "
            "WHERE COALESCE(file_state,'active')='active' ORDER BY track_id")]


def test_the_old_record_of_a_moved_file_is_settled_at_once(library):
    assert _live_songs(library["db"]) == [library["single"], library["album_track"]]   # listed twice

    assert _settle(library) == {library["old_record"]: "linked"}

    assert _state(library["db"], library["old_record"]) == "missing_confirmed"
    assert _state(library["db"], library["real_record"]) == "active"
    assert _live_songs(library["db"]) == [library["album_track"]]                      # listed once
    # the single is the same recording as the album track that holds the file,
    # so it is not read as missing and downloaded again
    assert _canonical(library["db"], library["single"]) == library["album_track"]
    assert os.path.exists(library["real"])                                              # disk untouched
    assert _settle(library) == {}                                                       # and it is done


def test_a_spare_record_on_the_same_track_is_dropped_from_the_live_set(library):
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET track_id=? WHERE id=?",
                     (library["album_track"], library["old_record"]))
        conn.commit()
    assert _settle(library) == {library["old_record"]: "spare_record"}
    assert _state(library["db"], library["old_record"]) == "missing_confirmed"
    assert _state(library["db"], library["real_record"]) == "active"
    assert _canonical(library["db"], library["single"]) is None


@pytest.mark.parametrize("change, why", [
    ("UPDATE lib2_track_files SET size=NULL WHERE id={old}", "a size the catalogue never recorded"),
    ("UPDATE lib2_track_files SET size=size+1 WHERE id={real}", "another size"),
    ("UPDATE lib2_track_files SET path=replace(path,'01 - Lost Stars','15 - Lost Stars') WHERE id={real}",
     "another file name"),
    ("UPDATE lib2_track_files SET file_state='missing_confirmed' WHERE id={real}", "a twin that is gone too"),
    ("UPDATE lib2_track_files SET owner_profile_id=7 WHERE id={real}", "another profile's library"),
    ("UPDATE lib2_tracks SET duration=90000 WHERE id={album_track}", "a different length: not the same recording"),
    ("UPDATE lib2_tracks SET canonical_track_id=(SELECT MIN(id) FROM lib2_tracks) WHERE id={album_track}",
     "a twin that is itself a duplicate of something"),
])
def test_anything_less_certain_is_left_to_the_ordinary_lifecycle(library, change, why):
    with closing(library["db"]._get_connection()) as conn:
        if "canonical_track_id=(SELECT" in change:
            other = conn.execute("INSERT INTO lib2_tracks(album_id, title) SELECT album_id, 'Other' "
                                 "FROM lib2_tracks WHERE id=?", (library["album_track"],)).lastrowid
            change = change.replace("(SELECT MIN(id) FROM lib2_tracks)", str(other))
        conn.execute(change.format(old=library["old_record"], real=library["real_record"],
                                   album_track=library["album_track"]))
        conn.commit()
    assert _settle(library) == {}, why
    assert _state(library["db"], library["old_record"]) == "active"
    assert _canonical(library["db"], library["single"]) is None


def test_two_present_candidates_are_not_guessed_between(library, tmp_path):
    second = tmp_path / "music" / "Adam Levine" / "Other" / "01 - Lost Stars.flac"
    second.parent.mkdir(parents=True)
    second.write_bytes(b"audio")
    with closing(library["db"]._get_connection()) as conn:
        track = conn.execute("INSERT INTO lib2_tracks(album_id, title) SELECT album_id, 'Lost Stars' "
                             "FROM lib2_tracks WHERE id=?", (library["album_track"],)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
                     (track, str(second), SIZE))
        conn.commit()
    assert _settle(library) == {}
    assert _state(library["db"], library["old_record"]) == "active"


def test_a_record_whose_own_storage_cannot_be_judged_is_not_touched(library):
    assert _settle(library, root_healthy=lambda path: False) == {}
    assert _state(library["db"], library["old_record"]) == "active"


def test_a_twin_that_is_not_on_disk_proves_nothing(library):
    os.remove(library["real"])
    assert _settle(library) == {}
    assert _state(library["db"], library["old_record"]) == "active"


def test_tracks_already_linked_the_other_way_are_not_relinked(library):
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("UPDATE lib2_tracks SET canonical_track_id=? WHERE id=?",
                     (library["single"], library["album_track"]))
        conn.commit()
    assert _settle(library) == {library["old_record"]: "linked"}
    assert _canonical(library["db"], library["album_track"]) == library["single"]
    assert _canonical(library["db"], library["single"]) is None
    assert _state(library["db"], library["old_record"]) == "missing_confirmed"


def test_the_scan_settles_it_and_does_not_report_the_song_as_gone(library, monkeypatch):
    from core.library2 import paths, scan

    monkeypatch.setattr(paths, "missing_path_root_is_healthy", lambda path, config_manager=None: True)
    monkeypatch.setattr(paths, "resolve_lib2_path",
                        lambda path, config_manager=None: path if os.path.exists(str(path)) else None)
    stats = {"scanned": 5, "missing": 0}
    presence, confirmed = [], []
    scan._observe_missing(library["db"], _rows(library["db"], library["old_record"]), stats=stats,
                          manual=False, ambiguous=set(), presence=presence, confirmed=confirmed)
    assert stats["moved_records"] == 1 and stats["missing"] == 1
    assert _state(library["db"], library["old_record"]) == "missing_confirmed"
    # not handed to acquisition as a deleted song, not released as deleted outside SoulSync
    assert presence == [] and confirmed == []


def test_a_storage_outage_settles_nothing(library, monkeypatch):
    from core.library2 import scan

    called = []
    monkeypatch.setattr(moved, "settle_absent_rows", lambda *a, **k: called.append(1) or {})
    rows = _rows(library["db"], library["old_record"]) * scan.MASS_MISSING_MIN
    scan._observe_missing(library["db"], rows, stats={"scanned": 0, "missing": 0}, manual=False,
                          ambiguous=set())
    assert called == []


def test_the_file_can_then_be_moved_onto_the_track_it_belongs_to(library):
    """the single and the album track disagree about the artist (Adam Levine,
    Maroon 5). once they are linked as one recording the file moves between
    them, and the dead record on the single does not count as a file."""
    db = _NoWishlist(library["db"])
    with closing(library["db"]._get_connection()) as conn:
        with pytest.raises(MoveError):          # not linked yet: the tags disagree
            move_track_file(db, conn, library["album_track"], library["single"])
        conn.rollback()
    _settle(library)
    with closing(library["db"]._get_connection()) as conn:
        result = move_track_file(db, conn, library["album_track"], library["single"])
    assert result["to_track_id"] == library["single"]
    with closing(library["db"]._get_connection()) as conn:
        assert conn.execute("SELECT track_id FROM lib2_track_files WHERE id=?",
                            (library["real_record"],)).fetchone()[0] == library["single"]
    # the single holds the file now, and the album track is its duplicate
    assert _canonical(library["db"], library["single"]) is None
    assert _canonical(library["db"], library["album_track"]) == library["single"]
    assert _live_songs(library["db"]) == [library["single"]]


def _same_artist(library):
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("UPDATE lib2_albums SET primary_artist_id=?", (library["adam"],))
        conn.commit()


@pytest.mark.parametrize("state, refused", [("active", True), ("missing_suspected", True),
                                            ("missing_confirmed", False)])
def test_only_a_record_whose_file_may_be_there_refuses_a_move(library, state, refused):
    _same_artist(library)
    db = _NoWishlist(library["db"])
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET file_state=? WHERE id=?",
                     (state, library["old_record"]))
        conn.commit()
        if refused:
            with pytest.raises(MoveError, match="already has a file"):
                move_track_file(db, conn, library["album_track"], library["single"])
        else:
            assert move_track_file(db, conn, library["album_track"],
                                   library["single"])["to_track_id"] == library["single"]


def test_a_track_whose_only_record_is_confirmed_gone_has_nothing_to_move(library):
    _same_artist(library)
    db = _NoWishlist(library["db"])
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET file_state='missing_confirmed' WHERE id=?",
                     (library["old_record"],))
        conn.execute("DELETE FROM lib2_track_files WHERE id=?", (library["real_record"],))
        conn.commit()
        with pytest.raises(MoveError, match="no file to move"):
            move_track_file(db, conn, library["single"], library["album_track"])
