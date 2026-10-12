"""#1656 follow-up, through the real route: POST /api/mirror-playlist must not
replace a non-empty mirror with an empty track list.

An empty payload is what a failed client-side fetch looks like — the #990
guard only refuses a non-empty list of blank tracks, so `tracks: []` used to
sail through with success:true and delete every stored track. A brand-new
mirror (or one already empty) may still be saved with zero tracks.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-empty-guard-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 's.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _post(client, pid, tracks, name="Empty Guard"):
    return client.post('/api/mirror-playlist', json={
        'source': 'spotify',
        'source_playlist_id': pid,
        'name': name,
        'tracks': tracks,
    })


def _tracks(client, pl_id):
    body = client.get(f'/api/mirrored-playlists/{pl_id}').get_json()
    return body['tracks']


def test_new_mirror_may_be_empty(client):
    # A genuinely empty upstream playlist is legal to mirror.
    r = _post(client, 'sp-new-empty', [])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True


def test_empty_payload_cannot_wipe_a_nonempty_mirror(client):
    r = _post(client, 'sp-wipe-me', [
        {'track_name': 't1', 'artist_name': 'a', 'source_track_id': '1'},
        {'track_name': 't2', 'artist_name': 'a', 'source_track_id': '2'},
    ])
    assert r.status_code == 200, r.get_json()
    pl_id = r.get_json()['playlist_id']

    r = _post(client, 'sp-wipe-me', [])
    assert r.status_code == 400, r.get_json()
    body = r.get_json()
    # the frontend checks res.success === false strictly, and readJson doesn't
    # throw on a 400 — a bare {"error": ...} would fall through to the success
    # branch and toast "Added to Playlists!".
    assert body['success'] is False
    assert 'empty track list' in body['error']

    # The stored tracks survived the refused write.
    assert len(_tracks(client, pl_id)) == 2


def test_empty_payload_on_already_empty_mirror_is_a_noop(client):
    r = _post(client, 'sp-already-empty', [])
    assert r.status_code == 200, r.get_json()
    r = _post(client, 'sp-already-empty', [])
    assert r.status_code == 200, r.get_json()


def test_nonempty_replacement_still_works(client):
    r = _post(client, 'sp-shrink', [
        {'track_name': 't1', 'artist_name': 'a', 'source_track_id': '1'},
        {'track_name': 't2', 'artist_name': 'a', 'source_track_id': '2'},
    ])
    assert r.status_code == 200, r.get_json()
    pl_id = r.get_json()['playlist_id']

    # A playlist that really shrank to one track still refreshes fine.
    r = _post(client, 'sp-shrink', [
        {'track_name': 't1', 'artist_name': 'a', 'source_track_id': '1'},
    ])
    assert r.status_code == 200, r.get_json()
    assert len(_tracks(client, pl_id)) == 1
