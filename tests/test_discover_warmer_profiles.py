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
