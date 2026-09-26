"""The redownload modal's source stream carries rejected rows and their reasons."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from core.download_plugins.types import TrackResult  # noqa: E402
from core.downloads import validation  # noqa: E402
from core.matching_engine import MusicMatchingEngine  # noqa: E402


def _hit(**over):
    base = dict(
        username='alice', filename='Mazzy Star/So Tonight/04 - Fade Into You.flac',
        size=30_000_000, bitrate=1411, duration=238_000, quality='flac',
        free_upload_slots=1, upload_speed=2_000_000, queue_length=0,
        artist='Mazzy Star', title='Fade Into You',
    )
    base.update(over)
    return TrackResult(**base)


class _Source:
    def __init__(self, hits):
        self.hits = hits
        self.queries = []

    async def search(self, query, timeout=20):
        self.queries.append(query)
        return [_clone(h) for h in self.hits], []


def _clone(hit):
    return TrackResult(**{f: getattr(hit, f) for f in (
        'username', 'filename', 'size', 'bitrate', 'duration', 'quality',
        'free_upload_slots', 'upload_speed', 'queue_length', 'artist', 'title')})


class _Passthrough:
    def filter_results_by_quality_preference(self, rows, profile_id=None):
        return list(rows)


class _Db:
    def __init__(self, blacklisted=()):
        self.blacklisted = set(blacklisted)

    def is_blacklisted(self, username, filename):
        return (username, filename) in self.blacklisted

    def _get_connection(self):
        raise RuntimeError('no library db in this test')


@pytest.fixture
def client(monkeypatch):
    soulseek = _Source([
        _hit(),
        _hit(username='bob', filename='Mazzy Star/Live/03 - Fade Into You (Live).flac', title=None),
        _hit(username='cat', filename='Mazzy Star/So Tonight/01 - Halah.flac', title='Halah'),
        _hit(username='mallory', filename='Mazzy Star/Bad/04 - Fade Into You.flac'),
    ])
    tidal = _Source([
        _hit(username='tidal', filename='tid||1'),
        _hit(username='tidal', filename='tid||2', duration=300_000),
    ])

    class _Orch:
        def configured_clients(self):
            return {'soulseek': soulseek, 'tidal': tidal}

        def client(self, name):
            return _Passthrough() if name == 'soulseek' else None

    engine = MusicMatchingEngine()
    monkeypatch.setattr(web_server, 'download_orchestrator', _Orch())
    monkeypatch.setattr(web_server, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    db = _Db(blacklisted={('mallory', 'Mazzy Star/Bad/04 - Fade Into You.flac')})
    monkeypatch.setattr(web_server, 'get_database', lambda *a, **k: db)
    web_server.app.config['TESTING'] = True
    return web_server.app.test_client()


def _stream(client):
    resp = client.post('/api/library/track/42/redownload/search-sources', json={
        'metadata': {'name': 'Fade Into You', 'artist': 'Mazzy Star',
                     'album': 'So Tonight That I Might See', 'duration_ms': 238_000},
    })
    assert resp.status_code == 200
    lines = [json.loads(l) for l in resp.get_data(as_text=True).splitlines() if l.strip()]
    assert lines[-1] == {'done': True}
    return {l['source']: l for l in lines[:-1]}


def test_each_source_line_splits_accepted_and_rejected(client):
    by_source = _stream(client)
    assert set(by_source) == {'soulseek', 'tidal'}

    slsk = by_source['soulseek']
    assert [c['username'] for c in slsk['candidates']] == ['alice']
    assert all(c['decision']['accepted'] for c in slsk['candidates'])
    codes = {r['username']: r['decision']['code'] for r in slsk['rejected']}
    assert codes == {'bob': 'version_conflict', 'cat': 'match_weak', 'mallory': 'blacklisted'}
    assert slsk['rejected_total'] == 3
    assert sum(slsk['rejected_counts'].values()) == 3

    tidal = by_source['tidal']
    assert [c['filename'] for c in tidal['candidates']] == ['tid||1']
    assert tidal['rejected'][0]['decision'] == {
        'accepted': False, 'code': 'duration_mismatch', 'detail': '5:00 vs expected 3:58',
        'stage': 'duration', 'score': None,
    }


def test_two_queries_of_the_same_hits_do_not_double_rows(client):
    slsk = _stream(client)['soulseek']
    keys = [(r['username'], r['filename']) for r in slsk['candidates'] + slsk['rejected']]
    assert len(keys) == len(set(keys))


def test_a_source_that_throws_still_gets_a_well_formed_line(client, monkeypatch):
    import core.downloads.candidate_pool as pool

    def boom(*_a, **_kw):
        raise RuntimeError('source fell over')

    monkeypatch.setattr(pool, 'build_source_rows', boom)
    by_source = _stream(client)
    for line in by_source.values():
        assert line['candidates'] == [] and line['rejected'] == []
        assert line['error'] == 'source fell over'
