import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.reconciliation import replacement as replacement
from core.reconciliation.fulfillment import record_request, settle_assignments
from core.reconciliation.evidence import file_identity
from core.reconciliation.identity import review_case_key
from core.repair_worker import RepairWorker


class Server:
    def __init__(self):
        self.playlists = {'p': {'id': 'p', 'name': 'Road songs', 'comment': 'Keep this description',
                              'public': True, 'owner': 'me', 'entry': [{'id': i} for i in ['x', 'old', 'old', 'y']]}}
        self.fail = False

    def ensure_connection(self):
        return True

    def _make_request(self, method, args=None):
        args = args or {}
        if method == 'getPlaylists':
            return {'playlists': {'playlist': [{'id': key} for key in self.playlists]}}
        if method == 'getPlaylist':
            return {'playlist': copy.deepcopy(self.playlists[args['id']])}
        if method == 'getSong':
            return {'song': {'id': args['id']}}
        if self.fail:
            return None
        target = self.playlists[args['playlistId']]
        if method == 'createPlaylist':
            target['entry'] = [{'id': i} for i in args['songId']]
            target['comment'] = ''
        if method == 'updatePlaylist':
            target.update({k: args[k] for k in ['name', 'comment']})
            target['public'] = args['public'] == 'true'
        return {'status': 'ok'}


def test_playlist_handoff_preserves_duplicates_order_identity_and_metadata():
    server = Server()
    before = copy.deepcopy(server.playlists['p'])
    journal, saves = {}, []
    replacement.migrate_playlists(server, 'old', 'new', journal, lambda j: saves.append(copy.deepcopy(j)))
    assert saves[0]['playlists']['p']['before']['tracks'] == ['x', 'old', 'old', 'y']
    after = server.playlists['p']
    assert after == {**before, 'entry': [{'id': i} for i in ['x', 'new', 'new', 'y']]}
    replacement.migrate_playlists(server, 'old', 'new', journal, lambda j: None)
    assert server.playlists['p'] == after


def test_playlist_retry_refuses_concurrent_user_edits():
    server, journal = Server(), {}
    server.fail = True
    with pytest.raises(ValueError, match='failed'):
        replacement.migrate_playlists(server, 'old', 'new', journal, lambda j: None)
    server.fail = False
    server.playlists['p']['entry'].insert(0, {'id': 'user-added'})
    with pytest.raises(ValueError, match='changed'):
        replacement.migrate_playlists(server, 'old', 'new', journal, lambda j: None)
    assert server.playlists['p']['entry'][0] == {'id': 'user-added'}
    assert server.playlists['p']['entry'][2] == {'id': 'old'}


def test_playlist_retry_recovers_interruption_before_metadata_restore():
    server, journal = Server(), {}
    original = server._make_request
    def interrupted(method, args=None):
        if method == 'updatePlaylist':
            raise RuntimeError('connection lost')
        return original(method, args)
    server._make_request = interrupted
    with pytest.raises(RuntimeError):
        replacement.migrate_playlists(server, 'old', 'new', journal, lambda j: None)
    server._make_request = original
    replacement.migrate_playlists(server, 'old', 'new', journal, lambda j: None)
    assert server.playlists['p']['comment'] == 'Keep this description'


@pytest.mark.parametrize('same_release', [True, False])
@pytest.mark.parametrize('verified,fail,changed,expected', [
    (False, False, False, 'waiting'), (True, True, False, 'attention'),
    (True, False, True, 'attention'), (True, False, False, 'complete')])
def test_approved_replacement_retires_only_verified_copy_after_playlist_handoff(tmp_path, monkeypatch, verified, fail, changed, expected, same_release):
    db_path = str(tmp_path / 'state.sqlite')
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(db_path))
    old, new = tmp_path / 'old.flac', tmp_path / 'new.flac'
    old.write_bytes(b'old audio')
    new.write_bytes(b'correct audio')
    stat = old.stat()
    details = {'album_id': 'album', 'album_folder': str(tmp_path), 'profile_id': 1,
               'source': 'musicbrainz', 'release_id': 'release', 'track_id': 'old',
               'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
               'file_identity': file_identity(stat), 'replacement_request': True,
               'replacement_track': {'source_track_id': 'recording'}}
    case = review_case_key(details, None)
    record_request(db, details, details['replacement_track'])
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE tracks(id TEXT,album_id TEXT,file_path TEXT)')
        conn.executemany('INSERT INTO tracks VALUES(?,?,?)', [('old', 'album', str(old)), ('new', 'album', str(new))])
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT,plan_json TEXT)')
        assignment = {'source_track_id': 'recording', 'file_path': str(new), 'status': 'confirmed' if verified else 'uncertain'}
        conn.execute('INSERT INTO reconcile_album_plans VALUES(?,?)', (case, json.dumps({'source': 'musicbrainz', 'release_id': 'release', 'assignments': [assignment], '_planning': {'files': [{'path': str(new), 'size_bytes': new.stat().st_size, 'mtime_ns': new.stat().st_mtime_ns, 'file_identity': file_identity(new.stat())}]}})))
        conn.execute("UPDATE reconcile_fulfillment SET state='published',published_path=?", (str(new),))
    server = Server()
    server.fail = fail
    monkeypatch.setattr('core.library.playlist_membership._active_server_and_client', lambda: ('navidrome', server))
    db.get_album_canonical = lambda _: {'source': 'musicbrainz', 'album_id': 'release' if same_release else 'another'}
    if verified and not same_release:
        expected = 'attention'
    replacement.approve(db, str(old), details)
    # Even a verified download remains in the waiting queue until handoff completes.
    settle_assignments(db, case, [assignment], 'release', 'musicbrainz')
    with db._get_connection() as conn:
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'published'
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker.transfer_folder = db, str(tmp_path)
    worker._schedule_album_refresh = lambda *args, **kwargs: True
    if changed:
        old.write_bytes(b'an external change')
    replacement.advance(worker)
    with db._get_connection() as conn:
        state, journal = conn.execute('SELECT state,journal FROM reconcile_replacements').fetchone()
    assert state == expected
    assert new.read_bytes() == b'correct audio'
    assert old.exists() == (expected != 'complete')
    if expected == 'complete':
        assert ['new', 'new'] == [row['id'] for row in server.playlists['p']['entry'][1:3]]
        assert open(json.loads(journal)['quarantine_path'], 'rb').read() == b'old audio'
        replacement.advance(worker)  # Restart is idempotent.


def test_reviewed_download_is_published_separately_even_with_identical_name(tmp_path):
    old = tmp_path / '01 Song.flac'
    old.write_bytes(b'playing')
    path = replacement.separate_delivery(str(old), {'reason': 'library_reconciliation_wrong_audio'})
    assert path != str(old) and path.endswith('.flac')
    assert old.read_bytes() == b'playing'
    assert replacement.separate_delivery(str(old), {}) == str(old)
