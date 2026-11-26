"""acoustid user keys (#2318).

from 1 january 2027 a lookup needs the user's own key with the application
key, or acoustid answers error 21. pinned here, with acoustid's http mocked:
  - which application key is sent (built in, env, the old setting)
  - request_link stores the user key at once; link_status settles it
  - every lookup carries the user key, in a header
  - error 21 (and a missing key after the cutoff) says "link your account",
    stops a scan, and never reads as a bad file or a bad app key
  - the user key is a secret: encrypted, masked, never in an answer
  - the new routes are admin only
"""

from __future__ import annotations

import sys
import types
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import acoustid_account as aa
import core.acoustid_client as acc
from core.acoustid_client import AcoustIDClient


class FakeConfig:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def clear_secret(self, key):
        self.data[key] = ''


class Resp:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status

    def json(self):
        return self.body


@pytest.fixture
def http(monkeypatch):
    """every acoustid call, recorded; answers queued per path"""
    import requests
    calls = []
    answers = {}

    def _send(method):
        def send(url, params=None, data=None, headers=None, timeout=None):
            calls.append(SimpleNamespace(method=method, url=url, form=dict(params or data or {}),
                                         headers=dict(headers or {})))
            path = url.split('/v2/', 1)[1]
            body = answers[path].pop(0) if isinstance(answers.get(path), list) else answers.get(path)
            return Resp(body)
        return send

    monkeypatch.setattr(requests, 'post', _send('POST'))
    monkeypatch.setattr(requests, 'get', _send('GET'))
    monkeypatch.setattr(aa, '_MIN_INTERVAL', 0)
    return SimpleNamespace(calls=calls, answers=answers)


LINK_OK = {"status": "ok", "user": {"apikey": "UzBdV3X80J", "link": {
    "code": "WT14-9E3S", "url": "https://acoustid.org/link?code=WT14-9E3S",
    "expires": "2999-12-01T12:15:00+00:00"}}}


def _state(state):
    return {"status": "ok", "key": {"state": state, "expires": "2999-12-01T12:15:00+00:00"}}


# ── the application key ──

def test_app_key_prefers_soulsyncs_own_then_env_then_the_old_setting(monkeypatch):
    cfg = FakeConfig({'acoustid.api_key': ' mine '})
    assert aa.app_key(cfg) == 'mine'
    monkeypatch.setattr(aa, 'SOULSYNC_ACOUSTID_APP_KEY', 'BUILTIN')
    assert aa.app_key(cfg) == 'BUILTIN'
    monkeypatch.setenv(aa.APP_KEY_ENV, 'FROMENV')
    assert aa.app_key(cfg) == 'FROMENV'
    assert aa.has_builtin_app_key()


def test_client_api_key_is_the_resolved_app_key(monkeypatch):
    cfg = {'acoustid.api_key': 'legacy', 'acoustid.user_key': 'U1'}
    monkeypatch.setattr(acc.config_manager, 'get', lambda k, d=None: cfg.get(k, d))
    c = AcoustIDClient()
    assert c.api_key == 'legacy' and c.user_key == 'U1'
    monkeypatch.setattr(aa, 'SOULSYNC_ACOUSTID_APP_KEY', 'BUILTIN')
    assert c.api_key == 'BUILTIN'


# ── linking ──

def test_request_link_stores_the_key_at_once_and_sends_the_app_key(http):
    cfg = FakeConfig({'acoustid.api_key': 'APP'})
    http.answers['user/request_link'] = LINK_OK
    res = aa.request_link(cfg, device='SoulSync on nas',
                          return_url='https://music.example/settings?open=acoustid')
    assert res['ok'] and res['state'] == 'pending' and res['code'] == 'WT14-9E3S'
    assert res['url'] == 'https://acoustid.org/link?code=WT14-9E3S'
    assert 'UzBdV3X80J' not in repr(res)            # the key is never handed out
    assert cfg.data['acoustid.user_key'] == 'UzBdV3X80J'
    call = http.calls[0]
    assert call.method == 'POST' and call.url.endswith('/v2/user/request_link')
    assert call.form['client'] == 'APP'
    assert call.form['return_url'] == 'https://music.example/settings?open=acoustid'
    assert call.form['device'] == 'SoulSync on nas'
    assert aa.link_pending(cfg)


def test_request_link_without_any_app_key_says_so(http):
    res = aa.request_link(FakeConfig())
    assert not res['ok'] and 'application key' in res['error']
    assert http.calls == []


def test_request_link_refuses_a_non_http_return_url(http):
    res = aa.request_link(FakeConfig({'acoustid.api_key': 'APP'}), return_url='javascript:alert(1)')
    assert not res['ok'] and http.calls == []


def test_request_link_error_keeps_nothing(http):
    cfg = FakeConfig({'acoustid.api_key': 'APP'})
    http.answers['user/request_link'] = {"status": "error", "error": {"code": 4, "message": "invalid API key"}}
    res = aa.request_link(cfg)
    assert not res['ok'] and 'invalid API key' in res['error']
    assert 'acoustid.user_key' not in cfg.data


def test_link_status_pending_then_approved_keeps_the_key(http):
    cfg = FakeConfig({'acoustid.api_key': 'APP'})
    http.answers['user/request_link'] = LINK_OK
    aa.request_link(cfg)
    http.answers['user/link_status'] = [_state('pending'), _state('approved')]
    assert aa.check_link(cfg)['state'] == 'pending'
    assert aa.check_link(cfg)['state'] == 'approved'
    assert cfg.data['acoustid.user_key'] == 'UzBdV3X80J'
    poll = http.calls[-1]
    assert poll.method == 'GET' and poll.url.endswith('/v2/user/link_status')
    assert poll.form['client'] == 'APP'
    assert poll.headers['Acoustid-User-Key'] == 'UzBdV3X80J'
    assert 'user' not in poll.form                     # the key stays out of the url
    st = aa.status(cfg)
    assert st['source'] == 'linked' and st['state'] == 'approved' and st['has_user_key']
    assert not aa.link_pending(cfg)


@pytest.mark.parametrize('state', ['declined', 'expired', 'revoked', 'unknown'])
def test_a_dead_link_discards_the_key(http, state):
    cfg = FakeConfig({'acoustid.api_key': 'APP'})
    http.answers['user/request_link'] = LINK_OK
    aa.request_link(cfg)
    http.answers['user/link_status'] = _state(state)
    assert aa.check_link(cfg)['state'] == state
    assert cfg.data['acoustid.user_key'] == ''
    st = aa.status(cfg)
    assert st['state'] == state and not st['has_user_key']


def test_a_pasted_key_is_never_discarded_by_an_old_link(http):
    cfg = FakeConfig({'acoustid.api_key': 'APP'})
    http.answers['user/request_link'] = LINK_OK
    aa.request_link(cfg)
    cfg.set('acoustid.user_key', 'PASTED')
    http.answers['user/link_status'] = _state('declined')
    aa.check_link(cfg)
    assert cfg.data['acoustid.user_key'] == 'PASTED'
    assert aa.status(cfg)['source'] == 'pasted'
    assert not any(c.url.endswith('link_status') for c in http.calls)


def test_unlink_forgets_the_key():
    cfg = FakeConfig({'acoustid.user_key': 'K', 'acoustid.link': {'state': 'approved'}})
    aa.unlink(cfg)
    assert cfg.data['acoustid.user_key'] == '' and cfg.data['acoustid.link'] == {}


def test_status_says_when_lookups_stop(monkeypatch):
    monkeypatch.setattr(aa, 'USER_KEY_REQUIRED_FROM', date(2027, 1, 1))
    cfg = FakeConfig()
    before = aa.status(cfg, today=date(2026, 12, 31))
    assert not before['needs_link'] and before['required_from'] == '2027-01-01'
    after = aa.status(cfg, today=date(2027, 1, 1))
    assert after['needs_link'] and after['message'] == aa.NEEDS_LINK_MESSAGE
    cfg.set('acoustid.user_key', 'K')
    assert not aa.status(cfg, today=date(2027, 1, 1))['needs_link']


# ── lookups carry the user key ──

def _fake_acoustid(monkeypatch, *, match=None):
    fake = types.ModuleType("acoustid")
    for name in ('WebServiceError', 'NoBackendError', 'FingerprintGenerationError'):
        setattr(fake, name, type(name, (Exception,), {}))
    fake.parse_lookup_result = MagicMock()
    fake.fingerprint_file = lambda path: (187.4, 'AQADtFP')
    fake.match = match or MagicMock(side_effect=AssertionError('pyacoustid cannot send a user key'))
    monkeypatch.setitem(sys.modules, "acoustid", fake)
    monkeypatch.setattr(acc, "ACOUSTID_AVAILABLE", True)
    return fake


def _client(monkeypatch, cfg):
    monkeypatch.setattr(acc.config_manager, 'get', lambda k, d=None: cfg.get(k, d))
    return AcoustIDClient()


OK_LOOKUP = {"status": "ok", "results": [{"score": 0.97, "recordings": [
    {"id": "mbid-1", "title": "Song", "duration": 187, "artists": [{"name": "Band"}]}]}]}


def test_every_lookup_sends_the_user_key_with_the_app_key(tmp_path, monkeypatch, http):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    _fake_acoustid(monkeypatch)
    c = _client(monkeypatch, {'acoustid.api_key': 'APP', 'acoustid.user_key': 'USERKEY'})
    http.answers['lookup'] = OK_LOOKUP
    res = c.lookup_with_status(str(f))
    assert res['status'] == 'ok' and res['recordings'][0]['mbid'] == 'mbid-1'
    assert res['recordings'][0]['duration'] == 187
    call = http.calls[0]
    assert call.method == 'POST' and call.url.endswith('/v2/lookup')
    assert call.headers == {'Acoustid-User-Key': 'USERKEY'}
    assert call.form['client'] == 'APP'
    assert call.form['fingerprint'] == 'AQADtFP' and call.form['duration'] == 187
    assert call.form['meta'] == 'recordings'


def test_without_a_user_key_before_the_cutoff_the_old_path_still_works(tmp_path, monkeypatch, http):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    fake = _fake_acoustid(monkeypatch, match=MagicMock(return_value=OK_LOOKUP))
    c = _client(monkeypatch, {'acoustid.api_key': 'APP'})
    assert c.lookup_with_status(str(f))['status'] == 'ok'
    assert fake.match.call_args[0][0] == 'APP'
    assert http.calls == []


def test_error_21_says_link_your_account(tmp_path, monkeypatch, http):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    _fake_acoustid(monkeypatch, match=MagicMock(return_value={
        "status": "error", "error": {"code": 21, "message": "user key required"}}))
    c = _client(monkeypatch, {'acoustid.api_key': 'APP'})
    res = c.lookup_with_status(str(f))
    assert res['status'] == 'error' and res['needs_user_key'] is True
    assert res['invalid_key'] is False
    assert res['error'] == aa.NEEDS_LINK_MESSAGE


def test_a_linked_key_not_approved_yet_points_at_the_link(tmp_path, monkeypatch, http):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    _fake_acoustid(monkeypatch)
    cfg = {'acoustid.api_key': 'APP', 'acoustid.user_key': 'LK',
           'acoustid.link': {'state': 'pending', 'key_tag': aa.key_tag('LK')}}
    c = _client(monkeypatch, cfg)
    http.answers['lookup'] = {"status": "error", "error": {"code": 4, "message": "invalid API key"}}
    res = c.lookup_with_status(str(f))
    assert res['needs_user_key'] is True and res['invalid_key'] is False
    assert res['error'] == aa.LINK_PENDING_MESSAGE


def test_after_the_cutoff_no_user_key_means_no_lookup(tmp_path, monkeypatch, http):
    monkeypatch.setattr(aa, 'USER_KEY_REQUIRED_FROM', date(2000, 1, 1))
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    fake = _fake_acoustid(monkeypatch)
    c = _client(monkeypatch, {'acoustid.api_key': 'APP', 'acoustid.enabled': True})
    monkeypatch.setattr(c, '_check_fingerprint_available', lambda: True)
    assert c.is_available() == (False, aa.NEEDS_LINK_MESSAGE)
    assert c.can_fingerprint() == (False, aa.NEEDS_LINK_MESSAGE)
    res = c.lookup_with_status(str(f))
    assert res['status'] == 'unavailable' and res['needs_user_key'] is True
    fake.match.assert_not_called()
    assert http.calls == []


def test_key_test_reports_error_21_as_link_your_account(monkeypatch, http):
    c = _client(monkeypatch, {'acoustid.api_key': 'APP', 'acoustid.user_key': 'U'})
    http.answers['lookup'] = {"status": "error", "error": {"code": 21, "message": "user key required"}}
    assert c.test_api_key() == (False, aa.NEEDS_LINK_MESSAGE)
    assert http.calls[0].headers == {'Acoustid-User-Key': 'U'}


def test_verification_and_scan_stop_on_error_21(tmp_path, monkeypatch):
    from core.acoustid_verification import AcoustIDVerification, VerificationResult
    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob

    v = AcoustIDVerification.__new__(AcoustIDVerification)
    v.acoustid_client = SimpleNamespace(
        is_available=lambda: (True, ''), can_fingerprint=lambda: (True, ''),
        lookup_with_status=lambda p: {'status': 'error', 'needs_user_key': True,
                                      'invalid_key': False, 'error': aa.NEEDS_LINK_MESSAGE})
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    ctx = {}
    verdict, msg = v.verify_audio_file(str(f), 'Song', 'Band', context=ctx)
    assert verdict == VerificationResult.ERROR and msg == aa.NEEDS_LINK_MESSAGE
    assert ctx.get('_acoustid_needs_user_key') is True

    # the scan stops at the first file: the rest would fail the same way
    f2 = tmp_path / "b.flac"
    f2.write_bytes(b"x")
    job = AcoustIDScannerJob()
    monkeypatch.setattr("core.repair_jobs.acoustid_scanner.hand_tagged_path_keys", lambda db: set())
    monkeypatch.setattr(job, "_load_db_tracks", lambda c: {
        "1": {"file_path": str(f), "title": "Song", "artist": "Band"},
        "2": {"file_path": str(f2), "title": "Song 2", "artist": "Band"}})
    monkeypatch.setattr(job, "_resolve_path", lambda path, c: path)
    monkeypatch.setattr(job, "_expected_artist", lambda fpath, expected, fname: 'Band')
    seen = []
    real_verify = AcoustIDVerification.verify_audio_file

    def counted(self, path, *a, **k):
        seen.append(path)
        return real_verify(self, path, *a, **k)

    monkeypatch.setattr(AcoustIDVerification, 'verify_audio_file', counted)
    monkeypatch.setattr(AcoustIDVerification, '__init__', lambda self: setattr(
        self, 'acoustid_client', v.acoustid_client))
    report = MagicMock()
    scan_ctx = SimpleNamespace(
        db=SimpleNamespace(), transfer_folder=str(tmp_path), manual=True,
        config_manager=SimpleNamespace(get=lambda k, d=None: d, set=lambda *a, **k: None),
        acoustid_client=SimpleNamespace(**vars(v.acoustid_client), test_api_key=lambda: (True, 'ok')),
        report_progress=report, update_progress=MagicMock(), check_stop=lambda: False,
        wait_if_paused=lambda: False, sleep_or_stop=lambda *a, **k: False)
    job.scan(scan_ctx)
    assert len(seen) == 1
    assert any('link your AcoustID account' in str(c) for c in report.call_args_list)


def test_scan_preflight_names_the_link_not_a_bad_key():
    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
    report = MagicMock()
    ctx = SimpleNamespace(
        db=SimpleNamespace(), transfer_folder="/music", manual=True,
        config_manager=SimpleNamespace(get=lambda k, d=None: d, set=lambda *a, **k: None),
        acoustid_client=SimpleNamespace(is_available=lambda: (True, 'ok'),
                                        test_api_key=lambda: (False, aa.NEEDS_LINK_MESSAGE)),
        report_progress=report, update_progress=MagicMock(), check_stop=lambda: False,
        wait_if_paused=lambda: False, sleep_or_stop=lambda *a, **k: False)
    assert AcoustIDScannerJob().scan(ctx).errors == 1
    report.assert_called_with(log_line=f'AcoustID scan not started: {aa.NEEDS_LINK_MESSAGE}',
                              log_type='error')


# ── the key is a secret ──

def test_user_key_is_encrypted_masked_and_kept_on_an_empty_save():
    from core.settings import ConfigManager
    assert 'acoustid.user_key' in ConfigManager._SENSITIVE_PATHS
    cm = ConfigManager.__new__(ConfigManager)
    cm.config_data = {'acoustid': {'user_key': 'REAL', 'api_key': ''}}
    cm._save_config = lambda: None
    assert cm.redacted_config()['acoustid']['user_key'] == ConfigManager.REDACTED_SENTINEL
    cm.set('acoustid.user_key', '')
    cm.set('acoustid.user_key', ConfigManager.REDACTED_SENTINEL)
    assert cm.config_data['acoustid']['user_key'] == 'REAL'
    cm.clear_secret('acoustid.user_key')
    assert cm.config_data['acoustid']['user_key'] == ''


def test_public_api_redacts_the_user_key():
    from api.settings import _redact_sensitive
    out = _redact_sensitive({'acoustid': {'user_key': 'REAL', 'enabled': True}})
    assert out['acoustid']['user_key'] == '***REDACTED***'
    assert out['acoustid']['enabled'] is True


# ── routes ──

@pytest.fixture
def app(monkeypatch, http):
    from flask import Flask
    from api import acoustid_routes
    cfg = FakeConfig({'acoustid.api_key': 'APP'})
    acoustid_routes.configure(config_manager_=cfg)
    app = Flask(__name__)
    app.register_blueprint(acoustid_routes.create_blueprint())
    app.cfg = cfg
    return app


def test_routes_are_admin_only(app, monkeypatch):
    from core import profile_context
    from core.permissions import declared_access
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith('/api/acoustid'):
            assert declared_access(app.view_functions[rule.endpoint]) == ('admin', None), rule.rule
    monkeypatch.setattr(profile_context, 'is_admin_request', lambda: False)
    c = app.test_client()
    assert c.get('/api/acoustid/account').status_code == 403
    assert c.post('/api/acoustid/link', json={}).status_code == 403
    assert c.post('/api/acoustid/unlink').status_code == 403
    assert 'acoustid.user_key' not in app.cfg.data


def test_link_routes_round_trip_without_ever_sending_the_key(app, monkeypatch, http):
    from core import profile_context
    monkeypatch.setattr(profile_context, 'is_admin_request', lambda: True)
    c = app.test_client()
    http.answers['user/request_link'] = LINK_OK
    r = c.post('/api/acoustid/link', json={'return_url': 'http://nas:8008/settings?open=acoustid'})
    body = r.get_json()
    assert r.status_code == 200 and body['state'] == 'pending' and body['code'] == 'WT14-9E3S'
    assert http.calls[0].form['return_url'] == 'http://nas:8008/settings?open=acoustid'
    assert http.calls[0].form['device'].startswith('SoulSync')
    http.answers['user/link_status'] = _state('approved')
    r = c.get('/api/acoustid/account?check=1')
    assert r.get_json()['state'] == 'approved'
    r = c.post('/api/acoustid/unlink')
    assert r.get_json()['has_user_key'] is False
    for resp_text in (str(body), r.get_data(as_text=True)):
        assert 'UzBdV3X80J' not in resp_text
