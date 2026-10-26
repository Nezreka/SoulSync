"""#1868: the settings page's template preview, through the real app.

the rendering is covered in tests/imports/test_path_preview_1868.py. this only
proves the route is wired: the form's templates go in, example paths come out,
and a broken body is a clean answer rather than a 500.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-preview-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'preview.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_form_templates_come_back_as_example_paths(client):
    r = client.post('/api/settings/path-preview', json={
        'scenario': 'multidisc',
        'templates': {'album_path': '$albumartist/$album/$track - $title'},
        'settings': {'disc_label': 'CD', 'auto_disambiguation': False},
    })
    body = r.get_json()
    assert r.status_code == 200 and body['success']
    assert body['scenario'] == 'multidisc'
    assert body['paths']['album_path'] == 'The Beatles/The Beatles/CD 2/01 - Birthday.flac'


def test_a_body_that_isnt_json_still_previews_the_defaults(client):
    r = client.post('/api/settings/path-preview', data='nope', content_type='text/plain')
    body = r.get_json()
    assert r.status_code == 200 and body['success'] and body['scenario'] == 'standard'
    assert body['paths']['album_path'].endswith('08 - Get Lucky.flac')
