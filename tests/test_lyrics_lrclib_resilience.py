"""#1879: LRCLib transient failures must not read as "no lyrics".

core/lyrics_client.py used to collapse every LRCLib failure — a 404
(definitively absent), a 429/503 (transient) and a timeout — into None,
so the Lyrics Filler scan recorded rate-limited tracks as lyric-less and
the repair worker told users the lyrics were "no longer available".

The contract is now tri-state (found / absent / unknown), transient
failures get bounded retries honouring Retry-After, the search fallback
never fires after a transient exact-match failure, every call is paced
by core.lrclib_throttle, and the scan stops early after a run of
consecutive unknowns instead of burning the whole library.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from core import lrclib_throttle
from core.lyrics_client import (
    LyricsClient,
    LyricsFetchResult,
    STATUS_ABSENT,
    STATUS_FOUND,
    STATUS_UNKNOWN,
    classify_lyrics_error,
)


class FakeHTTPError(Exception):
    """Duck-typed like lrclibapi's APIError family: status_code + headers."""

    def __init__(self, status_code, headers=None):
        super().__init__(f"{status_code} error")
        self.status_code = status_code
        self.headers = headers or {}


_REAL_WAIT_FOR_SLOT = lrclib_throttle.wait_for_slot


@pytest.fixture(autouse=True)
def _no_throttle_waits(monkeypatch):
    lrclib_throttle._reset_for_tests()
    monkeypatch.setattr(lrclib_throttle, "wait_for_slot", lambda **k: True)
    yield
    lrclib_throttle._reset_for_tests()


def _client(api):
    c = LyricsClient.__new__(LyricsClient)
    c.api = api
    c.max_attempts = 3
    c.sleeps = []
    c._sleep = lambda s: c.sleeps.append(s)
    return c


def _lyrics(synced="[00:01]hi", plain=None):
    return SimpleNamespace(synced_lyrics=synced, plain_lyrics=plain)


# ── classification ───────────────────────────────────────────────────────

def test_classify_404_is_absent():
    out = classify_lyrics_error(FakeHTTPError(404))
    assert out.status == STATUS_ABSENT and out.error_kind == 'not_found'


def test_classify_429_is_transient_with_retry_after():
    out = classify_lyrics_error(FakeHTTPError(429, {'Retry-After': '7'}))
    assert out.status == STATUS_UNKNOWN and out.error_kind == 'rate_limited'
    assert out.retry_after == 7.0
    assert 'rate-limited' in out.reason and '429' in out.reason


def test_classify_503_is_transient_server():
    out = classify_lyrics_error(FakeHTTPError(503, {'Retry-After': '1'}))
    assert out.status == STATUS_UNKNOWN and out.error_kind == 'server'
    assert out.retry_after == 1.0


def test_classify_other_4xx_is_unknown_not_absent():
    out = classify_lyrics_error(FakeHTTPError(400))
    assert out.status == STATUS_UNKNOWN and out.error_kind == 'permanent'


def test_classify_retry_after_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
    out = classify_lyrics_error(FakeHTTPError(503, {'Retry-After': future}))
    assert out.error_kind == 'server'
    assert out.retry_after is not None and 0 < out.retry_after <= 30


def test_classify_timeout_and_connection_are_transient():
    assert classify_lyrics_error(requests.exceptions.ReadTimeout('x')).error_kind == 'timeout'
    assert classify_lyrics_error(requests.exceptions.ConnectionError('x')).error_kind == 'network'


def test_classify_real_lrclib_exceptions():
    lrclib_exceptions = pytest.importorskip("lrclib.exceptions")

    class _Resp:
        status_code = 429
        reason = 'Too Many Requests'
        url = 'https://lrclib.net/api/get'
        text = ''
        headers = {'Retry-After': '3'}

    out = classify_lyrics_error(lrclib_exceptions.RateLimitError(_Resp()))
    assert out.error_kind == 'rate_limited' and out.retry_after == 3.0
    _Resp.status_code = 404
    out = classify_lyrics_error(lrclib_exceptions.NotFoundError(_Resp()))
    assert out.status == STATUS_ABSENT


# ── fetch strategy: retries, no fallback on transient ────────────────────

def test_404_exact_falls_back_to_search_and_absent_when_empty():
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(404)
    api.search_lyrics.return_value = []
    c = _client(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is False
    assert api.get_lyrics.call_count == 1          # 404 is never retried
    assert api.search_lyrics.call_count == 1


def test_429_exact_retries_bounded_and_never_searches():
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(429, {'Retry-After': '7'})
    c = _client(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is None   # unknown, NOT False
    assert api.get_lyrics.call_count == 3          # bounded retries
    api.search_lyrics.assert_not_called()          # no fallback while rate-limited
    # Retry-After honoured exactly (no jitter on an explicit instruction)
    assert c.sleeps == [7.0, 7.0]


def test_huge_retry_after_gives_up_instead_of_parking_the_thread():
    # Retry-After: 60 → single-attempt delay caps at 30s, which exceeds
    # the 20s per-call wait budget, so the caller gets the transient
    # outcome immediately instead of parking a gthread for a minute.
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(429, {'Retry-After': '60'})
    c = _client(api)
    result = c.lookup_remote_lyrics("Song", "Artist", "Album", 200)
    assert result.status == STATUS_UNKNOWN and result.error_kind == 'rate_limited'
    assert api.get_lyrics.call_count == 1
    assert c.sleeps == []


def test_503_search_only_path_is_unknown():
    api = MagicMock()
    api.search_lyrics.side_effect = FakeHTTPError(503, {'Retry-After': '1'})
    c = _client(api)
    result = c.lookup_remote_lyrics("Song", "Artist")
    assert result.status == STATUS_UNKNOWN and result.error_kind == 'server'
    assert api.search_lyrics.call_count == 3


def test_timeout_is_unknown_and_never_searches():
    api = MagicMock()
    api.get_lyrics.side_effect = requests.exceptions.ReadTimeout('slow')
    c = _client(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is None
    api.search_lyrics.assert_not_called()


def test_other_4xx_on_exact_falls_back_to_search():
    # A 400 from /get (e.g. an empty artist name — lrclibapi raises
    # IncorrectPublishTokenError for any 400) is a permanent error for
    # THAT query, but search takes different parameters and may still
    # hit. The old code always fell through; only transient failures
    # block the fallback.
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(400)
    api.search_lyrics.return_value = [_lyrics()]
    c = _client(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is True
    assert api.get_lyrics.call_count == 1          # permanent: never retried
    assert api.search_lyrics.call_count == 1


def test_other_4xx_on_both_strategies_is_unknown():
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(400)
    api.search_lyrics.side_effect = FakeHTTPError(400)
    c = _client(api)
    result = c.lookup_remote_lyrics("Song", "Artist", "Album", 200)
    assert result.status == STATUS_UNKNOWN and result.error_kind == 'permanent'
    assert api.get_lyrics.call_count == 1 and api.search_lyrics.call_count == 1


def test_transient_then_success_returns_found():
    api = MagicMock()
    api.get_lyrics.side_effect = [FakeHTTPError(429, {'Retry-After': '1'}), _lyrics()]
    c = _client(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is True
    assert api.get_lyrics.call_count == 2


def test_found_record_without_lyrics_content_is_absent():
    api = MagicMock()
    api.get_lyrics.return_value = _lyrics(synced=None, plain=None)
    c = _client(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is False


def test_no_api_is_unknown_not_absent():
    c = _client(None)
    assert c.has_remote_lyrics("Song", "Artist") is None
    assert c.lookup_remote_lyrics("Song", "Artist").error_kind == 'unavailable'


# ── create_lrc_file_detailed ─────────────────────────────────────────────

def test_create_detailed_surfaces_rate_limit_reason(tmp_path):
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"x")
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(429, {'Retry-After': '2'})
    c = _client(api)
    ok, reason = c.create_lrc_file_detailed(str(audio), "Song", "Artist", "Album", 200)
    assert ok is False
    assert reason and 'rate-limited' in reason
    assert c.create_lrc_file(str(audio), "Song", "Artist", "Album", 200) is False


def test_create_detailed_absent_reason_is_none(tmp_path):
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"x")
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(404)
    api.search_lyrics.return_value = []
    c = _client(api)
    ok, reason = c.create_lrc_file_detailed(str(audio), "Song", "Artist", "Album", 200)
    assert ok is False and reason is None


# ── throttle ─────────────────────────────────────────────────────────────

def test_throttle_reservations_are_spaced():
    lrclib_throttle._reset_for_tests()
    first = lrclib_throttle.reserve_request_slot()
    second = lrclib_throttle.reserve_request_slot()
    assert second - first >= lrclib_throttle.DEFAULT_MIN_GAP_SECONDS - 0.05


def test_throttle_cooldown_pushes_next_slot(monkeypatch):
    lrclib_throttle._reset_for_tests()
    lrclib_throttle.note_rate_limited(60)
    import time as _time
    at = lrclib_throttle.reserve_request_slot()
    assert at - _time.monotonic() >= 50  # cooldown honoured (bounded, ~60s)
    assert lrclib_throttle.status()['cooldown_remaining'] > 0


def test_lrclib_throttle_settings_are_registered():
    # The pacing knobs must exist in core/settings.py defaults (and the
    # web_server persist list) — an unregistered key would silently read
    # as the fallback forever and "turn it down in Settings" would be a
    # lie. Under the test config (pure defaults) they read back as the
    # shipped defaults.
    assert lrclib_throttle._settings() == (
        lrclib_throttle.DEFAULT_MIN_GAP_SECONDS,
        lrclib_throttle.DEFAULT_MAX_PER_WINDOW)


def test_throttle_note_rate_limited_floor_cap_and_zero():
    lrclib_throttle._reset_for_tests()
    lrclib_throttle.note_rate_limited(None)          # no header → 30s default
    assert 25 <= lrclib_throttle.status()['cooldown_remaining'] <= 30
    lrclib_throttle._reset_for_tests()
    lrclib_throttle.note_rate_limited(0)             # explicit 0 is NOT "missing"
    assert 0 < lrclib_throttle.status()['cooldown_remaining'] <= 5   # floor only
    lrclib_throttle._reset_for_tests()
    lrclib_throttle.note_rate_limited(9999)          # absurd → capped at 300s
    assert 250 <= lrclib_throttle.status()['cooldown_remaining'] <= 300


def test_throttle_window_cap_defers_beyond_window(monkeypatch):
    monkeypatch.setattr(lrclib_throttle, "_settings", lambda: (0.0, 3))
    lrclib_throttle._reset_for_tests()
    slots = [lrclib_throttle.reserve_request_slot() for _ in range(7)]
    # 4th reservation must wait for the oldest to leave the 60s window,
    # and the cap applies per-window, not once: reservations past the
    # cap must not all collapse onto the same instant (the min_gap=0
    # burst the old _TIMES[0] formula produced).
    assert slots[3] - slots[0] >= 59.0
    assert slots[6] - slots[3] >= 59.0
    assert slots == sorted(slots)                   # list stays ordered


def test_busy_refusal_when_interactive_wait_exceeds_cooldown(monkeypatch):
    # Player-style call: a 60s cooldown is in effect and the caller will
    # only wait 0.05s → no request is fired at all, busy comes back.
    # (Uses the real wait_for_slot, captured before the autouse stub.)
    monkeypatch.setattr(lrclib_throttle, "wait_for_slot", _REAL_WAIT_FOR_SLOT)
    lrclib_throttle._reset_for_tests()
    lrclib_throttle.note_rate_limited(60)
    api = MagicMock()
    c = _client(api)
    result = c.lookup_remote_lyrics("Song", "Artist", max_wait_seconds=0.05)
    assert result.status == STATUS_UNKNOWN and result.error_kind == 'rate_limited'
    assert 'busy' in result.reason
    api.get_lyrics.assert_not_called()
    api.search_lyrics.assert_not_called()


def test_retry_budget_is_elapsed_based(monkeypatch):
    # Retry-After: 12 twice would need 24s of waiting: the second wait
    # would push elapsed past the 20s budget, so the call gives up after
    # 2 attempts instead of parking the thread. Fake clock: the client's
    # sleeper advances it; the throttle is stubbed by the autouse fixture.
    import time as _time_module
    clock = [1000.0]
    monkeypatch.setattr(_time_module, "monotonic", lambda: clock[0])
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(429, {'Retry-After': '12'})
    c = _client(api)
    c._sleep = lambda s: clock.__setitem__(0, clock[0] + s)
    result = c.lookup_remote_lyrics("Song", "Artist", "Album", 200)
    assert result.status == STATUS_UNKNOWN and result.error_kind == 'rate_limited'
    assert api.get_lyrics.call_count == 2
    api.search_lyrics.assert_not_called()


# ── scan: unknowns counted separately, abort after consecutive run ──────

class _DB:
    def __init__(self, rows):
        self._rows = rows

    def _get_connection(self):
        cur = MagicMock()
        cur.execute.return_value = None
        cur.fetchone.return_value = [len(self._rows)]
        cur.fetchall.return_value = self._rows
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn


def _ctx(db, findings):
    return SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda k, d=None: d),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )


def _scan_with_probe(tmp_path, monkeypatch, probe, n=7):
    from core.repair_jobs.missing_lyrics import MissingLyricsJob
    rows = []
    for i in range(n):
        f = tmp_path / f"t{i}.flac"
        f.write_bytes(b"x")
        rows.append((i + 1, f"Song{i}", "Artist", "Album", str(f), 200000))
    fake_client = SimpleNamespace(api=object(), check_remote_lyrics=probe)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)
    findings = []
    result = MissingLyricsJob().scan(_ctx(_DB(rows), findings))
    return result, findings


def test_scan_stops_early_after_consecutive_unknowns(tmp_path, monkeypatch):
    probe = lambda *a, **k: LyricsFetchResult(  # noqa: E731
        STATUS_UNKNOWN, reason='LRCLib rate-limited (429) — retry later',
        error_kind='rate_limited')
    result, findings = _scan_with_probe(tmp_path, monkeypatch, probe)
    assert findings == []
    assert result.findings_created == 0
    assert result.skipped == 0                       # unknowns are NOT skips
    assert result.stopped_early and 'LRCLib' in result.stopped_early
    assert result.scanned == 5                       # stopped at the threshold


def test_scan_unknown_run_resets_on_definitive_answer(tmp_path, monkeypatch):
    answers = iter([
        LyricsFetchResult(STATUS_UNKNOWN, reason='LRCLib server error (503) — retry later', error_kind='server'),
        LyricsFetchResult(STATUS_UNKNOWN, reason='LRCLib server error (503) — retry later', error_kind='server'),
        LyricsFetchResult(STATUS_FOUND, data=_lyrics()),
        LyricsFetchResult(STATUS_ABSENT),
        LyricsFetchResult(STATUS_UNKNOWN, reason='LRCLib server error (503) — retry later', error_kind='server'),
        LyricsFetchResult(STATUS_ABSENT),
        LyricsFetchResult(STATUS_ABSENT),
    ])
    probe = lambda *a, **k: next(answers)  # noqa: E731
    result, findings = _scan_with_probe(tmp_path, monkeypatch, probe)
    assert result.stopped_early == ''
    assert result.findings_created == 1
    assert result.skipped == 3                       # only the definitive absents


def test_scan_permanent_unknown_resets_the_transient_streak(tmp_path, monkeypatch):
    # "Consecutive" must be literally true: a permanent unknown means
    # LRCLib answered, so the transient streak restarts after it.
    transient = LyricsFetchResult(
        STATUS_UNKNOWN, reason='LRCLib rate-limited (429) — retry later',
        error_kind='rate_limited')
    permanent = LyricsFetchResult(
        STATUS_UNKNOWN, reason='LRCLib rejected the request (400)',
        error_kind='permanent')
    answers = iter([transient] * 4 + [permanent] + [transient] * 4 +
                   [LyricsFetchResult(STATUS_ABSENT)] * 2)
    probe = lambda *a, **k: next(answers)  # noqa: E731
    result, findings = _scan_with_probe(tmp_path, monkeypatch, probe, n=11)
    assert result.stopped_early == ''          # 4 + reset + 4 never reaches 5
    assert result.skipped == 2


def test_scan_permanent_unknowns_do_not_stop_the_scan(tmp_path, monkeypatch):
    # A permanent query error (400 for one album's bad metadata) is not
    # LRCLib failing: seven in a row must NOT stop the scan, and the
    # tracks are still not counted as definitive skips.
    probe = lambda *a, **k: LyricsFetchResult(  # noqa: E731
        STATUS_UNKNOWN, reason='LRCLib rejected the request (400)',
        error_kind='permanent')
    result, findings = _scan_with_probe(tmp_path, monkeypatch, probe)
    assert findings == []
    assert result.stopped_early == ''
    assert result.skipped == 0
    assert result.scanned == 7                       # every track probed


def test_scan_raising_probe_aborts_with_reason_visible(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('probe exploded')
    result, findings = _scan_with_probe(tmp_path, monkeypatch, boom)
    assert findings == []
    # The user-facing stopped_early names the error TYPE, not the raw
    # exception text (that lives in the log line).
    assert result.stopped_early and 'RuntimeError' in result.stopped_early
    assert 'probe exploded' not in result.stopped_early
    assert result.skipped == 0


def test_scan_check_returning_bare_bool_is_mapped(tmp_path, monkeypatch):
    # A check_remote_lyrics that returns a bare bool (not a result
    # object) must not be read as 'absent' for a truthy hit.
    probe = lambda title, *a, **k: title == "Song0"  # noqa: E731
    result, findings = _scan_with_probe(tmp_path, monkeypatch, probe, n=2)
    assert result.findings_created == 1
    assert result.skipped == 1


def test_scan_unrecognized_probe_status_is_unknown_not_absent(tmp_path, monkeypatch):
    probe = lambda *a, **k: SimpleNamespace(status='available', reason='')  # noqa: E731
    result, findings = _scan_with_probe(tmp_path, monkeypatch, probe)
    assert findings == []
    assert result.skipped == 0
    assert result.stopped_early and 'unrecognized probe status' in result.stopped_early


def test_scan_falls_back_to_bool_probe(tmp_path, monkeypatch):
    # Clients without check_remote_lyrics: True/False/None from
    # has_remote_lyrics maps to found/absent/unknown.
    from core.repair_jobs.missing_lyrics import MissingLyricsJob
    f1 = tmp_path / "a.flac"; f1.write_bytes(b"x")
    f2 = tmp_path / "b.flac"; f2.write_bytes(b"x")
    rows = [(1, "Song", "Artist", "Album", str(f1), 200000),
            (2, "Gone", "Artist", "Album", str(f2), 200000)]
    fake_client = SimpleNamespace(
        api=object(),
        has_remote_lyrics=lambda title, *a, **k: True if title == "Song" else None)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)
    findings = []
    result = MissingLyricsJob().scan(_ctx(_DB(rows), findings))
    assert result.findings_created == 1
    assert result.skipped == 0 and result.stopped_early == ''


# ── repair worker: real reason instead of "no longer available?" ─────────

def _worker(tmp_path, monkeypatch, fake_client):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w.transfer_folder = str(tmp_path)
    w._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)
    monkeypatch.setattr("core.repair_worker._resolve_file_path", lambda raw, *a, **k: raw)
    return w


def test_fix_missing_lyrics_surfaces_transient_reason(tmp_path, monkeypatch):
    audio = tmp_path / "song.flac"; audio.write_bytes(b"x")
    fake = SimpleNamespace(
        create_lrc_file_detailed=lambda *a, **k: (False, 'LRCLib rate-limited (429) — retry later'))
    w = _worker(tmp_path, monkeypatch, fake)
    res = w._fix_missing_lyrics("track", "1", None, {
        "file_path": str(audio), "track_title": "Song", "artist": "Artist",
        "album_title": "Album", "duration": 200})
    assert res["success"] is False
    assert res["error"] == 'LRCLib rate-limited (429) — retry later'


def test_fix_missing_lyrics_absent_keeps_old_message(tmp_path, monkeypatch):
    audio = tmp_path / "song.flac"; audio.write_bytes(b"x")
    fake = SimpleNamespace(create_lrc_file_detailed=lambda *a, **k: (False, None))
    w = _worker(tmp_path, monkeypatch, fake)
    res = w._fix_missing_lyrics("track", "1", None, {
        "file_path": str(audio), "track_title": "Song", "artist": "Artist",
        "album_title": "Album", "duration": 200})
    assert res["success"] is False
    assert res["error"] == 'Could not fetch lyrics (no longer available?)'


# ── player endpoint: transient reason surfaced, not "no lyrics found" ────

def _player_post(monkeypatch, fake_client):
    from flask import Flask
    import api.stats as stats
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)
    app = Flask(__name__)
    app.register_blueprint(stats.bp)
    return app.test_client().post("/api/lyrics/fetch", json={
        "title": "Song", "artist": "Artist", "album": "Album", "duration": 200})


def test_player_endpoint_surfaces_rate_limit(monkeypatch):
    seen = {}

    def _lookup(**k):
        seen.update(k)
        return LyricsFetchResult(
            STATUS_UNKNOWN, reason='LRCLib rate-limited (429) — retry later',
            error_kind='rate_limited')

    fake = SimpleNamespace(api=object(), lookup_remote_lyrics=_lookup)
    resp = _player_post(monkeypatch, fake)
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"] == 'LRCLib rate-limited (429) — retry later'
    assert body["retryable"] is True
    # The endpoint must forward its interactive wait cap — if this
    # argument is dropped tomorrow, the player can park on a cooldown.
    assert seen.get('max_wait_seconds') == 10


def test_player_endpoint_permanent_error_is_not_retryable(monkeypatch):
    fake = SimpleNamespace(
        api=object(),
        lookup_remote_lyrics=lambda **k: LyricsFetchResult(
            STATUS_UNKNOWN, reason='LRCLib rejected the request (400)',
            error_kind='permanent'))
    resp = _player_post(monkeypatch, fake)
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"] == 'LRCLib rejected the request (400)'
    assert body["retryable"] is False


def test_player_endpoint_absent_still_no_lyrics_found(monkeypatch):
    fake = SimpleNamespace(
        api=object(),
        lookup_remote_lyrics=lambda **k: LyricsFetchResult(STATUS_ABSENT))
    resp = _player_post(monkeypatch, fake)
    body = resp.get_json()
    assert body["success"] is False and body["error"] == 'no lyrics found'


def test_player_endpoint_found_returns_lyrics(monkeypatch):
    fake = SimpleNamespace(
        api=object(),
        lookup_remote_lyrics=lambda **k: LyricsFetchResult(STATUS_FOUND, data=_lyrics()))
    resp = _player_post(monkeypatch, fake)
    body = resp.get_json()
    assert body["success"] is True and body["synced"] == "[00:01]hi"
    assert body["source"] == 'lrclib'


def test_player_endpoint_real_client_429_on_search(monkeypatch):
    # End-to-end through the REAL client classification: the exact match
    # 404s (definitive), the search fallback then gets a 429, and the
    # endpoint must surface the rate-limit reason as retryable — not
    # 'no lyrics found'.
    api = MagicMock()
    api.get_lyrics.side_effect = FakeHTTPError(404)
    api.search_lyrics.side_effect = FakeHTTPError(429, {'Retry-After': '1'})
    client = _client(api)
    resp = _player_post(monkeypatch, client)
    body = resp.get_json()
    assert body["success"] is False
    assert 'rate-limited' in body["error"]
    assert body["retryable"] is True
    assert api.search_lyrics.call_count == 3      # bounded retries happened


# ── bulk Fix All: LRCLib circuit breaker ─────────────────────────────────

def _bulk_worker(types, fix):
    import threading

    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w._bulk_fix_stop_event = threading.Event()
    w.should_stop = False
    w._bulk_fix_thread = None
    w._bulk_fix_state = {
        'running': True, 'total': 0, 'done': 0, 'fixed': 0, 'failed': 0,
        'stopped': False, 'job_id': None, 'finding_type': None,
        'safe_only': False, 'errors': [], 'error': None, 'note': None,
        'per_type': {},
    }
    w._types_for_findings = lambda ids: types
    w.fix_finding = fix
    return w


_TRANSIENT = {'success': False, 'error': 'LRCLib rate-limited (429) — retry later'}


def test_bulk_fix_stops_after_consecutive_lrclib_transients(monkeypatch):
    calls = []
    w = _bulk_worker(
                     {i: 'missing_lyrics' for i in range(1, 9)},
                     lambda fid, fix_action=None: calls.append(fid) or dict(_TRANSIENT))
    w._bulk_fix_state['total'] = 8
    w._run_bulk_fix(list(range(1, 9)))
    state = w._bulk_fix_state
    assert calls == [1, 2, 3, 4, 5]                    # stopped at the breaker
    assert state['done'] == 5 and state['failed'] == 5 and state['fixed'] == 0
    assert state['stopped'] is True
    assert state['error'] and 'LRCLib stopped answering' in state['error']
    assert state['per_type']['missing_lyrics'] == {'fixed': 0, 'failed': 5}


def test_bulk_fix_breaker_ignores_local_failures_and_resets_on_definitive():
    # A purely local failure (stale file) neither increments nor resets
    # the streak; a definitive LRCLib answer resets it.
    local = {'success': False, 'error': 'File not found on disk: /music/gone.flac'}
    outcomes = iter([dict(_TRANSIENT), dict(_TRANSIENT), local,
                     dict(_TRANSIENT), dict(_TRANSIENT), dict(_TRANSIENT)])
    w = _bulk_worker({i: 'missing_lyrics' for i in range(1, 8)},
                     lambda fid, fix_action=None: next(outcomes))
    w._bulk_fix_state['total'] = 7
    w._run_bulk_fix(list(range(1, 8)))
    state = w._bulk_fix_state
    assert state['done'] == 6 and state['stopped'] is True   # tripped on the 5th transient
    assert len(state['errors']) == 6

    for definitive_error in (
            'Could not fetch lyrics (no longer available?)',
            'LRCLib rejected the request (400)'):
        definitive = {'success': False, 'error': definitive_error}
        outcomes2 = iter([dict(_TRANSIENT)] * 4 + [definitive] +
                         [dict(_TRANSIENT)] * 4 + [{'success': True}])
        w2 = _bulk_worker({i: 'missing_lyrics' for i in range(1, 11)},
                          lambda fid, fix_action=None: next(outcomes2))
        w2._bulk_fix_state['total'] = 10
        w2._run_bulk_fix(list(range(1, 11)))
        assert w2._bulk_fix_state['done'] == 10, definitive_error
        assert w2._bulk_fix_state['stopped'] is False, definitive_error


def test_bulk_fix_breaker_mixed_run_skips_only_lyrics():
    # Lyrics trip the breaker; the genre findings queued behind them
    # still run, and the remaining lyrics findings are skipped (left
    # pending, counted in done but neither fixed nor failed).
    types = {i: 'missing_lyrics' for i in range(1, 8)}
    types.update({8: 'genre_cleanup', 9: 'genre_cleanup'})
    attempted = []

    def fix(fid, fix_action=None):
        attempted.append(fid)
        if types[fid] == 'missing_lyrics':
            return dict(_TRANSIENT)
        return {'success': True}

    w = _bulk_worker(types, fix)
    w._bulk_fix_state['total'] = 9
    w._run_bulk_fix(list(range(1, 10)))
    state = w._bulk_fix_state
    assert attempted == [1, 2, 3, 4, 5, 8, 9]        # 6, 7 skipped
    assert state['done'] == 9
    assert state['fixed'] == 2 and state['failed'] == 5
    assert state['stopped'] is False and state['error'] is None
    assert state['note'] and 'LRCLib stopped answering' in state['note']
    assert '2 remaining lyrics finding(s) skipped' in state['note']
    assert state['per_type']['missing_lyrics'] == {'fixed': 0, 'failed': 5}
    assert state['per_type']['genre_cleanup'] == {'fixed': 2, 'failed': 0}


def test_bulk_fix_breaker_trips_with_only_other_types_remaining():
    # The trip lands on the last lyrics finding; only genre findings
    # remain. The note must not claim lyrics findings were skipped.
    types = {i: 'missing_lyrics' for i in range(1, 6)}
    types.update({6: 'genre_cleanup', 7: 'genre_cleanup'})

    def fix(fid, fix_action=None):
        return dict(_TRANSIENT) if types[fid] == 'missing_lyrics' else {'success': True}

    w = _bulk_worker(types, fix)
    w._bulk_fix_state['total'] = 7
    w._run_bulk_fix(list(range(1, 8)))
    state = w._bulk_fix_state
    assert state['done'] == 7 and state['fixed'] == 2 and state['failed'] == 5
    assert state['stopped'] is False
    assert state['note'] and 'LRCLib stopped answering' in state['note']
    assert 'skipped' not in state['note']


def test_bulk_fix_breaker_trip_on_final_finding_is_not_stopped_early():
    w = _bulk_worker({i: 'missing_lyrics' for i in range(1, 6)},
                     lambda fid, fix_action=None: dict(_TRANSIENT))
    w._bulk_fix_state['total'] = 5
    w._run_bulk_fix(list(range(1, 6)))
    state = w._bulk_fix_state
    assert state['done'] == 5 and state['failed'] == 5
    assert state['stopped'] is False and state['error'] is None


def test_bulk_fix_breaker_resets_on_success(monkeypatch):
    outcomes = iter([dict(_TRANSIENT)] * 4 + [{'success': True}] +
                    [dict(_TRANSIENT)] * 4 + [{'success': True}])
    w = _bulk_worker(
                     {i: 'missing_lyrics' for i in range(1, 11)},
                     lambda fid, fix_action=None: next(outcomes))
    w._bulk_fix_state['total'] = 10
    w._run_bulk_fix(list(range(1, 11)))
    state = w._bulk_fix_state
    assert state['done'] == 10 and state['stopped'] is False
    assert state['fixed'] == 2 and state['failed'] == 8


def test_bulk_fix_breaker_ignores_other_types(monkeypatch):
    w = _bulk_worker(
                     {i: 'genre_cleanup' for i in range(1, 8)},
                     lambda fid, fix_action=None: dict(_TRANSIENT))
    w._bulk_fix_state['total'] = 7
    w._run_bulk_fix(list(range(1, 8)))
    assert w._bulk_fix_state['done'] == 7 and w._bulk_fix_state['stopped'] is False


def test_permanent_exact_plus_empty_search_stays_unknown():
    # Exact query rejected (400, e.g. empty artist) and search — same
    # parameters — returns nothing: that is NOT proof of absence.
    class _Api:
        def get_lyrics(self, **kw):
            raise FakeHTTPError(400)

        def search_lyrics(self, **kw):
            return []

    client = _client(_Api())
    result = client.lookup_remote_lyrics('Song', '', album_name='Album',
                                         duration_seconds=200)
    assert result.status == STATUS_UNKNOWN and result.error_kind == 'permanent'
    assert client.has_remote_lyrics('Song', '', 'Album', 200) is None

    class _Api404:
        def get_lyrics(self, **kw):
            raise FakeHTTPError(404)

        def search_lyrics(self, **kw):
            return []

    client2 = _client(_Api404())
    result2 = client2.lookup_remote_lyrics('Song', 'Artist', album_name='Album',
                                           duration_seconds=200)
    assert result2.status == STATUS_ABSENT       # 404 + empty search IS absent


def test_create_detailed_catch_all_reason_is_generic(monkeypatch):
    client = _client(SimpleNamespace())
    monkeypatch.setattr(client, 'lookup_remote_lyrics',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('/secret/path exploded')))
    ok, reason = client.create_lrc_file_detailed('/tmp/x.flac', 'Song', 'Artist')
    assert ok is False
    assert reason == 'Lyrics fetch/write failed — see app log for details'
    assert 'secret' not in reason


def test_retry_throttle_wait_is_capped_by_remaining_budget(monkeypatch):
    # The budget check alone is not enough: when a retry DOES wait for
    # a slot, that wait must be capped by the remaining per-call
    # budget, and refusing must return the real transient outcome, not
    # a busy. (A timeout sets no shared cooldown, so the retry path is
    # exercised directly.)
    from core import lrclib_throttle

    class _ApiTimeout:
        def get_lyrics(self, **kw):
            raise requests.exceptions.ReadTimeout('slow')

        def search_lyrics(self, **kw):  # pragma: no cover - must not be called
            raise AssertionError('transient exact failure must not fall through')

    client = _client(_ApiTimeout())
    waits = []

    def fake_wait(max_wait_seconds=None):
        waits.append(max_wait_seconds)
        return max_wait_seconds is None or max_wait_seconds >= 28

    monkeypatch.setattr(lrclib_throttle, 'wait_for_slot', fake_wait)
    result = client.lookup_remote_lyrics('Song', 'Artist', album_name='Album',
                                         duration_seconds=200)
    assert result.status == STATUS_UNKNOWN
    assert result.error_kind == 'timeout'
    assert 'busy' not in result.reason
    assert waits[0] is None                       # first attempt uncapped
    assert waits[1] is not None and waits[1] <= 20.0   # retry capped by budget


def test_headerless_429_skips_the_doomed_retry(monkeypatch):
    # A 429 without Retry-After sets a 30s shared cooldown against a
    # 20s per-call budget: the retry could never fire inside the
    # budget, so the client returns after ONE attempt — no wasted
    # backoff sleep, no refused slot wait.
    from core import lrclib_throttle

    class _Api429:
        def get_lyrics(self, **kw):
            raise FakeHTTPError(429)

        def search_lyrics(self, **kw):  # pragma: no cover - must not be called
            raise AssertionError('transient exact failure must not fall through')

    client = _client(_Api429())
    waits = []
    monkeypatch.setattr(lrclib_throttle, 'wait_for_slot',
                        lambda max_wait_seconds=None: waits.append(max_wait_seconds) or True)
    result = client.lookup_remote_lyrics('Song', 'Artist', album_name='Album',
                                         duration_seconds=200)
    assert result.status == STATUS_UNKNOWN
    assert result.error_kind == 'rate_limited'
    assert waits == [None]        # exactly one attempt's wait
    assert client.sleeps == []    # and no backoff sleep before giving up


def test_every_transient_reason_trips_the_breaker_classifier():
    # Tie the breaker's text classifier to the client's actual reasons:
    # if a transient reason is ever reworded, THIS test should fail
    # rather than the breaker silently never tripping.
    from core.lyrics_client import classify_lyrics_error
    from core.repair_worker import _is_lrclib_transient_error
    reasons = [
        classify_lyrics_error(FakeHTTPError(429)).reason,
        classify_lyrics_error(FakeHTTPError(503)).reason,
        classify_lyrics_error(requests.exceptions.ReadTimeout('slow')).reason,
        classify_lyrics_error(requests.exceptions.ConnectionError('down')).reason,
        'LRCLib API not available',
        'LRCLib busy (rate-limit cooldown) — retry later',
    ]
    for reason in reasons:
        assert _is_lrclib_transient_error(reason), reason
    # …and definitive answers never trip it.
    assert not _is_lrclib_transient_error(
        'Could not fetch lyrics (no longer available?)')
    assert not _is_lrclib_transient_error('disk full')


# ── import path: transient failure is not logged as "No lyrics found" ────

def test_generate_lrc_file_logs_transient_reason(monkeypatch, caplog):
    import logging

    from core.metadata import lyrics as metadata_lyrics

    fake = SimpleNamespace(
        create_lrc_file_detailed=lambda *a, **k: (False, 'LRCLib rate-limited (429) — retry later'))
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake)
    monkeypatch.setattr(metadata_lyrics, "get_config_manager",
                        lambda: SimpleNamespace(get=lambda k, d=None: d))
    with caplog.at_level(logging.WARNING, logger="metadata.lyrics"):
        ok = metadata_lyrics.generate_lrc_file(
            "/x/song.flac",
            {"original_search": {"title": "Song", "duration_ms": 200000}},
            {"name": "Artist"}, {})
    assert ok is False
    assert 'rate-limited' in caplog.text
    assert 'No lyrics found' not in caplog.text


# ── pipeline cap: an import never waits out a shared cooldown ────────────

def test_pipeline_lookup_does_not_park_behind_a_long_cooldown(monkeypatch, tmp_path):
    """LRCLib asked for Retry-After: 300 and the shared cooldown holds it.
    The download pipeline fetches lyrics inline for every imported track,
    and the first slot wait had no cap for it, so each track slept the
    whole cooldown (299s, measured). With the pipeline cap the lookup is
    refused at once, no request fires, and the reason says busy instead
    of "no lyrics"."""
    import time as _time
    monkeypatch.setattr(lrclib_throttle, "wait_for_slot", _REAL_WAIT_FOR_SLOT)
    lrclib_throttle._reset_for_tests()
    lrclib_throttle.note_rate_limited(300)
    slept = []
    monkeypatch.setattr(_time, "sleep", lambda s: slept.append(s))
    api = MagicMock()
    c = _client(api)
    from core.lyrics_client import PIPELINE_MAX_WAIT_SECONDS
    ok, reason = c.create_lrc_file_detailed(
        str(tmp_path / "song.flac"), "Song", "Artist", "Album", 200,
        max_wait_seconds=PIPELINE_MAX_WAIT_SECONDS)
    assert ok is False and 'busy' in reason
    assert slept == [] and c.sleeps == []
    api.get_lyrics.assert_not_called()
    api.search_lyrics.assert_not_called()


def test_import_and_youtube_paths_pass_the_pipeline_cap(monkeypatch):
    from core.lyrics_client import PIPELINE_MAX_WAIT_SECONDS
    from core.metadata import lyrics as metadata_lyrics
    from core.youtube_client import YouTubeClient

    seen = {}

    def detailed(*a, **k):
        seen.update(k)
        return True, None

    monkeypatch.setattr("core.lyrics_client.lyrics_client",
                        SimpleNamespace(api=object(), create_lrc_file_detailed=detailed))
    monkeypatch.setattr(metadata_lyrics, "get_config_manager",
                        lambda: SimpleNamespace(get=lambda k, d=None: d))
    assert metadata_lyrics.generate_lrc_file(
        "/x/song.flac",
        {"original_search": {"title": "Song", "duration_ms": 200000}},
        {"name": "Artist"}, {}) is True
    assert seen.get("max_wait_seconds") == PIPELINE_MAX_WAIT_SECONDS

    seen.clear()
    track = SimpleNamespace(artists=["Artist"], name="Song", album="Album", duration_ms=200000)
    YouTubeClient._create_lyrics_file(object.__new__(YouTubeClient), "/x/song.mp3", track)
    assert seen.get("max_wait_seconds") == PIPELINE_MAX_WAIT_SECONDS
