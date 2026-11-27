"""a various artists compilation stays one album under various artists.

pertti's "Disco House Heaters, Vol. 2" sat in Compilations/ with every file
tagged album artist Various Artists, while the library filed each track
under its own performer: track 11 was Mark Whites' album. Library Re-tag
then offered "Album artist: Various Artists -> Mark Whites" for the file.
"""

from __future__ import annotations

import json

import pytest

from core.library2 import autolink as A
from core.library2.materialize import materialize_from_spotify_track

TITLE = "Disco House Heaters, Vol. 2"
PERFORMERS = [f"Performer {n}" for n in range(1, 21)]
PERFORMERS[10] = "Mark Whites"


@pytest.fixture(autouse=True)
def _no_artwork_warmup(monkeypatch):
    monkeypatch.setattr(A, "_warm_new_artwork", lambda *_args, **_kwargs: None)


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    from core.settings import config_manager
    real_get = config_manager.get
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: (
        True if key == "features.library_v2" else real_get(key, default)))
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return legacy_db


def _albums(conn):
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT al.title, ar.name FROM lib2_albums al"
        " JOIN lib2_artists ar ON ar.id = al.primary_artist_id"
        " WHERE al.title = ?", (TITLE,))]


def _payload(n, performer, album):
    return {"id": f"trk-{n}", "name": f"Song {n}", "track_number": n, "disc_number": 1,
            "artists": [{"id": f"art-{n}", "name": performer}], "album": album}


@pytest.mark.parametrize("album", [
    {"id": "alb-dhh2", "name": TITLE, "album_type": "compilation", "total_tracks": 20,
     "artists": [{"id": "0LyfQWJT6nXafLPZqxe9Of", "name": "Various Artists"}]},
    # a source whose album names its artist as a plain string
    {"id": "alb-dhh2", "name": TITLE, "album_type": "compilation", "total_tracks": 20,
     "album_artist": "Various Artists"},
])
def test_a_wishlisted_compilation_is_one_album_under_various_artists(imported_conn, album):
    for n, performer in enumerate(PERFORMERS, start=1):
        materialize_from_spotify_track(imported_conn, _payload(n, performer, album))
    assert _albums(imported_conn) == [(TITLE, "Various Artists")]
    tracks = imported_conn.execute(
        "SELECT t.track_artist FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id"
        " WHERE al.title = ? ORDER BY t.track_number", (TITLE,)).fetchall()
    assert [r[0] for r in tracks] == PERFORMERS


def test_downloaded_compilation_tracks_stay_one_album(lib2_enabled, imported_conn, monkeypatch):
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: "Various Artists")
    for n, performer in enumerate(PERFORMERS, start=1):
        A.link_download_into_library_v2({
            "_final_processed_path": f"/music/Compilations/{TITLE}/{n:02d} - {performer} - Song {n}.mp3",
            "username": "usenet",
            "spotify_artist": {"name": "Various Artists"},
            "spotify_album": {"name": TITLE, "album_type": "compilation", "total_tracks": 20,
                              "artists": [{"name": "Various Artists"}]},
            "track_info": {"name": f"Song {n}", "artists": [{"name": performer}],
                           "track_number": n, "provider": "spotify"},
            "_embedded_id_tags": {},
        })
    assert _albums(imported_conn) == [(TITLE, "Various Artists")]


# --- a library the old filing already split ----------------------------------

def _split_library(conn, *, per_performer):
    """the compilation as the old wishlist add left it: one album per
    performer (or the whole release under one of them), every file tagged
    album artist Various Artists"""
    whole = None
    for n, performer in enumerate(PERFORMERS, start=1):
        artist_id = conn.execute("INSERT INTO lib2_artists(name) VALUES(?)", (performer,)).lastrowid
        if per_performer or whole is None:
            album_id = conn.execute(
                "INSERT INTO lib2_albums(primary_artist_id, title, origin, album_type,"
                " expected_track_count) VALUES(?,?,'library','compilation',20)",
                (artist_id, TITLE)).lastrowid
            conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role)"
                         " VALUES(?,?,'primary')", (album_id, artist_id))
            whole = album_id
        track_id = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number)"
                                " VALUES(?,?,?)", (whole, f"Song {n}", n)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
                     " VALUES(?,?,'primary',0)", (track_id, artist_id))
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, is_primary, tags_json) VALUES(?,?,1,?)",
            (track_id, f"/music/Compilations/{TITLE}/{n:02d} - {performer} - Song {n}.mp3",
             json.dumps({"albumartist": "Various Artists"})))
    conn.commit()


@pytest.mark.parametrize("per_performer", [True, False])
def test_the_repair_puts_a_split_compilation_back_together(imported_conn, per_performer):
    from core.library2.release_artist_repair import rehome_track_lead_releases
    _split_library(imported_conn, per_performer=per_performer)
    rehome_track_lead_releases(imported_conn, read_album_artist=lambda p: "")
    imported_conn.commit()
    assert _albums(imported_conn) == [(TITLE, "Various Artists")]
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id"
        " WHERE al.title = ?", (TITLE,)).fetchone()[0] == 20
    # each performer keeps their track
    row = imported_conn.execute(
        "SELECT ar.name FROM lib2_tracks t JOIN lib2_track_artists ta ON ta.track_id = t.id"
        " JOIN lib2_artists ar ON ar.id = ta.artist_id WHERE t.title = 'Song 11'").fetchone()
    assert row[0] == "Mark Whites"


def test_the_repair_runs_again_for_libraries_split_after_its_last_pass(
        legacy_db, imported_conn, monkeypatch):
    from core.library2 import bootstrap
    from core.library2.bootstrap import _rehome_track_lead_releases_once
    monkeypatch.setattr(bootstrap, "get_state", lambda db: {"status": "done"})
    from core.library2.schema import _mark_done
    _mark_done(imported_conn.cursor(), "track_lead_releases_rehomed_v2")
    _split_library(imported_conn, per_performer=True)
    stats = _rehome_track_lead_releases_once(legacy_db, imported_conn)
    assert stats.get("albums_moved") == 1 and stats.get("albums_merged") == 19
    assert _albums(imported_conn) == [(TITLE, "Various Artists")]


# --- the re-tag scan ----------------------------------------------------------

def _album_artist_check(conn, track_title):
    from core.library2.validation import validate_tags
    file_id = conn.execute(
        "SELECT tf.id FROM lib2_track_files tf JOIN lib2_tracks t ON t.id = tf.track_id"
        " WHERE t.title = ?", (track_title,)).fetchone()[0]
    tags = {"title": track_title, "artist": "Mark Whites", "album": TITLE,
            "album_artist": "Various Artists", "track_number": 11}
    return validate_tags(conn, file_id, tags, {})["checks"].get("albumartist")


def test_retag_never_offers_a_performer_over_various_artists(imported_conn):
    """unrepaired, the file's Various Artists is right and the library wrong:
    nothing to write"""
    _split_library(imported_conn, per_performer=True)
    assert _album_artist_check(imported_conn, "Song 11") != "mismatch"

    from core.library2.retag import _policy_diff, retag_options
    diff = _policy_diff({"album_artist": "Various Artists"},
                        {"artist_name": "Mark Whites", "track_artist": "Mark Whites"},
                        retag_options({}))
    assert not [d for d in diff if d["file_key"] == "album_artist" and d["changed"]]


def test_retag_after_the_repair_agrees_with_the_file(imported_conn):
    from core.library2.release_artist_repair import rehome_track_lead_releases
    _split_library(imported_conn, per_performer=True)
    rehome_track_lead_releases(imported_conn, read_album_artist=lambda p: "")
    imported_conn.commit()
    assert _album_artist_check(imported_conn, "Song 11") == "correct"


def test_retag_still_fixes_a_wrong_album_artist_on_a_normal_album(imported_conn):
    from core.library2.retag import _policy_diff, retag_options
    diff = _policy_diff({"album_artist": "Someone Else"},
                        {"artist_name": "Mark Whites", "track_artist": "Mark Whites"},
                        retag_options({}))
    assert [d for d in diff if d["file_key"] == "album_artist" and d["changed"]]
