"""Diagnostics for the Tidal DOWNLOAD path (search + trackManifests), which —
unlike the enrichment worker — is NOT behind core.tidal_client's global rate
limiter and can burst a batch straight into Tidal's anti-bot (401/403 + captcha,
"bot-like behavior") that deauths the download session. These test the request-
rate meter + pushback classifier/logger that stamp each pushback with the rate
that preceded it, so a user's logs show whether we burst (our bug) or got
flagged at low volume (their IP/account). Instrumentation only — no throttling.
"""
import core.tidal_download_client as tdc


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
