"""#2156: the page shell renders the signed-in profile in, so the sidebar is
cut down before first paint. these run GET / through the real app."""

from __future__ import annotations

import json
import os
import re
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-bootprof-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'bootprof.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

_BOOT = re.compile(r'window\.SOULSYNC_BOOT_PROFILE = (.*?);\n')


def _boot_profile(client):
    html = client.get('/').get_data(as_text=True)
    m = _BOOT.search(html)
    assert m, 'the page no longer renders the boot profile'
    return json.loads(m.group(1))


def _client(**session):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s.update(session)
    return c


@pytest.fixture
def video_member():
    db = web_server.get_database()
    pid = db.create_profile(name=f'boot_{uuid4().hex[:8]}', can_download=False,
                            allowed_pages=['video-search', 'video-discover', 'video-library'],
                            allowed_sides='video')
    yield pid
    db.delete_profile(pid)


def test_member_page_carries_their_pages(video_member):
    """#2156: a member's page knows their pages before any fetch"""
    got = _boot_profile(_client(profile_id=video_member))
    assert got['id'] == video_member
    assert got['is_admin'] is False
    assert got['allowed_sides'] == 'video'
    assert got['allowed_pages'] == ['video-search', 'video-discover', 'video-library']
    assert got['can_download'] is False


def test_picker_page_knows_nobody(video_member):
    """#2156: with 2+ profiles and none picked the page renders null, and the
    nav waits hidden instead of showing the admin's"""
    assert _boot_profile(_client()) is None


def test_signed_out_in_login_mode_knows_nobody(video_member, monkeypatch):
    """#2156: login mode without a sign-in renders null, even with a stale id"""
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: True if k == 'security.require_login' else real_get(k, d))
    assert _boot_profile(_client(profile_id=video_member)) is None
