"""the admin switches a profile to its own library through PUT /api/profiles/<id> (#1199)."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-ownlib-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'ownlib.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def sam():
    return web_server.get_database().create_profile(name=f'sam_{os.urandom(3).hex()}')


@pytest.mark.skip(reason=(
    "upstream's own-library model: these seed the catalogue through insert_or_update_media_*(owner_profile_id=...) and assert on artists/albums/tracks. this branch retired those tables, keeps ownership on lib2_track_files, and its scan is import-controlled -- it maps a server item onto a row it already has and never creates one. the scenarios are worth keeping verbatim; they get rewritten against lib2 when the read scope lands (docs/library-v2-dir-ownership.md, Stufe 3)"
))
def test_admin_switches_a_profile_to_its_own_library(client, sam, tmp_path):
    root = str(tmp_path / 'sam')
    os.makedirs(root)
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': root})
    assert r.status_code == 200 and r.get_json()['success'], r.data
    assert web_server.get_database().get_profile_library(sam) == {'mode': 'own', 'root': root}
    listed = next(p for p in client.get('/api/profiles').get_json()['profiles'] if p['id'] == sam)
    assert (listed['library_mode'], listed['library_root']) == ('own', root)
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'shared'})
    assert r.get_json()['success'] and web_server.get_database().get_profile_library(sam)['mode'] == 'shared'


def test_an_own_library_without_a_folder_or_on_the_shared_folder_is_refused(client, sam, monkeypatch):
    assert client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': ''}).status_code == 400
    monkeypatch.setattr(web_server.config_manager, 'get', lambda k, d=None: '/music/shared' if k == 'soulseek.transfer_path' else d)
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': '/music/shared/'})
    assert r.status_code == 400 and 'shared' in r.get_json()['error']
    assert web_server.get_database().get_profile_library(sam)['mode'] == 'shared'


def test_the_admin_profile_cannot_be_given_an_own_library(client):
    r = client.put('/api/profiles/1', json={'library_mode': 'own', 'library_root': '/music/x'})
    assert r.status_code == 400


def test_a_non_admin_cannot_switch_their_own_library_mode(client, sam):
    with client.session_transaction() as sess:
        sess['profile_id'] = sam
    r = client.put(f'/api/profiles/{sam}', json={'name': 'Sam', 'library_mode': 'own', 'library_root': '/music/sam'})
    assert r.status_code == 200          # the name save is theirs to make
    assert web_server.get_database().get_profile_library(sam)['mode'] == 'shared', "a non-admin set their own library mode"


@pytest.mark.skip(reason=(
    "parked with the feature: the negative half of this test passes, but its final assertion is that a valid switch SUCCEEDS, and while core.library_scope.SCOPE_PARKED is true the endpoint refuses on purpose rather than half-applying it (docs/library-v2-dir-ownership.md, Stufe 3)"
))
def test_an_own_library_folder_must_exist_and_be_writable(client, sam, tmp_path, monkeypatch):
    import api.user_profiles as up
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': str(tmp_path / 'missing')})
    assert r.status_code == 400 and 'does not exist' in r.get_json()['error']
    good = tmp_path / 'sam'
    good.mkdir()
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': str(good)})
    assert r.status_code == 200 and r.get_json()['success'], r.data
    # in docker the message says how to fix it
    monkeypatch.setattr(up, '_is_docker', lambda: True)
    r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': '/app/libraries/sam'})
    assert r.status_code == 400 and 'docker-compose.yml' in r.get_json()['error']


def test_the_profiles_list_carries_the_docker_root_hint(client, monkeypatch):
    # prefilled on every install; outside docker the admin corrects it
    assert client.get('/api/profiles').get_json()['own_library_root_hint'] == '/app/libraries/<name>'


@pytest.mark.skip(reason=(
    "parked with the feature: the negative half of this test passes, but its final assertion is that a valid switch SUCCEEDS, and while core.library_scope.SCOPE_PARKED is true the endpoint refuses on purpose rather than half-applying it (docs/library-v2-dir-ownership.md, Stufe 3)"
))
def test_a_folder_overlapping_the_shared_one_or_another_profiles_is_refused(client, sam, tmp_path, monkeypatch):
    shared = tmp_path / 'shared'; shared.mkdir()
    inside = shared / 'sam'; inside.mkdir()
    above = tmp_path                                  # holds the shared folder
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: str(shared) if k == 'soulseek.transfer_path' else d)
    for bad in (inside, above):
        r = client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': str(bad)})
        assert r.status_code == 400 and 'overlaps' in r.get_json()['error'], (bad, r.data)
    own = tmp_path / 'own'; own.mkdir()
    assert client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': str(own)}).status_code == 200
    # another profile cannot take sam's folder, or a folder inside it
    db = web_server.get_database()
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    nested = own / 'deeper'; nested.mkdir()
    for bad in (own, nested):
        r = client.put(f'/api/profiles/{kim}', json={'library_mode': 'own', 'library_root': str(bad)})
        assert r.status_code == 400 and 'library' in r.get_json()['error'], (bad, r.data)
    assert db.get_profile_library(kim)['mode'] == 'shared'
    # sam re-saving their own folder is fine
    assert client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': str(own)}).status_code == 200


@pytest.mark.skip(reason=(
    "upstream's own-library model: these seed the catalogue through insert_or_update_media_*(owner_profile_id=...) and assert on artists/albums/tracks. this branch retired those tables, keeps ownership on lib2_track_files, and its scan is import-controlled -- it maps a server item onto a row it already has and never creates one. the scenarios are worth keeping verbatim; they get rewritten against lib2 when the read scope lands (docs/library-v2-dir-ownership.md, Stufe 3)"
))
def test_a_profiles_own_rows_go_when_it_is_shared_again_or_deleted(client, sam, tmp_path):
    from types import SimpleNamespace
    db = web_server.get_database()
    own = tmp_path / 'own'; own.mkdir()
    assert client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': str(own)}).status_code == 200

    def seed(owner, prefix):
        db.insert_or_update_media_artist(SimpleNamespace(ratingKey=f'{prefix}-ar', title=f'Artist {prefix}', thumb=None, genres=[], summary=''),
                                         server_source='plex', owner_profile_id=owner)
        db.insert_or_update_media_album(SimpleNamespace(ratingKey=f'{prefix}-al', title=f'Album {prefix}', year=2020, thumb=None, genres=[]),
                                        f'{prefix}-ar', server_source='plex', owner_profile_id=owner)
        t = SimpleNamespace(ratingKey=f'{prefix}-t', title='Song', trackNumber=1, duration=1000, parentIndex=1,
                            media=[SimpleNamespace(parts=[SimpleNamespace(file=f'/m/{prefix}.flac', size=1)], bitrate=1)])
        db.insert_or_update_media_track(t, f'{prefix}-al', f'{prefix}-ar', server_source='plex', owner_profile_id=owner)

    def owners():
        with db._get_connection() as conn:
            return {t: sorted((str(r[0]) for r in conn.execute(
                        f"SELECT owner_profile_id FROM {t} WHERE id LIKE ? OR id LIKE ?", (f'own{sam}-%', f'sh{sam}-%')).fetchall()))
                    for t in ('tracks', 'albums', 'artists')}
    seed(sam, f'own{sam}'); seed(None, f'sh{sam}')
    assert owners() == {t: sorted(['None', str(sam)]) for t in ('tracks', 'albums', 'artists')}
    # back to shared: sam's rows go, the shared ones stay
    assert client.put(f'/api/profiles/{sam}', json={'library_mode': 'shared'}).status_code == 200
    assert owners() == {t: ['None'] for t in ('tracks', 'albums', 'artists')}
    # and deleting an own-library profile takes its rows with it
    assert client.put(f'/api/profiles/{sam}', json={'library_mode': 'own', 'library_root': str(own)}).status_code == 200
    seed(sam, f'own{sam}')
    assert owners()['tracks'] == sorted(['None', str(sam)])
    assert client.delete(f'/api/profiles/{sam}').status_code == 200
    assert owners() == {t: ['None'] for t in ('tracks', 'albums', 'artists')}
