"""Library v2 track source-info (download provenance) resolution.

Mirrors the legacy ``/api/library/track/<id>/source-info`` popover, but resolves
by the lib2 track's primary FILE PATH (lib2 ids differ from legacy track ids).
"""

from __future__ import annotations

from core.library2 import source_info as SI


def _make_downloads_table(conn) -> None:
    conn.execute(
        """CREATE TABLE track_downloads(
               id INTEGER PRIMARY KEY,
               track_id TEXT,
               file_path TEXT,
               source_service TEXT,
               source_username TEXT,
               source_filename TEXT,
               source_size INTEGER,
               audio_quality TEXT,
               bitrate INTEGER,
               sample_rate INTEGER,
               bit_depth INTEGER,
               status TEXT,
               track_title TEXT,
               track_artist TEXT,
               created_at TIMESTAMP)"""
    )


def _one_dance_track_id(conn) -> int:
    # Legacy seed track 100 ('One Dance' on 'Views') has file_path '/m/01.flac'.
    return conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]


def test_source_info_resolves_by_exact_file_path(imported_conn):
    _make_downloads_table(imported_conn)
    imported_conn.execute(
        """INSERT INTO track_downloads(
               id, file_path, source_service, source_username,
               source_filename, source_size, status)
           VALUES(1, '/m/01.flac', 'soulseek', 'cooluser',
                  'One Dance.flac', 12345678, 'completed')"""
    )

    rows = SI.track_source_info(imported_conn, _one_dance_track_id(imported_conn))

    assert len(rows) == 1
    assert rows[0]["source_service"] == "soulseek"
    assert rows[0]["source_username"] == "cooluser"
    assert rows[0]["source_filename"] == "One Dance.flac"


def test_source_info_falls_back_to_filename_suffix(imported_conn):
    # The provenance row's path is a DIFFERENT directory (e.g. Plex-side path)
    # but the same filename — legacy matches on the filename suffix.
    _make_downloads_table(imported_conn)
    imported_conn.execute(
        """INSERT INTO track_downloads(id, file_path, source_service, source_filename)
           VALUES(1, '/plex/media/One Dance/01.flac', 'deezer', '01.flac')"""
    )

    rows = SI.track_source_info(imported_conn, _one_dance_track_id(imported_conn))

    assert len(rows) == 1
    assert rows[0]["source_service"] == "deezer"


def test_source_info_returns_all_records_newest_first(imported_conn):
    _make_downloads_table(imported_conn)
    imported_conn.execute(
        "INSERT INTO track_downloads(id, file_path, source_service) "
        "VALUES(1, '/m/01.flac', 'old-grab')"
    )
    imported_conn.execute(
        "INSERT INTO track_downloads(id, file_path, source_service) "
        "VALUES(2, '/m/01.flac', 'new-grab')"
    )

    rows = SI.track_source_info(imported_conn, _one_dance_track_id(imported_conn))

    assert [r["source_service"] for r in rows] == ["new-grab", "old-grab"]


def test_source_info_prefers_exact_path_over_suffix(imported_conn):
    # An exact-path match must not be mixed with unrelated suffix matches.
    _make_downloads_table(imported_conn)
    imported_conn.execute(
        "INSERT INTO track_downloads(id, file_path, source_service, source_filename) "
        "VALUES(1, '/somewhere-else/01.flac', 'suffix-only', '01.flac')"
    )
    imported_conn.execute(
        "INSERT INTO track_downloads(id, file_path, source_service, source_filename) "
        "VALUES(2, '/m/01.flac', 'exact', '01.flac')"
    )

    rows = SI.track_source_info(imported_conn, _one_dance_track_id(imported_conn))

    assert [r["source_service"] for r in rows] == ["exact"]


def test_source_info_without_downloads_table_returns_empty(imported_conn):
    # No legacy provenance table at all (fresh DB) must not raise.
    assert SI.track_source_info(imported_conn, _one_dance_track_id(imported_conn)) == []


def test_source_info_for_fileless_track_returns_empty(imported_conn):
    _make_downloads_table(imported_conn)
    # Legacy seed track 101 ('Hotline Bling') has no file.
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=101"
    ).fetchone()[0]

    assert SI.track_source_info(imported_conn, track_id) == []


def test_source_info_resolves_by_legacy_track_id_after_file_moved(imported_conn):
    # Section 16.1: the provenance row was written at download time keyed on the
    # legacy track id (100). Later the file was renamed/reorganized, so neither
    # the lib2 primary path ('/m/01.flac') nor its filename ('01.flac') match the
    # provenance row's stored path anymore. Path-only resolution returns nothing;
    # the legacy_track_id link must still find it (mirrors the legacy route).
    _make_downloads_table(imported_conn)
    imported_conn.execute(
        """INSERT INTO track_downloads(
               id, track_id, file_path, source_service, source_username,
               source_filename, status)
           VALUES(1, '100', '/old/download/dir/Track 1 - One Dance.flac',
                  'soulseek', 'grabber', 'Track 1 - One Dance.flac', 'completed')"""
    )

    rows = SI.track_source_info(imported_conn, _one_dance_track_id(imported_conn))

    assert len(rows) == 1
    assert rows[0]["source_service"] == "soulseek"
    assert rows[0]["source_username"] == "grabber"


def test_source_info_legacy_id_preferred_over_stale_suffix_collision(imported_conn):
    # A DIFFERENT track's provenance happens to share the moved file's basename;
    # resolving by the authoritative legacy id must not pull in that unrelated
    # suffix collision.
    _make_downloads_table(imported_conn)
    imported_conn.execute(
        """INSERT INTO track_downloads(id, track_id, file_path, source_service, source_filename)
           VALUES(1, '100', '/somewhere/new/01.flac', 'right-by-id', '01.flac')"""
    )
    imported_conn.execute(
        """INSERT INTO track_downloads(id, track_id, file_path, source_service, source_filename)
           VALUES(2, '999', '/unrelated/other/01.flac', 'wrong-suffix', '01.flac')"""
    )

    rows = SI.track_source_info(imported_conn, _one_dance_track_id(imported_conn))

    assert [r["source_service"] for r in rows] == ["right-by-id"]


def test_source_info_falls_back_to_path_when_legacy_id_is_null(imported_conn):
    # An autolink-created lib2 track has no legacy_track_id; the path/suffix
    # chain must still resolve its provenance (backwards-compatible).
    _make_downloads_table(imported_conn)
    track_id = _one_dance_track_id(imported_conn)
    imported_conn.execute(
        "UPDATE lib2_tracks SET legacy_track_id=NULL WHERE id=?", (track_id,)
    )
    imported_conn.execute(
        "UPDATE lib2_track_files SET legacy_track_id=NULL WHERE track_id=?", (track_id,)
    )
    imported_conn.execute(
        """INSERT INTO track_downloads(id, file_path, source_service)
           VALUES(1, '/m/01.flac', 'path-fallback')"""
    )

    rows = SI.track_source_info(imported_conn, track_id)

    assert [r["source_service"] for r in rows] == ["path-fallback"]
