"""flaresolverr.url can be saved, like every other connection setting.

core.flaresolverr read it and every EXT.to / Beatport error said "set
flaresolverr.url", but /api/settings dropped the section without a word
(200, nothing stored) and no settings panel had the field: an install
without a solver at localhost:8191 could not point SoulSync at its own.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-flaresolverr-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'flaresolverr.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_the_solver_url_is_saved_and_used(client):
    from core.flaresolverr import flaresolverr_url
    from core.settings import config_manager
    before = config_manager.get('flaresolverr.url')
    try:
        resp = client.post('/api/settings', json={'flaresolverr': {'url': 'http://solver:8191/'}})
        assert resp.status_code == 200 and resp.get_json()['success']
        assert config_manager.get('flaresolverr.url') == 'http://solver:8191/'
        assert flaresolverr_url() == 'http://solver:8191'
    finally:
        config_manager.set('flaresolverr.url', before)


def test_the_default_is_the_local_solver():
    from core.settings import config_manager
    assert config_manager._get_default_config()['flaresolverr'] == {'url': 'http://localhost:8191'}
