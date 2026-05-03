"""Integration tests for SoundCloud wiring inside DownloadOrchestrator.

The standalone SoundcloudClient is exhaustively unit-tested in
``tests/test_soundcloud_client.py``. These tests verify the *plumbing*:
the orchestrator constructs a SoundCloud client at startup, exposes it
via the same lookup APIs every other source uses, dispatches downloads
to it when the username matches, and includes it in the hybrid-mode
fan-out / status / cancel / clear paths.

The intent is plug-and-play parity: any code that walks the
orchestrator's source list (UI, status endpoints, batch tracker)
picks up SoundCloud automatically without per-source special cases.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch, MagicMock

import pytest

from core.download_orchestrator import DownloadOrchestrator
from core.soundcloud_client import SoundcloudClient


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def orchestrator() -> DownloadOrchestrator:
    """Real orchestrator with real (but mostly idle) clients."""
    return DownloadOrchestrator()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_orchestrator_constructs_soundcloud_client(orchestrator: DownloadOrchestrator) -> None:
    assert orchestrator.soundcloud is not None
    assert isinstance(orchestrator.soundcloud, SoundcloudClient)


def test_client_lookup_resolves_soundcloud(orchestrator: DownloadOrchestrator) -> None:
    """Verify the dict-based name → client lookup includes SoundCloud."""
    assert orchestrator._client('soundcloud') is orchestrator.soundcloud


def test_client_lookup_returns_none_for_unknown(orchestrator: DownloadOrchestrator) -> None:
    """Sanity: unknown sources don't somehow resolve to SoundCloud."""
    assert orchestrator._client('made_up') is None


# ---------------------------------------------------------------------------
# Status surface
# ---------------------------------------------------------------------------


def test_get_source_status_includes_soundcloud(orchestrator: DownloadOrchestrator) -> None:
    """Every other source has a key here; SoundCloud should too. The UI
    walks this dict to render configured-status badges."""
    status = orchestrator.get_source_status()
    assert 'soundcloud' in status
    # yt-dlp is in requirements.txt → SoundCloud is configured by default
    assert status['soundcloud'] is True


# ---------------------------------------------------------------------------
# Download dispatch
# ---------------------------------------------------------------------------


def test_download_routes_soundcloud_username_to_client(orchestrator: DownloadOrchestrator) -> None:
    """The dispatcher must route ``username='soundcloud'`` to the SoundCloud
    client, not to Soulseek (the default fallback path)."""
    sentinel = 'sc-download-id-xyz'

    async def _fake_download(username, filename, file_size=0):
        return sentinel

    with patch.object(orchestrator.soundcloud, 'download', side_effect=_fake_download) as mock_dl:
        result = _run(orchestrator.download(
            'soundcloud',
            '999||https://soundcloud.com/x/y||Display',
            file_size=0,
        ))
    assert result == sentinel
    mock_dl.assert_called_once()


def test_download_unknown_username_still_falls_to_soulseek(orchestrator: DownloadOrchestrator) -> None:
    """Adding SoundCloud must not change the legacy Soulseek-fallback
    behavior for unrecognized usernames."""
    if orchestrator.soulseek is None:
        pytest.skip("Soulseek client unavailable in this environment")

    async def _fake_soulseek_download(username, filename, file_size=0):
        return 'soulseek-id'

    with patch.object(orchestrator.soulseek, 'download', side_effect=_fake_soulseek_download) as mock_dl:
        result = _run(orchestrator.download('some_random_user', 'file.mp3', 0))
    assert result == 'soulseek-id'
    mock_dl.assert_called_once()


# ---------------------------------------------------------------------------
# Hybrid mode
# ---------------------------------------------------------------------------


def test_hybrid_search_iterates_soundcloud_when_in_order(orchestrator: DownloadOrchestrator) -> None:
    """When SoundCloud appears in the hybrid_order list, the orchestrator
    must walk through its search results just like any other source."""
    orchestrator.mode = 'hybrid'
    orchestrator.hybrid_order = ['soundcloud']

    fake_track = MagicMock()
    fake_track.username = 'soundcloud'

    async def _fake_search(query, timeout=None, progress_callback=None):
        return ([fake_track], [])

    with patch.object(orchestrator.soundcloud, 'search', side_effect=_fake_search), \
         patch.object(orchestrator.soundcloud, 'is_configured', return_value=True):
        tracks, albums = _run(orchestrator.search("any query"))

    assert tracks == [fake_track]
    assert albums == []


def test_hybrid_search_skips_unconfigured_soundcloud(orchestrator: DownloadOrchestrator) -> None:
    """Defensive: if SoundCloud is unconfigured (yt-dlp missing), the
    hybrid loop must skip it cleanly and continue to the next source."""
    orchestrator.mode = 'hybrid'
    orchestrator.hybrid_order = ['soundcloud', 'soulseek']

    if orchestrator.soulseek is None:
        pytest.skip("Soulseek client unavailable in this environment")

    soulseek_track = MagicMock()
    soulseek_track.username = 'unrelated_user'

    async def _fake_soulseek_search(query, timeout=None, progress_callback=None):
        return ([soulseek_track], [])

    with patch.object(orchestrator.soundcloud, 'is_configured', return_value=False), \
         patch.object(orchestrator.soulseek, 'is_configured', return_value=True), \
         patch.object(orchestrator.soulseek, 'search', side_effect=_fake_soulseek_search):
        tracks, _ = _run(orchestrator.search("any"))

    assert tracks == [soulseek_track]


# ---------------------------------------------------------------------------
# Aggregate operations
# ---------------------------------------------------------------------------


def test_get_all_downloads_walks_soundcloud(orchestrator: DownloadOrchestrator) -> None:
    """Active-downloads endpoint pulls from every client; SoundCloud's
    queue must show up in the aggregate."""
    fake_status = MagicMock(id='sc-1', filename='x', state='InProgress, Downloading')

    async def _fake_get_all():
        return [fake_status]

    with patch.object(orchestrator.soundcloud, 'get_all_downloads', side_effect=_fake_get_all):
        all_dl = _run(orchestrator.get_all_downloads())

    assert any(d is fake_status for d in all_dl)


def test_get_download_status_finds_soundcloud_id(orchestrator: DownloadOrchestrator) -> None:
    """Status lookup must check SoundCloud — orchestrator iterates every
    client until one finds the id."""
    fake_status = MagicMock(id='sc-2')

    async def _fake_get_status(download_id):
        return fake_status if download_id == 'sc-2' else None

    with patch.object(orchestrator.soundcloud, 'get_download_status', side_effect=_fake_get_status):
        result = _run(orchestrator.get_download_status('sc-2'))

    assert result is fake_status


def test_cancel_routes_soundcloud_username(orchestrator: DownloadOrchestrator) -> None:
    """Username-routed cancel must dispatch to the SoundCloud client when
    username='soundcloud' is provided."""
    async def _fake_cancel(download_id, username=None, remove=False):
        return True

    with patch.object(orchestrator.soundcloud, 'cancel_download', side_effect=_fake_cancel) as mock_cancel:
        ok = _run(orchestrator.cancel_download('sc-3', username='soundcloud'))
    assert ok is True
    mock_cancel.assert_called_once()


def test_clear_completed_walks_soundcloud(orchestrator: DownloadOrchestrator) -> None:
    """Bulk clear-all-completed must call SoundCloud's clear method.

    We assert SoundCloud got called — not that the overall result is
    True, since other sibling clients in the same orchestrator may
    return False for unrelated reasons (e.g. an unrelated client
    throwing). The contract this test pins is "SoundCloud is included
    in the iteration", which is what plug-and-play parity requires.
    """
    async def _fake_clear():
        return True

    with patch.object(orchestrator.soundcloud, 'clear_all_completed_downloads', side_effect=_fake_clear) as mock_clear:
        _run(orchestrator.clear_all_completed_downloads())
    mock_clear.assert_called_once()


# ---------------------------------------------------------------------------
# Mode-only routing
# ---------------------------------------------------------------------------


def test_soundcloud_only_mode_uses_soundcloud(orchestrator: DownloadOrchestrator) -> None:
    """When mode='soundcloud', search must be dispatched only to the
    SoundCloud client — not soulseek or any other source."""
    orchestrator.mode = 'soundcloud'

    async def _fake_search(query, timeout=None, progress_callback=None):
        return ([MagicMock(username='soundcloud')], [])

    with patch.object(orchestrator.soundcloud, 'search', side_effect=_fake_search) as mock_sc, \
         patch.object(orchestrator.soulseek, 'search', side_effect=AssertionError("soulseek must not be searched")):
        tracks, _ = _run(orchestrator.search("any"))

    assert len(tracks) == 1
    mock_sc.assert_called_once()


def test_streaming_sources_tuple_includes_soundcloud() -> None:
    """The validation/streaming-source tuples used to pick scoring
    behavior must include SoundCloud — otherwise SoundCloud results
    would skip the matching-engine validation in
    search_and_download_best."""
    from core.downloads import validation
    from inspect import getsource
    src = getsource(validation.filter_streaming_results) if hasattr(validation, 'filter_streaming_results') else getsource(validation)
    assert 'soundcloud' in src, "core.downloads.validation must include 'soundcloud' in _streaming_sources"
