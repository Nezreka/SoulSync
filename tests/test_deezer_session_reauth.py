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
    c._auth_generation = 0
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
    """With the REAL login: a failed heal clears the cached token, so later
    calls present no token at all — the sharing must come from the backoff
    timestamp itself, not from matching the dead token's string."""
    logins = {"n": 0}

    def dead_arl_handler(method, token):
        if method == "deezer.getUserData":
            logins["n"] += 1
            return {"results": {"USER": {"USER_ID": 0}}}  # dead ARL: no user
        return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}

    c = _client(dead_arl_handler, None)  # None = keep the real _authenticate
    for _ in range(3):
        assert c._gw_call("song.getData", {"sng_id": "42"}) is None
    assert logins["n"] == 1  # one login attempt, not one per call
    assert c._api_token is None  # the seam the stub-based version missed
    assert c._pending_arl == "arl-123"
    assert c._auth_retry_at > time.time()


def test_a_successful_reconnect_reopens_the_heal_gate():
    """A heal fails and the backoff is running; a settings save then logs in
    fine. The successful login clears the backoff, so when the new session
    is rejected later the heal is attempted — not suppressed by the stale
    failure."""
    state = {"arl_dead": True, "logins": 0}

    def handler(method, token):
        if method == "deezer.getUserData":
            state["logins"] += 1
            if state["arl_dead"]:
                return {"results": {"USER": {"USER_ID": 0}}}
            return {"results": {
                "USER": {"USER_ID": 7, "BLOG_NAME": "Tester",
                         "OPTIONS": {"license_token": "lic", "web_hq": True}},
                "checkForm": "new-token",
            }}
        if token == "new-token" and not state["arl_dead"]:
            return {"results": {"SNG_ID": "42", "TRACK_TOKEN": "tt"}}
        return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}

    c = _client(handler, None)  # real login throughout
    assert c._gw_call("song.getData", {"sng_id": "42"}) is None
    assert state["logins"] == 1
    assert c._auth_retry_at > time.time()

    state["arl_dead"] = False
    assert c.reconnect() is True  # the settings save
    assert c._auth_retry_at == 0.0

    state["arl_dead"] = True  # the new session gets rejected too
    assert c._gw_call("song.getData", {"sng_id": "42"}) is None
    assert state["logins"] == 3  # heal attempted again (save + heal), not gated


def test_login_bootstrap_never_triggers_a_heal():
    """deezer.getUserData IS the login: when a settings-save login's own
    bootstrap is token-rejected, healing it would just be a second login
    before giving up. Exactly one login post may happen."""
    posts = {"n": 0}

    def rejecting_handler(method, token):
        if method == "deezer.getUserData":
            posts["n"] += 1
            return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}
        return {"results": {}}

    c = _client(rejecting_handler, None)  # real login
    assert c.reconnect() is False
    assert posts["n"] == 1


def test_concurrent_heal_shared_when_token_string_unchanged():
    """Deezer may re-issue the same token string on re-login (the session
    cookies persist). Waiting callers must still share the one heal — the
    auth generation, not the token string, is what proves the refresh."""
    state = {"valid": False}
    auth_calls = []
    auth_lock = threading.Lock()

    def handler(method, token):
        if state["valid"]:
            return {"results": {"SNG_ID": "42", "TRACK_TOKEN": "tt"}}
        return {"error": {"VALID_TOKEN_REQUIRED": "valid token required"}}

    def same_string_auth(arl):
        with auth_lock:
            auth_calls.append(arl)
        time.sleep(0.05)
        state["valid"] = True
        c._api_token = "old-token"  # the SAME string comes back
        c._authenticated = True
        return True

    c = _client(handler, same_string_auth)
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
