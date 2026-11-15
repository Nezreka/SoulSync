"""The scan, with the real path resolver, sees the old record of a moved file.

wolf39us, Nov 14 2026: Dead File Cleaner never listed the record of
`Adam Levine/2015 - V (Deluxe)/01 - Lost Stars.flac`, a folder that was gone.
The resolver's last resort for a wrong album folder looks through the artist's
other album folders, found `2014 - Lost Stars/01 - Lost Stars.flac` (the file
another record holds at exactly that path) and handed it over, so the dead
record was probed and verified as that file on every scan. The moved-file
tests stubbed the resolver with os.path.exists, which is why they were green."""

from __future__ import annotations

import os
from contextlib import closing

import pytest

from core.library2 import paths, scan
from database.music_database import MusicDatabase

# the size the scan will measure: both records were sized from the one file
AUDIO = b"audio"
SIZE = len(AUDIO)


class _Config:
    def __init__(self, root):
        self._values = {"library.music_paths": [root], "soulseek.transfer_path": root}

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "music"
    real = root / "Adam Levine" / "2014 - Lost Stars" / "01 - Lost Stars.flac"
    real.parent.mkdir(parents=True)
    real.write_bytes(AUDIO)
    gone = root / "Adam Levine" / "2015 - V (Deluxe)" / "01 - Lost Stars.flac"   # never created
    config = _Config(str(root))
    real_resolve = paths.resolve_lib2_path
    monkeypatch.setattr(paths, "resolve_lib2_path",
                        lambda path, config_manager=None: real_resolve(path, config))
    real_healthy = paths.missing_path_root_is_healthy
    monkeypatch.setattr(paths, "missing_path_root_is_healthy",
                        lambda path, config_manager=None: real_healthy(path, config))
    db = MusicDatabase(str(tmp_path / "music.db"))
    # the holder check asks the app's database
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
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
            "VALUES(?, 'Lost Stars', 1, 267933, 1)", (single_album,)).lastrowid
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
    return {"config": config, "db": db, "single": single, "album_track": album_track, "old_record": old_record,
            "real_record": real_record, "real": str(real), "gone": str(gone), "root": root}


def _file(db, file_id):
    with closing(db._get_connection()) as conn:
        return dict(conn.execute(
            "SELECT COALESCE(file_state,'active') AS state, path FROM lib2_track_files WHERE id=?",
            (file_id,)).fetchone())


def _canonical(db, track_id):
    with closing(db._get_connection()) as conn:
        return conn.execute("SELECT canonical_track_id FROM lib2_tracks WHERE id=?",
                            (track_id,)).fetchone()[0]


def test_the_last_resort_alone_finds_the_other_records_file(library):
    """the trap itself: asked for the folder that is gone, the sibling album
    folder search answers with the twin"""
    from core.library.path_resolver import _resolve_via_sibling_album_folder

    assert not os.path.exists(library["gone"])
    parts = [part for part in library["gone"].replace("\\", "/").split("/") if part]
    found = _resolve_via_sibling_album_folder(parts, [str(library["root"])])
    assert os.path.samefile(found, library["real"])


def test_the_resolver_itself_refuses_it_so_callers_that_skip_the_library_wrapper_are_covered(library):
    """repair jobs and routes call the resolver directly"""
    from core.library.path_resolver import (
        resolve_library_file_path, resolve_via_last_resort_fallbacks,
    )

    assert resolve_library_file_path(library["gone"], config_manager=library["config"]) is None
    assert resolve_via_last_resort_fallbacks(library["gone"], [str(library["root"])]) is None


def test_the_library_does_not_take_another_records_file_for_the_dead_one(library):
    """what every Library operation asks: delete, retag, replaygain, lyrics, art"""
    assert paths.resolve_lib2_path(library["gone"]) is None
    assert os.path.samefile(paths.resolve_lib2_path(library["real"]), library["real"])


def test_a_path_no_record_holds_resolves_as_before(library):
    """a legacy or made-up path that is not a Library record's is not judged"""
    other = str(library["root"] / "Adam Levine" / "Some Other Folder" / "01 - Lost Stars.flac")
    assert os.path.samefile(paths.resolve_lib2_path(other), library["real"])


def test_one_unattended_scan_settles_the_dead_record(library):
    stats = scan.rescan_files(library["db"], file_ids=[library["old_record"], library["real_record"]])

    assert _file(library["db"], library["old_record"])["state"] == "missing_confirmed"
    assert _file(library["db"], library["real_record"])["state"] == "active"
    # the single is the same recording as the track that holds the file
    assert _canonical(library["db"], library["single"]) == library["album_track"]
    assert stats.get("moved_records") == 1
    assert os.path.exists(library["real"])


def test_the_dead_record_is_seen_when_scanned_on_its_own(library):
    """Dead File Cleaner scans in chunks: the twin may be in another one"""
    scan.rescan_files(library["db"], file_ids=[library["old_record"]])
    assert _file(library["db"], library["old_record"])["state"] == "missing_confirmed"


def test_a_path_the_server_made_up_still_resolves_when_no_record_holds_the_file(library):
    """#1127: a wrong album folder with no other record on the real file is
    that record's file, as before"""
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("DELETE FROM lib2_track_files WHERE id=?", (library["real_record"],))
        conn.commit()
    scan.rescan_files(library["db"], file_ids=[library["old_record"]])
    assert _file(library["db"], library["old_record"])["state"] == "active"


def test_a_record_in_another_profiles_library_does_not_take_the_file_away(library):
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET owner_profile_id=7 WHERE id=?",
                     (library["real_record"],))
        conn.commit()
    scan.rescan_files(library["db"], file_ids=[library["old_record"]])
    assert _file(library["db"], library["old_record"])["state"] == "active"


def test_a_record_already_missing_does_not_take_the_file_away(library):
    with closing(library["db"]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET file_state='missing_confirmed' WHERE id=?",
                     (library["real_record"],))
        conn.commit()
    scan.rescan_files(library["db"], file_ids=[library["old_record"]])
    assert _file(library["db"], library["old_record"])["state"] == "active"


@pytest.mark.parametrize("stored, found, expected", [
    ("/data/music/A/2015 - V/01 - S.flac", "/app/music/A/2014 - S/01 - S.flac",
     "/data/music/A/2014 - S/01 - S.flac"),
    ("A/Guero/01-01 - E-Pro.flac", "/music/A/A - Guero/01 - E-Pro.flac",
     "A/A - Guero/01 - E-Pro.flac"),
    ("D:\\Music\\A\\Old\\01 - S.flac", "D:\\Music\\A\\New\\01 - S.flac",
     "D:\\Music\\A\\New\\01 - S.flac"),
])
def test_where_the_holder_would_have_stored_it(stored, found, expected):
    assert expected in paths._path_where_found(stored, found)
