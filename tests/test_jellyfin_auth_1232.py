"""SoulSync could not connect to Jellyfin 12 (#1232).

Two problems, and the second is why the first was hard to see.

Auth: every request carried only ``X-Emby-Token``, the Emby-compatibility
header Jellyfin has accepted for years and newer servers no longer honour. The
modern ``Authorization: MediaBrowser ...`` header is now sent alongside it —
alongside, not instead, so every 10.x install keeps working untouched.

Diagnostics: the connection test answered "Check URL and API Key" for a 401, a
404, a timeout and a refused connection alike, so a server rejecting the auth
header looked exactly like a typo.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.jellyfin_client import JellyfinClient


def _client(api_key="abc123", base_url="http://jelly.invalid"):
    client = JellyfinClient.__new__(JellyfinClient)
    client.api_key = api_key
    client.base_url = base_url
    client.last_error = ""
    return client


# ---------------------------------------------------------------------------
# The header
# ---------------------------------------------------------------------------

def test_the_modern_authorization_header_carries_the_token():
    value = _client()._auth_header()
    assert value.startswith("MediaBrowser ")
    assert 'Token="abc123"' in value


def test_the_header_identifies_the_client():
    # Jellyfin logs and its dashboard show these; an unnamed client is a
    # support question waiting to happen.
    value = _client()._auth_header()
    for part in ('Client="SoulSync"', 'Device="SoulSync"', "DeviceId=", "Version="):
        assert part in value


def test_a_missing_api_key_does_not_crash_the_header():
    assert 'Token=""' in _client(api_key=None)._auth_header()


def test_both_headers_are_sent(monkeypatch):
    """Alongside, never instead.

    A server that wants the old header ignores the extra one, and a server
    that wants the new one ignores the old — so this cannot break a working
    10.x install.
    """
    client = _client()
    seen = {}

    def _get(url, headers=None, params=None, timeout=None):
        seen.update(headers or {})
        response = MagicMock()
        response.raise_for_status = lambda: None
        response.json = lambda: {"ServerName": "Test"}
        return response

    monkeypatch.setattr(requests, "get", _get)
    with patch("core.jellyfin_client.config_manager") as cfg:
        cfg.get_jellyfin_config.return_value = {"api_timeout": 120}
        client._make_request("/System/Info")

    assert seen.get("X-Emby-Token") == "abc123"
    assert seen.get("Authorization", "").startswith("MediaBrowser ")


def test_every_request_site_sends_both():
    # 15 places build a Jellyfin header dict; one missed is a call that works
    # on 10.x and fails on 12 for no visible reason.
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "core/jellyfin_client.py").read_text(
        encoding="utf-8", errors="ignore")
    for line in source.splitlines():
        if "'X-Emby-Token': self.api_key" in line:
            assert "_auth_header()" in line, line.strip()


# ---------------------------------------------------------------------------
# Saying what went wrong
# ---------------------------------------------------------------------------

def test_an_http_error_keeps_the_status_and_the_servers_words(monkeypatch):
    client = _client()

    def _get(url, headers=None, params=None, timeout=None):
        response = MagicMock()
        response.status_code = 401
        response.text = "Unauthorized"
        error = requests.exceptions.HTTPError(response=response)
        response.raise_for_status = MagicMock(side_effect=error)
        return response

    monkeypatch.setattr(requests, "get", _get)
    with patch("core.jellyfin_client.config_manager") as cfg:
        cfg.get_jellyfin_config.return_value = {"api_timeout": 120}
        assert client._make_request("/System/Info") is None

    assert "401" in client.last_error
    assert "Unauthorized" in client.last_error


def test_a_transport_failure_names_itself(monkeypatch):
    client = _client()

    def _get(url, headers=None, params=None, timeout=None):
        raise requests.exceptions.ConnectTimeout("timed out")

    monkeypatch.setattr(requests, "get", _get)
    with patch("core.jellyfin_client.config_manager") as cfg:
        cfg.get_jellyfin_config.return_value = {"api_timeout": 120}
        assert client._make_request("/System/Info") is None

    assert "ConnectTimeout" in client.last_error


def test_diagnostics_never_throw_on_an_unreadable_body(monkeypatch):
    # A body that cannot be read must not replace the real error with a
    # traceback about reading it.
    client = _client()

    def _get(url, headers=None, params=None, timeout=None):
        response = MagicMock()
        response.status_code = 500
        type(response).text = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no body")))
        error = requests.exceptions.HTTPError(response=response)
        response.raise_for_status = MagicMock(side_effect=error)
        return response

    monkeypatch.setattr(requests, "get", _get)
    with patch("core.jellyfin_client.config_manager") as cfg:
        cfg.get_jellyfin_config.return_value = {"api_timeout": 120}
        assert client._make_request("/System/Info") is None

    assert "500" in client.last_error


def test_the_connection_test_repeats_the_reason():
    from core import connection_test

    failing = MagicMock()
    failing.is_connected.return_value = False
    failing.last_error = "HTTP 401 from /System/Info: Unauthorized"

    with patch.object(connection_test, "JellyfinClient", return_value=failing):
        ok, message = connection_test.run_service_test("jellyfin", {})

    assert ok is False
    assert "401" in message


def test_the_connection_test_still_reads_well_with_no_detail():
    from core import connection_test

    failing = MagicMock()
    failing.is_connected.return_value = False
    failing.last_error = ""

    with patch.object(connection_test, "JellyfinClient", return_value=failing):
        ok, message = connection_test.run_service_test("jellyfin", {})

    assert ok is False
    assert message.endswith("Check URL and API Key.")
