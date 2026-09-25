"""the enriched download routes basic search calls: match picked files to a
release, then start them as a real batch. every metadata lookup is stubbed,
no network."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.runtime_state import download_batches, download_tasks

web_server = pytest.importorskip("web_server")


@pytest.fixture()
def client(monkeypatch):
    web_server.app.config["TESTING"] = True
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(web_server, "_preflight_mb_release", lambda *a, **k: None)
    yield web_server.app.test_client()
    for bid in [b for b, batch in download_batches.items() if batch.get('source_page') == 'Search']:
        for tid in download_batches[bid].get('queue', []):
            download_tasks.pop(tid, None)
        download_batches.pop(bid, None)


RELEASE = {
    'success': True, 'source': 'deezer',
    'album': {'id': 'al1', 'name': 'Glastonbury 2003', 'release_date': '2003-06-28',
              'artists': [{'name': 'Radiohead'}], 'image_url': 'http://img', 'total_tracks': 2},
    'tracks': [
        {'id': 't1', 'name': '2 + 2 = 5', 'track_number': 1, 'disc_number': 1, 'duration_ms': 199_000,
         'artists': [{'name': 'Radiohead'}]},
        {'id': 't2', 'name': 'Lucky', 'track_number': 2, 'disc_number': 1, 'duration_ms': 259_000,
         'artists': [{'name': 'Radiohead'}]},
    ],
}


def _files(username='peer'):
    return [
        {'username': username, 'filename': r'Music\Glasto\02 - Lucky.flac', 'title': 'Lucky',
         'artist': 'Radiohead', 'track_number': 2, 'duration': 259_000, 'size': 30},
        {'username': username, 'filename': r'Music\Glasto\01 - 2 + 2 = 5.flac', 'title': '2 + 2 = 5',
         'artist': 'Radiohead', 'track_number': 1, 'duration': 199_000, 'size': 30},
    ]


def _tracks_patch(release=RELEASE):
    return patch('core.metadata.album_tracks.get_artist_album_tracks', lambda *a, **k: release)


def test_match_maps_files_to_the_release(client):
    with _tracks_patch():
        r = client.post('/api/search/enriched/match', json={
            'source': 'deezer', 'album_id': 'al1', 'album_name': 'Glastonbury 2003',
            'artist': 'Radiohead', 'files': _files()})
    body = r.get_json()
    assert body['success'] is True
    assert [t['name'] for t in body['tracks']] == ['2 + 2 = 5', 'Lucky']
    assert [a['track_index'] for a in body['assignments']] == [1, 0]
    assert body['album']['source'] == 'deezer'


def test_match_refuses_a_different_providers_answer(client):
    with _tracks_patch({**RELEASE, 'source': 'itunes'}):
        r = client.post('/api/search/enriched/match', json={
            'source': 'deezer', 'album_id': 'al1', 'files': _files()})
    assert r.status_code == 502
    assert 'Try another source' in r.get_json()['error']


def test_match_needs_a_release_and_files(client):
    assert client.post('/api/search/enriched/match', json={'source': 'deezer'}).status_code == 400


def test_start_album_is_one_batch_of_pinned_tagged_tasks(client):
    files = _files()
    with _tracks_patch(), patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch:
        r = client.post('/api/search/enriched/start', json={
            'source': 'deezer', 'album_id': 'al1', 'album_name': 'Glastonbury 2003', 'artist': 'Radiohead',
            'files': files,
            'assignments': [
                {'file_key': f"peer::{files[0]['filename']}", 'track_index': 1},
                {'file_key': f"peer::{files[1]['filename']}", 'track_index': None},  # skipped
            ]})
    body = r.get_json()
    assert body['success'] is True, body
    batch = download_batches[body['batch_id']]
    assert batch['is_album_download'] is True
    assert batch['album_context']['name'] == 'Glastonbury 2003'
    (task_id,) = batch['queue']
    info = download_tasks[task_id]['track_info']
    assert info['name'] == 'Lucky' and info['track_number'] == 2
    assert info['_is_explicit_album_download'] is True
    assert info['source'] == 'deezer'
    assert download_tasks[task_id]['_pinned_candidate']['filename'] == files[0]['filename']
    dispatch.assert_called_once()


def test_start_with_nothing_assigned_is_refused(client):
    with _tracks_patch():
        r = client.post('/api/search/enriched/start', json={
            'source': 'deezer', 'album_id': 'al1', 'files': _files(), 'assignments': []})
    assert r.status_code == 400


def test_start_album_from_a_torrent_stays_on_the_direct_route(client):
    with _tracks_patch(), \
            patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch, \
            patch.object(web_server, '_start_enhanced_album_download', return_value=2) as direct:
        files = _files('torrent')
        r = client.post('/api/search/enriched/start', json={
            'source': 'deezer', 'album_id': 'al1', 'files': files,
            'assignments': [{'file_key': f"torrent::{f['filename']}", 'track_index': i}
                            for i, f in enumerate(files)]})
    assert r.get_json()['success'] is True
    dispatch.assert_not_called()
    direct.assert_called_once()


def test_start_single_uses_the_providers_track(client):
    details = {'id': 'x9', 'name': 'Lucky', 'track_number': 2, 'disc_number': 1, 'duration_ms': 259_000,
               'artists': ['Radiohead'], 'album': {'id': 'al1', 'name': 'OK Computer', 'total_tracks': 12,
                                                   'album_type': 'album', 'release_date': '1997'}}
    with patch.object(web_server._enriched, 'single_track_details', return_value=details), \
            patch.object(web_server._pinned_batch, 'dispatch_pinned_batch'):
        r = client.post('/api/search/enriched/start', json={
            'source': 'deezer', 'track_id': 'x9', 'files': [_files()[0]],
            'track': {'name': 'Lucky', 'artist': 'Radiohead', 'image_url': 'http://cover'}})
    body = r.get_json()
    assert body['success'] is True, body
    (task_id,) = download_batches[body['batch_id']]['queue']
    info = download_tasks[task_id]['track_info']
    assert info['album']['name'] == 'OK Computer'
    assert info['album']['image_url'] == 'http://cover'
    assert info['_is_explicit_album_download'] is True
    assert download_batches[body['batch_id']]['playlist_name'] == 'Radiohead - Lucky'


def test_start_single_asks_before_a_blocklisted_artist(client):
    db = web_server.get_database()
    eid = db.add_blocklist_entry(1, 'artist', 'Radiohead', spotify_id='rh-sp')
    try:
        with patch.object(web_server._enriched, 'single_track_details', return_value={'name': 'Lucky', 'artists': ['Radiohead']}), \
                patch.object(web_server._pinned_batch, 'dispatch_pinned_batch') as dispatch:
            r = client.post('/api/search/enriched/start', json={
                'source': 'deezer', 'track_id': 'x9', 'files': [_files()[0]], 'track': {}})
        assert r.status_code == 409 and r.get_json()['blocked'] is True
        dispatch.assert_not_called()
    finally:
        db.remove_blocklist_entry(1, eid)
