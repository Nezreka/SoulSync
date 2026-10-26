"""A dead Deezer ARL says so.

Before this, an ARL that expired was silent: downloads fell back to the next
source and nothing said why. Now the download client sends
``deezer_signin_failed`` when Deezer rejects the ARL (once per failure, not
on every retry) and ``deezer_signin_restored`` when an ARL signs in again
after a failure, or a different ARL than the last one that worked. A dropped
connection is not a dead ARL and sends nothing. The seeded 'Check Deezer
Sign-in' asks Deezer every 6 hours, so an ARL that dies while nothing needs
it is caught too.

No request reaches Deezer: the gateway session is a fake.
"""

from __future__ import annotations

import ast
import threading
import types
from pathlib import Path

import pytest

import core.boot_phase as boot
from core.deezer_download_client import DeezerDownloadClient


@pytest.fixture(autouse=True)
def _post_boot(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Jar(dict):
    """the bit of a cookie jar the client uses"""

    def set(self, name, value):
        self[name] = value


class _Deezer:
    """what deezer knows: which arls are good. shared by every session"""

    def __init__(self):
        self.good = {"arl-1", "arl-2", "arl-old", "arl-new"}
        self.user = {"USER_ID": 7, "BLOG_NAME": "pb", "OPTIONS": {"web_lossless": True}}
        self.error = None


class _Gateway:
    """a session against the fake deezer. like the real one, a 'sid' from an
    earlier good login keeps answering for that account whatever arl is set"""

    def __init__(self, deezer=None):
        self.deezer = deezer or _Deezer()
        self.calls = []
        self.cookies = _Jar()
        self.headers = {"User-Agent": "test"}

    @property
    def user(self):
        return self.deezer.user

    @user.setter
    def user(self, value):
        self.deezer.user = value

    @property
    def error(self):
        return self.deezer.error

    @error.setter
    def error(self, value):
        self.deezer.error = value

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append(dict(params or {}))
        if self.deezer.error:
            raise self.deezer.error
        signed_in = self.cookies.get("arl") in self.deezer.good or self.cookies.get("sid")
        if not signed_in or not self.deezer.user.get("USER_ID"):
            return _Resp({"results": {"USER": {"USER_ID": 0}, "checkForm": ""}})
        self.cookies["sid"] = "sid-1"
        return _Resp({"results": {"USER": self.deezer.user, "checkForm": "tok"}})


class _Config:
    def __init__(self, arl="arl-1"):
        self.arl = arl

    def get(self, key, default=None):
        return self.arl if key == "deezer_download.arl" else default


def _client(arl="arl-1", last_good="arl-1"):
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._session = _Gateway()
    c._probes = []

    def _probe():
        g = _Gateway(c._session.deezer)
        c._probes.append(g)
        return g
    c._probe_session_factory = _probe
    c._api_lock = threading.Lock()
    c._last_request = 0
    c._min_interval = 0
    c._api_token = None
    c._license_token = None
    c._user_data = None
    c._authenticated = False
    c._pending_arl = None
    c._auth_retry_at = 0.0
    c._auth_retry_wait = 0.0
    c._reauth_lock = threading.Lock()
    c._reauth_thread = None
    c._auth_generation = 0
    c._config = _Config(arl)
    c._engine = None
    c.shutdown_check = None
    c._signin_lock = threading.Lock()
    c._signin_failing = False
    c._last_good_arl = last_good
    c.events = []
    c._event_emit = lambda event, data: c.events.append((event, data))
    return c


def _dead(c):
    c._session.user = {"USER_ID": 0}


def _names(c):
    return [e for e, _ in c.events]


# -- the events --

def test_a_rejected_arl_says_so_once():
    c = _client()
    _dead(c)
    for _ in range(4):           # the first try and three backoff retries
        assert c._authenticate("arl-1") is False
    assert _names(c) == ["deezer_signin_failed"]
    assert c.events[0][1]["reason"] == "invalid_arl" and "fresh one" in c.events[0][1]["detail"]


def test_a_good_sign_in_after_a_failure_says_so():
    c = _client()
    _dead(c)
    c._authenticate("arl-1")
    c._session.user = {"USER_ID": 9, "BLOG_NAME": "pb", "OPTIONS": {"web_lossless": True}}
    assert c._authenticate("arl-2") is True
    assert _names(c) == ["deezer_signin_failed", "deezer_signin_restored"]
    assert c.events[1][1] == {"user": "pb", "tier": "HiFi"}


def test_a_fresh_failure_after_a_recovery_is_announced_again():
    c = _client()
    _dead(c)
    c._authenticate("arl-1")
    c._session.user = {"USER_ID": 9, "BLOG_NAME": "pb", "OPTIONS": {}}
    c._authenticate("arl-1")
    _dead(c)
    c._authenticate("arl-1")
    assert _names(c) == ["deezer_signin_failed", "deezer_signin_restored", "deezer_signin_failed"]


def test_an_ordinary_sign_in_with_the_same_arl_sends_nothing():
    c = _client()
    assert c._authenticate("arl-1") is True
    assert c.events == []


def test_a_new_arl_that_signs_in_says_so():
    """replacing the arl before the old one died: you still hear it worked"""
    c = _client(last_good="arl-old")
    assert c._authenticate("arl-new") is True
    assert _names(c) == ["deezer_signin_restored"]


def test_the_first_ever_sign_in_sends_nothing():
    c = _client(last_good=None)
    assert c._authenticate("arl-1") is True
    assert c.events == []


@pytest.mark.parametrize("error", [ConnectionError("down"), TimeoutError("slow")])
def test_an_unreachable_deezer_is_not_a_dead_arl(error):
    c = _client()
    c._session.error = error
    assert c._authenticate("arl-1") is False
    assert c.events == [] and c._signin_failing is False


def test_a_failure_before_the_events_were_wired_is_sent_on_wiring():
    c = _client()
    c._event_emit = None
    _dead(c)
    c._authenticate("arl-1")      # at startup, nowhere to send it yet
    sent = []
    c.set_event_emitter(lambda e, d: sent.append(e))
    assert sent == ["deezer_signin_failed"]


def test_wiring_a_healthy_client_sends_nothing():
    c = _client()
    sent = []
    c.set_event_emitter(lambda e, d: sent.append(e))
    assert sent == []


def test_a_broken_emitter_never_breaks_the_login():
    c = _client()
    _dead(c)

    def boom(e, d):
        raise RuntimeError("engine down")
    c._event_emit = boom
    assert c._authenticate("arl-1") is False      # still just a failed login
    c._session.user = {"USER_ID": 9, "BLOG_NAME": "pb", "OPTIONS": {}}
    assert c._authenticate("arl-1") is True


def test_a_client_built_without_init_still_signs_in():
    """upstream's own tests build clients with __new__; the event state must
    not be what breaks a login"""
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._session = _Gateway()
    c._api_lock = threading.Lock()
    c._last_request = 0
    c._min_interval = 0
    c._api_token = None
    c._reauth_thread = None
    assert c._authenticate("arl-1") is True


# -- the scheduled check --

def test_no_arl_means_nothing_to_check():
    c = _client(arl="")
    assert c.check_signin() == {"status": "skipped", "reason": "no ARL set"}
    assert c._session.calls == []


def test_a_signed_in_client_is_probed_without_touching_its_session():
    c = _client()
    c._authenticated = True
    c._api_token = "live-token"
    assert c.check_signin() == {"status": "ok"}
    assert c._session.calls == []                       # its own session untouched
    probe = c._probes[-1]
    assert probe.calls[-1]["api_token"] == "null" and dict(probe.cookies) == {"arl": "arl-1", "sid": "sid-1"}
    assert c._api_token == "live-token" and c._authenticated is True
    assert c.events == []


def test_the_check_catches_an_arl_that_died_while_idle():
    c = _client()
    c._authenticated = True
    _dead(c)
    assert c.check_signin() == {"status": "failed"}
    assert _names(c) == ["deezer_signin_failed"]
    c.check_signin()               # the next check, still dead: no repeat
    assert _names(c) == ["deezer_signin_failed"]


def test_an_unreachable_check_is_not_a_failure():
    c = _client()
    c._authenticated = True
    c._session.error = ConnectionError("down")
    assert c.check_signin()["status"] == "unreachable"
    assert c.events == []


def test_a_signed_out_client_gets_a_real_sign_in():
    c = _client()
    assert c.check_signin() == {"status": "ok"}
    assert c._authenticated is True


def test_a_signed_out_client_with_a_dead_arl_reports_failed():
    c = _client()
    _dead(c)
    assert c.check_signin() == {"status": "failed"}
    assert _names(c) == ["deezer_signin_failed"]


# -- the handler, the blocks, the seeded automation, the wiring --

def _deps(client):
    lines = []
    orch = types.SimpleNamespace(client=lambda name: client if name == "deezer_dl" else None)
    deps = types.SimpleNamespace(download_orchestrator=orch,
                                 update_progress=lambda aid, **kw: lines.append(kw))
    return deps, lines


@pytest.mark.parametrize("state, status", [("ok", "completed"), ("failed", "completed"),
                                           ("unreachable", "completed"), ("skipped", "skipped")])
def test_the_handler_reports_what_the_check_found(state, status):
    from core.automation.handlers.deezer_signin import auto_check_deezer_signin
    client = types.SimpleNamespace(check_signin=lambda: {"status": state})
    deps, lines = _deps(client)
    result = auto_check_deezer_signin({"_automation_id": 1}, deps)
    assert result == {"status": status, "signin": state} and lines


def test_the_handler_without_deezer_set_up_skips():
    from core.automation.handlers.deezer_signin import auto_check_deezer_signin
    deps, _ = _deps(None)
    assert auto_check_deezer_signin({}, deps)["status"] == "skipped"


def test_the_blocks_exist_for_the_builder():
    from core.automation.blocks import ACTIONS, TRIGGERS, block_category
    triggers = {t["type"]: t for t in TRIGGERS}
    assert triggers["deezer_signin_failed"]["variables"] == ["reason", "detail"]
    assert triggers["deezer_signin_restored"]["variables"] == ["user", "tier"]
    assert "check_deezer_signin" in {a["type"] for a in ACTIONS}
    for name in ("deezer_signin_failed", "deezer_signin_restored", "check_deezer_signin"):
        assert block_category(name) == "Downloads"


def test_the_check_is_seeded_every_six_hours_and_on():
    from core.automation_engine import SYSTEM_AUTOMATIONS
    spec = next(s for s in SYSTEM_AUTOMATIONS if s["action_type"] == "check_deezer_signin")
    assert spec["trigger_type"] == "schedule"
    assert spec["trigger_config"] == {"interval": 6, "unit": "hours"}
    assert spec.get("enabled_on_create", True) is True


def test_web_server_wires_the_events():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "web_server.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "set_event_emitter"]
    assert calls, "web_server never hands the deezer client the automation engine"


# -- a stale deezer session must not vouch for a changed arl --

def test_a_changed_arl_is_really_tried_not_carried_by_the_old_session():
    """found live: deezer's 'sid' from the last good login answered for the old
    account, so saving a dead arl 'signed in' fine and nothing failed"""
    c = _client()
    assert c._authenticate("arl-1") is True          # leaves a sid in the jar
    assert c._session.cookies.get("sid")
    assert c._authenticate("dead-arl") is False      # the old sid must not carry it
    assert _names(c) == ["deezer_signin_failed"]


def test_putting_the_good_arl_back_after_that_says_restored():
    c = _client()
    c._authenticate("arl-1")
    c._authenticate("dead-arl")
    assert c._authenticate("arl-1") is True
    assert _names(c) == ["deezer_signin_failed", "deezer_signin_restored"]


def test_the_check_tests_the_saved_arl_not_the_live_session():
    """the saved arl died (or was changed to a bad one) while the live session
    still holds a good sid: the check must still say failed"""
    c = _client()
    c._authenticate("arl-1")
    c._authenticated = True
    c._config.arl = "dead-arl"
    assert c.check_signin() == {"status": "failed"}
    assert _names(c) == ["deezer_signin_failed"]
    assert c._session.cookies.get("sid") == "sid-1"   # downloads' session left alone
