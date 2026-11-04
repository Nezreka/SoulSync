"""a bpm a file carries reaches the library when it has none (#1939)"""

from core.library2.tag_cache import adopt_file_bpm
from tests import lib2_seed


def _seed(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    ensure_library_v2_schema(conn)
    track_id = lib2_seed.track(conn, "Artist", "Album", "Song", path="/m/song.flac")
    file_id = conn.execute("SELECT id FROM lib2_track_files WHERE track_id=?", (track_id,)).fetchone()[0]
    conn.execute("UPDATE lib2_track_files SET is_primary=1 WHERE id=?", (file_id,))
    conn.commit()
    return conn, track_id, file_id


def _bpm(conn, track_id):
    return conn.execute("SELECT bpm FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]


def test_an_empty_bpm_takes_the_files(tmp_path):
    conn, track_id, file_id = _seed(tmp_path)
    assert adopt_file_bpm(conn, file_id, "128") is True
    assert _bpm(conn, track_id) == 128.0


def test_a_bpm_the_library_has_is_never_replaced(tmp_path):
    conn, track_id, file_id = _seed(tmp_path)
    conn.execute("UPDATE lib2_tracks SET bpm=87.5 WHERE id=?", (track_id,))
    assert adopt_file_bpm(conn, file_id, 175) is False
    assert _bpm(conn, track_id) == 87.5


def test_junk_and_a_second_copy_are_left_alone(tmp_path):
    conn, track_id, file_id = _seed(tmp_path)
    for junk in (None, "", "abc", 0, 9999, float("nan")):
        assert adopt_file_bpm(conn, file_id, junk) is False
    conn.execute("UPDATE lib2_track_files SET is_primary=0 WHERE id=?", (file_id,))
    assert adopt_file_bpm(conn, file_id, 128) is False
    assert _bpm(conn, track_id) is None


def test_reading_the_files_tags_fills_it(tmp_path):
    from core.library2.tag_cache import persist_tag_cache
    conn, track_id, file_id = _seed(tmp_path)
    persist_tag_cache(conn, file_id, {"title": "Song", "artist": "Artist", "bpm": 140.0})
    assert _bpm(conn, track_id) == 140.0
