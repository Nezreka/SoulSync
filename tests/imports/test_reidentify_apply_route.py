"""/api/reidentify/apply: what the single-track re-identify stages.

review 5 W2-WD-3: it acts on the copy in the library being worked in, never
another library's (a track's primary flag spans every library).
review 5 R5-D-3 (single-track twin): the copy is made with no hint
transaction open, and a staged file always has a committed hint. a staged
copy whose hint failed was picked up by auto-import as a new track.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-reid-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'reid.db'))
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from database.music_database import MusicDatabase  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    import core.imports.rematch_search as rematch_search
    import core.library_scope as library_scope

    db = MusicDatabase(str(tmp_path / 'music.db'))
    shared = tmp_path / 'music' / '01 Song.flac'
    own = tmp_path / 'profiles' / '3' / '01 Song.flac'
    for path, body in ((shared, b'shared copy'), (own, b'own copy')):
        path.parent.mkdir(parents=True)
        path.write_bytes(body)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'Band')")
        conn.execute("INSERT INTO lib2_albums(id, title, primary_artist_id) VALUES(1, 'Record', 1)")
        conn.execute("INSERT INTO lib2_tracks(id, album_id, title) VALUES(7, 1, 'Song')")
        # the shared copy holds the track's one primary flag
        conn.execute("INSERT INTO lib2_track_files(id, track_id, path, is_primary, owner_profile_id)"
                     " VALUES(11, 7, ?, 1, NULL)", (str(shared),))
        conn.execute("INSERT INTO lib2_track_files(id, track_id, path, is_primary, owner_profile_id)"
                     " VALUES(10, 7, ?, 0, 3)", (str(own),))
        conn.commit()
    staging = tmp_path / 'Staging'
    monkeypatch.setattr(web_server, 'get_database', lambda: db)
    monkeypatch.setattr(web_server, '_resolve_library_file_path', lambda p: p)
    monkeypatch.setattr(web_server, 'docker_resolve_path', lambda p: str(staging))
    monkeypatch.setattr(rematch_search, 'resolve_hint_fields', lambda source, track_id: {
        'source': source, 'track_id': track_id, 'album_id': 'alb', 'album_name': 'Other Record',
        'artist_name': 'Band', 'track_title': 'Song', 'album_type': 'album'})
    # profile 3 keeps a library of its own, and the admin works in it
    monkeypatch.setattr(library_scope, 'own_library_ids', lambda: frozenset({3}))
    client = web_server.app.test_client()
    with client.session_transaction() as sess:
        sess['profile_id'] = 1
        sess[library_scope.SESSION_KEY] = 3
    return db, client, staging


def _apply(client):
    return client.post('/api/reidentify/apply', json={
        'library_track_id': 7, 'source': 'spotify', 'track_id': 'sp-1', 'replace': True})


def _hints(db):
    with db._get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM rematch_hints")]


def test_reidentify_stages_the_copy_of_the_library_worked_in(env):
    db, client, staging = env
    resp = _apply(client)
    assert resp.status_code == 200, resp.get_json()
    staged = resp.get_json()['staged_path']
    with open(staged, 'rb') as fh:
        assert fh.read() == b'own copy'
    (hint,) = _hints(db)
    assert hint['status'] == 'pending' and hint['replace_file_id'] == 10
    assert not [name for name in os.listdir(staging) if name.endswith('.part')]


def test_a_hint_that_cannot_be_written_leaves_no_staged_file(env, monkeypatch):
    import core.imports.rematch_hints as rematch_hints
    db, client, staging = env

    def broken(cursor, hint):
        raise RuntimeError('database is locked')

    monkeypatch.setattr(rematch_hints, 'create_hint', broken)
    resp = _apply(client)
    assert resp.status_code == 500
    # a copy left here would be imported as a brand new track
    assert os.listdir(staging) == []
    assert _hints(db) == []
