"""The candidate inspector opened from a failed download or a wishlist item."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from core.download_plugins.types import TrackResult  # noqa: E402
from core.downloads import validation  # noqa: E402
from core.matching_engine import MusicMatchingEngine  # noqa: E402
from core.runtime_state import download_batches, download_tasks  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402

TRACK = {
    'id': 'sp-fade', 'name': 'Fade Into You', 'artists': [{'name': 'Mazzy Star'}],
    'album': {'name': 'So Tonight That I Might See'}, 'duration_ms': 238_000,
}


def _hit(**over):
    base = dict(username='alice', filename='Mazzy Star/So Tonight/04 - Fade Into You.flac',
                size=30_000_000, bitrate=1411, duration=238_000, quality='flac',
                free_upload_slots=1, upload_speed=2_000_000, queue_length=0,
                artist='Mazzy Star', title='Fade Into You')
    base.update(over)
    return TrackResult(**base)


class _Source:
    def __init__(self, make_hits):
        self.make_hits = make_hits
        self.queries = []

    async def search(self, query, timeout=20):
        self.queries.append(query)
        return self.make_hits(), []


class _Passthrough:
    def filter_results_by_quality_preference(self, rows, profile_id=None):
        return list(rows)


@pytest.fixture
def env(tmp_path, monkeypatch):
    download_tasks.clear()
    download_batches.clear()
    db = MusicDatabase(str(tmp_path / 'm.db'))
    soulseek = _Source(lambda: [
        _hit(),
        _hit(username='bob', filename='Mazzy Star/Live/03 - Fade Into You (Live).flac', title=None),
    ])

    class _Orch:
        def configured_clients(self):
            return {'soulseek': soulseek}

        def client(self, name):
            return _Passthrough() if name == 'soulseek' else None

    engine = MusicMatchingEngine()
    monkeypatch.setattr(web_server, 'download_orchestrator', _Orch())
    monkeypatch.setattr(web_server, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(web_server, 'get_database', lambda *a, **k: db)
    monkeypatch.setattr(web_server, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr(web_server, 'check_download_permission', lambda: None)
    monkeypatch.setattr(web_server, 'add_activity_item', lambda *a, **k: None)
    dispatched = []
    monkeypatch.setattr(web_server._pinned_batch, 'dispatch_pinned_batch',
                        lambda batch_id, task_ids, deps: dispatched.append((batch_id, task_ids)))
    web_server.app.config['TESTING'] = True
    yield {'client': web_server.app.test_client(), 'db': db, 'dispatched': dispatched,
           'soulseek': soulseek}
    download_tasks.clear()
    download_batches.clear()


def _lines(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    lines = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
    assert lines[-1] == {'done': True}
    return {line['source']: line for line in lines[:-1]}


# ---------------------------------------------------------------------------
# failed download -> inspector
# ---------------------------------------------------------------------------

def test_task_inspect_streams_verdicts_for_the_tasks_track(env):
    download_tasks['t1'] = {'status': 'not_found', 'track_info': dict(TRACK, quality_profile_id=None)}
    slsk = _lines(env['client'].post('/api/downloads/task/t1/inspect'))['soulseek']
    assert [c['username'] for c in slsk['candidates']] == ['alice']
    assert slsk['rejected'][0]['decision']['code'] == 'version_conflict'
    assert any('fade into you' in q.lower() for q in env['soulseek'].queries)


def test_task_inspect_unknown_task_is_404(env):
    assert env['client'].post('/api/downloads/task/nope/inspect').status_code == 404


def test_task_inspect_without_a_name_is_400(env):
    download_tasks['t1'] = {'status': 'failed', 'track_info': {}}
    assert env['client'].post('/api/downloads/task/t1/inspect').status_code == 400


def test_download_candidate_carries_a_quality_override(env, monkeypatch):
    monkeypatch.setattr(web_server.threading, 'Thread',
                        lambda *a, **k: type('T', (), {'start': lambda self: None})())
    download_tasks['t1'] = {'status': 'failed', 'track_info': dict(TRACK), 'batch_id': None}
    resp = env['client'].post('/api/downloads/task/t1/download-candidate', json={
        'username': 'alice', 'filename': 'x.mp3', 'override': {'code': 'below_profile', 'stage': 'quality'},
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert download_tasks['t1']['_override_quality'] is True
    assert download_tasks['t1']['_user_manual_pick'] is True

    download_tasks['t1']['status'] = 'failed'
    env['client'].post('/api/downloads/task/t1/download-candidate', json={
        'username': 'alice', 'filename': 'x.mp3', 'override': {'code': 'artist_mismatch'},
    })
    assert download_tasks['t1']['_override_quality'] is False


# ---------------------------------------------------------------------------
# wishlist -> inspector
# ---------------------------------------------------------------------------

def _wish(db, quality_profile_id=None):
    assert db.add_to_wishlist(TRACK, source_type='manual', profile_id=1,
                              quality_profile_id=quality_profile_id)


def test_wishlist_inspect_streams_for_a_track_on_the_wishlist(env):
    _wish(env['db'])
    slsk = _lines(env['client'].post('/api/wishlist/inspect', json={'track_id': 'sp-fade'}))['soulseek']
    assert [c['username'] for c in slsk['candidates']] == ['alice']


def test_wishlist_inspect_refuses_what_isnt_on_this_profiles_wishlist(env):
    assert env['client'].post('/api/wishlist/inspect', json={'track_id': 'sp-fade'}).status_code == 404
    assert env['client'].post('/api/wishlist/inspect', json={}).status_code == 404


def test_wishlist_pick_starts_a_pinned_download_with_the_wishlist_metadata(env):
    _wish(env['db'], quality_profile_id=None)
    resp = env['client'].post('/api/wishlist/inspect/download', json={
        'track_id': 'sp-fade',
        'candidate': {'username': 'alice', 'filename': 'Mazzy Star/x/04 - Fade Into You.flac',
                      'size': 30_000_000, 'quality': 'FLAC'},
    })
    body = resp.get_json()
    assert resp.status_code == 200 and body['success'], body
    batch = download_batches[body['batch_id']]
    assert batch['source_page'] == 'Wishlist'
    assert batch['playlist_id'].startswith('wishlist_pick_')
    assert batch['skip_failed_wishlist'] is True
    task = download_tasks[body['task_id']]
    assert task['_user_manual_pick'] is True
    assert task['_pinned_candidate']['filename'] == 'Mazzy Star/x/04 - Fade Into You.flac'
    assert task['track_info']['id'] == 'sp-fade'
    assert task['track_info']['name'] == 'Fade Into You'
    assert task['track_info']['wishlist_id']
    assert not task.get('_override_quality')
    assert env['dispatched'] == [(body['batch_id'], [body['task_id']])]


def test_wishlist_pick_quality_override_rides_on_the_task(env):
    _wish(env['db'])
    body = env['client'].post('/api/wishlist/inspect/download', json={
        'track_id': 'sp-fade', 'candidate': {'username': 'alice', 'filename': 'a.mp3'},
        'override': {'code': 'below_profile', 'stage': 'quality'},
    }).get_json()
    assert download_tasks[body['task_id']]['_override_quality'] is True


@pytest.mark.parametrize('payload, status', [
    ({'track_id': 'sp-fade'}, 400),
    ({'track_id': 'sp-fade', 'candidate': {'username': 'torrent', 'filename': 't||1'}}, 400),
    ({'track_id': 'nope', 'candidate': {'username': 'alice', 'filename': 'a.flac'}}, 404),
])
def test_wishlist_pick_refusals(env, payload, status):
    _wish(env['db'])
    resp = env['client'].post('/api/wishlist/inspect/download', json=payload)
    assert resp.status_code == status
    assert env['dispatched'] == []
