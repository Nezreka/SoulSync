"""Tidal DOWNLOAD session restore must refresh an expired token, not drop it (#1002).

QT3496: Tidal download source disconnects on every restart. Cause: the saved
access token is expired after a restart, and ``check_login()`` verifies but
does NOT refresh — so the restore declared the session invalid and dropped it,
even though the stored refresh token was still good.

_restore_and_verify now refreshes explicitly when the loaded token is stale.
These pin: the recovery path, and the safety guards (save only on confirmed
success; never save/overwrite on failure so the next restart can retry).
"""

from __future__ import annotations

import core.tidal_download_client as tdc
from core.tidal_download_client import TidalDownloadClient


class _Cfg:
    def __init__(self):
        self.saved = []

    def set(self, key, value):
        self.saved.append((key, value))

    def get(self, key, default=None):
        return default


class _FakeSession:
    def __init__(self, *, load_ok=True, check_results=None, refresh=None):
        self.token_type = "Bearer"
        self.access_token = "tok"
        self.refresh_token = "r"
        self.expiry_time = None
        self._load_ok = load_ok
        self._checks = list(check_results if check_results is not None else [True])
        self._refresh = refresh  # True | False | an Exception instance
        self.token_refresh_calls = 0

    def load_oauth_session(self, **kw):
        return self._load_ok

    def check_login(self):
        return self._checks.pop(0) if self._checks else False

    def token_refresh(self, refresh_token):
        self.token_refresh_calls += 1
        if isinstance(self._refresh, Exception):
            raise self._refresh
        return self._refresh


def _client(session, monkeypatch):
    cfg = _Cfg()
    monkeypatch.setattr(tdc, "config_manager", cfg)
    c = TidalDownloadClient.__new__(TidalDownloadClient)
    c.session = session
    return c, cfg


def _saved_session(cfg):
    return [v for k, v in cfg.saved if k == "tidal_download.session"]


# --- valid token: restore succeeds, no refresh ------------------------------

def test_valid_token_restores_without_refresh(monkeypatch):
    s = _FakeSession(check_results=[True])
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is True
    assert s.token_refresh_calls == 0
    assert len(_saved_session(cfg)) == 1


# --- THE FIX: expired token -> explicit refresh -> recovered ----------------

def test_expired_token_is_refreshed_and_recovered(monkeypatch):
    # First check_login False (expired) -> refresh True -> check_login True.
    s = _FakeSession(check_results=[False, True], refresh=True)
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is True
    assert s.token_refresh_calls == 1
    assert len(_saved_session(cfg)) == 1   # refreshed session persisted


# --- safety: failures never save / overwrite the stored tokens --------------

def test_refresh_returns_false_does_not_save(monkeypatch):
    s = _FakeSession(check_results=[False], refresh=False)
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is False
    assert _saved_session(cfg) == []       # stored config tokens left intact


def test_refresh_raises_is_swallowed_and_not_saved(monkeypatch):
    from tidalapi.exceptions import AuthenticationError
    s = _FakeSession(check_results=[False], refresh=AuthenticationError("expired"))
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is False
    assert s.token_refresh_calls == 1
    assert _saved_session(cfg) == []


def test_no_refresh_token_no_refresh_attempt(monkeypatch):
    s = _FakeSession(check_results=[False])
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "", 0) is False
    assert s.token_refresh_calls == 0
    assert _saved_session(cfg) == []


def test_load_failure_returns_false_no_refresh(monkeypatch):
    s = _FakeSession(load_ok=False)
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is False
    assert s.token_refresh_calls == 0
    assert _saved_session(cfg) == []


# --- the Netti93 restart loop: boot-phase stash must survive __init__ --------
# During a boot-phase construction _init_session stashes the saved tokens in
# _boot_session_tokens for deferred verification; __init__ used to re-initialize
# that attribute AFTER calling _init_session, silently wiping the stash. Every
# Docker/gunicorn restart then came up unauthenticated with no error anywhere,
# while the config still held valid tokens (2.7.0 -> 2.8.6 regression).

def test_boot_phase_construction_keeps_the_deferred_tokens(monkeypatch, tmp_path):
    import core.boot_phase as boot_phase

    cfg = _Cfg()
    cfg.get = lambda key, default=None: (
        {"token_type": "Bearer", "access_token": "tok",
         "refresh_token": "r", "expiry_time": 9999999999}
        if key == "tidal_download.session" else
        (str(tmp_path) if key == "soulseek.download_path" else default)
    )
    monkeypatch.setattr(tdc, "config_manager", cfg)
    monkeypatch.setattr(boot_phase, "is_boot_phase", lambda: True)

    class _FakeTidalapi:
        Session = _FakeSession

    monkeypatch.setattr(tdc, "tidalapi", _FakeTidalapi)

    c = TidalDownloadClient(download_path=str(tmp_path))
    assert c._boot_session_tokens is not None, \
        "boot-phase token stash must survive __init__ (the restart-loop bug)"
    assert c._boot_session_tokens.get("access_token") == "tok"
    assert c.is_authenticated() is True              # boot phase: pending tokens count


# --- transient restore failures keep the tokens and retry --------------------

def test_transient_restore_failure_keeps_pending_and_stays_authenticated(monkeypatch):
    import requests
    s = _FakeSession(load_ok=True, check_results=[])

    def network_down(**kw):
        raise requests.ConnectionError("Name or service not known")

    s.load_oauth_session = network_down
    c, cfg = _client(s, monkeypatch)
    c._boot_session_tokens = {"token_type": "Bearer", "access_token": "tok",
                              "refresh_token": "r", "expiry_time": 0}
    c._next_restore_retry_at = 0.0
    monkeypatch.setattr(tdc, "tidalapi", object())

    assert c._complete_deferred_session() is True    # optimistic, not dropped
    assert c._boot_session_tokens is not None        # tokens kept for retry
    assert c._next_restore_retry_at > 0              # retry throttled
    assert _saved_session(cfg) == []                 # stored config untouched


def test_definitive_rejection_drops_pending(monkeypatch):
    s = _FakeSession(load_ok=False)                  # Tidal says the tokens are dead
    c, cfg = _client(s, monkeypatch)
    c._boot_session_tokens = {"token_type": "Bearer", "access_token": "tok",
                              "refresh_token": "r", "expiry_time": 0}
    c._next_restore_retry_at = 0.0
    monkeypatch.setattr(tdc, "tidalapi", object())

    assert c._complete_deferred_session() is False
    assert c._boot_session_tokens is None            # definitive -> cleared


def test_retry_succeeds_after_transient_failure(monkeypatch):
    import requests
    s = _FakeSession(check_results=[True])
    calls = {"n": 0}
    real_load = s.load_oauth_session

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("boot: network not up yet")
        return real_load(**kw)

    s.load_oauth_session = flaky
    c, cfg = _client(s, monkeypatch)
    c._boot_session_tokens = {"token_type": "Bearer", "access_token": "tok",
                              "refresh_token": "r", "expiry_time": 0}
    c._next_restore_retry_at = 0.0
    monkeypatch.setattr(tdc, "tidalapi", object())

    assert c._complete_deferred_session() is True    # transient, kept
    c._next_restore_retry_at = 0.0                   # fast-forward the throttle
    assert c._complete_deferred_session() is True    # retry restores for real
    assert c._boot_session_tokens is None            # done — no more pending
    assert len(_saved_session(cfg)) == 1             # restored session persisted
