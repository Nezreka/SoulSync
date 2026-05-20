"""Phase A pinning tests for SoulseekClient's download lifecycle.

These tests pin the OBSERVABLE BEHAVIOR of `SoulseekClient.download` /
`get_all_downloads` / `cancel_download` so the upcoming download
engine refactor (which lifts shared state + thread workers + search
retry into a central engine) can't drift the per-source contract.

The contract these tests pin is what the engine will call into via
`plugin.download_raw(target_id)` / `plugin.cancel_raw(target_id)`
after the refactor lands. If a future commit breaks any of these
expectations, the diff fails fast — long before a real download
attempt against a live slskd would have surfaced the bug.

NOTE: Soulseek is structurally different from the streaming sources.
It has NO local thread worker — slskd manages downloads server-side
and the client just polls for state. So Soulseek skips most of the
engine refactor's thread-extraction work; what stays critical is
the slskd HTTP API contract (endpoints, payload shape, id
extraction). That's what these tests pin.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.soulseek_client import SoulseekClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Configuration / lifecycle
# ---------------------------------------------------------------------------


def test_is_configured_returns_false_when_no_base_url():
    """Pinning: an unconfigured client (no slskd URL set) reports
    is_configured() == False. The orchestrator's hybrid fallback +
    every consumer that gates on is_configured() depends on this."""
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = None
    client.api_key = None
    assert client.is_configured() is False


def test_is_configured_returns_true_when_base_url_set():
    """Pinning: configured client (slskd URL present) reports True."""
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = 'http://localhost:5030'
    client.api_key = 'test-key'
    assert client.is_configured() is True


# ---------------------------------------------------------------------------
# download()
# ---------------------------------------------------------------------------


@pytest.fixture
def configured_client():
    """A SoulseekClient with the slskd URL set but no real network. Tests
    individually patch `_make_request` to return whatever shape they
    want to exercise."""
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = 'http://localhost:5030'
    client.api_key = 'test-key'
    client.download_path = Path('./test_downloads')
    return client


def test_download_returns_none_when_not_configured():
    """Pinning: an unconfigured client refuses downloads — returns
    None silently rather than raising. Used as the soft-fail signal
    by the orchestrator's per-source fallback chain."""
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = None
    result = _run_async(client.download('user', 'song.flac', 1024))
    assert result is None


def test_download_hits_transfers_downloads_username_endpoint(configured_client):
    """Pinning: the primary download endpoint is
    `transfers/downloads/<username>` POST. This shape was chosen to
    match slskd's web-interface API exactly. Changing it breaks
    every download against current slskd builds."""
    captured = []

    async def fake_request(method, endpoint, json=None, **kwargs):
        captured.append((method, endpoint, json))
        return {'id': 'dl-id-from-slskd'}

    with patch.object(configured_client, '_make_request', side_effect=fake_request):
        result = _run_async(configured_client.download('user', 'song.flac', 1024))

    assert result == 'dl-id-from-slskd'
    method, endpoint, payload = captured[0]
    assert method == 'POST'
    assert endpoint == 'transfers/downloads/user'
    # Payload is the slskd web-interface array format.
    assert isinstance(payload, list)
    assert payload[0]['filename'] == 'song.flac'
    assert payload[0]['size'] == 1024


def test_download_extracts_id_from_dict_response(configured_client):
    """Pinning: when slskd returns `{id: ...}`, that's the
    download_id the orchestrator uses to track the download."""
    with patch.object(configured_client, '_make_request',
                      AsyncMock(return_value={'id': 'abc123'})):
        result = _run_async(configured_client.download('user', 'song.flac', 1024))
    assert result == 'abc123'


def test_download_extracts_id_from_list_response(configured_client):
    """Pinning: slskd sometimes returns a list of file objects.
    The first item's id is the download_id."""
    with patch.object(configured_client, '_make_request',
                      AsyncMock(return_value=[{'id': 'list-id'}, {'id': 'second'}])):
        result = _run_async(configured_client.download('user', 'song.flac', 1024))
    assert result == 'list-id'


def test_download_falls_back_to_filename_when_no_id_in_response(configured_client):
    """Pinning: defensive — older slskd builds returned 201 Created
    with no id field. The client uses the filename as the download
    identifier in that case so downstream tracking still works."""
    with patch.object(configured_client, '_make_request',
                      AsyncMock(return_value={'status': 'queued'})):
        result = _run_async(configured_client.download('user', 'song.flac', 1024))
    assert result == 'song.flac'


# ---------------------------------------------------------------------------
# get_all_downloads()
# ---------------------------------------------------------------------------


def test_get_all_downloads_returns_empty_when_not_configured():
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = None
    result = _run_async(client.get_all_downloads())
    assert result == []


def test_get_all_downloads_parses_nested_user_directory_files_response(configured_client):
    """Pinning: slskd's `transfers/downloads` returns
    `[{username, directories: [{files: [...]}]}]`. The client
    flattens that into a list of DownloadStatus objects, one per
    file. Engine refactor's state aggregation depends on this shape."""
    fake_response = [
        {
            'username': 'peer1',
            'directories': [{
                'files': [
                    {'id': 'f1', 'filename': 'a.flac', 'state': 'InProgress',
                     'size': 100, 'bytesTransferred': 50, 'averageSpeed': 1024},
                    {'id': 'f2', 'filename': 'b.flac', 'state': 'Completed, Succeeded',
                     'size': 200, 'bytesTransferred': 200, 'averageSpeed': 2048},
                ],
            }],
        },
    ]

    with patch.object(configured_client, '_make_request',
                      AsyncMock(return_value=fake_response)):
        result = _run_async(configured_client.get_all_downloads())

    assert len(result) == 2
    assert result[0].id == 'f1'
    assert result[0].username == 'peer1'
    assert result[0].state == 'InProgress'
    assert result[1].id == 'f2'
    # Pinning: 'Completed' state forces progress=100 regardless of source data.
    assert result[1].progress == 100.0


def test_get_all_downloads_endpoint_is_transfers_downloads(configured_client):
    """Pinning: the listing endpoint is `transfers/downloads` (no
    username). The 404'd `users/.../downloads` variant was tried
    once and removed — keep it gone."""
    captured = []

    async def fake_request(method, endpoint, **kwargs):
        captured.append((method, endpoint))
        return []

    with patch.object(configured_client, '_make_request', side_effect=fake_request):
        _run_async(configured_client.get_all_downloads())

    assert captured == [('GET', 'transfers/downloads')]


# ---------------------------------------------------------------------------
# cancel_download()
# ---------------------------------------------------------------------------


def test_cancel_download_returns_false_when_not_configured():
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = None
    result = _run_async(client.cancel_download('dl-id', 'user', remove=False))
    assert result is False


def test_cancel_download_looks_up_username_when_not_provided(configured_client):
    """Pinning: orchestrator may call cancel_download without a
    username hint. The client falls back to scanning all downloads
    to find which peer owns it. Engine refactor must preserve this
    so existing API endpoints that don't pass username keep working."""
    fake_listing = [
        {
            'username': 'peer-owner',
            'directories': [{
                'files': [{'id': 'target-dl', 'filename': 'x.flac',
                           'state': 'InProgress', 'size': 0,
                           'bytesTransferred': 0, 'averageSpeed': 0}],
            }],
        },
    ]

    captured_endpoints = []

    async def fake_request(method, endpoint, **kwargs):
        captured_endpoints.append((method, endpoint))
        if method == 'GET' and endpoint == 'transfers/downloads':
            return fake_listing
        # The DELETE for cancel — return success
        return True

    with patch.object(configured_client, '_make_request', side_effect=fake_request):
        _run_async(configured_client.cancel_download('target-dl', None, remove=False))

    # The lookup hit get_all_downloads first, then the DELETE used the discovered username.
    assert ('GET', 'transfers/downloads') in captured_endpoints
    delete_calls = [(m, e) for m, e in captured_endpoints if m == 'DELETE']
    assert delete_calls, "Expected at least one DELETE after username lookup"
    # The cancel endpoint URL contains the discovered username.
    assert any('peer-owner' in e for _, e in delete_calls)


def test_cancel_download_returns_false_when_username_lookup_fails(configured_client):
    """Pinning: if the download_id isn't in the active list, return
    False rather than raising. Orchestrator treats False as "couldn't
    cancel" and continues; an exception would propagate to the user."""
    with patch.object(configured_client, '_make_request',
                      AsyncMock(return_value=[])):
        result = _run_async(configured_client.cancel_download('missing-id', None))
    assert result is False


# ---------------------------------------------------------------------------
# HTTP timeout config (issue #499 — prevent worker thread deadlock)
# ---------------------------------------------------------------------------


def test_default_timeout_constant_has_bounded_values():
    """Pin issue #499 fix: the module-level timeout config is defined
    with a hard ceiling so an unresponsive slskd can't wedge the
    download worker thread permanently. Any future change that drops
    or unbounds the timeout would re-introduce the
    'downloads stop after 2-3 hours' deadlock."""
    from core.soulseek_client import _SLSKD_DEFAULT_TIMEOUT
    import aiohttp

    assert isinstance(_SLSKD_DEFAULT_TIMEOUT, aiohttp.ClientTimeout)
    # Total timeout must be set and bounded — prevents infinite hang.
    assert _SLSKD_DEFAULT_TIMEOUT.total is not None
    assert _SLSKD_DEFAULT_TIMEOUT.total > 0
    assert _SLSKD_DEFAULT_TIMEOUT.total <= 300, (
        f"Total timeout {_SLSKD_DEFAULT_TIMEOUT.total}s exceeds 5min "
        "ceiling — slskd metadata calls should never legitimately take this long"
    )
    # Connect timeout bounded — TCP connect to slskd should be fast.
    assert _SLSKD_DEFAULT_TIMEOUT.connect is not None
    assert _SLSKD_DEFAULT_TIMEOUT.connect <= 60


def test_make_request_returns_none_on_timeout(configured_client):
    """Pin: when the slskd HTTP call times out (asyncio.TimeoutError),
    ``_make_request`` returns None rather than raising. The download
    worker thread unblocks; the caller treats None as a normal failure
    and the batch's stuck-detection later marks the task not_found.
    Pre-fix this raised → propagated up the call stack → eventually
    the worker thread died but only after wedging the executor pool."""

    async def _raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError("simulated slskd hang")

    # Patch aiohttp.ClientSession to return a session whose request()
    # context manager raises TimeoutError on entry.
    class _StubSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None

        def request(self, *args, **kwargs):
            class _Cm:
                async def __aenter__(self_inner):
                    raise asyncio.TimeoutError("simulated slskd hang")
                async def __aexit__(self_inner, *args):
                    return None
            return _Cm()

        async def close(self):
            return None

    with patch('aiohttp.ClientSession', return_value=_StubSession()):
        result = _run_async(configured_client._make_request('GET', 'transfers/downloads'))

    assert result is None, "Timeout must return None, not raise"


def test_make_direct_request_returns_none_on_timeout(configured_client):
    """Same pin for ``_make_direct_request`` (the non-/api/v0/ helper).
    Both code paths are used by different slskd endpoints — neither
    can be allowed to wedge."""

    class _StubSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None

        def request(self, *args, **kwargs):
            class _Cm:
                async def __aenter__(self_inner):
                    raise asyncio.TimeoutError("simulated slskd hang")
                async def __aexit__(self_inner, *args):
                    return None
            return _Cm()

        async def close(self):
            return None

    with patch('aiohttp.ClientSession', return_value=_StubSession()):
        result = _run_async(configured_client._make_direct_request('GET', 'health'))

    assert result is None


# ---------------------------------------------------------------------------
# Issue #649 — connection-error log spam suppression
# ---------------------------------------------------------------------------


def _build_unreachable_session(error_message: str = 'Cannot connect to host'):
    """Stub aiohttp session whose request() raises ClientConnectorError."""
    import aiohttp
    from unittest.mock import MagicMock

    class _Cm:
        async def __aenter__(self_inner):
            # ClientConnectorError needs a connection_key + OSError. The
            # exact values don't matter for the test — we just need an
            # instance of the right class so the except-branch fires.
            os_err = OSError(-2, 'Name or service not known')
            raise aiohttp.ClientConnectorError(MagicMock(), os_err)
        async def __aexit__(self_inner, *args):
            return None

    class _StubSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        def request(self, *args, **kwargs):
            return _Cm()
        async def close(self):
            return None

    return _StubSession


def test_unreachable_slskd_returns_none_not_raises(configured_client):
    """Pin: ClientConnectorError must not propagate. Caller treats None
    as a normal failure (same as a 5xx) — every consumer that gates on
    `if response is None` keeps working when slskd is unreachable."""
    StubSession = _build_unreachable_session()
    with patch('aiohttp.ClientSession', return_value=StubSession()):
        result = _run_async(configured_client._make_request('GET', 'transfers/downloads'))
    assert result is None


def test_unreachable_slskd_logs_warning_once_then_debug(configured_client, caplog):
    """Issue #649: status polling at /api/downloads/status fans out to
    every plugin including soulseek even when the user has soulseek
    toggled out, so each frontend poll produced an ERROR log line. Pin
    that the FIRST unreachable response emits one WARNING with
    actionable context, and subsequent repeats demote to DEBUG so the
    log isn't spammed for the lifetime of every non-soulseek download."""
    import logging
    configured_client._last_unreachable_logged = False
    StubSession = _build_unreachable_session()

    with patch('aiohttp.ClientSession', return_value=StubSession()):
        with caplog.at_level(logging.DEBUG, logger='soulseek_client'):
            # Three repeated polls — first must warn, rest must stay quiet.
            _run_async(configured_client._make_request('GET', 'transfers/downloads'))
            _run_async(configured_client._make_request('GET', 'transfers/downloads'))
            _run_async(configured_client._make_request('GET', 'transfers/downloads'))

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING
                       and 'slskd unreachable' in r.message]
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR
                     and 'Error making API request' in r.message]
    assert len(warning_records) == 1, \
        f"Expected exactly 1 WARNING (one-time slskd-unreachable notice), got {len(warning_records)}"
    assert len(error_records) == 0, \
        "Connection errors must not log at ERROR — that's the spam pattern #649 reported"
    assert configured_client._last_unreachable_logged is True


def test_unreachable_flag_resets_on_successful_response(configured_client, caplog):
    """When slskd comes back up after a stretch of being down, a fresh
    WARNING should fire if it goes down again later — the suppression is
    per-outage, not per-process-lifetime. The flag resets on any
    successful (200/201/204) response."""
    import logging
    configured_client._last_unreachable_logged = True  # Simulate prior outage already warned

    # Simulate a 200 response — must reset the suppression flag.
    class _OkCm:
        async def __aenter__(self_inner):
            class _Resp:
                status = 200
                reason = 'OK'
                async def text(self_resp):
                    return '{"ok": true}'
                async def json(self_resp):
                    return {'ok': True}
            return _Resp()
        async def __aexit__(self_inner, *args):
            return None

    class _OkSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        def request(self, *args, **kwargs):
            return _OkCm()
        async def close(self):
            return None

    with patch('aiohttp.ClientSession', return_value=_OkSession()):
        _run_async(configured_client._make_request('GET', 'server/state'))

    assert configured_client._last_unreachable_logged is False, \
        "Successful response must reset the suppression flag so a future outage warns again"


def test_make_direct_request_also_suppresses_unreachable_spam(configured_client, caplog):
    """`_make_direct_request` shares the same base_url and same outage
    mode, so it gets the same WARNING-once + DEBUG-after treatment."""
    import logging
    configured_client._last_unreachable_logged = False
    StubSession = _build_unreachable_session()

    with patch('aiohttp.ClientSession', return_value=StubSession()):
        with caplog.at_level(logging.DEBUG, logger='soulseek_client'):
            _run_async(configured_client._make_direct_request('GET', 'health'))
            _run_async(configured_client._make_direct_request('GET', 'health'))

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING
                       and 'slskd unreachable' in r.message]
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR
                     and 'Error making direct API request' in r.message]
    assert len(warning_records) == 1
    assert len(error_records) == 0


def test_non_connection_exception_still_logs_error(configured_client, caplog):
    """Guard: only ClientConnectorError gets the suppression treatment.
    Any other exception (programming bug, unexpected aiohttp behaviour,
    etc.) must still surface at ERROR so we don't accidentally hide
    real problems behind the noise reduction."""
    import logging

    class _BoomCm:
        async def __aenter__(self_inner):
            raise ValueError("not a connection error — should still log ERROR")
        async def __aexit__(self_inner, *args):
            return None

    class _BoomSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        def request(self, *args, **kwargs):
            return _BoomCm()
        async def close(self):
            return None

    with patch('aiohttp.ClientSession', return_value=_BoomSession()):
        with caplog.at_level(logging.DEBUG, logger='soulseek_client'):
            result = _run_async(configured_client._make_request('GET', 'transfers/downloads'))

    assert result is None  # Still returns None — non-raising contract preserved
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR
                     and 'Error making API request' in r.message]
    assert len(error_records) == 1, "Non-connection exceptions must still log ERROR"


# ---------------------------------------------------------------------------
# Issue #652 — quarantined-source dedup in the candidate filter
# ---------------------------------------------------------------------------


def _mk_track_result(username='peer', filename='song.flac', quality='flac',
                     bitrate=1411, size=10_000_000, duration=180_000):
    """Build a minimal TrackResult for the candidate filter tests."""
    from core.download_plugins.types import TrackResult
    return TrackResult(
        username=username,
        filename=filename,
        size=size,
        bitrate=bitrate,
        duration=duration,
        quality=quality,
        free_upload_slots=1,
        upload_speed=1_000_000,
        queue_length=0,
    )


def test_drop_quarantined_sources_keeps_clean_candidates(configured_client, tmp_path, monkeypatch):
    """When no candidate matches a quarantined `(username, filename)`,
    every result passes through. Filter is a no-op for clean searches."""
    quarantine_dir = tmp_path / 'ss_quarantine'
    quarantine_dir.mkdir()

    # Patch config_manager to point at our temp download path.
    import core.soulseek_client as sc
    monkeypatch.setattr(sc.config_manager, 'get',
                        lambda key, default=None: str(tmp_path) if key == 'soulseek.download_path' else default)

    results = [
        _mk_track_result(username='goodpeer1', filename='a.flac'),
        _mk_track_result(username='goodpeer2', filename='b.flac'),
    ]

    kept = configured_client._drop_quarantined_sources(results)

    assert len(kept) == 2
    assert {r.username for r in kept} == {'goodpeer1', 'goodpeer2'}


def test_drop_quarantined_sources_drops_known_bad(configured_client, tmp_path, monkeypatch):
    """Issue #652 core contract: a candidate whose `(username, filename)`
    matches a quarantined entry is dropped before the quality picker
    ranks it. Stops the loop where the same source kept winning the
    quality picker and re-downloading itself."""
    import json as _json
    quarantine_dir = tmp_path / 'ss_quarantine'
    quarantine_dir.mkdir()

    # Write a sidecar matching the bad source.
    sidecar = {
        "original_filename": "bad.flac",
        "quarantine_reason": "AcoustID mismatch",
        "context": {
            "original_search_result": {
                "username": "badpeer", "filename": "albums/bad.flac",
            },
        },
    }
    (quarantine_dir / "20260518_120000.json").write_text(_json.dumps(sidecar))

    import core.soulseek_client as sc
    monkeypatch.setattr(sc.config_manager, 'get',
                        lambda key, default=None: str(tmp_path) if key == 'soulseek.download_path' else default)

    results = [
        _mk_track_result(username='badpeer', filename='albums/bad.flac'),
        _mk_track_result(username='goodpeer', filename='albums/good.flac'),
    ]

    kept = configured_client._drop_quarantined_sources(results)

    assert len(kept) == 1
    assert kept[0].username == 'goodpeer'


def test_drop_quarantined_sources_returns_input_when_quarantine_missing(configured_client, tmp_path, monkeypatch):
    """No quarantine directory yet (fresh install / never used) —
    helper returns an empty set; filter returns the input unchanged.
    Defaults to today's behaviour for users with no quarantine history."""
    import core.soulseek_client as sc
    monkeypatch.setattr(sc.config_manager, 'get',
                        lambda key, default=None: str(tmp_path) if key == 'soulseek.download_path' else default)

    results = [_mk_track_result(username='peer', filename='song.flac')]

    kept = configured_client._drop_quarantined_sources(results)

    assert kept == results


def test_drop_quarantined_sources_swallows_filesystem_errors(configured_client, monkeypatch):
    """If something goes wrong loading the quarantine keys (permissions,
    OS quirk, etc.), the filter must NOT break the download pipeline.
    Returns input unchanged so legitimate downloads keep working —
    same defensive contract as the existing 401/connection handlers."""
    import core.soulseek_client as sc

    def _broken_get(key, default=None):
        raise RuntimeError("config explosion")

    monkeypatch.setattr(sc.config_manager, 'get', _broken_get)

    results = [_mk_track_result(username='peer', filename='song.flac')]

    kept = configured_client._drop_quarantined_sources(results)

    assert kept == results


def test_filter_results_by_quality_runs_quarantine_dedup_first(configured_client, tmp_path, monkeypatch):
    """Integration pin: `filter_results_by_quality_preference` calls
    the quarantine dedup BEFORE the quality picker. If a candidate is
    on the quarantine record, it can't win the picker by virtue of
    superior bitrate — that's how the #652 loop manifested."""
    import json as _json
    quarantine_dir = tmp_path / 'ss_quarantine'
    quarantine_dir.mkdir()

    sidecar = {
        "context": {
            "original_search_result": {
                "username": "badpeer", "filename": "high_bitrate_bad.flac",
            },
        },
    }
    (quarantine_dir / "20260518_120000.json").write_text(_json.dumps(sidecar))

    import core.soulseek_client as sc
    monkeypatch.setattr(sc.config_manager, 'get',
                        lambda key, default=None: str(tmp_path) if key == 'soulseek.download_path' else default)

    # Mock the DB call inside filter_results_by_quality_preference so the
    # test doesn't need a real DB. Quality profile permits FLAC.
    class _FakeDB:
        def get_quality_profile(self):
            return {
                'preset': 'flac',
                'qualities': {
                    'flac':    {'enabled': True, 'min_kbps': 800, 'max_kbps': 99999},
                    'mp3_320': {'enabled': False, 'min_kbps': 0, 'max_kbps': 0},
                    'mp3_256': {'enabled': False, 'min_kbps': 0, 'max_kbps': 0},
                    'mp3_192': {'enabled': False, 'min_kbps': 0, 'max_kbps': 0},
                },
                'priority': ['flac'],
            }

    import database.music_database as md
    monkeypatch.setattr(md, 'MusicDatabase', lambda: _FakeDB())

    results = [
        # The "bad" source has the BEST quality on paper — pre-fix would win the picker.
        _mk_track_result(username='badpeer', filename='high_bitrate_bad.flac',
                         quality='flac', bitrate=1411, size=20_000_000, duration=180_000),
        _mk_track_result(username='goodpeer', filename='good.flac',
                         quality='flac', bitrate=1411, size=20_000_000, duration=180_000),
    ]

    kept = configured_client.filter_results_by_quality_preference(results)

    usernames = {r.username for r in kept}
    assert 'badpeer' not in usernames, "Quarantined source must be filtered before the quality picker"
    assert 'goodpeer' in usernames
