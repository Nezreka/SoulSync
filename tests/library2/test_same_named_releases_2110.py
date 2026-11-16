"""#2110: weezer's three self-titled albums merged into one album.

the files carry no provider or release id, only tags, so the import writer
matched every "weezer" to the first one and the album showed three track 1s.
the tagged year and folder keep them apart now, and rows merged before the
fix split back out.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.imports.album_grouping import find_existing_soulsync_album_id
from core.library2 import autolink as A
from core.library2 import release_split as RS
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture(autouse=True)
def _no_artwork_warmup(monkeypatch):
    monkeypatch.setattr(A, "_warm_new_artwork", lambda *_a, **_k: None)


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return legacy_db


def _flac(path, **tags):
    from tests.test_album_consistency_adopt import _make_flac
    path.parent.mkdir(parents=True, exist_ok=True)
    _make_flac(path, tags)
    return str(path)


def _weezer(root, year, folder, number, title):
    return _flac(root / "Weezer" / folder / f"{number:02d}.flac",
                 artist="Weezer", albumartist="Weezer", album="Weezer",
                 title=title, date=str(year), tracknumber=str(number))


def _albums(conn, title="Weezer"):
    return sorted(
        sorted(r["title"] for r in conn.execute(
            "SELECT title FROM lib2_tracks WHERE album_id=?", (a["id"],)))
        for a in conn.execute("SELECT id FROM lib2_albums WHERE title=?", (title,)))


def _refresh(path):
    return A.link_download_into_library_v2({
        "_final_processed_path": path, "username": "standalone_refresh",
        "_download_username": "standalone_refresh"})


def test_three_self_titled_albums_stay_three(lib2_enabled, imported_conn, tmp_path):
    """#2110: same name, same artist, different year and folder: three albums."""
    for year, folder, title in [(1994, "1994 - Weezer", "My Name Is Jonas"),
                                (2001, "2001 - Weezer", "Don't Let Go"),
                                (2008, "2008 - Weezer", "Troublemaker")]:
        assert _refresh(_weezer(tmp_path, year, folder, 1, title)) is not None
    assert _albums(imported_conn) == [["Don't Let Go"], ["My Name Is Jonas"], ["Troublemaker"]]


def test_a_scan_adopted_file_brings_its_year_and_folder(lib2_enabled, imported_conn, tmp_path):
    """#2110: the media-server scan path (track_info from tags) splits too."""
    for year, folder, title in [(1994, "1994 - Weezer", "My Name Is Jonas"),
                                (2001, "2001 - Weezer", "Don't Let Go")]:
        path = _weezer(tmp_path, year, folder, 1, title)
        assert A.link_download_into_library_v2({
            "_final_processed_path": path, "username": "plex", "_scan_discovered": True,
            "track_info": {"name": title, "artist": "Weezer", "album": "Weezer",
                           "track_number": 1, "year": str(year)},
        }) is not None
    assert _albums(imported_conn) == [["Don't Let Go"], ["My Name Is Jonas"]]


def test_disc_folders_of_one_year_stay_one_album(lib2_enabled, imported_conn, tmp_path):
    _refresh(_weezer(tmp_path, 2004, "Deluxe/CD1", 1, "My Name Is Jonas"))
    _refresh(_weezer(tmp_path, 2004, "Deluxe/CD2", 1, "Mykel & Carli"))
    assert _albums(imported_conn) == [["My Name Is Jonas", "Mykel & Carli"]]


def test_a_file_in_the_same_folder_joins_whatever_its_year(lib2_enabled, imported_conn, tmp_path):
    _refresh(_weezer(tmp_path, 1994, "Weezer", 1, "My Name Is Jonas"))
    _refresh(_weezer(tmp_path, 2004, "Weezer", 2, "No One Else"))
    assert _albums(imported_conn) == [["My Name Is Jonas", "No One Else"]]


# --- the import writer's own grouping (soulsync standalone) ------------------


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO lib2_artists(id, name, name_key) VALUES(1,'Weezer','weezer')")
    yield cursor
    conn.close()


def test_the_name_hash_never_folds_another_provider_album_in(cur):
    """#2110: the blue album holds the "weezer::weezer" hash; the green album
    has its own spotify id and must not land on it."""
    blue = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, server_source, server_id,"
        " spotify_id, external_ids) VALUES(1,'Weezer','library','soulsync','nk','sp-blue',?)",
        (json.dumps({"deezer": "dz-blue"}),)).lastrowid
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="sp-green", source="spotify") is None
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="dz-green", source="deezer") is None
    # the same release still finds its row
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="sp-blue", source="spotify") == blue


# --- healing rows merged before the fix --------------------------------------


def _merged(cur, files, *, year=None):
    album = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year) VALUES(1, 'Weezer', ?)",
        (year,)).lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,1,'primary')",
                (album,))
    for number, title, path, tags in files:
        track = cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) VALUES(?,?,?,1)",
            (album, title, number)).lastrowid
        cur.execute("INSERT INTO lib2_track_files(track_id, path, tags_json, file_state)"
                    " VALUES(?,?,?, 'active')", (track, path, json.dumps(tags)))
    return album


def _w(year):
    return {"album": "Weezer", "year": str(year)}


def test_a_merged_weezer_row_splits_into_its_releases(cur):
    """#2110 heal: one row holding three releases becomes three rows."""
    album = _merged(cur, [
        (1, "My Name Is Jonas", "/m/Weezer/1994 - Weezer/01.flac", _w(1994)),
        (2, "No One Else", "/m/Weezer/1994 - Weezer/02.flac", _w(1994)),
        (1, "Don't Let Go", "/m/Weezer/2001 - Weezer/01.flac", _w(2001)),
        (1, "Troublemaker", "/m/Weezer/2008 - Weezer/01.flac", _w("2008-06-03")),
    ], year=2001)
    stats = RS.split_same_named_releases(cur.connection)
    assert stats == {"albums_split": 1, "albums_made": 2}
    conn = cur.connection
    rows = {r["year"]: sorted(t["title"] for t in conn.execute(
                "SELECT title FROM lib2_tracks WHERE album_id=?", (r["id"],)))
            for r in conn.execute("SELECT id, year FROM lib2_albums WHERE title='Weezer'").fetchall()}
    assert rows == {2001: ["Don't Let Go"], 1994: ["My Name Is Jonas", "No One Else"],
                    2008: ["Troublemaker"]}
    # the row's own year kept its folder, and every new row has the artist
    assert cur.execute("SELECT year FROM lib2_albums WHERE id=?", (album,)).fetchone()[0] == 2001
    assert cur.execute("SELECT COUNT(*) FROM lib2_album_artists").fetchone()[0] == 3
    # a second pass finds nothing
    assert RS.split_same_named_releases(cur.connection) == {"albums_split": 0, "albums_made": 0}


@pytest.mark.parametrize("files", [
    # disc folders of one year
    [(1, "A", "/m/W/CD1/01.flac", _w(2004)), (1, "B", "/m/W/CD2/01.flac", _w(2004))],
    # a single's file filed on the album: its own tags name another album
    [(1, "A", "/m/W/Weezer/01.flac", _w(1994)),
     (1, "B", "/m/W/Single/01.flac", {"album": "Buddy Holly", "year": "1995"})],
    # no year to tell them apart
    [(1, "A", "/m/W/One/01.flac", {"album": "Weezer"}),
     (1, "B", "/m/W/Two/01.flac", {"album": "Weezer"})],
])
def test_rows_that_are_not_proven_two_releases_stay_whole(cur, files):
    _merged(cur, files)
    assert RS.split_same_named_releases(cur.connection) == {"albums_split": 0, "albums_made": 0}
    assert cur.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 1
