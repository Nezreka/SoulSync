"""Auto-linking finished downloads into Library v2 (post-processing hook)."""

from __future__ import annotations

import pytest

from core.library2 import autolink as A


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    """Enable the feature flag and point get_database at the test DB."""
    from config.settings import config_manager

    real_get = config_manager.get

    def fake_get(key, default=None):
        if key == "features.library_v2":
            return True
        return real_get(key, default)

    monkeypatch.setattr(config_manager, "get", fake_get)
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return legacy_db


def _context(**overrides):
    ctx = {
        "_final_processed_path": "/music/Drake/Scorpion/01 Nonstop.flac",
        "username": "usenet",
        "track_info": {
            "name": "Nonstop",
            "artists": [{"name": "Drake"}],
            "album": {"name": "Scorpion", "id": "sp-scorpion", "total_tracks": 25,
                      "album_type": "album"},
            "track_number": 1,
            "provider": "spotify",
            "id": "sp-track-nonstop",
        },
        "_embedded_id_tags": {"SPOTIFY_TRACK_ID": "sp-track-nonstop"},
    }
    ctx.update(overrides)
    return ctx


def test_disabled_flag_is_noop(monkeypatch, legacy_db, imported_conn):
    from config.settings import config_manager
    monkeypatch.setattr(config_manager, "get",
                        lambda key, default=None: False if key == "features.library_v2" else default)
    assert A.link_download_into_library_v2(_context()) is None


def test_links_new_album_track_and_file(lib2_enabled, imported_conn):
    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None

    row = imported_conn.execute(
        """SELECT t.title, t.spotify_id, al.title AS album, al.spotify_id AS album_sp,
                  tf.path, tf.source
             FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
            WHERE tf.id = ?""", (file_id,),
    ).fetchone()
    assert row["title"] == "Nonstop"
    assert row["spotify_id"] == "sp-track-nonstop"
    assert row["album"] == "Scorpion"
    assert row["album_sp"] == "sp-scorpion"
    assert row["source"] == "usenet"
    # Reuses the existing Drake artist row (no duplicate artist).
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_artists WHERE name='Drake'").fetchone()["c"] == 1


def test_attaches_file_to_materialized_missing_track(lib2_enabled, imported_conn):
    """A fileless provider-tracklist row (wanted/missing) gains the file instead
    of a duplicate track being created."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, origin) "
        "VALUES(?, 'Scorpion', 'album', 'sp-scorpion', 'discography')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Nonstop', 1, 1)", (album_id,))
    conn.commit()
    conn.close()

    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None
    # Still exactly one Scorpion album and one Nonstop track.
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'").fetchone()["c"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_tracks WHERE title='Nonstop'").fetchone()["c"] == 1


def test_linking_file_graduates_discography_album_to_library(lib2_enabled, imported_conn):
    """Attaching a real file to a provider-only release must flip its origin —
    'My Library' filters on origin/monitored, so an unmonitored discography row
    with a file would otherwise be invisible despite the file existing."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, "
        "origin, monitored) VALUES(?, 'Scorpion', 'album', 'sp-scorpion', 'discography', 0)",
        (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.commit()
    conn.close()

    assert A.link_download_into_library_v2(_context()) is not None
    row = imported_conn.execute(
        "SELECT origin FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
    assert row["origin"] == "library"


def test_idempotent_relink_updates_not_duplicates(lib2_enabled, imported_conn):
    first = A.link_download_into_library_v2(_context())
    second = A.link_download_into_library_v2(_context())
    assert first == second
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_track_files WHERE path LIKE '%Nonstop%'"
    ).fetchone()["c"] == 1


def test_direct_entity_link_beats_name_heuristics(lib2_enabled, imported_conn):
    """A grab that started from Library v2 carries the server-resolved entity
    (audit P1-16). The file must land on THAT track even when the download's
    metadata names something the heuristics would match elsewhere."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Care Package', 'album')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Nonstop', 1, 1)", (album_id,))
    target_track = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    file_id = A.link_download_into_library_v2(_context(
        lib2_entity={"track_id": target_track, "album_id": album_id,
                     "quality_profile_id": 1}))
    assert file_id is not None
    row = imported_conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE id=?", (file_id,)).fetchone()
    assert row["track_id"] == target_track
    # No new Scorpion album was created from the metadata (heuristics skipped).
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'").fetchone()["c"] == 0


def test_direct_album_link_creates_track_inside_that_album(lib2_enabled, imported_conn):
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Care Package', 'album')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.commit()
    conn.close()

    file_id = A.link_download_into_library_v2(_context(
        lib2_entity={"album_id": album_id, "quality_profile_id": 1}))
    assert file_id is not None
    row = imported_conn.execute(
        """SELECT t.album_id FROM lib2_track_files tf
           JOIN lib2_tracks t ON t.id = tf.track_id WHERE tf.id=?""",
        (file_id,)).fetchone()
    assert row["album_id"] == album_id


def test_stale_entity_falls_back_to_heuristics(lib2_enabled, imported_conn):
    """The named track was deleted between grab and import — fall back to the
    heuristic path rather than dropping the link entirely."""
    file_id = A.link_download_into_library_v2(_context(
        lib2_entity={"track_id": 999999, "album_id": 999999,
                     "quality_profile_id": 1}))
    assert file_id is not None
    row = imported_conn.execute(
        """SELECT t.title FROM lib2_track_files tf
           JOIN lib2_tracks t ON t.id = tf.track_id WHERE tf.id=?""",
        (file_id,)).fetchone()
    assert row["title"] == "Nonstop"
