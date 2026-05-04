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
