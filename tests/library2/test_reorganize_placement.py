"""#1701 native placement memory protects albums from automatic naming drift."""
from database.music_database import MusicDatabase
from tests import lib2_seed


def test_placement_survives_auto_metadata_but_not_naming_pin_or_file_changes(tmp_path):
    from core.library2.reorganize_placement import record_placement, placement_settled
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        tid = lib2_seed.track(conn, 'Artist', 'Album', 'Song')
        aid = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (tid,)).fetchone()[0]
        record_placement(conn, aid, 'naming-1')
        conn.execute("UPDATE lib2_albums SET title='Automatic metadata change', canonical_source='deezer', canonical_album_id='auto', canonical_locked=0 WHERE id=?", (aid,))
        assert placement_settled(conn, aid, 'naming-1')
        assert not placement_settled(conn, aid, 'naming-2')
        conn.execute('UPDATE lib2_albums SET canonical_locked=1 WHERE id=?', (aid,))
        assert not placement_settled(conn, aid, 'naming-1')
        record_placement(conn, aid, 'naming-1')
        conn.execute("UPDATE lib2_track_files SET path='/new/location' WHERE track_id=?", (tid,))
        assert not placement_settled(conn, aid, 'naming-1')


def test_manual_metadata_change_invalidates_placement(tmp_path):
    from core.library2.reorganize_placement import record_placement, placement_settled
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        tid = lib2_seed.track(conn, 'Artist', 'Album', 'Song')
        aid = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (tid,)).fetchone()[0]
        record_placement(conn, aid, 'naming')
        conn.execute("INSERT INTO lib2_metadata_overrides (entity_type,entity_id,field_name,value_json) VALUES ('track',?,'title','\"My title\"')", (tid,))
        assert not placement_settled(conn, aid, 'naming')
