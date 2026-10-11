"""Quarantine shows the intended target's current physical library copy."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import api.quarantine as routes
import database.music_database as databases
from core.library_scope import reset_library_scope, set_library_scope


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = databases.MusicDatabase(str(tmp_path / 'library.db'))
    library = tmp_path / 'Library'
    library.mkdir()
    quarantine = tmp_path / 'ss_quarantine'
    quarantine.mkdir()
    settings = {'soulseek.download_path': str(tmp_path), 'soulseek.transfer_path': str(library)}
    config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default),
                             get_active_media_server=lambda: 'soulsync')
    monkeypatch.setattr(routes, 'config_manager', config)
    monkeypatch.setattr(routes, 'docker_resolve_path', lambda p: p)
    monkeypatch.setattr(databases, 'get_database', lambda *args, **kwargs: db)
    monkeypatch.setattr('core.imports.quarantine_library.library_scope_for_profile', lambda pid: pid if pid and pid > 1 else 'shared')
    monkeypatch.setattr('core.imports.quarantine_library.library_root_for_profile', lambda pid, **kwargs: str(library) if pid and pid > 1 else None)
    token = set_library_scope('shared')
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    yield SimpleNamespace(db=db, library=library, quarantine=quarantine, client=app.test_client(), settings=settings)
    reset_library_scope(token)


def candidate(env, *, title='Xtal', artist='Aphex Twin', owner=None, filename='Xtal.flac', isrc=None):
    path = env.library / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'library audio')
    with env.db._get_connection() as conn:
        conn.execute('INSERT OR IGNORE INTO artists(id,name,server_source,owner_profile_id) VALUES(1,?,?,?)',
                     (artist, 'soulsync', owner))
        conn.execute('INSERT OR IGNORE INTO albums(id,artist_id,title,server_source,owner_profile_id) VALUES(1,1,?,?,?)',
                     ('Selected Ambient Works', 'soulsync', owner))
        conn.execute('INSERT INTO tracks(album_id,artist_id,title,file_path,server_source,owner_profile_id,isrc) VALUES(1,1,?,?,?,?,?)',
                     (title, str(path), 'soulsync', owner, isrc))
        conn.execute('UPDATE artists SET name_norm = ?, name_key = ? WHERE id=1',
                     (env.db._normalize_for_comparison(artist), artist.lower().replace(' ', '')))
        conn.execute('UPDATE tracks SET title_norm = ? WHERE file_path = ?',
                     (env.db._normalize_for_comparison(title), str(path)))
    return path


def quarantined(env, *, name='q1', title='Xtal', artist='Aphex Twin', context=None):
    (env.quarantine / f'{name}.flac.quarantined').write_bytes(b'bad audio')
    (env.quarantine / f'{name}.json').write_text(json.dumps({
        'expected_track': title, 'expected_artist': artist, 'original_filename': f'{name}.flac',
        'context': context or {'profile_id': 1, 'track_info': {'name': title}},
        'trigger': 'acoustid_unverified', 'quarantine_reason': 'no fingerprint match',
    }))


def entries(env):
    response = env.client.get('/api/quarantine/list')
    assert response.status_code == 200
    return response.get_json()['entries']


def test_presence_updates_after_a_later_download_then_file_deletion(env):
    quarantined(env)
    assert entries(env)[0]['library_status'] == 'missing'
    path = candidate(env)
    env.db.add_library_history_entry('download', 'Xtal', artist_name='Aphex Twin',
                                    album_name='Selected Ambient Works', file_path=str(path),
                                    quality='FLAC 16-bit', verification_status='human_verified')
    hit = entries(env)[0]
    assert hit['library_status'] == 'present'
    assert hit['library_match']['album'] == 'Selected Ambient Works'
    assert hit['library_match']['quality'] == 'FLAC 16-bit'
    assert hit['library_match']['verification_status'] == 'human_verified'
    path.unlink()
    assert entries(env)[0]['library_status'] != 'present'


@pytest.mark.parametrize('title,artist', [('Xtal (Live)', 'Aphex Twin'), ('Xtal', 'Other Artist'), ('Xtal Remix', 'Aphex Twin')])
def test_different_recordings_are_not_owned(env, title, artist):
    candidate(env, title=title, artist=artist)
    quarantined(env)
    assert entries(env)[0]['library_status'] == 'missing'


def test_a_copy_in_another_library_does_not_count(env):
    candidate(env, owner=2)
    quarantined(env)
    assert entries(env)[0]['library_status'] == 'missing'
    quarantined(env, name='own', context={'profile_id': 2, 'track_info': {'name': 'Xtal'}})
    token = set_library_scope(2)
    try:
        rows = env.db.get_quarantine_library_candidates('Xtal', ['Aphex Twin'], {}, 'soulsync')
    finally:
        reset_library_scope(token)
    assert rows, 'own-library candidates absent'
    by_id = {entry['id']: entry for entry in entries(env)}
    assert by_id['own']['library_status'] == 'present', (rows, by_id)


def test_album_staging_never_counts_as_a_published_copy(env):
    candidate(env, filename='.soulsync_atomic_staging/batch/Xtal.flac')
    quarantined(env)
    assert entries(env)[0]['library_status'] == 'missing'


def test_an_import_counts_before_the_media_server_scan(env):
    path = env.library / 'Xtal.flac'
    path.write_bytes(b'newly published audio')
    env.db.add_library_history_entry('download', 'Xtal', artist_name='Aphex Twin',
                                    file_path=str(path), verification_status='verified')
    quarantined(env)
    assert entries(env)[0]['library_status'] == 'present'


def test_conflicting_recording_ids_do_not_count(env):
    candidate(env, isrc='OTHER0000001')
    quarantined(env, context={'profile_id': 1, 'track_info': {'name': 'Xtal', 'isrc': 'TARGET000001'}})
    assert entries(env)[0]['library_status'] == 'missing'


def test_lookup_failure_keeps_the_queue_and_reports_unknown(env, monkeypatch):
    quarantined(env)
    def unavailable(*args, **kwargs):
        raise RuntimeError('database temporarily unavailable')
    monkeypatch.setattr(env.db, 'get_quarantine_library_candidates', unavailable, raising=False)
    result = entries(env)[0]
    assert result['id'] == 'q1'
    assert result['library_status'] == 'unknown'
    assert result['library_match'] is None


def test_album_extra_provenance_survives_the_sidecar(env):
    quarantined(env, context={'profile_id': 1, '_album_import_extra': {
        'album': 'Selected Ambient Works', 'download_source': 'usenet'}, 'track_info': {'name': 'Xtal'}})
    assert entries(env)[0]['album_extra'] == {'album': 'Selected Ambient Works', 'download_source': 'usenet'}
    quarantined(env, name='requested', context={'profile_id': 1, 'is_album_download': True, 'track_info': {'name': 'Xtal'}})
    by_id = {entry['id']: entry for entry in entries(env)}
    assert by_id['requested']['album_extra'] is None


def test_history_does_not_override_a_conflicting_recording_id(env):
    path = candidate(env, isrc='OTHER0000001')
    env.db.add_library_history_entry('download', 'Xtal', artist_name='Aphex Twin', file_path=str(path))
    quarantined(env, context={'profile_id': 1, 'track_info': {'name': 'Xtal', 'isrc': 'TARGET000001'}})
    assert entries(env)[0]['library_status'] != 'present'


@pytest.mark.parametrize('folder', ['.deleted', 'deleted'])
def test_deleted_library_copies_do_not_count(env, folder):
    candidate(env, filename=f'{folder}/Xtal.flac')
    quarantined(env)
    assert entries(env)[0]['library_status'] != 'present'


def test_download_seed_copy_does_not_replace_a_missing_library_copy(env):
    path = candidate(env)
    path.unlink()
    (env.library.parent / 'Xtal.flac').write_bytes(b'retained download')
    quarantined(env)
    assert entries(env)[0]['library_status'] != 'present'


def test_another_library_cannot_supply_a_missing_shared_copy(env, monkeypatch):
    path = candidate(env)
    path.unlink()
    foreign = env.library.parent / 'Private'
    foreign.mkdir()
    (foreign / 'Xtal.flac').write_bytes(b'private audio')
    env.settings['library.music_paths'] = [str(foreign)]
    monkeypatch.setattr(env.db, 'get_own_library_profiles', lambda **kwargs: [{'id': 2, 'root': str(foreign)}])
    quarantined(env)
    assert entries(env)[0]['library_status'] != 'present'


def test_path_aliases_cannot_bypass_conflicting_recording_ids(env):
    path = candidate(env, isrc='OTHER0000001')
    with env.db._get_connection() as conn:
        conn.execute('UPDATE tracks SET file_path=?', ('Xtal.flac',))
    env.db.add_library_history_entry('download', 'Xtal', artist_name='Aphex Twin', file_path=str(path))
    quarantined(env, context={'profile_id': 1, 'track_info': {'name': 'Xtal', 'isrc': 'TARGET000001'}})
    assert entries(env)[0]['library_status'] != 'present'


def test_history_uses_the_mapped_import_root(env, monkeypatch):
    path = env.library / 'Xtal.flac'
    path.write_bytes(b'imported audio')
    env.settings['soulseek.transfer_path'] = 'Z:\\Music'
    monkeypatch.setattr('core.imports.paths.docker_resolve_path', lambda raw: str(env.library) if raw == 'Z:\\Music' else raw)
    env.db.add_library_history_entry('download', 'Xtal', artist_name='Aphex Twin', file_path=str(path))
    quarantined(env)
    assert entries(env)[0]['library_status'] == 'present'


def test_unrelated_recordings_do_not_trigger_filesystem_fallbacks(env, monkeypatch):
    path = candidate(env, title='Xtal (Live)')
    path.unlink()
    calls = []
    monkeypatch.setattr('core.imports.quarantine_library.resolve_library_file_path',
                        lambda *args, **kwargs: calls.append(args) or None)
    quarantined(env)
    assert entries(env)[0]['library_status'] == 'missing'
    assert not calls


def test_failed_library_boundary_lookup_reports_unknown(env, monkeypatch):
    import sqlite3
    candidate(env).unlink()
    foreign = env.library.parent / 'Private'
    foreign.mkdir()
    (foreign / 'Xtal.flac').write_bytes(b'private audio')
    env.settings['library.music_paths'] = [str(foreign)]
    quarantined(env)
    connect = env.db._get_connection
    calls = 0
    def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError('profile lookup unavailable')
        return connect()
    monkeypatch.setattr(env.db, '_get_connection', fail_once)
    assert entries(env)[0]['library_status'] == 'unknown'


def test_recording_identifier_lookups_do_not_scan_the_catalogue(env, monkeypatch):
    candidate(env)
    with env.db._get_connection() as conn:
        conn.executemany("INSERT INTO tracks(album_id,artist_id,title,server_source) VALUES(1,1,?,'soulsync')", ((f'Unowned {i}',) for i in range(2000)))
    connect = env.db._get_connection
    steps = 0
    def counted_connection():
        conn = connect()
        def progress():
            nonlocal steps
            steps += 1
            return 0
        conn.set_progress_handler(progress, 100)
        return conn
    monkeypatch.setattr(env.db, '_get_connection', counted_connection)
    assert not env.db.get_quarantine_library_candidates('Missing', [], {'spotify': 'absent', 'isrc': 'ABSENT'})
    assert steps < 20
