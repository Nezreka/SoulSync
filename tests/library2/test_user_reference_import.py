"""An upgrade must preserve references across overlapping catalogue IDs."""

from contextlib import closing

from core.library2.bootstrap import run_bootstrap_if_needed
from core.library2.importer import import_legacy_library
from database.music_database import MusicDatabase


def _overlapping_tracks(database):
    with closing(database._get_connection()) as conn:
        conn.execute('DELETE FROM tracks')
        conn.execute('ALTER TABLE tracks ADD COLUMN server_source TEXT')
        conn.execute("INSERT INTO tracks(id,album_id,artist_id,title,file_path,server_source) VALUES(2,10,1,'Chosen','/m/chosen.flac','plex')")
        conn.execute("INSERT INTO tracks(id,album_id,artist_id,title,file_path,server_source) VALUES(100,10,1,'Other','/m/other.flac','plex')")
        conn.commit()


def test_bootstrap_preserves_manual_match_despite_reused_server_id(legacy_db):
    from core.sync.match_overrides import manual_match_server_id, resolve_durable_match_server_id, build_bulk_override_lookup

    _overlapping_tracks(legacy_db)
    with closing(legacy_db._get_connection()) as conn:
        conn.execute('CREATE TABLE manual_library_track_matches(id INTEGER PRIMARY KEY, profile_id INTEGER, source TEXT, source_track_id TEXT, server_source TEXT, library_track_id INTEGER, library_file_path TEXT, updated_at TEXT)')
        conn.execute("INSERT INTO manual_library_track_matches VALUES(1,1,'spotify','chosen','plex',2,'/m/chosen.flac',CURRENT_TIMESTAMP)")
        conn.commit()
    assert run_bootstrap_if_needed(legacy_db, lambda *_: True)['success']
    database = MusicDatabase.__new__(MusicDatabase)
    database._get_connection = legacy_db._get_connection
    match = database.find_manual_library_match_by_source_track_id(1, 'chosen', 'plex')
    assert manual_match_server_id(database, match['library_track_id'], 'plex') == '2'
    assert resolve_durable_match_server_id(database, 1, 'chosen', 'plex', {'2', '100'}) == '2'
    bulk = build_bulk_override_lookup(database, 1, 'plex', {'2', '100'}, [{'source_track_id': 'chosen'}])
    assert bulk('chosen') == '2'
    import_legacy_library(legacy_db)
    assert database.find_manual_library_match_by_source_track_id(1, 'chosen', 'plex')['library_track_id'] == match['library_track_id']
    # A new native match can itself collide with a different server ID.
    with closing(legacy_db._get_connection()) as conn:
        conn.execute("UPDATE manual_library_track_matches SET library_track_id=2, library_track_id_kind='lib2'")
        conn.commit()
    assert resolve_durable_match_server_id(database, 1, 'chosen', 'plex', {'2', '100'}) == '100'
    bulk = build_bulk_override_lookup(database, 1, 'plex', {'2', '100'}, [{'source_track_id': 'chosen'}])
    assert bulk('chosen') == '100'


def test_bootstrap_preserves_stash_source_and_invalidates_legacy_caches(legacy_db, monkeypatch):
    _overlapping_tracks(legacy_db)
    with closing(legacy_db._get_connection()) as conn:
        conn.execute('''CREATE TABLE sample_stash(
            id INTEGER PRIMARY KEY, name TEXT, track_id INTEGER NOT NULL, file_path TEXT,
            tags_json TEXT, start_s REAL DEFAULT 0, end_s REAL DEFAULT 1, pitch_st REAL DEFAULT 0,
            target_bpm REAL, format TEXT DEFAULT 'wav16', created_at REAL DEFAULT 0,
            stem TEXT, folder TEXT, normalize TEXT, fade_ms REAL, reverse INTEGER, space REAL, delay_json TEXT)''')
        conn.execute("INSERT INTO sample_stash(id,name,track_id,file_path) VALUES(1,'Saved chop',2,'/chops/saved.wav')")
        conn.execute("INSERT INTO sample_stash(id,name,track_id,file_path) VALUES(2,'Missing source',101,'/chops/missing.wav')")
        conn.execute('CREATE TABLE sample_analysis(track_id INTEGER PRIMARY KEY, bpm REAL)')
        conn.execute('INSERT INTO sample_analysis VALUES(2,120)')
        conn.execute('CREATE TABLE sample_stems(track_id INTEGER, file_path TEXT)')
        conn.execute("INSERT INTO sample_stems VALUES(2,'/stems/cached.wav')")
        conn.commit()
    assert run_bootstrap_if_needed(legacy_db, lambda *_: True)['success']
    with closing(legacy_db._get_connection()) as conn:
        stash = conn.execute('SELECT t.title,s.file_path,s.track_id FROM sample_stash s JOIN lib2_tracks t ON t.id=s.track_id WHERE s.id=1').fetchone()
        assert tuple(stash[:2]) == ('Chosen', '/chops/saved.wav')
        assert conn.execute('SELECT COUNT(*) FROM sample_analysis').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM sample_stems').fetchone()[0] == 0
        conn.execute('INSERT INTO sample_analysis VALUES(?,123)', (stash[2],))
        conn.execute("INSERT INTO lib2_tracks(id,album_id,title) SELECT 101,id,'Unrelated native track' FROM lib2_albums LIMIT 1")
        conn.commit()
    from core.sample import store
    monkeypatch.setattr(store, 'get_database', lambda: legacy_db)
    assert store.get_stash_entry(1)['track_title'] == 'Chosen'
    assert store.get_stash_entry(2)['track_title'] == ''
    assert store.get_stash_entry(2)['track_id'] is None
    import_legacy_library(legacy_db)
    with closing(legacy_db._get_connection()) as conn:
        assert conn.execute('SELECT bpm FROM sample_analysis').fetchone()[0] == 123
        assert conn.execute('SELECT track_id FROM sample_stash').fetchone()[0] == stash[2]
