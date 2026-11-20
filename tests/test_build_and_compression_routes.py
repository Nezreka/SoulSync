"""through the real app: the page carries the build it was served with, the
socket says which build the server runs (so an old tab can offer a reload),
and pages, JSON and static files go out compressed while audio does not.

heavy (imports web_server once), so it lives in its own module like the other
route tests.
"""

from __future__ import annotations

import gzip
import os
import re
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-build-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'build_routes.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')
brotli = pytest.importorskip('brotli')


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_the_page_names_its_build(client):
    html = client.get('/').get_data(as_text=True)
    m = re.search(r'<meta name="soulsync-build" content="([^"]*)">', html)
    assert m and m.group(1) == web_server._server_build_id()
    assert m.group(1).startswith(web_server.SOULSYNC_VERSION + '/')


def test_the_socket_says_which_build_the_server_runs():
    ws = web_server.socketio.test_client(web_server.app)
    try:
        assert ws.is_connected()
        got = [e for e in ws.get_received() if e['name'] == 'server:build']
        assert got and got[0]['args'][0] == {'build': web_server._server_build_id()}
    finally:
        ws.disconnect()


def test_the_page_goes_out_compressed(client):
    r = client.get('/', headers={'Accept-Encoding': 'gzip, deflate, br'})
    assert r.headers['Content-Encoding'] == 'br'
    assert 'Accept-Encoding' in r.headers['Vary']
    assert b'soulsync-build' in brotli.decompress(r.get_data())
    r = client.get('/', headers={'Accept-Encoding': 'gzip'})
    assert r.headers['Content-Encoding'] == 'gzip'
    assert b'soulsync-build' in gzip.decompress(r.get_data())


def test_static_js_is_compressed_and_keeps_its_type(client):
    r = client.get('/static/core.js', headers={'Accept-Encoding': 'br'})
    assert r.status_code == 200
    assert r.headers['Content-Encoding'] == 'br'
    assert 'javascript' in r.headers['Content-Type']
    plain = client.get('/static/core.js')
    assert brotli.decompress(r.get_data()) == plain.get_data()
    assert 'Content-Encoding' not in plain.headers


def test_a_range_on_a_static_file_is_the_plain_bytes(client):
    r = client.get('/static/core.js', headers={'Accept-Encoding': 'br', 'Range': 'bytes=0-15'})
    assert r.status_code == 206 and 'Content-Encoding' not in r.headers
    assert len(r.get_data()) == 16


def test_images_are_never_compressed(client):
    r = client.get('/static/favicon.png', headers={'Accept-Encoding': 'br, gzip'})
    assert r.status_code == 200 and 'Content-Encoding' not in r.headers
