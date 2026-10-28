"""the device list follows sign-outs.

signing a profile out everywhere (or a new password, a password reset,
turning the profile off) ended every other session but left each browser on
the profile's device list, with its old "last seen". a browser that moved
to another profile left its old row there too.
"""

from uuid import uuid4

import pytest

web_server = pytest.importorskip('web_server')

_GATED = '/api/profiles/me/connections'
_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0) Chrome/120'}


@pytest.fixture(autouse=True)
def _fresh_caches():
    from core.security import devices, session_epoch
    devices._reset_for_tests()
    session_epoch._reset_for_tests()
    yield
    devices._reset_for_tests()
    session_epoch._reset_for_tests()


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


def _signed_in(pid):
    """a browser that picked the card itself, so it has a device row"""
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    assert c.post('/api/profiles/select', json={'profile_id': pid}, headers=_UA).status_code == 200
    return c


def _device_of(client):
    with client.session_transaction() as s:
        return s.get('device_id')


def _listed(pid):
    return {d['id'] for d in _as(1).get(f'/api/profiles/{pid}/devices').get_json()['devices']}


@pytest.fixture
def member():
    db = web_server.get_database()
    pid = db.create_profile(name=f'd_{uuid4().hex[:8]}')
    yield pid
    db.delete_profile(pid)


def test_sign_out_everywhere_on_yourself_leaves_only_this_browser(member):
    here, phone = _signed_in(member), _signed_in(member)
    assert len(_listed(member)) == 2
    assert here.post(f'/api/profiles/{member}/sign-out-everywhere').get_json()['success']
    assert _listed(member) == {_device_of(here)}
    assert phone.get(_GATED).status_code == 401
    assert here.get(_GATED).status_code == 200


def test_signing_back_in_after_it_adds_one_row_not_two(member):
    here, _phone = _signed_in(member), _signed_in(member)
    here.post(f'/api/profiles/{member}/sign-out-everywhere')
    again = _signed_in(member)
    assert _listed(member) == {_device_of(here), _device_of(again)}


def test_an_admin_signing_someone_out_empties_their_list(member):
    _signed_in(member), _signed_in(member)
    assert _as(1).post(f'/api/profiles/{member}/sign-out-everywhere').get_json()['success']
    assert _listed(member) == set()


def test_only_that_profile_s_rows_go(member):
    db = web_server.get_database()
    other = db.create_profile(name=f'o_{uuid4().hex[:8]}')
    try:
        _signed_in(member)
        theirs = _signed_in(other)
        _as(1).post(f'/api/profiles/{member}/sign-out-everywhere')
        assert _listed(other) == {_device_of(theirs)}
    finally:
        db.delete_profile(other)


def test_a_new_password_leaves_only_the_browser_that_set_it(member):
    here, elsewhere = _signed_in(member), _signed_in(member)
    assert here.post(f'/api/profiles/{member}/set-password', json={'password': 'newpass1'}).get_json()['success']
    assert _listed(member) == {_device_of(here)}
    assert elsewhere.get(_GATED).status_code == 401
    assert here.get(_GATED).status_code == 200


def test_an_admin_setting_a_password_empties_the_list(member):
    _signed_in(member)
    assert _as(1).post(f'/api/profiles/{member}/set-password', json={'password': 'newpass1'}).get_json()['success']
    assert _listed(member) == set()


def test_a_password_reset_leaves_only_the_browser_that_reset_it(member):
    db = web_server.get_database()
    name = db.get_profile(member)['name']
    db.set_profile_password(member, 'oldpassword')
    db.set_profile_recovery(member, 'First pet?', 'Rex')
    web_server._login_limiter.reset()
    _signed_in(member)
    resetter = web_server.app.test_client()
    r = resetter.post('/api/auth/recovery-reset',
                      json={'username': name, 'answer': 'rex', 'new_password': 'brandnew1'}, headers=_UA)
    assert r.status_code == 200 and r.get_json()['success']
    assert _listed(member) == {_device_of(resetter)}


def test_turning_a_profile_off_empties_its_list(member):
    _signed_in(member)
    assert _as(1).put(f'/api/profiles/{member}', json={'disabled': True}).get_json()['success']
    assert _listed(member) == set()


def test_a_browser_that_moves_to_another_profile_leaves_the_first_list(member):
    db = web_server.get_database()
    other = db.create_profile(name=f'o_{uuid4().hex[:8]}')
    try:
        c = _signed_in(member)
        first = _device_of(c)
        assert c.post('/api/profiles/select', json={'profile_id': other}, headers=_UA).status_code == 200
        assert first not in _listed(member)
        assert _listed(other) == {_device_of(c)}
        assert c.get(_GATED).status_code == 200
    finally:
        db.delete_profile(other)


def test_picking_your_own_card_again_keeps_your_row(member):
    c = _signed_in(member)
    mine = _device_of(c)
    assert c.post('/api/profiles/select', json={'profile_id': member}, headers=_UA).status_code == 200
    assert _listed(member) == {mine}


# -- the one-time cleanup of rows left by earlier sign-outs --

def _age(device_id, days):
    db = web_server.get_database()
    with db._get_connection() as conn:
        conn.execute("UPDATE profile_devices SET last_seen = datetime('now', ?) WHERE id = ?",
                     (f'-{days} days', device_id))
        conn.commit()


def _run_cleanup(db, *, again=False):
    with db._get_connection() as conn:
        if not again:
            conn.execute("DELETE FROM metadata WHERE key = 'device_rows_stale_v1'")
        cur = conn.cursor()
        db._revoke_stale_device_rows(cur)
        conn.commit()


def test_the_upgrade_takes_off_rows_not_used_for_a_week_once(member):
    db = web_server.get_database()
    old, recent = f'old-{uuid4().hex}', f'recent-{uuid4().hex}'
    assert db.add_profile_device(old, member, 'Browser', '')
    assert db.add_profile_device(recent, member, 'Browser', '')
    _age(old, 8)
    _age(recent, 6)
    _run_cleanup(db)
    assert _listed(member) == {recent}
    later = f'later-{uuid4().hex}'
    assert db.add_profile_device(later, member, 'Browser', '')
    _age(later, 30)
    _run_cleanup(db, again=True)   # once only: a restart takes nothing else off
    assert _listed(member) == {recent, later}
    with db._get_connection() as conn:
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = 'device_rows_stale_v1'").fetchone()


def test_the_oidc_lifetime_upgrade_takes_its_signed_out_browsers_off(member):
    db = web_server.get_database()
    plain = db.create_profile(name=f'p_{uuid4().hex[:8]}')
    try:
        db.set_profile_oidc_identity(member, f'https://idp.example|{uuid4().hex}')
        linked_row, plain_row = f'l-{uuid4().hex}', f'p-{uuid4().hex}'
        assert db.add_profile_device(linked_row, member, 'Browser', '')
        assert db.add_profile_device(plain_row, plain, 'Browser', '')
        with db._get_connection() as conn:
            conn.execute("DELETE FROM metadata WHERE key = 'oidc_session_lifetime_v1'")
            cur = conn.cursor()
            db._add_device_oidc_session(cur)
            conn.commit()
        assert _listed(member) == set()
        assert _listed(plain) == {plain_row}
    finally:
        db.delete_profile(plain)
