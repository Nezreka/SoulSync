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


def _fake_resolver(ids, seen=None):
    """resolve_ids_fn stub returning the given service ids. When ``seen`` is given, records
    the search_id_fn it was called with (to assert the backfill toggle wiring)."""
    def fn(tracks, service, search_id_fn=None, on_progress=None):
        if seen is not None:
            seen['search_id_fn'] = search_id_fn
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


def test_backfill_off_passes_no_search_fn():
    job = {}   # no 'backfill' key → off
    seen = {}
    ws._run_service_export(job, _FakeDB([{}]), 5, 'PL', 'deezer',
                           _FakeClient({'success': True, 'playlist_id': 'p', 'added': 1}),
                           _fake_resolver(['dz'], seen))
    assert seen['search_id_fn'] is None


def test_backfill_on_wires_search_fn(monkeypatch):
    job = {'backfill': True}
    seen = {}
    monkeypatch.setattr(ws, '_build_service_search_id_fn', lambda service: 'SEARCH_FN')
    ws._run_service_export(job, _FakeDB([{}]), 5, 'PL', 'spotify',
                           _FakeClient({'success': True, 'playlist_id': 'p', 'added': 1}),
                           _fake_resolver(['sx'], seen))
    assert seen['search_id_fn'] == 'SEARCH_FN'


def test_spotify_backfill_search_disables_cross_service_fallback(monkeypatch):
    """REGRESSION: Spotify's search_tracks falls back to iTunes/Deezer (non-Spotify ids) under
    rate-limit/free. The backfill MUST disable that or it pushes wrong ids into the Spotify
    playlist. Assert the search is invoked with allow_fallback=False."""
    seen = {}

    class _FakeSpotify:
        def search_tracks(self, q, limit=10, allow_fallback=True):
            seen['allow_fallback'] = allow_fallback
            return []

    monkeypatch.setattr(ws, 'get_spotify_client', lambda: _FakeSpotify())
    fn = ws._build_service_search_id_fn('spotify')
    assert fn is not None
    fn('Kendrick Lamar', 'Not Like Us')   # drives the search
    assert seen['allow_fallback'] is False


def test_spotify_export_endpoint_demands_auth_when_no_write_scope(monkeypatch):
    """The export endpoint must return needs_auth (not start a doomed job) when the Spotify
    token lacks write scope — and it must short-circuit BEFORE touching the DB."""
    import types
    monkeypatch.setattr(ws, 'spotify_client',
                        types.SimpleNamespace(has_write_scope=lambda: False))
    resp = ws.app.test_client().post('/api/playlists/5/export/service/spotify')
    data = resp.get_json()
    assert data['needs_auth'] is True
    assert data['auth_url'] == '/auth/spotify/export'
    assert data['success'] is False


def test_spotify_export_endpoint_proceeds_when_write_scope_present(monkeypatch):
    """With write scope, the spotify path must NOT short-circuit on needs_auth (it goes on to
    start a job — here it just must not be a needs_auth response)."""
    import types
    monkeypatch.setattr(ws, 'spotify_client',
                        types.SimpleNamespace(has_write_scope=lambda: True))
    resp = ws.app.test_client().post('/api/playlists/999999/export/service/spotify')
    data = resp.get_json()
    assert not data.get('needs_auth')
