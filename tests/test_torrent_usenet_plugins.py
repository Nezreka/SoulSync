"""Tests for ``core/download_plugins/torrent.py`` and ``usenet.py``.

Both plugins compose a Prowlarr client + an adapter + the archive
pipeline. The tests mock the Prowlarr client and the active adapter
factory so we can pin the projection logic, filename encoding /
decoding, finalize path, and the cancel / clear lifecycle without
touching the network or filesystem (beyond ``tmp_path``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from core.download_plugins.torrent import (
    TorrentDownloadPlugin,
    _adapter_state_to_display,
    _decode_filename,
    _FILENAME_SEP,
    _guess_quality_from_title,
    _parse_release_title,
)
from core.download_plugins.usenet import UsenetDownloadPlugin
from core.prowlarr_client import ProwlarrSearchResult
from core.torrent_clients.base import TorrentStatus
from core.usenet_clients.base import UsenetStatus


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_decode_filename_splits_on_separator() -> None:
    url, display = _decode_filename(f"https://x/y.torrent{_FILENAME_SEP}Album Name")
    assert url == 'https://x/y.torrent'
    assert display == 'Album Name'


def test_decode_filename_without_separator_returns_none_url() -> None:
    url, display = _decode_filename('just a name')
    assert url is None
    assert display == 'just a name'


def test_decode_filename_handles_magnet_with_embedded_separators() -> None:
    """Magnet URIs contain ``=`` and ``&`` but no ``||`` — so a
    magnet must round-trip cleanly through the encoder."""
    magnet = 'magnet:?xt=urn:btih:abc123&dn=Album+Name'
    encoded = f"{magnet}{_FILENAME_SEP}Display"
    url, display = _decode_filename(encoded)
    assert url == magnet
    assert display == 'Display'


def test_guess_quality_from_title() -> None:
    assert _guess_quality_from_title('Album [FLAC]') == 'flac'
    assert _guess_quality_from_title('Album 24-bit Hi-Res') == 'flac'
    assert _guess_quality_from_title('Album [MP3 320]') == 'mp3'
    assert _guess_quality_from_title('Album [AAC 256]') == 'aac'
    assert _guess_quality_from_title('Album [OGG]') == 'ogg'
    # Default fallback so quality_score doesn't crash on bare titles.
    assert _guess_quality_from_title('Just A Title') == 'mp3'
    assert _guess_quality_from_title('') == 'mp3'


def test_parse_release_title_splits_artist_dash_title() -> None:
    """Most release titles follow 'Artist - Title' / 'Artist - Album'."""
    assert _parse_release_title('Danny Brown - Atrocity Exhibition') == ('Danny Brown', 'Atrocity Exhibition')
    assert _parse_release_title('Kendrick Lamar - DAMN.') == ('Kendrick Lamar', 'DAMN.')


def test_parse_release_title_strips_trailing_tags() -> None:
    """Quality / year tags at the end shouldn't pollute the title."""
    artist, title = _parse_release_title('Danny Brown - Atrocity Exhibition [FLAC]')
    assert artist == 'Danny Brown'
    assert title == 'Atrocity Exhibition'
    artist, title = _parse_release_title('Danny Brown - Atrocity Exhibition (2016)')
    assert artist == 'Danny Brown'
    assert title == 'Atrocity Exhibition'


def test_parse_release_title_handles_no_dash() -> None:
    """Some indexers post bare titles. Caller should fall back to
    the indexer name as the 'artist' field."""
    artist, title = _parse_release_title('JustATitle')
    assert artist == ''
    assert title == 'JustATitle'


def test_parse_release_title_handles_dashes_in_title() -> None:
    """Track titles can themselves contain dashes — only split on
    the FIRST one so subtitles survive."""
    artist, title = _parse_release_title('Artist - Title - Live Version')
    assert artist == 'Artist'
    assert title == 'Title - Live Version'


def test_parse_release_title_rejects_url_prefix() -> None:
    """Defensive: if a URL somehow lands in the title field, refuse
    to call it an artist."""
    artist, title = _parse_release_title('https://example.com/x - Album')
    assert artist == ''


def test_adapter_state_mapping_covers_complete_states() -> None:
    assert _adapter_state_to_display('downloading') == 'InProgress, Downloading'
    assert _adapter_state_to_display('seeding') == 'Completed, Succeeded'
    assert _adapter_state_to_display('completed') == 'Completed, Succeeded'
    assert _adapter_state_to_display('error') == 'Completed, Errored'
    assert _adapter_state_to_display('stalled') == 'InProgress, Stalled'
    # Unknown state falls through with title-casing rather than crashing.
    assert _adapter_state_to_display('weird') == 'Weird'


# ---------------------------------------------------------------------------
# Torrent plugin — search projection
# ---------------------------------------------------------------------------


def _make_torrent_result(**overrides) -> ProwlarrSearchResult:
    base = dict(
        guid='guid-1', title='Danny Brown - Atrocity Exhibition [FLAC]', indexer_id=3,
        indexer_name='Indexer', protocol='torrent',
        download_url='https://x/y.torrent', magnet_uri=None,
        info_url=None, size=500_000_000, seeders=12, leechers=3,
        grabs=100, publish_date='2026-01-01', categories=[3040],
        raw={},
    )
    base.update(overrides)
    return ProwlarrSearchResult(**base)


def test_torrent_project_results_drops_non_torrent_protocol() -> None:
    plugin = TorrentDownloadPlugin()
    results = [
        _make_torrent_result(),
        _make_torrent_result(protocol='usenet', title='Usenet Album'),
    ]
    tracks, albums = plugin._project_results(results)
    assert len(tracks) == 1
    assert tracks[0].title == 'Atrocity Exhibition'
    assert tracks[0].artist == 'Danny Brown'
    assert len(albums) == 1


def test_torrent_project_results_drops_releases_without_download_url() -> None:
    plugin = TorrentDownloadPlugin()
    results = [_make_torrent_result(download_url=None, magnet_uri=None)]
    tracks, albums = plugin._project_results(results)
    assert tracks == []
    assert albums == []


def test_torrent_project_results_prefers_magnet_when_available() -> None:
    plugin = TorrentDownloadPlugin()
    magnet = 'magnet:?xt=urn:btih:abc'
    results = [_make_torrent_result(magnet_uri=magnet, download_url='https://x/y.torrent')]
    tracks, _ = plugin._project_results(results)
    url, _ = _decode_filename(tracks[0].filename)
    assert url == magnet


def test_torrent_project_results_encodes_url_and_title_in_filename() -> None:
    plugin = TorrentDownloadPlugin()
    tracks, _ = plugin._project_results([_make_torrent_result()])
    url, display = _decode_filename(tracks[0].filename)
    assert url == 'https://x/y.torrent'
    assert display == 'Danny Brown - Atrocity Exhibition [FLAC]'


def test_torrent_project_falls_back_to_indexer_name_when_title_lacks_dash() -> None:
    """When the title has no 'Artist -' prefix we'd auto-parse the
    filename (which starts with the indexer download URL) and end
    up showing the URL in the UI's 'by' field. Pre-filling artist
    with the indexer name avoids that."""
    plugin = TorrentDownloadPlugin()
    tracks, _ = plugin._project_results([_make_torrent_result(title='JustATitle')])
    assert tracks[0].artist == 'Indexer'
    # And the URL is definitely not the artist.
    assert 'http' not in tracks[0].artist
    assert '||' not in tracks[0].artist


def test_torrent_project_results_neutralizes_soulseek_specific_fields() -> None:
    """TrackResult.quality_score punishes results with no upload
    slots; torrent results don't have that concept so the
    projection has to fill in non-punishing neutral values."""
    plugin = TorrentDownloadPlugin()
    tracks, _ = plugin._project_results([_make_torrent_result(seeders=0)])
    # seeders=0 means we should still hand the picker something
    # usable. free_upload_slots floors at 1 to avoid the 0-slot
    # penalty applied to dead Soulseek peers.
    assert tracks[0].free_upload_slots >= 1


# ---------------------------------------------------------------------------
# Torrent plugin — is_configured / check_connection
# ---------------------------------------------------------------------------


def test_torrent_is_configured_requires_both_sides() -> None:
    plugin = TorrentDownloadPlugin()
    with patch.object(plugin._prowlarr, 'is_configured', return_value=False), \
         patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=None):
        assert plugin.is_configured() is False
    fake_adapter = MagicMock()
    fake_adapter.is_configured.return_value = False
    with patch.object(plugin._prowlarr, 'is_configured', return_value=True), \
         patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=fake_adapter):
        assert plugin.is_configured() is False
    fake_adapter.is_configured.return_value = True
    with patch.object(plugin._prowlarr, 'is_configured', return_value=True), \
         patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=fake_adapter):
        assert plugin.is_configured() is True


# ---------------------------------------------------------------------------
# Torrent plugin — finalize / cancel / clear
# ---------------------------------------------------------------------------


def test_torrent_finalize_picks_first_audio_file(tmp_path: Path) -> None:
    plugin = TorrentDownloadPlugin()
    # Seed an in-flight download row
    plugin.active_downloads['dl-1'] = {
        'id': 'dl-1', 'filename': 'x', 'username': 'torrent',
        'display_name': 'X', 'state': 'InProgress, Downloading',
        'progress': 50.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'torrent_hash': 'h1', 'error': None,
    }
    # Drop two audio files in the save dir
    (tmp_path / 'b.flac').write_bytes(b'fLaC')
    (tmp_path / 'a.mp3').write_bytes(b'ID3')
    plugin._finalize_download('dl-1', str(tmp_path))
    row = plugin.active_downloads['dl-1']
    assert row['state'] == 'Completed, Succeeded'
    assert row['progress'] == 100.0
    # Walker sorts → 'a.mp3' wins as first.
    assert row['file_path'].endswith('a.mp3')


def test_torrent_finalize_marks_error_when_no_audio(tmp_path: Path) -> None:
    plugin = TorrentDownloadPlugin()
    plugin.active_downloads['dl-1'] = {
        'id': 'dl-1', 'filename': 'x', 'username': 'torrent',
        'display_name': 'X', 'state': 'InProgress, Downloading',
        'progress': 50.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'torrent_hash': 'h1', 'error': None,
    }
    # tmp_path has no audio files
    plugin._finalize_download('dl-1', str(tmp_path))
    assert plugin.active_downloads['dl-1']['state'] == 'Completed, Errored'
    assert 'No audio files' in plugin.active_downloads['dl-1']['error']


def test_torrent_finalize_marks_error_when_save_path_missing() -> None:
    plugin = TorrentDownloadPlugin()
    plugin.active_downloads['dl-1'] = {
        'id': 'dl-1', 'filename': 'x', 'username': 'torrent',
        'display_name': 'X', 'state': 'InProgress, Downloading',
        'progress': 50.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'torrent_hash': 'h1', 'error': None,
    }
    plugin._finalize_download('dl-1', None)
    assert plugin.active_downloads['dl-1']['state'] == 'Completed, Errored'
    assert 'no save_path' in plugin.active_downloads['dl-1']['error'].lower()


def test_torrent_clear_completed_drops_only_done_rows() -> None:
    plugin = TorrentDownloadPlugin()
    plugin.active_downloads['a'] = {'id': 'a', 'state': 'InProgress, Downloading'}
    plugin.active_downloads['b'] = {'id': 'b', 'state': 'Completed, Succeeded'}
    plugin.active_downloads['c'] = {'id': 'c', 'state': 'Completed, Errored'}
    plugin.active_downloads['d'] = {'id': 'd', 'state': 'Cancelled'}
    _run(plugin.clear_all_completed_downloads())
    assert list(plugin.active_downloads.keys()) == ['a']


def test_torrent_get_all_returns_status_objects() -> None:
    plugin = TorrentDownloadPlugin()
    plugin.active_downloads['a'] = {
        'id': 'a', 'filename': 'f', 'username': 'torrent',
        'state': 'InProgress, Downloading', 'progress': 50.0,
        'size': 100, 'transferred': 50, 'speed': 1000,
        'file_path': None,
    }
    statuses = _run(plugin.get_all_downloads())
    assert len(statuses) == 1
    assert statuses[0].id == 'a'
    assert statuses[0].progress == 50.0


# ---------------------------------------------------------------------------
# Usenet plugin — projection
# ---------------------------------------------------------------------------


def _make_usenet_result(**overrides) -> ProwlarrSearchResult:
    base = dict(
        guid='guid-u', title='Some Artist - Some Album', indexer_id=5,
        indexer_name='UsenetIndexer', protocol='usenet',
        download_url='https://x/y.nzb', magnet_uri=None,
        info_url=None, size=400_000_000, seeders=None, leechers=None,
        grabs=42, publish_date='2026-01-01', categories=[3010],
        raw={},
    )
    base.update(overrides)
    return ProwlarrSearchResult(**base)


def test_usenet_project_drops_torrent_protocol() -> None:
    plugin = UsenetDownloadPlugin()
    results = [_make_usenet_result(), _make_usenet_result(protocol='torrent', title='T')]
    tracks, albums = plugin._project_results(results)
    assert len(tracks) == 1
    assert tracks[0].username == 'usenet'


def test_usenet_project_drops_results_without_download_url() -> None:
    """Usenet plugins reject magnet-only results entirely — NZBs
    don't have a magnet equivalent."""
    plugin = UsenetDownloadPlugin()
    results = [_make_usenet_result(download_url=None)]
    tracks, _ = plugin._project_results(results)
    assert tracks == []


def test_usenet_project_encodes_url_in_filename() -> None:
    plugin = UsenetDownloadPlugin()
    tracks, _ = plugin._project_results([_make_usenet_result()])
    url, display = _decode_filename(tracks[0].filename)
    assert url == 'https://x/y.nzb'
    assert display == 'Some Artist - Some Album'
    # Artist + title should be parsed out, not auto-extracted from filename.
    assert tracks[0].artist == 'Some Artist'
    assert tracks[0].title == 'Some Album'


def test_usenet_finalize_picks_first_audio_file(tmp_path: Path) -> None:
    """Same finalize contract as torrent — sanity check the shared
    helper path works for usenet too."""
    plugin = UsenetDownloadPlugin()
    plugin.active_downloads['u-1'] = {
        'id': 'u-1', 'filename': 'x', 'username': 'usenet',
        'display_name': 'X', 'state': 'InProgress, Downloading',
        'progress': 50.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'job_id': 'j1', 'error': None,
    }
    (tmp_path / 'track1.flac').write_bytes(b'fLaC')
    plugin._finalize_download('u-1', str(tmp_path))
    assert plugin.active_downloads['u-1']['state'] == 'Completed, Succeeded'
    assert plugin.active_downloads['u-1']['file_path'].endswith('track1.flac')


def test_usenet_is_configured_requires_both_sides() -> None:
    plugin = UsenetDownloadPlugin()
    fake_adapter = MagicMock()
    fake_adapter.is_configured.return_value = True
    with patch.object(plugin._prowlarr, 'is_configured', return_value=False), \
         patch('core.download_plugins.usenet.get_active_usenet_adapter', return_value=fake_adapter):
        assert plugin.is_configured() is False
    with patch.object(plugin._prowlarr, 'is_configured', return_value=True), \
         patch('core.download_plugins.usenet.get_active_usenet_adapter', return_value=None):
        assert plugin.is_configured() is False
    with patch.object(plugin._prowlarr, 'is_configured', return_value=True), \
         patch('core.download_plugins.usenet.get_active_usenet_adapter', return_value=fake_adapter):
        assert plugin.is_configured() is True


# ---------------------------------------------------------------------------
# Plugin conformance — both must satisfy the DownloadSourcePlugin Protocol
# ---------------------------------------------------------------------------


def test_plugins_conform_to_protocol() -> None:
    from core.download_plugins.base import DownloadSourcePlugin
    assert isinstance(TorrentDownloadPlugin(), DownloadSourcePlugin)
    assert isinstance(UsenetDownloadPlugin(), DownloadSourcePlugin)


# ---------------------------------------------------------------------------
# Registry — both should register cleanly
# ---------------------------------------------------------------------------


def test_registry_includes_torrent_and_usenet() -> None:
    """The registry decides what shows up in the orchestrator's
    iteration helpers. If we forget to register a new plugin the
    download source dropdown will silently no-op."""
    from core.download_plugins.registry import build_default_registry
    registry = build_default_registry()
    names = registry.names()
    assert 'torrent' in names
    assert 'usenet' in names
