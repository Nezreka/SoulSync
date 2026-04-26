"""Tests for the SPA deep-linking catch-all route.

The real web_server.py cannot be imported at test time (it initializes
Spotify, Soulseek, Plex, etc.), so we replicate the routing behavior in a
minimal Flask app that matches the real implementation verbatim.
"""

import pytest
from flask import Flask, abort


# ---------------------------------------------------------------------------
# App factory — mirrors the routes added in web_server.py
# ---------------------------------------------------------------------------

def _build_app():
    app = Flask(__name__)
    app.testing = True

    @app.route('/')
    def index():
        return 'INDEX_HTML', 200

    @app.route('/<path:page>')
    def spa_catch_all(page):
        if page.startswith(('api/', 'static/', 'auth/', 'callback', 'deezer/', 'tidal/', 'status')):
            abort(404)
        return 'INDEX_HTML', 200

    # Stand-ins for real routes so we can verify the catch-all does not shadow them
    @app.route('/api/ping')
    def api_ping():
        return {'ok': True}, 200

    @app.route('/auth/spotify')
    def auth_spotify():
        return 'AUTH_SPOTIFY', 200

    @app.route('/callback')
    def oauth_callback():
        return 'OAUTH_CALLBACK', 200

    @app.route('/tidal/callback')
    def tidal_callback():
        return 'TIDAL_CALLBACK', 200

    @app.route('/deezer/callback')
    def deezer_callback():
        return 'DEEZER_CALLBACK', 200

    @app.route('/status')
    def status():
        return 'STATUS', 200

    return app


@pytest.fixture
def client():
    return _build_app().test_client()


# ---------------------------------------------------------------------------
# Group A — SPA routes serve index.html
# ---------------------------------------------------------------------------

class TestSpaRoutes:
    """Deep-link paths for valid client pages should serve index.html."""

    @pytest.mark.parametrize("page", [
        'dashboard', 'sync', 'search', 'downloads', 'discover', 'artists',
        'automations', 'library', 'import', 'settings', 'help',
        'issues', 'stats', 'watchlist', 'wishlist', 'active-downloads',
        'artist-detail', 'playlist-explorer', 'hydrabase', 'tools',
    ])
    def test_valid_page_serves_index(self, client, page):
        resp = client.get(f'/{page}')
        assert resp.status_code == 200
        assert resp.data == b'INDEX_HTML'

    def test_root_still_serves_index(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert resp.data == b'INDEX_HTML'

    def test_unknown_page_still_serves_index(self, client):
        # Standard SPA behavior — unknown paths fall through to the client router.
        resp = client.get('/this-page-does-not-exist')
        assert resp.status_code == 200
        assert resp.data == b'INDEX_HTML'

    def test_nested_sub_path_serves_index(self, client):
        # Future-proofing: /artists/Linkin%20Park-style deep links.
        resp = client.get('/artists/Linkin%20Park')
        assert resp.status_code == 200
        assert resp.data == b'INDEX_HTML'


# ---------------------------------------------------------------------------
# Group B — Reserved prefixes are not shadowed
# ---------------------------------------------------------------------------

class TestReservedPrefixes:
    """The catch-all must never swallow real API / auth / static routes."""

    def test_real_api_route_wins(self, client):
        resp = client.get('/api/ping')
        assert resp.status_code == 200
        assert resp.get_json() == {'ok': True}

    def test_unknown_api_path_returns_404(self, client):
        # Catch-all's abort(404) prevents /api/* from being answered with index.html.
        resp = client.get('/api/not-a-real-endpoint')
        assert resp.status_code == 404
        assert b'INDEX_HTML' not in resp.data

    def test_unknown_static_path_returns_404(self, client):
        resp = client.get('/static/does-not-exist.js')
        assert resp.status_code == 404
        assert b'INDEX_HTML' not in resp.data

    def test_unknown_auth_path_returns_404(self, client):
        resp = client.get('/auth/unknown-provider')
        assert resp.status_code == 404
        assert b'INDEX_HTML' not in resp.data

    def test_real_auth_route_wins(self, client):
        resp = client.get('/auth/spotify')
        assert resp.status_code == 200
        assert resp.data == b'AUTH_SPOTIFY'

    def test_real_callback_route_wins(self, client):
        resp = client.get('/callback')
        assert resp.status_code == 200
        assert resp.data == b'OAUTH_CALLBACK'

    def test_callback_prefix_without_real_route_returns_404(self, client):
        # Anything starting with 'callback' is reserved even if Flask has no match.
        resp = client.get('/callback-fake')
        assert resp.status_code == 404
        assert b'INDEX_HTML' not in resp.data

    def test_real_tidal_callback_wins(self, client):
        resp = client.get('/tidal/callback')
        assert resp.status_code == 200
        assert resp.data == b'TIDAL_CALLBACK'

    def test_unknown_tidal_path_returns_404(self, client):
        resp = client.get('/tidal/other')
        assert resp.status_code == 404
        assert b'INDEX_HTML' not in resp.data

    def test_real_deezer_callback_wins(self, client):
        resp = client.get('/deezer/callback')
        assert resp.status_code == 200
        assert resp.data == b'DEEZER_CALLBACK'

    def test_unknown_deezer_path_returns_404(self, client):
        resp = client.get('/deezer/other')
        assert resp.status_code == 404
        assert b'INDEX_HTML' not in resp.data

    def test_real_status_route_wins(self, client):
        resp = client.get('/status')
        assert resp.status_code == 200
        assert resp.data == b'STATUS'

    def test_status_prefix_without_real_route_returns_404(self, client):
        resp = client.get('/status-extra')
        assert resp.status_code == 404
        assert b'INDEX_HTML' not in resp.data


# ---------------------------------------------------------------------------
# Group C — HTTP method restrictions
# ---------------------------------------------------------------------------

class TestHttpMethods:
    """Catch-all should only respond to GET (Flask default)."""

    def test_post_to_spa_path_not_allowed(self, client):
        resp = client.post('/discover')
        assert resp.status_code == 405

    def test_put_to_spa_path_not_allowed(self, client):
        resp = client.put('/discover')
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Group D — Query string and fragment handling
# ---------------------------------------------------------------------------

class TestQueryStrings:
    """Query strings must not affect routing decisions."""

    def test_spa_path_with_query_string(self, client):
        resp = client.get('/discover?q=linkin+park')
        assert resp.status_code == 200
        assert resp.data == b'INDEX_HTML'

    def test_setup_wizard_query_preserved_on_root(self, client):
        resp = client.get('/?setup=1')
        assert resp.status_code == 200
        assert resp.data == b'INDEX_HTML'
