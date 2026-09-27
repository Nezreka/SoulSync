"""Regression: Deezer download client `is_configured()` must fire the lazy ARL auth.

The hybrid-mode source gate + the green-light status both read `is_configured()`. It used to return
the raw `_authenticated` flag, which stays False until something calls `is_authenticated()` (the lazy
ARL auth). So Deezer downloaded fine as a *primary* source (that path auths) but was silently dropped
from a hybrid chain and showed no green light. The fix routes is_configured()/is_available() through
is_authenticated() so the three can't drift.
"""

from __future__ import annotations

import core.boot_phase as boot
from core.deezer_download_client import DeezerDownloadClient


def _bare_client(authenticated=False, pending_arl=None):
    """A client without __init__ (no config/network) — just the auth-state seam we're testing."""
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._authenticated = authenticated
    c._pending_arl = pending_arl
    return c


def test_is_configured_fires_lazy_arl_auth_post_boot(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    c = _bare_client(authenticated=False, pending_arl="fake-arl")
    calls = []

    def fake_auth(arl):
        calls.append(arl)
        c._authenticated = True

    c._authenticate = fake_auth

    # The hybrid gate / green light call this — it must trigger the deferred auth, not report False.
    assert c.is_configured() is True
    assert c.is_available() is True
    assert calls == ["fake-arl"]
    assert c._pending_arl is None

    # Idempotent: once authed, no repeat network auth.
    assert c.is_configured() is True
    assert calls == ["fake-arl"]


def test_is_configured_defers_during_boot(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: True)
    c = _bare_client(authenticated=False, pending_arl="fake-arl")

    def boom(arl):
        raise AssertionError("must not authenticate (network) during boot")

    c._authenticate = boom
    assert c.is_configured() is False  # deferred — stays unauthenticated, but never auths mid-boot


def test_is_configured_true_when_already_authed():
    c = _bare_client(authenticated=True, pending_arl=None)
    assert c.is_configured() is True
    assert c.is_available() is True


def test_is_configured_false_with_no_arl(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    c = _bare_client(authenticated=False, pending_arl=None)  # no ARL configured at all
    assert c.is_configured() is False


# a settings save calls reconnect(). on sept 24 deezer dropped the connection
# right then ("Remote end closed connection without response") and downloads
# stayed logged out until the next save, because a failed login was never
# tried again.

def _flaky_client(monkeypatch, results):
    """results: what each login attempt does, True = logs in."""
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    c = _bare_client()
    c._config = type("Cfg", (), {"get": lambda self, k, d=None: d})()
    attempts = []

    def fake_auth(arl):
        attempts.append(arl)
        ok = results[len(attempts) - 1]
        c._authenticated = ok
        return ok

    c._authenticate = fake_auth
    return c, attempts


def test_failed_reconnect_is_retried_later(monkeypatch):
    import core.deezer_download_client as mod
    clock = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    c, attempts = _flaky_client(monkeypatch, [False, True])

    assert c.reconnect("arl") is False
    # right away: waits, doesn't hammer deezer on every status poll
    assert c.is_configured() is False
    assert attempts == ["arl"]
    clock[0] += 31
    assert c.is_configured() is True
    assert attempts == ["arl", "arl"]
    assert c._pending_arl is None


def test_retry_backs_off_for_a_bad_arl(monkeypatch):
    import core.deezer_download_client as mod
    clock = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    c, attempts = _flaky_client(monkeypatch, [False] * 10)

    c.reconnect("bad")
    clock[0] += 31
    c.is_configured()          # 2nd try fails, next wait is 60s
    clock[0] += 31
    c.is_configured()          # too soon
    assert len(attempts) == 2
    clock[0] += 30
    c.is_configured()
    assert len(attempts) == 3
    assert c._pending_arl == "bad"


def test_successful_reconnect_leaves_nothing_pending(monkeypatch):
    c, attempts = _flaky_client(monkeypatch, [True])
    assert c.reconnect("arl") is True
    assert c._pending_arl is None
    assert c.is_configured() is True
    assert attempts == ["arl"]
