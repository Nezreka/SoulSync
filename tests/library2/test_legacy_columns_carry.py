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


def test_a_release_type_3_6_1_stored_is_a_known_kind(legacy_db):
    """review 4 R4-B-2: the import never set album_type_known, so every album
    3.6.1 had typed read as the bare default, and the #829 guard let the
    "Ocean Avenue" single into "Ocean Avenue" the album's folder"""
    from core.library2.importer import import_legacy_library
    from tests.lib2_seed import row_conn
    conn = row_conn(legacy_db.path)
    conn.execute("ALTER TABLE albums ADD COLUMN record_type TEXT")
    conn.execute("UPDATE albums SET record_type='album' WHERE id=10")
    conn.commit()
    conn.close()
    import_legacy_library(legacy_db)
    conn = row_conn(legacy_db.path)
    known = dict(conn.execute("SELECT legacy_album_id, album_type_known FROM lib2_albums "
                              "WHERE album_type='album'").fetchall())
    assert known.pop("10", known.pop(10, None)) == 1
    # an untyped legacy row stays unknown, the same as 3.6.1 read NULL
    assert set(known.values()) <= {0}
    conn.close()


def test_an_install_that_already_upgraded_gets_its_known_kinds(imported_conn):
    conn = imported_conn
    conn.execute("ALTER TABLE albums ADD COLUMN record_type TEXT")
    conn.execute("UPDATE albums SET record_type='album' WHERE id=10")
    conn.execute("UPDATE lib2_albums SET album_type_known=0")
    conn.execute("INSERT OR IGNORE INTO lib2_upgrade_repairs(name) VALUES('legacy_columns_v1')")
    conn.commit()
    carry_legacy_columns(conn)
    assert conn.execute("SELECT album_type_known FROM lib2_albums WHERE legacy_album_id=10"
                        ).fetchone()[0] == 1
    # once: a later change is never undone
    conn.execute("UPDATE lib2_albums SET album_type_known=0 WHERE legacy_album_id=10")
    conn.commit()
    carry_legacy_columns(conn)
    assert conn.execute("SELECT album_type_known FROM lib2_albums WHERE legacy_album_id=10"
                        ).fetchone()[0] == 0


def test_the_carry_finds_3_6_1_rows_through_their_text_key(imported_conn):
    """Boulder's live upgrade: 3.6.1's ids are TEXT keys and lib2's legacy ids
    are INTEGER. compared bare, the legacy key couldn't serve, so each lib2 row
    scanned the whole legacy table (316k x 310k) and the carry never finished,
    holding the write lock"""
    conn = imported_conn
    conn.execute("ALTER TABLE tracks RENAME TO tracks_int")
    conn.execute("CREATE TABLE tracks (id TEXT PRIMARY KEY, track_artist TEXT, "
                 "server_path TEXT, server_source TEXT)")
    conn.execute("INSERT INTO tracks(id, track_artist) SELECT CAST(id AS TEXT), track_artist FROM tracks_int")
    conn.execute("ALTER TABLE albums RENAME TO albums_int")
    conn.execute("CREATE TABLE albums (id TEXT PRIMARY KEY, musicbrainz_release_group_id TEXT)")
    conn.execute("INSERT INTO albums(id) SELECT CAST(id AS TEXT) FROM albums_int")
    conn.execute("UPDATE albums SET musicbrainz_release_group_id='rg-views' WHERE id='10'")
    conn.execute("UPDATE lib2_tracks SET track_artist=NULL")
    conn.commit()
    updates = []
    conn.set_trace_callback(lambda sql: updates.append(sql) if sql.lstrip().startswith("UPDATE") else None)
    carry_legacy_columns(conn)
    conn.set_trace_callback(None)

    assert _lib2_track(conn, 100)["track_artist"] == "Drake feat. Wizkid"
    assert conn.execute("SELECT musicbrainz_release_group_id FROM lib2_albums WHERE legacy_album_id=10"
                        ).fetchone()[0] == "rg-views"
    for sql in updates:
        plan = " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
        assert "SCAN lt" not in plan and "SCAN la" not in plan, plan


def test_the_compilation_carry_keeps_a_release_type_the_user_set(imported_conn):
    """review 4 R4-A-9: on an install that upgraded before the carry, a user
    who set a 'compile' release to album got it flipped back"""
    conn = imported_conn
    conn.execute("ALTER TABLE albums ADD COLUMN record_type TEXT")
    conn.execute("UPDATE albums SET record_type='compile' WHERE id=10")
    album = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()[0]
    conn.execute("UPDATE lib2_albums SET album_type='album' WHERE id=?", (album,))
    from core.library2.metadata_overrides import ensure_metadata_overrides_schema
    ensure_metadata_overrides_schema(conn.cursor())
    conn.execute("INSERT INTO lib2_metadata_overrides(entity_type, entity_id, field_name, value_json) "
                 "VALUES('release_group', ?, 'album_type', ?)", (album, '"album"'))
    conn.commit()
    carry_legacy_columns(conn)
    assert conn.execute("SELECT album_type FROM lib2_albums WHERE id=?", (album,)).fetchone()[0] == "album"
