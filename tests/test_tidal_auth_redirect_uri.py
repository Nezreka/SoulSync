"""Regression tests for Tidal /auth/tidal redirect_uri selection.

Discord-reported (Foxxify): Tidal returned error 1002 ("Invalid
redirect URI") on every authentication attempt. The user had
``http://127.0.0.1:8889/tidal/callback`` registered in his Tidal
Developer Portal (matching the SoulSync UI default + docs), but
SoulSync was sending a network-IP-derived URI like
``http://192.168.x.x:8889/tidal/callback`` because the empty-config
fallback in /auth/tidal built the URI from ``request.host``. Tidal
compares strings exactly, so the URIs didn't match and authentication
failed before the user could even see Tidal's consent screen.

These tests pin:
1. When ``tidal.redirect_uri`` is configured, that value is sent to
   Tidal verbatim.
2. When the config is empty, SoulSync uses the constructor default
   (``http://127.0.0.1:<port>/tidal/callback``) — NOT a value built
   from request.host.
3. Both cases work whether the user is accessing SoulSync via
   localhost or a network IP (the access path is independent from the
   authorize redirect_uri).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest


@pytest.fixture
def auth_route_client(monkeypatch: pytest.MonkeyPatch):
    """Flask test client wired to render the Tidal auth flow without
    spawning a real TidalClient or hitting external services."""
    # Force the "remote/docker" branch (route only renders the
    # instructions page when one of those flags is true).
    monkeypatch.setattr(
        "os.path.exists",
        lambda p: p == "/.dockerenv" or False,
    )

    fake_client = MagicMock()
    fake_client.client_id = "fake-id"
    fake_client.code_verifier = "v" * 40
    fake_client.code_challenge = "c" * 40
    fake_client.auth_url = "https://login.tidal.com/authorize"
    # Constructor default that mirrors core/tidal_client.py:124.
    fake_client.redirect_uri = "http://127.0.0.1:8889/tidal/callback"
    fake_client._generate_pkce_challenge = MagicMock()

    with patch("core.tidal_client.TidalClient", return_value=fake_client):
        with patch("web_server.add_activity_item"):
            from web_server import app as flask_app
            flask_app.config['TESTING'] = True
            yield flask_app.test_client(), fake_client


def _extract_authorize_url(html: str) -> str | None:
    """Pull the Tidal authorize URL out of the rendered instructions page."""
    import re
    m = re.search(r'href="(https://login\.tidal\.com/authorize\?[^"]+)"', html)
    return m.group(1) if m else None


def _extract_redirect_uri(html: str) -> str | None:
    auth_url = _extract_authorize_url(html)
    if not auth_url:
        return None
    parsed = urlparse(auth_url)
    qs = parse_qs(parsed.query)
    val = qs.get('redirect_uri', [None])[0]
    return val


# ---------------------------------------------------------------------------
# Configured redirect_uri honored verbatim
# ---------------------------------------------------------------------------


class TestConfiguredRedirectUriIsHonored:
    def test_localhost_config_sent_when_user_accesses_via_network_ip(
        self, auth_route_client, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The reported Foxxify scenario: user has 127.0.0.1:8889
        registered in Tidal portal AND set in SoulSync config, accesses
        the Web UI from his network IP. The authorize URL must contain
        the configured 127.0.0.1 URI, NOT a value built from
        request.host (which would mismatch the portal and yield
        Tidal error 1002)."""
        client, _fake = auth_route_client

        from config.settings import config_manager
        monkeypatch.setattr(
            config_manager, "get",
            lambda key, default=None: (
                "http://127.0.0.1:8889/tidal/callback"
                if key == "tidal.redirect_uri" else default
            ),
        )

        response = client.get("/auth/tidal", base_url="http://192.168.86.50:8008")
        html = response.get_data(as_text=True)

        sent = _extract_redirect_uri(html)
        assert sent == "http://127.0.0.1:8889/tidal/callback", (
            f"Configured redirect_uri must be sent verbatim — got {sent!r}"
        )

    def test_custom_port_config_sent_verbatim(
        self, auth_route_client, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """User with non-default port (e.g. SOULSYNC_TIDAL_CALLBACK_PORT=9999)
        and matching portal registration."""
        client, _fake = auth_route_client

        from config.settings import config_manager
        monkeypatch.setattr(
            config_manager, "get",
            lambda key, default=None: (
                "http://127.0.0.1:9999/tidal/callback"
                if key == "tidal.redirect_uri" else default
            ),
        )

        response = client.get("/auth/tidal", base_url="http://192.168.86.50:8008")
        html = response.get_data(as_text=True)

        sent = _extract_redirect_uri(html)
        assert sent == "http://127.0.0.1:9999/tidal/callback"

    def test_explicit_network_ip_config_also_honored(
        self, auth_route_client, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """User who deliberately registered their network IP with Tidal
        and configured SoulSync to match — that registration must also
        be honored, not overridden."""
        client, _fake = auth_route_client

        from config.settings import config_manager
        monkeypatch.setattr(
            config_manager, "get",
            lambda key, default=None: (
                "http://192.168.86.50:8889/tidal/callback"
                if key == "tidal.redirect_uri" else default
            ),
        )

        response = client.get("/auth/tidal", base_url="http://192.168.86.50:8008")
        html = response.get_data(as_text=True)

        sent = _extract_redirect_uri(html)
        assert sent == "http://192.168.86.50:8889/tidal/callback"


# ---------------------------------------------------------------------------
# Empty config falls back to constructor default — NOT request.host
# ---------------------------------------------------------------------------


class TestEmptyConfigFallsBackToDefault:
    def test_empty_config_uses_constructor_default_not_request_host(
        self, auth_route_client, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The actual Foxxify case (his SoulSync UI display showed the
        default but config was empty because the placeholder never got
        saved): empty config from a non-localhost request must NOT build
        a network-IP redirect URI. The constructor default (matching
        the documented portal registration) wins instead."""
        client, _fake = auth_route_client

        from config.settings import config_manager
        monkeypatch.setattr(
            config_manager, "get",
            lambda key, default=None: ("" if key == "tidal.redirect_uri" else default),
        )

        response = client.get("/auth/tidal", base_url="http://192.168.86.50:8008")
        html = response.get_data(as_text=True)

        sent = _extract_redirect_uri(html)
        assert sent == "http://127.0.0.1:8889/tidal/callback", (
            f"Empty config must fall back to constructor default — got {sent!r}. "
            "If this looks like 'http://192.168.x.x:8889/tidal/callback' the "
            "request-host fallback got reintroduced and Foxxify's bug is back."
        )

    def test_empty_config_localhost_access_uses_default(
        self, auth_route_client, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, _fake = auth_route_client

        from config.settings import config_manager
        monkeypatch.setattr(
            config_manager, "get",
            lambda key, default=None: ("" if key == "tidal.redirect_uri" else default),
        )

        response = client.get("/auth/tidal", base_url="http://127.0.0.1:8008")
        html = response.get_data(as_text=True)

        sent = _extract_redirect_uri(html)
        assert sent == "http://127.0.0.1:8889/tidal/callback"
