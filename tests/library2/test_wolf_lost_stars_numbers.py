"""wolf39us, Nov 18: Maroon 5's "V" read "1 Maps" (missing) and "1 Lost Stars"
with the file `2014 - V/15 - Lost Stars.flac`. Other versions then lined
"1. Lost Stars" up with "1. Maps" (the mapper itself is covered in
tests/imports/test_reassign_album_mapping.py). Here: the row's number, the
tracklist pass that let a file claim another song's slot, and the deleted
file that a Refresh & Scan kept finding in another album folder.
"""

from __future__ import annotations

import json
import os
from contextlib import closing

import pytest

from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn
from tests.library2.test_api_routes import api  # noqa: F401 - the route fixture

V_STANDARD = [("Maps", 190000), ("Animals", 231000), ("It Was Always You", 240000),
              ("Unkiss Me", 238000), ("Sugar", 235000), ("Leaving California", 204000),
              ("In Your Pocket", 220000), ("New Love", 197000), ("Coming Back for You", 227000),
              ("Feelings", 195000), ("My Heart Is Open (feat. Gwen Stefani)", 238000),
              ("This Summer's Gonna Hurt Like a MotherF****r", 224000), ("Shoot Love", 190000),
              ("Sex and Candy", 265000)]
LOST_STARS_MS = 268000


@pytest.fixture
def conn(tmp_path):
    conn = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(conn)
    yield conn
    conn.close()


def _album_v(conn, *, tracklist=False):
    artist = conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES('Maroon 5','Maroon 5')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, year, tracklist_json)"
        " VALUES(?, 'V', 'album', 2015, ?)",
        (artist, json.dumps([{"title": t, "track_number": i, "disc_number": 1, "duration_ms": ms}
                             for i, (t, ms) in enumerate(V_STANDARD, start=1)]) if tracklist else None),
    ).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'primary')",
                 (album, artist))
    return artist, album


def _track(conn, album, title, number, duration, path=None, tags=None):
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, duration)"
        " VALUES(?,?,?,1,?)", (album, title, number, duration)).lastrowid
    if path:
        conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary, tags_json)"
                     " VALUES(?,?,1,?)", (track, path, json.dumps(tags or {})))
    return track


def _rows(conn, album):
    return [(r["title"], r["track_number"]) for r in conn.execute(
        "SELECT title, track_number FROM lib2_tracks WHERE album_id=? ORDER BY track_number, id",
        (album,))]


def _lost_stars(conn, album, number=1, tags=None):
    return _track(conn, album, "Lost Stars", number, LOST_STARS_MS,
                  "/music/Maroon 5/2014 - V/15 - Lost Stars.flac",
                  {"track_number": "15"} if tags is None else tags)


# ── the row's number ────────────────────────────────────────────────────────

def test_the_reported_album_heals_lost_stars_to_its_own_number(conn):
    """the state on the screenshot: Maps and Lost Stars both at 1"""
    from core.library2.track_numbers import heal_colliding_numbers
    _artist, album = _album_v(conn, tracklist=True)
    _track(conn, album, "Maps", 1, 190000)
    lost = _lost_stars(conn, album)
    _track(conn, album, "Sugar", 5, 235000, "/music/Maroon 5/2014 - V/05 - Sugar.flac",
           {"track_number": "5"})

    assert heal_colliding_numbers(conn, [album]) == 1

    assert conn.execute("SELECT track_number FROM lib2_tracks WHERE id=?", (lost,)).fetchone()[0] == 15
    assert ("Maps", 1) in _rows(conn, album)


def test_the_filename_speaks_when_the_tag_is_unread(conn):
    from core.library2.track_numbers import heal_colliding_numbers
    _artist, album = _album_v(conn, tracklist=True)
    _track(conn, album, "Maps", 1, 190000)
    lost = _lost_stars(conn, album, tags={})

    assert heal_colliding_numbers(conn, [album]) == 1
    assert conn.execute("SELECT track_number FROM lib2_tracks WHERE id=?", (lost,)).fetchone()[0] == 15


def test_a_hand_set_number_stays(conn):
    from core.library2.track_numbers import heal_colliding_numbers
    _artist, album = _album_v(conn, tracklist=True)
    _track(conn, album, "Maps", 1, 190000)
    lost = _lost_stars(conn, album)
    conn.execute("INSERT INTO lib2_metadata_overrides(entity_type, entity_id, field_name, value_json)"
                 " VALUES('track', ?, 'track_number', '1')", (lost,))

    assert heal_colliding_numbers(conn, [album]) == 0


def test_a_taken_number_or_the_same_song_is_left_alone(conn):
    """the file's number is held by another row: no guess. and a file whose
    own tag says this position is where it belongs (a translated title)."""
    from core.library2.track_numbers import heal_colliding_numbers
    _artist, album = _album_v(conn)
    _track(conn, album, "Maps", 1, 190000)
    lost = _lost_stars(conn, album)
    _track(conn, album, "Bonus", 15, 200000, "/music/Maroon 5/2014 - V/15 - Bonus.flac",
           {"track_number": "15"})
    _track(conn, album, "Animals", 2, 231000)
    translated = _track(conn, album, "Animaux", 2, 231000, "/music/Maroon 5/2014 - V/02 - Animaux.flac",
                        {"track_number": "2"})

    assert heal_colliding_numbers(conn, [album]) == 0
    assert conn.execute("SELECT track_number FROM lib2_tracks WHERE id=?", (lost,)).fetchone()[0] == 1
    assert conn.execute("SELECT track_number FROM lib2_tracks WHERE id=?", (translated,)).fetchone()[0] == 2


def test_the_tracklist_pass_gives_maps_its_own_row_and_lost_stars_its_number(conn):
    """the tracklist pass claimed a row by position alone: the Lost Stars file
    at 1 became "Maps"' row (kept its title, Maps never existed)"""
    from core.library2.completeness import _persist_tracklist_tracks
    _artist, album = _album_v(conn)
    lost = _lost_stars(conn, album)
    entries = [{"title": t, "track_number": i, "disc_number": 1, "duration_ms": ms}
               for i, (t, ms) in enumerate(V_STANDARD, start=1)]

    _persist_tracklist_tracks(conn, album, entries, complete=True)

    rows = _rows(conn, album)
    assert ("Maps", 1) in rows
    assert ("Lost Stars", 15) in rows
    assert conn.execute("SELECT track_number FROM lib2_tracks WHERE id=?", (lost,)).fetchone()[0] == 15
    assert len(rows) == 15


def test_the_tracklist_pass_still_claims_a_slot_by_number_when_the_length_agrees(conn):
    """a file whose title differs (a translation, a tagger's spelling) but whose
    length is the slot's is still that song"""
    from core.library2.completeness import _persist_tracklist_tracks
    _artist, album = _album_v(conn)
    file_row = _track(conn, album, "Cartes", 1, 191000, "/music/Maroon 5/2014 - V/01 - Cartes.flac",
                      {"track_number": "1"})

    _persist_tracklist_tracks(conn, album, [{"title": "Maps", "track_number": 1, "duration_ms": 190000}],
                              complete=True)

    assert _rows(conn, album) == [("Cartes", 1)]
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album,)).fetchone()[0] == 1
    assert file_row


def test_merging_another_row_renumbers_a_song_landing_on_another_songs_number(conn):
    """#2178's merge moves the rest of the tracks "as they are": Lost Stars
    came over from a single at number 1"""
    from core.library2.album_versions import merge_album_into
    artist, album = _album_v(conn, tracklist=True)
    _track(conn, album, "Maps", 1, 190000)
    single = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type)"
                          " VALUES(?, 'V', 'single')", (artist,)).lastrowid
    lost = _lost_stars(conn, single)
    conn.commit()

    stats = merge_album_into(conn, album, single)

    assert stats["tracks_moved"] == 1 and stats["tracks_renumbered"] == 1
    assert conn.execute("SELECT album_id, track_number FROM lib2_tracks WHERE id=?",
                        (lost,)).fetchone()[:] == (album, 15)


# ── the deleted file ────────────────────────────────────────────────────────

class _Config:
    def __init__(self, root):
        self._values = {"library.music_paths": [root], "soulseek.transfer_path": root}

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture
def disk(tmp_path):
    root = tmp_path / "music"
    album = root / "Maroon 5" / "2014 - V"
    album.mkdir(parents=True)
    (album / "05 - Sugar.flac").write_bytes(b"sugar")
    single = root / "Maroon 5" / "2014 - Lost Stars"
    single.mkdir(parents=True)
    (single / "01 - Lost Stars.flac").write_bytes(b"lost stars single")
    deleted = album / "15 - Lost Stars.flac"   # wolf deleted it
    return {"root": str(root), "deleted": str(deleted), "single": str(single / "01 - Lost Stars.flac"),
            "config": _Config(str(root))}


def test_a_file_deleted_from_its_album_folder_is_not_found_in_another(disk):
    from core.library.path_resolver import (
        resolve_library_file_path, resolve_via_last_resort_fallbacks,
    )
    assert resolve_library_file_path(disk["deleted"], config_manager=disk["config"]) is None
    assert resolve_library_file_path("/server/Maroon 5/2014 - V/15 - Lost Stars.flac",
                                     config_manager=disk["config"]) is None
    assert resolve_via_last_resort_fallbacks(disk["deleted"], [disk["root"]]) is None


def test_a_wrong_album_folder_still_finds_the_file(disk):
    """#1127: the album folder the server made up doesn't exist"""
    from core.library.path_resolver import resolve_library_file_path
    found = resolve_library_file_path("/server/Maroon 5/Lost Stars/01 - Lost Stars.flac",
                                      config_manager=disk["config"])
    assert found and os.path.samefile(found, disk["single"])


def test_refresh_and_scan_marks_the_deleted_file_missing(disk, tmp_path, monkeypatch):
    from core.library2 import paths, scan
    from database.music_database import MusicDatabase

    real_resolve = paths.resolve_lib2_path
    monkeypatch.setattr(paths, "resolve_lib2_path",
                        lambda path, config_manager=None: real_resolve(path, disk["config"]))
    real_healthy = paths.missing_path_root_is_healthy
    monkeypatch.setattr(paths, "missing_path_root_is_healthy",
                        lambda path, config_manager=None: real_healthy(path, disk["config"]))
    monkeypatch.setattr("core.library2.scan._redownload_externally_deleted", lambda: True)
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    with closing(db._get_connection()) as c:
        _artist, album = _album_v(c)
        lost = _track(c, album, "Lost Stars", 15, LOST_STARS_MS, disk["deleted"])
        c.commit()
        file_id = c.execute("SELECT id FROM lib2_track_files WHERE track_id=?", (lost,)).fetchone()[0]

    stats = scan.rescan_files(db, album_ids=[album], manual=True)

    assert stats["missing_confirmed"] == 1
    with closing(db._get_connection()) as c:
        assert c.execute("SELECT file_state FROM lib2_track_files WHERE id=?",
                         (file_id,)).fetchone()[0] == "missing_confirmed"


def test_the_artist_refresh_and_scan_renumbers_it(api, monkeypatch):  # noqa: F811
    """the button wolf pressed: it re-reads the files, so it is where the
    file's own number gets applied"""
    from tests.library2.test_api_routes import _wait_for_job
    client, db, _ids = api
    monkeypatch.setattr("core.library2.scan.rescan_files", lambda *a, **k: {"scanned": 2, "updated": 0})
    with closing(db._get_connection()) as c:
        artist, album = _album_v(c, tracklist=True)
        _track(c, album, "Maps", 1, 190000)
        lost = _lost_stars(c, album)
        c.commit()

    started = client.post(f"/api/library/v2/artists/{artist}/refresh").get_json()
    state = _wait_for_job(client, started["job_id"])

    assert state["error"] is None
    assert state["result"]["tracks_renumbered"] == 1
    with closing(db._get_connection()) as c:
        assert c.execute("SELECT track_number FROM lib2_tracks WHERE id=?", (lost,)).fetchone()[0] == 15
