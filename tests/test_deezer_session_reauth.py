"""#1677: the Deezer gateway's session token dies hours before the ARL does.

Login caches the api_token from getUserData and every download rides it.
When it aged out, Deezer answered VALID_TOKEN_REQUIRED, the gateway call
returned None, and downloads failed with "expired ARL" until the container
restarted — the ARL was fine, only the session was dead. The gateway call
now re-logins once with the configured ARL and retries; the download error
only blames the ARL when the re-login itself failed.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

import core.boot_phase as boot
from core.deezer_download_client import DeezerDownloadClient


@pytest.fixture(autouse=True)
def _post_boot(monkeypatch):
    """Heals (like the lazy ARL auth) stay deferred during boot; these tests
    exercise the post-boot client, like the is_configured suite does."""
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeSession:
    """Routes gateway posts by method + the api_token they rode in on."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []
        self.cookies = types.SimpleNamespace(set=lambda *a, **k: None)

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append(params.get('method'))
        return _Resp(self._handler(params.get('method'), params.get('api_token')))


class _FakeConfig:
    def get(self, key, default=None):
        return "arl-123" if key == "deezer_download.arl" else default


def _client(handler, authenticate):
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._session = _FakeSession(handler)
    c._api_lock = threading.Lock()
    c._last_request = 0
    c._min_interval = 0
    c._api_token = "old-token"
    c._license_token = "old-license"
    c._user_data = {"USER_ID": 7}
    c._authenticated = True
    c._pending_arl = None
    c._auth_retry_at = 0.0
    c._auth_retry_wait = 0.0
    c._reauth_lock = threading.Lock()
    c._reauth_thread = None
    c._heal_failed_token = None
    c._config = _FakeConfig()
    if authenticate is not None:
        c._authenticate = authenticate
    c._engine = None
    c.shutdown_check = None
    return c


def _track_handler(method, token):
    if token == "old-token":
        return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}
    return {"results": {"SNG_ID": "42", "TRACK_TOKEN": "tt"}}


def test_gw_call_heals_dead_session_token_and_retries():
    auth_calls = []

    def heal(arl):
        auth_calls.append(arl)
        c._api_token = "new-token"
        c._authenticated = True
        return True

    c = _client(_track_handler, heal)
    assert c._get_track_data("42") == {"SNG_ID": "42", "TRACK_TOKEN": "tt"}
    assert auth_calls == ["arl-123"]  # exactly one re-login, with the configured ARL
    assert c._session.calls == ["song.getData", "song.getData"]  # fail, heal, retry
    assert c._authenticated is True


def test_gw_call_dead_arl_drops_to_pending_backoff():
    def dead_auth(arl):
        c._authenticated = False
        return False

    c = _client(_track_handler, dead_auth)

    assert c._get_track_data("42") is None
    # the client stops claiming to be authenticated and the existing
    # is_authenticated() backoff takes over — no re-login loop
    assert c._authenticated is False
    assert c._pending_arl == "arl-123"
    assert c._auth_retry_at > time.time()
    assert c._session.calls == ["song.getData"]


def test_gw_call_does_not_heal_other_errors():
    def boom_auth(arl):
        raise AssertionError("must not re-authenticate for a gateway error")

    def gateway_error(method, token):
        return {"error": {"GATEWAY_ERROR": "boom"}}

    c = _client(gateway_error, boom_auth)
    assert c._get_track_data("42") is None
    assert c._authenticated is True


def test_gw_call_never_heals_from_inside_a_login():
    """reconnect() -> _authenticate() -> _gw_call('deezer.getUserData'): when
    THAT call is the one rejected, a nested heal would recurse forever."""
    auth_calls = []

    def fake_auth(arl):
        auth_calls.append(arl)
        return True

    c = _client(_track_handler, fake_auth)
    c._reauth_thread = threading.get_ident()  # as a heal sets for its own login
    assert c._gw_call("deezer.getUserData") is None
    assert auth_calls == []


def test_concurrent_downloads_share_one_heal():
    auth_calls = []
    auth_lock = threading.Lock()

    def slow_auth(arl):
        with auth_lock:
            auth_calls.append(arl)
        time.sleep(0.05)
        c._api_token = "new-token"
        c._authenticated = True
        return True

    c = _client(_track_handler, slow_auth)
    results = []
    barrier = threading.Barrier(3)

    def worker():
        barrier.wait()
        results.append(c._get_track_data("42"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    barrier.wait()
    for t in threads:
        t.join()

    assert results == [{"SNG_ID": "42", "TRACK_TOKEN": "tt"}] * 2
    assert len(auth_calls) == 1


class _FakeEngine:
    def __init__(self):
        self.updates = []

    def update_record(self, source, record_id, fields):
        self.updates.append(fields)


def _download_error(authenticated):
    c = _client(_track_handler, lambda arl: True)
    c._authenticated = authenticated
    c._engine = _FakeEngine()
    c._get_track_data = lambda track_id: None
    assert c._download_sync("dl-1", "42", "Song") is None
    return c._engine.updates[-1]["error"]


def test_download_error_blames_arl_only_when_login_failed():
    assert "expired ARL" in _download_error(authenticated=False)
    region_error = _download_error(authenticated=True)
    assert "ARL" not in region_error
    assert "region" in region_error


def _bootstrap_handler(method, token):
    """Models Deezer's bootstrap contract: getUserData only answers when the
    call presents no session token ('null'); it issues the fresh token."""
    if method == "deezer.getUserData":
        if token != "null":
            return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}
        return {"results": {
            "USER": {"USER_ID": 7, "BLOG_NAME": "Tester",
                     "OPTIONS": {"license_token": "new-license", "web_hq": True}},
            "checkForm": "new-token",
        }}
    if token == "new-token":
        return {"results": {"SNG_ID": "42", "TRACK_TOKEN": "tt"}}
    return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}


def test_heal_composes_with_the_real_login():
    """The full chain with the REAL _authenticate/reconnect: the login's
    bootstrap must not present the dead token, or Deezer rejects the very
    call meant to replace it."""
    c = _client(_bootstrap_handler, None)  # None = keep the real _authenticate
    assert c._get_track_data("42") == {"SNG_ID": "42", "TRACK_TOKEN": "tt"}
    assert c._session.calls == ["song.getData", "deezer.getUserData", "song.getData"]
    assert c._api_token == "new-token"
    assert c._license_token == "new-license"
    assert c._authenticated is True


def test_a_failed_heal_is_shared_until_the_backoff_expires():
    auth_calls = []

    def dead_auth(arl):
        auth_calls.append(arl)
        return False

    c = _client(_track_handler, dead_auth)
    for _ in range(3):
        assert c._get_track_data("42") is None
    assert auth_calls == ["arl-123"]  # one login attempt, not one per download
    assert c._pending_arl == "arl-123"
    assert c._auth_retry_at > time.time()


def test_a_successful_reconnect_clears_the_heal_failure_marker():
    """Round-2 minor: a heal fails (marker + backoff set), then a settings
    save reconnects successfully. If that login re-issues the same token
    string and it is rejected again inside the old backoff window, the
    stale marker must not suppress the next heal."""
    auth_calls = []

    def dead_auth(arl):
        auth_calls.append(arl)
        return False

    c = _client(_track_handler, dead_auth)
    assert c._get_track_data("42") is None
    assert c._heal_failed_token == "old-token"
    assert c._auth_retry_at > time.time()

    def same_token_auth(arl):
        auth_calls.append(arl)
        c._api_token = "old-token"  # Deezer re-issues the same session token
        c._authenticated = True
        return True

    c._authenticate = same_token_auth
    assert c.reconnect() is True
    assert c._heal_failed_token is None

    c._authenticate = dead_auth
    assert c._get_track_data("42") is None
    # the heal was attempted (third login overall), not marker-suppressed
    assert auth_calls == ["arl-123", "arl-123", "arl-123"]


def test_a_write_is_healed_but_never_reexecuted():
    """playlist.addSongs/create are not idempotent: if the token died
    mid-request the write may already have landed. The heal still refreshes
    the session for the next call, but the write itself is not retried."""
    def handler(method, token):
        if method == "playlist.addSongs":
            return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}
        return _track_handler(method, token)

    def heal(arl):
        c._api_token = "new-token"
        c._authenticated = True
        return True

    c = _client(handler, heal)
    assert c._gw_call("playlist.addSongs", {"playlist_id": "1", "songs": []}) is None
    assert c._session.calls == ["playlist.addSongs"]  # exactly one attempt
    assert c._get_track_data("42") == {"SNG_ID": "42", "TRACK_TOKEN": "tt"}  # healed
