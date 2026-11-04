"""The Library v2 mirror and upstream's music requests (#requests).

A profile without download rights keeps a wishlist of requests the scheduled
run leaves alone until an admin approves them. The lib2 mirror writes into
such a profile's wishlist only for the admin (that profile cannot monitor,
E-13), so its rows land approved and never count against the request quota.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-mirrorreq-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'mirrorreq.db'))
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.library2 import mirror_outbox as MO  # noqa: E402


def _payload(tid, album_id):
    return {'id': tid, 'name': f'song {tid}', 'artists': [{'name': 'Radiohead'}],
            'album': {'id': album_id, 'name': f'Album {album_id}', 'album_type': 'album'}}


@pytest.fixture
def asker():
    """can't download, one ask per week, and that ask already made."""
    db = web_server.get_database()
    pid = db.create_profile(name=f'asker_{uuid4().hex[:8]}', can_download=False)
    db.update_profile(pid, request_limit=1, request_limit_days=7)
    tag = uuid4().hex[:6]
    db.add_to_wishlist(spotify_track_data=_payload(f'{tag}own', f'own{tag}'), source_type='album',
                       profile_id=pid, user_initiated=True)
    yield db, pid, tag
    db.clear_wishlist(profile_id=pid)
    db.delete_profile(pid)


def test_the_members_own_add_past_the_quota_is_refused(asker):
    db, pid, tag = asker
    assert db.add_to_wishlist(spotify_track_data=_payload(f'{tag}x', f'x{tag}'), source_type='album',
                              profile_id=pid, user_initiated=True) is False


def test_a_mirrored_add_lands_approved_and_ignores_the_quota(asker):
    db, pid, tag = asker
    MO._execute_op(db, 'wishlist_add', {'payload': _payload(f'{tag}m', f'm{tag}'), 'source_type': 'album'},
                   pid, user_initiated=False)
    approved = {t['spotify_track_id'] for t in db.get_wishlist_tracks(profile_id=pid, approved_only=True)}
    assert approved == {f'{tag}m::m{tag}'}
    # the member's own ask is still waiting for the admin
    assert [r['spotify_track_id'] for r in db.get_pending_request_rows(pid)] == [f'{tag}own::own{tag}']


def test_a_profile_that_downloads_gets_an_ordinary_row():
    db = web_server.get_database()
    pid = db.create_profile(name=f'dl_{uuid4().hex[:8]}')
    tag = uuid4().hex[:6]
    try:
        MO._execute_op(db, 'wishlist_add', {'payload': _payload(f'{tag}d', f'd{tag}'), 'source_type': 'album'},
                       pid, user_initiated=False)
        assert db.wishlist_has_track(pid, f'{tag}d::d{tag}')
        assert db.get_wishlist_tracks(profile_id=pid, approved_only=True) == []
    finally:
        db.clear_wishlist(profile_id=pid)
        db.delete_profile(pid)
