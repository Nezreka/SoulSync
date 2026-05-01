"""Regression tests for Tidal auth instruction page port rendering.

Discord-reported bug: the auth-instructions page shown after clicking
the Tidal "Authenticate" button rendered example callback URLs with
port ``8888`` (Spotify's port) instead of ``8889`` (Tidal's port).
Users who followed the instructions literally saved Spotify's port
into their ``tidal.redirect_uri`` setting; that mismatched their
Tidal Developer App's registered ``:8889`` redirect URI and Tidal
returned error 1002 (invalid redirect URI) on every auth attempt.

These tests make sure the rendered instructions show whatever port
the OAuth URL itself was built with, so the displayed example always
matches what the user must register in their Tidal app.
"""

from typing import Callable
from unittest.mock import MagicMock, patch

import pytest


# Run the route through Flask's test client so we get the real HTML
# the user would see. We patch out:
#   - TidalClient (the real client tries to connect to Tidal),
#   - the activity-feed call (writes to runtime state),
#   - request.host detection (so the Docker code path is exercised
#     and the instructions page is the one with the example URL).
@pytest.fixture
def auth_route_client(monkeypatch: pytest.MonkeyPatch):
    """Return a Flask test client wired up enough to render the
    Tidal auth-instructions page."""
    # Force the "remote/docker" branch by faking a remote-host request.
    # Easier than mocking is_docker; the route only needs ONE of the
    # two flags to render the instructions page.
    monkeypatch.setattr(
        "os.path.exists",
        lambda p: p == "/.dockerenv" or False,
    )

    fake_client = MagicMock()
    fake_client.client_id = "fake-id"
    fake_client.code_verifier = "v" * 40
    fake_client.code_challenge = "c" * 40
    fake_client.auth_url = "https://login.tidal.com/authorize"

    def _set_redirect_uri(value):
        fake_client.redirect_uri = value
    fake_client._generate_pkce_challenge = MagicMock()

    with patch("core.tidal_client.TidalClient", return_value=fake_client):
        with patch("web_server.add_activity_item"):
            from web_server import app as flask_app
            flask_app.config['TESTING'] = True
            yield flask_app.test_client(), fake_client


def _extract_html(response) -> str:
    return response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_instructions_show_tidal_port_not_spotify_port_when_config_uses_8889(
    auth_route_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported scenario: tidal.redirect_uri config carries port
    8889, the rendered instructions must show 8889 (not Spotify's
    8888) in both the Step 2 example and the Step 3 highlighted URL."""
    client, fake_client = auth_route_client

    fake_client.redirect_uri = "http://127.0.0.1:8889/tidal/callback"

    from config.settings import config_manager
    monkeypatch.setattr(
        config_manager, "get",
        lambda key, default=None: (
            "http://127.0.0.1:8889/tidal/callback"
            if key == "tidal.redirect_uri" else default
        ),
    )

    response = client.get("/auth/tidal", base_url="http://192.168.1.50:8008")
    html = _extract_html(response)

    # Both example URLs in the instructions must use Tidal's port.
    assert ":8889/tidal/callback" in html, (
        "Step 2/3 example URLs must reflect the configured Tidal port"
    )
    assert ":8888/tidal/callback" not in html, (
        "Spotify's port must not appear in Tidal auth instructions"
    )


def test_instructions_respect_custom_callback_port_from_env(
    auth_route_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SOULSYNC_TIDAL_CALLBACK_PORT env var changes which port the
    Tidal callback server binds to; the instructions must reflect
    that custom port too, not assume the 8889 default."""
    client, fake_client = auth_route_client

    fake_client.redirect_uri = "http://127.0.0.1:9999/tidal/callback"

    from config.settings import config_manager
    monkeypatch.setattr(
        config_manager, "get",
        lambda key, default=None: (
            "http://127.0.0.1:9999/tidal/callback"
            if key == "tidal.redirect_uri" else default
        ),
    )
    monkeypatch.setenv("SOULSYNC_TIDAL_CALLBACK_PORT", "9999")

    response = client.get("/auth/tidal", base_url="http://192.168.1.50:8008")
    html = _extract_html(response)

    assert ":9999/tidal/callback" in html


def test_instructions_fall_back_to_default_port_when_redirect_uri_is_unparseable(
    auth_route_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: if redirect_uri somehow has no port (corrupted
    config, schemeless string, etc.), the instructions fall back to
    the default Tidal port from the env var instead of crashing or
    showing the Spotify port."""
    client, fake_client = auth_route_client

    fake_client.redirect_uri = "not-a-valid-url"

    from config.settings import config_manager
    monkeypatch.setattr(
        config_manager, "get",
        lambda key, default=None: (
            "not-a-valid-url" if key == "tidal.redirect_uri" else default
        ),
    )

    response = client.get("/auth/tidal", base_url="http://192.168.1.50:8008")
    html = _extract_html(response)

    # Falls back to Tidal default 8889, never to Spotify's 8888.
    assert ":8889/tidal/callback" in html
    assert ":8888/tidal/callback" not in html
