"""/api/search/manual/start: the user's typed release becomes a pinned batch
whose every task carries the manual flag. no metadata lookup, no network."""

from __future__ import annotations

import base64
import os
from unittest.mock import patch

import pytest

from core.runtime_state import download_batches, download_tasks

web_server = pytest.importorskip("web_server")


@pytest.fixture()
def client(monkeypatch):
    web_server.app.config["TESTING"] = True
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    yield web_server.app.test_client()
    for bid in [b for b, batch in download_batches.items() if batch.get('source_page') == 'Search']:
        for tid in download_batches[bid].get('queue', []):
            download_tasks.pop(tid, None)
        download_batches.pop(bid, None)


def _files(username='deadair'):
    return [
        {'username': username, 'filename': r'Glasto\01 - a.flac', 'size': 1, 'duration': 199_000},
        {'username': username, 'filename': r'Glasto\02 - b.flac', 'size': 1, 'duration': 259_000},
    ]


def _payload(username='deadair', **album):
    return {
        'files': _files(username),
        'album': {'name': 'Live at Glastonbury 2003', 'artist': 'Radiohead', 'date': '2003-06-28',
                  'genre': '', 'type': 'live', **album},
        'tracks': [
            {'file_key': f'{username}::Glasto\\01 - a.flac', 'title': '2 + 2 = 5', 'track_number': 1, 'disc_number': 1},
            {'file_key': f'{username}::Glasto\\02 - b.flac', 'title': 'Lucky', 'track_number': 2, 'disc_number': 1},
        ],
    }


def test_a_typed_album_becomes_one_batch_of_manual_tasks(client):
    with patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch:
        body = client.post('/api/search/manual/start', json=_payload()).get_json()
    assert body['success'] is True, body
    batch = download_batches[body['batch_id']]
    assert batch['playlist_name'] == 'Live at Glastonbury 2003'
    assert batch['is_album_download'] is True
    infos = [download_tasks[t]['track_info'] for t in batch['queue']]
    assert [i['name'] for i in infos] == ['2 + 2 = 5', 'Lucky']
    assert all(i['_manual_metadata'] is True for i in infos)
    assert all(i['_explicit_album_context']['name'] == 'Live at Glastonbury 2003' for i in infos)
    dispatch.assert_called_once()


def test_an_uploaded_cover_is_saved_and_handed_to_every_task(client):
    url = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG' + b'x' * 50).decode()
    with patch.object(web_server._pinned_batch, 'dispatch_pinned_batch'):
        body = client.post('/api/search/manual/start', json=_payload(image_data=url)).get_json()
    queue = download_batches[body['batch_id']]['queue']
    paths = {download_tasks[t]['track_info']['_manual_cover_path'] for t in queue}
    (path,) = paths
    try:
        assert os.path.isfile(path) and path.endswith('.png')
    finally:
        os.remove(path)


def test_bad_input_is_refused_with_a_sentence_and_nothing_starts(client):
    with patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch:
        r = client.post('/api/search/manual/start', json=_payload(name=' '))
    assert r.status_code == 400
    assert r.get_json()['error'] == 'Give the album a name.'
    dispatch.assert_not_called()


def test_a_bad_cover_is_refused_before_anything_starts(client):
    with patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch:
        r = client.post('/api/search/manual/start',
                        json=_payload(image_data='data:text/plain;base64,aGk='))
    assert r.status_code == 400
    dispatch.assert_not_called()


def test_a_blocklisted_artist_asks_first(client):
    db = web_server.get_database()
    eid = db.add_blocklist_entry(1, 'artist', 'Radiohead', spotify_id='rh-manual')
    try:
        with patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch:
            r = client.post('/api/search/manual/start', json=_payload())
        assert r.status_code == 409 and r.get_json()['blocked'] is True
        dispatch.assert_not_called()
    finally:
        db.remove_blocklist_entry(1, eid)


def test_a_torrent_release_goes_the_direct_route_with_the_flag(client):
    with patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch, \
            patch.object(web_server, '_start_enhanced_album_download', return_value=2) as direct:
        body = client.post('/api/search/manual/start', json=_payload('torrent')).get_json()
    assert body['success'] is True
    dispatch.assert_not_called()
    enhanced = direct.call_args[0][0]
    assert all(item['spotify_track']['_manual_metadata'] is True for item in enhanced)
