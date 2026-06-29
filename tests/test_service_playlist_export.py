"""_run_service_export orchestration (#945): resolve mirrored tracks → service IDs → push →
store the target for idempotent re-export. Deps injected (fake db/client/resolve_fn) so this
needs no DB or live Spotify/Deezer."""

import web_server as ws


class _FakeDB:
    def __init__(self, tracks, existing=None):
        self._tracks, self._existing, self.set_calls = tracks, existing, []

    def get_mirrored_playlist_tracks(self, pid):
        return self._tracks

    def get_playlist_export_target(self, pid, service):
        return self._existing

    def set_playlist_export_target(self, pid, service, target):
        self.set_calls.append((pid, service, target))


class _FakeClient:
    def __init__(self, result):
        self.result, self.calls = result, []

    def create_or_update_playlist(self, title, ids, existing_id=None):
        self.calls.append((title, list(ids), existing_id))
        return self.result


def _resolve(mapping):
    return lambda artist, title: (mapping.get(title), 'library' if mapping.get(title) else None)


def test_success_stores_target():
    job = {}
    db = _FakeDB([{'artist': 'A', 'title': 'X'}, {'artist': 'A', 'title': 'Y'}])
    client = _FakeClient({'success': True, 'playlist_id': 'pl-1', 'added': 2})
    ws._run_service_export(job, db, 5, 'My PL', 'spotify', client, _resolve({'X': 'sx', 'Y': 'sy'}))
    assert job['phase'] == 'done'
    assert client.calls[0] == ('My PL', ['sx', 'sy'], None)
    assert db.set_calls == [(5, 'spotify', 'pl-1')]


def test_no_matched_ids_errors_no_push():
    job = {}
    client = _FakeClient({'success': True})
    ws._run_service_export(job, _FakeDB([{'artist': 'A', 'title': 'X'}]), 5, 'PL', 'deezer',
                           client, _resolve({}))
    assert job['phase'] == 'error' and 'nothing to export' in job['error']
    assert client.calls == []


def test_client_none_errors():
    job = {}
    ws._run_service_export(job, _FakeDB([{'artist': 'A', 'title': 'X'}]), 5, 'PL', 'spotify',
                           None, _resolve({'X': 'sx'}))
    assert job['phase'] == 'error' and 'not connected' in job['error']


def test_push_failure_surfaces_error_no_target_store():
    job = {}
    db = _FakeDB([{'artist': 'A', 'title': 'X'}])
    client = _FakeClient({'success': False, 'error': 'Reconnect Spotify'})
    ws._run_service_export(job, db, 5, 'PL', 'spotify', client, _resolve({'X': 'sx'}))
    assert job['phase'] == 'error' and job['error'] == 'Reconnect Spotify'
    assert db.set_calls == []


def test_reexport_passes_existing_target():
    job = {}
    db = _FakeDB([{'artist': 'A', 'title': 'X'}], existing='pl-old')
    client = _FakeClient({'success': True, 'playlist_id': 'pl-old', 'added': 1})
    ws._run_service_export(job, db, 5, 'PL', 'deezer', client, _resolve({'X': 'dz'}))
    assert client.calls[0][2] == 'pl-old'
