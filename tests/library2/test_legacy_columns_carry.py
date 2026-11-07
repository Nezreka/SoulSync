"""3.6.1 columns the import left behind come over once after it (third #1062
review, importer pass P2A-1/2/4)."""

from __future__ import annotations

from core.library2.legacy_columns import carry_legacy_columns


def _lib2_track(conn, legacy_id):
    return conn.execute("SELECT * FROM lib2_tracks WHERE legacy_track_id=?", (legacy_id,)).fetchone()


def test_the_performer_comes_over_where_it_is_not_the_album_artist(imported_conn):
    conn = imported_conn
    conn.execute("UPDATE lib2_tracks SET track_artist=NULL")
    conn.commit()
    carry_legacy_columns(conn)
    assert _lib2_track(conn, 100)["track_artist"] == "Drake feat. Wizkid"
    assert not _lib2_track(conn, 101)["track_artist"]


def test_the_servers_path_lands_on_the_tracks_mapping(imported_conn):
    """#1573: an M3U export writes the path the server can open"""
    conn = imported_conn
    conn.execute("ALTER TABLE tracks ADD COLUMN server_path TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN server_source TEXT")
    conn.execute("UPDATE tracks SET server_path='/music/Drake/Views/01.flac', server_source='plex' WHERE id=100")
    track = _lib2_track(conn, 100)["id"]
    conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, server_id) "
                 "VALUES('track', ?, 'plex', 'rk-100')", (track,))
    conn.commit()
    carry_legacy_columns(conn)
    assert conn.execute("SELECT server_path FROM lib2_media_server_mappings WHERE entity_id=? "
                        "AND entity_type='track'", (track,)).fetchone()[0] == "/music/Drake/Views/01.flac"


def test_a_deezer_compile_release_is_a_compilation_and_it_runs_once(imported_conn):
    conn = imported_conn
    conn.execute("ALTER TABLE albums ADD COLUMN record_type TEXT")
    conn.execute("UPDATE albums SET record_type='compile' WHERE id=10")
    conn.commit()
    assert carry_legacy_columns(conn)["compilations"] == 1
    assert conn.execute("SELECT album_type FROM lib2_albums WHERE legacy_album_id=10").fetchone()[0] \
        == "compilation"
    conn.execute("UPDATE lib2_albums SET album_type='album' WHERE legacy_album_id=10")
    conn.commit()
    assert not any(carry_legacy_columns(conn).values())


def test_the_import_reads_compile_as_a_compilation():
    from core.library2.importer import _normalize_album_type
    assert _normalize_album_type("compile", 1, 1) == "compilation"


def test_the_release_group_id_comes_over(imported_conn):
    conn = imported_conn
    conn.execute("ALTER TABLE albums ADD COLUMN musicbrainz_release_group_id TEXT")
    conn.execute("UPDATE albums SET musicbrainz_release_group_id='rg-views' WHERE id=10")
    conn.commit()
    carry_legacy_columns(conn)
    assert conn.execute("SELECT musicbrainz_release_group_id FROM lib2_albums WHERE legacy_album_id=10"
                        ).fetchone()[0] == "rg-views"
