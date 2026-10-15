"""the discover warmer warmed nothing on a multi-profile install.

it asked anonymously through a test client. since no-profile = no rights, a
request with no profile on an install with two profiles is a 401, so every
warm request bounced in a few ms, the log said "sweep finished in 0.3s", and
every visit to discover paid the full cold compute (~20s). it now signs each
profile in and warms as them.

the suite's test clients start as the admin (conftest), which is exactly what
hid this, so these use a plain anonymous client like the warmer's own.
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

PATH = '/api/discover/decades/available'   # a shelf-cached route


@pytest.fixture()
def ws(monkeypatch):
    import web_server
    import api.discover_routes as routes

    monkeypatch.setattr(web_server.app, 'test_client_class', FlaskClient)
    db = web_server.get_database()
    if len(db.get_all_profiles()) < 2:
        db.create_profile('Warm Second')
    routes.invalidate_discover_shelf_cache()
    yield web_server
    routes.invalidate_discover_shelf_cache()


def test_an_anonymous_request_is_refused_on_a_multi_profile_install(ws):
    """the precondition the old warmer ran into."""
    with ws.app.test_client() as client:
        assert client.get(PATH).status_code == 401


def test_the_sweep_warms_every_profile(ws):
    import api.discover_routes as routes

    profiles = [p['id'] for p in ws.get_database().get_all_profiles() if not p.get('disabled')]
    stats = ws._discover_warm_sweep(paths=[PATH])
    assert stats['failed'] == []
    assert stats['ok'] + stats['skipped'] == len(profiles)
    warmed = {key[2] for key in routes._DISCOVER_SHELF_CACHE if key[0] == 'get_available_decades'}
    assert 1 in warmed and len(warmed) == stats['ok']


def test_a_refused_sweep_is_reported_not_silent(ws, monkeypatch):
    # a profile whose sign-out epoch moved on cannot be warmed: it must show up
    # as failed, because a sweep that warms nothing looks just like a fast one
    import core.security.session_epoch as se
    se._reset_for_tests()
    monkeypatch.setattr(se, '_epoch', lambda pid, load: 5 if pid == 1 else 0)
    stats = ws._discover_warm_sweep(paths=[PATH])
    assert (1, PATH, 401) in stats['failed']


# ---------------------------------------------------------------------------
# every other sweep refreshed nothing. the warmer runs every 25 min, the shelf
# cache lasts 30, so a sweep that read the cache like a user found everything
# still valid. the answers then expired at 30 and sat cold until ~50, and the
# live log showed sweeps alternating 90 s / 2.5 s. sweeps recompute now.
# ---------------------------------------------------------------------------

def _plant_sentinel(routes, func_name):
    keys = [k for k in routes._DISCOVER_SHELF_CACHE if k[0] == func_name and k[2] == 1]
    assert keys, "the first sweep cached nothing to refresh"
    key = keys[0]
    expiry, (_body, status, ctype) = routes._DISCOVER_SHELF_CACHE[key]
    routes._DISCOVER_SHELF_CACHE[key] = (expiry, (b'{"sentinel": true}', status, ctype))
    return key


def test_a_sweep_recomputes_a_still_valid_answer(ws):
    import api.discover_routes as routes

    ws._discover_warm_sweep(paths=[PATH])
    key = _plant_sentinel(routes, 'get_available_decades')
    ws._discover_warm_sweep(paths=[PATH])
    assert b'sentinel' not in routes._DISCOVER_SHELF_CACHE[key][1][0]


def test_a_user_request_still_reads_the_cache(ws):
    import api.discover_routes as routes

    ws._discover_warm_sweep(paths=[PATH])
    _plant_sentinel(routes, 'get_available_decades')
    with ws.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['profile_id'] = 1
            sess['profile_epoch'] = int((ws.get_database().get_profile(1) or {}).get('session_epoch') or 0)
            sess['login_authenticated'] = True
            sess['launch_pin_verified'] = True
        resp = client.get(PATH)
    assert resp.status_code == 200
    assert resp.get_json() == {'sentinel': True}


def test_the_refresh_flag_is_off_outside_a_sweep():
    from core.discovery.warm_refresh import refresh, refreshing

    assert refreshing() is False
    with refresh():
        assert refreshing() is True
        with refresh():
            assert refreshing() is True
        assert refreshing() is True
    assert refreshing() is False


def test_the_refresh_flag_never_leaks_to_another_thread():
    import threading

    from core.discovery.warm_refresh import refresh, refreshing

    seen = []
    with refresh():
        t = threading.Thread(target=lambda: seen.append(refreshing()))
        t.start()
        t.join()
    assert seen == [False]


def test_the_hero_cache_read_honours_the_refresh_flag():
    import inspect

    from core.discovery import hero

    src = inspect.getsource(hero.get_discover_hero)
    assert "if _cached and _time.time() < _cached[0] and not refreshing():" in src
