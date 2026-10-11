"""The routes behind the library's Interactive Search outside the library:
an entity search without text, and the library track a failed download is for."""

from __future__ import annotations

from contextlib import closing

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from core.runtime_state import download_tasks  # noqa: E402
from tests.downloads.test_inspector_routes import _hit, _Source, env  # noqa: E402,F401


def _library_track(db) -> int:
    with closing(db._get_connection()) as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Mazzy Star')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'So Tonight That I Might See')",
            (artist,)).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, duration) VALUES(?, 'Fade Into You', 238000)",
            (album,)).lastrowid
        conn.commit()
    return track


def test_a_failed_download_names_its_library_track(env):  # noqa: F811
    track_id = _library_track(env['db'])
    client = env['client']
    download_tasks['linked'] = {'status': 'failed', 'track_info': {
        'name': 'Fade Into You', 'source_info': f'{{"lib2_track_id": {track_id}}}'}}
    download_tasks['entity'] = {'status': 'failed', 'track_info': {'lib2_entity': {'track_id': track_id}}}
    download_tasks['gone'] = {'status': 'failed', 'track_info': {'source_info': {'lib2_track_id': 99999}}}
    download_tasks['plain'] = {'status': 'failed', 'track_info': {'name': 'Fade Into You'}}

    def library_track(task):
        return client.get(f'/api/downloads/task/{task}/library-track').get_json()['track_id']

    assert library_track('linked') == track_id
    assert library_track('entity') == track_id
    assert library_track('gone') is None
    assert library_track('plain') is None
    assert client.get('/api/downloads/task/nope/library-track').status_code == 404


def test_an_entity_search_needs_no_text_and_judges_every_hit(env, monkeypatch):  # noqa: F811
    track_id = _library_track(env['db'])
    source = _Source(lambda: [
        _hit(),
        _hit(username='bob', filename='Mazzy Star/Live/03 - Fade Into You (Live).flac', title=None),
    ])

    class _Orch:
        def client(self, name):
            return source

    monkeypatch.setattr(web_server, 'download_orchestrator', _Orch())

    body = env['client'].post('/api/search', json={
        'source': 'soulseek', 'lib2_track_id': track_id}).get_json()

    assert body['queries'] == ['Mazzy Star Fade Into You', 'Mazzy Star So Tonight That I Might See']
    assert source.queries == body['queries']
    verdicts = {row['username']: row['decision'] for row in body['results']}
    assert verdicts['alice']['accepted'] is True
    assert verdicts['bob'] == {**verdicts['bob'], 'accepted': False, 'code': 'version_conflict'}
