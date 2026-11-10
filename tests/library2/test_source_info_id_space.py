"""Source Info reads a track's whole download history: the 3.6.1 rows (keyed
on legacy ids, mapped once after the upgrade) and every download since,
newest first (third #1062 review G09-1)."""

from __future__ import annotations

from core.library2.source_info import carry_legacy_download_ids, track_source_info
from database.music_database import MusicDatabase
from tests import lib2_seed


def _world(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    track = lib2_seed.track(conn, "Artist", "Album", "Song", path="/m/new.flac")
    conn.execute("UPDATE lib2_tracks SET legacy_track_id=48213 WHERE id=?", (track,))
    from core.library2.bootstrap import ensure_bootstrap_schema
    ensure_bootstrap_schema(conn.cursor())
    conn.execute("UPDATE lib2_bootstrap_state SET started_at='2026-10-29 10:00:00' WHERE id=1")
    # the 3.6.1 download, then a re-download after the upgrade
    conn.execute("INSERT INTO track_downloads(track_id, file_path, source_service, source_username, created_at) "
                 "VALUES('48213', '/m/old.flac', 'soulseek', 'old_uploader', '2026-09-01 00:00:00')")
    conn.execute("INSERT INTO track_downloads(track_id, file_path, source_service, source_username, created_at) "
                 "VALUES(?, '/m/moved-away.flac', 'soulseek', 'new_uploader', '2026-11-05 00:00:00')",
                 (str(track),))
    conn.commit()
    return db, conn, track


def test_the_newest_download_comes_first_after_the_carry(tmp_path):
    _db, conn, track = _world(tmp_path)
    assert carry_legacy_download_ids(conn) == 1
    rows = track_source_info(conn, track)
    assert [r["source_username"] for r in rows] == ["new_uploader", "old_uploader"]


def test_a_legacy_number_never_reads_as_another_catalogue_track(tmp_path):
    """on Plex the 3.6.1 ids are ratingKeys; an unmapped one must not be read
    as the catalogue track that happens to carry that number"""
    _db, conn, track = _world(tmp_path)
    conn.execute("INSERT INTO track_downloads(track_id, source_service, source_username, created_at) "
                 "VALUES(?, 'soulseek', 'stranger', '2026-08-01 00:00:00')", (str(track),))
    conn.execute("UPDATE lib2_tracks SET legacy_track_id=NULL WHERE id=?", (track,))
    conn.commit()
    carry_legacy_download_ids(conn)
    assert "stranger" not in [r["source_username"] for r in track_source_info(conn, track)]


def test_a_same_day_download_after_the_upgrade_keeps_its_catalogue_id(tmp_path):
    """review 4 R4-A-2: started_at is ISO and created_at isn't; as text a row
    from later the same day sorted before the upgrade, was read as a 3.6.1
    id and moved to whichever track carried that legacy number"""
    _db, conn, track = _world(tmp_path)
    other = lib2_seed.track(conn, "Other", "Album", "Unrelated", path="/m/other.flac")
    conn.execute("UPDATE lib2_tracks SET legacy_track_id=? WHERE id=?", (track, other))
    conn.execute("UPDATE lib2_bootstrap_state SET started_at='2026-10-29T10:00:00.123456+00:00' WHERE id=1")
    conn.execute("INSERT INTO track_downloads(track_id, source_service, source_username, created_at) "
                 "VALUES(?, 'soulseek', 'same_day', '2026-10-29 10:30:00')", (str(track),))
    conn.commit()
    carry_legacy_download_ids(conn)
    row = conn.execute("SELECT track_id, track_id_kind FROM track_downloads "
                       "WHERE source_username='same_day'").fetchone()
    assert tuple(row) == (str(track), "lib2")
