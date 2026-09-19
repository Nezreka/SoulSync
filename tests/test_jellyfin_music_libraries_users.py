"""/api/jellyfin/music-libraries carries the users a profile can sync as.

personal settings has rendered a jellyfin user dropdown keyed on `users`
since it was built, and the endpoint never sent that field, so no profile
could ever pick a jellyfin user (#1265)."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-jfusers-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'jfusers.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


class _FakeJellyfin:
    music_library_id = 'lib-1'

    def get_available_music_libraries(self):
        return [{'key': 'lib-1', 'title': 'Music'}]

    def get_available_users(self):
        return [{'id': 'u-kid', 'name': 'Kid'}, {'id': 'u-annie', 'name': 'Annie'}, {'id': '', 'name': 'ghost'}]


@pytest.fixture
def jellyfin(monkeypatch):
    fake = _FakeJellyfin()
    monkeypatch.setattr(web_server.media_server_engine, 'client', lambda name: fake if name == 'jellyfin' else None)
    return fake


def test_users_are_listed_for_the_profile_picker(jellyfin):
    body = web_server.app.test_client().get('/api/jellyfin/music-libraries').get_json()
    assert body['success']
    assert body['users'] == [{'id': 'u-kid', 'name': 'Kid'}, {'id': 'u-annie', 'name': 'Annie'}]
    assert body['current'] == 'Music'


def test_a_failing_user_list_does_not_take_the_libraries_down(jellyfin, monkeypatch):
    def boom():
        raise RuntimeError('jellyfin 500')
    monkeypatch.setattr(jellyfin, 'get_available_users', boom)
    body = web_server.app.test_client().get('/api/jellyfin/music-libraries').get_json()
    assert body['success'] and body['users'] == [] and body['libraries']
