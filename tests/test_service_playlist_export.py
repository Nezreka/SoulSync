"""_run_service_export orchestration (#945): resolve mirrored tracks → service IDs
(discovery cache → library) → push → store the target for idempotent re-export. Deps
injected so this needs no real DB or live Spotify/Deezer."""

import json
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


def _fake_resolver(ids):
    """resolve_ids_fn stub returning the given service ids as the resolved set."""
    def fn(tracks, service, on_progress=None):
        resolved = [{'artist': 'A', 'title': f't{i}', 'service_track_id': s}
                    for i, s in enumerate(ids)]
        matched = sum(1 for s in ids if s)
        return {'resolved': resolved,
                'stats': {'total': len(ids), 'resolved': matched, 'unmatched': len(ids) - matched}}
    return fn


def _discovered(artist, title, service, tid):
    return {'artist_name': artist, 'track_name': title,
            'extra_data': json.dumps({'discovered': True, 'provider': service,
                                      'matched_data': {'id': tid}})}


def test_success_resolves_from_discovery_cache_and_stores_target():
    """Real resolver: both tracks were discovered to Deezer, so their IDs come straight
    from extra_data (the cache) with no DB/API — the gap Boulder spotted."""
    job = {}
    db = _FakeDB([_discovered('A', 'X', 'deezer', 111), _discovered('A', 'Y', 'deezer', 222)])
    client = _FakeClient({'success': True, 'playlist_id': 'pl-1', 'added': 2})
    ws._run_service_export(job, db, 5, 'My PL', 'deezer', client)   # real resolve_service_track_ids
    assert job['phase'] == 'done'
    assert client.calls[0] == ('My PL', ['111', '222'], None)
    assert db.set_calls == [(5, 'deezer', 'pl-1')]
    assert job['stats']['from_cache'] == 2 and job['stats']['unmatched'] == 0


def test_no_match_errors_no_push():
    job = {}
    client = _FakeClient({'success': True})
    ws._run_service_export(job, _FakeDB([{}]), 5, 'PL', 'deezer', client, _fake_resolver([None]))
    assert job['phase'] == 'error' and 'nothing to export' in job['error']
    assert client.calls == []


def test_client_none_errors():
    job = {}
    ws._run_service_export(job, _FakeDB([{}]), 5, 'PL', 'spotify', None, _fake_resolver(['sx']))
    assert job['phase'] == 'error' and 'not connected' in job['error']


def test_push_failure_surfaces_error_no_target_store():
    job = {}
    db = _FakeDB([{}])
    client = _FakeClient({'success': False, 'error': 'Reconnect Spotify'})
    ws._run_service_export(job, db, 5, 'PL', 'spotify', client, _fake_resolver(['sx']))
    assert job['phase'] == 'error' and job['error'] == 'Reconnect Spotify'
    assert db.set_calls == []


def test_reexport_passes_existing_target():
    job = {}
    db = _FakeDB([{}], existing='pl-old')
    client = _FakeClient({'success': True, 'playlist_id': 'pl-old', 'added': 1})
    ws._run_service_export(job, db, 5, 'PL', 'deezer', client, _fake_resolver(['dz']))
    assert client.calls[0][2] == 'pl-old'
