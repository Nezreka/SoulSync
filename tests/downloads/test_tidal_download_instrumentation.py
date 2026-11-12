"""Diagnostics for the Tidal DOWNLOAD path (search + trackManifests), which —
unlike the enrichment worker — is NOT behind core.tidal_client's global rate
limiter and can burst a batch straight into Tidal's anti-bot (401/403 + captcha,
"bot-like behavior") that deauths the download session. These test the request-
rate meter + pushback classifier/logger that stamp each pushback with the rate
that preceded it, so a user's logs show whether we burst (our bug) or got
flagged at low volume (their IP/account). Instrumentation only — no throttling.
"""
import core.tidal_download_client as tdc


import pytest


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    # the meter's tests record requests back to back; the pacing has its own
    # tests below and would only add seconds here
    monkeypatch.setattr(tdc, '_download_request_gap', lambda: 0.0)
    with tdc._dl_req_lock:
        tdc._dl_next_slot[0] = 0.0


def _reset_meter():
    with tdc._dl_req_lock:
        tdc._dl_req_times.clear()


def test_rate_meter_counts_recent_requests():
    _reset_meter()
    for _ in range(5):
        tdc._record_download_request()
    assert tdc._download_request_rate(10.0) == 5
    assert tdc._download_request_rate(60.0) == 5


def test_rate_meter_excludes_requests_outside_window(monkeypatch):
    _reset_meter()
    clock = {'t': 1000.0}
    monkeypatch.setattr(tdc.time, 'time', lambda: clock['t'])

    # 3 requests at t=1000
    for _ in range(3):
        tdc._record_download_request()
    # 2 more 30s later
    clock['t'] = 1030.0
    for _ in range(2):
        tdc._record_download_request()

    # A 10s window now (t=1030) only sees the 2 recent ones; 60s sees all 5.
    assert tdc._download_request_rate(10.0) == 2
    assert tdc._download_request_rate(60.0) == 5


def test_classify_pushback_labels():
    assert tdc._classify_tidal_pushback('HTTP 429 Too Many Requests') == 'rate_limit'
    assert tdc._classify_tidal_pushback('Please complete the CAPTCHA') == 'bot_challenge'
    assert tdc._classify_tidal_pushback('detected bot-like behavior') == 'bot_challenge'
    assert tdc._classify_tidal_pushback('401 Unauthorized') == 'deauth'
    assert tdc._classify_tidal_pushback('403 Forbidden') == 'deauth'
    assert tdc._classify_tidal_pushback('200 OK, here are your tracks') is None
    assert tdc._classify_tidal_pushback('') is None


def test_pushback_logger_records_event_with_rate_snapshot(monkeypatch):
    _reset_meter()
    for _ in range(7):
        tdc._record_download_request()

    events = []
    from core.api_call_tracker import api_call_tracker
    monkeypatch.setattr(
        api_call_tracker, 'record_event',
        lambda service, event_type, endpoint='', detail='', duration=0:
            events.append((service, event_type, endpoint, detail)),
    )

    tdc._log_tidal_download_pushback('manifest', status=403, body='bot-like behavior detected')

    assert len(events) == 1
    service, event_type, endpoint, detail = events[0]
    assert service == 'tidal'
    assert event_type == 'bot_challenge'          # body wins over status
    assert endpoint == 'download:manifest'
    assert '/10s' in detail and 'status=403' in detail


def test_pushback_logger_never_raises(monkeypatch):
    # Even if the tracker blows up, the download path must not be disturbed.
    from core.api_call_tracker import api_call_tracker

    def _boom(*a, **k):
        raise RuntimeError("tracker down")

    monkeypatch.setattr(api_call_tracker, 'record_event', _boom)
    # Should swallow and return None, not propagate.
    assert tdc._log_tidal_download_pushback('search', exc=Exception('429')) is None


# ── #2069: requests are spaced, and an entitlement refusal is not a deauth ──
# Downloads were already one at a time per source. Nothing spaced the requests,
# so a serial queue could still send five in ten seconds: one track asks for a
# manifest per quality as it steps down the chain.

def test_download_requests_are_spaced(monkeypatch):
    monkeypatch.setattr(tdc, '_download_request_gap', lambda: 1.0)
    clock = {'t': 100.0}
    slept = []
    monkeypatch.setattr(tdc.time, 'monotonic', lambda: clock['t'])
    monkeypatch.setattr(tdc.time, 'sleep', lambda s: (slept.append(s), clock.__setitem__('t', clock['t'] + s)))
    _reset_meter()

    for _ in range(4):
        tdc._record_download_request()

    # the first goes straight out, each one after it waits its second
    assert slept == [1.0, 1.0, 1.0]
    assert tdc._download_request_rate(10.0) == 4


def test_two_requests_arriving_together_get_different_slots(monkeypatch):
    monkeypatch.setattr(tdc, '_download_request_gap', lambda: 2.0)
    monkeypatch.setattr(tdc.time, 'monotonic', lambda: 50.0)
    assert [tdc._reserve_download_slot() for _ in range(3)] == [0.0, 2.0, 4.0]


def test_a_gap_of_zero_turns_the_pacing_off(monkeypatch):
    monkeypatch.setattr(tdc, '_download_request_gap', lambda: 0.0)
    monkeypatch.setattr(tdc.time, 'monotonic', lambda: 50.0)
    assert [tdc._reserve_download_slot() for _ in range(3)] == [0.0, 0.0, 0.0]


def test_the_gap_setting_is_read_and_bounded(monkeypatch):
    monkeypatch.undo()          # the real reader, not the autouse stand-in
    values = {}
    monkeypatch.setattr(tdc.config_manager, 'get', lambda key, default=None: values.get(key, default))
    assert tdc._download_request_gap() == tdc.DEFAULT_DOWNLOAD_REQUEST_GAP_SECONDS
    values['tidal_download.min_request_gap_seconds'] = 0
    assert tdc._download_request_gap() == 0.0
    values['tidal_download.min_request_gap_seconds'] = 5000
    assert tdc._download_request_gap() == 60.0
    values['tidal_download.min_request_gap_seconds'] = 'soon'
    assert tdc._download_request_gap() == tdc.DEFAULT_DOWNLOAD_REQUEST_GAP_SECONDS


def test_client_not_entitled_is_not_called_a_deauth():
    body = ('403 {"errors":[{"status":"403","code":"CLIENT_NOT_ENTITLED",'
            '"detail":"Client cannot access this content"}]}')
    assert tdc._classify_tidal_pushback(body) == 'not_entitled'
    # a plain 403 with no such code is still the deauth it was
    assert tdc._classify_tidal_pushback('403 Forbidden') == 'deauth'


def test_an_entitlement_refusal_is_a_warning_about_the_track_not_pushback(monkeypatch, caplog):
    import logging
    events = []
    from core.api_call_tracker import api_call_tracker
    monkeypatch.setattr(api_call_tracker, 'record_event', lambda *a, **k: events.append((a, k)))
    seen = []
    monkeypatch.setattr(tdc.logger, 'warning', lambda msg, *a, **k: seen.append(('warning', msg % a)))
    monkeypatch.setattr(tdc.logger, 'error', lambda msg, *a, **k: seen.append(('error', msg % a)))

    tdc._log_tidal_download_pushback(
        'manifest', status=403,
        body='{"errors":[{"status":"403","code":"CLIENT_NOT_ENTITLED","detail":"Client cannot access this content"}]}')

    assert [level for level, _ in seen] == ['warning']
    assert 'Not a sign-in or rate problem' in seen[0][1] and 'PUSHBACK' not in seen[0][1]
    assert not events        # nothing filed as pushback
