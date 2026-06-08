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
from unittest.mock import AsyncMock, MagicMock, patch

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


class _FakeClock:
    """Deterministic monotonic + sleep so the per-track poll loop runs
    in microseconds and never actually blocks."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _drive_download_thread(plugin, statuses, *, window_seconds=10.0):
    """Run ``_download_thread`` end-to-end against a scripted adapter.

    ``statuses`` is the sequence of ``UsenetStatus`` reads the poll loop
    will see (one per poll). Returns the finished active_downloads row."""
    download_id = 'u-poll'
    plugin.active_downloads[download_id] = {
        'id': download_id, 'filename': 'x', 'username': 'usenet',
        'display_name': 'X', 'state': 'Initializing', 'progress': 0.0,
        'size': 0, 'transferred': 0, 'speed': 0, 'file_path': None,
        'audio_files': [], 'job_id': None, 'error': None,
    }
    adapter = MagicMock()
    adapter.is_configured.return_value = True
    adapter.add_nzb.return_value = 'job1'
    adapter.get_status.side_effect = list(statuses)
    clock = _FakeClock()
    with patch('core.download_plugins.usenet.get_active_usenet_adapter', return_value=adapter), \
         patch('core.download_plugins.usenet.run_async', side_effect=lambda x: x), \
         patch('core.download_plugins.usenet.get_completed_no_path_window_seconds',
               return_value=window_seconds), \
         patch('core.download_plugins.usenet.time', clock), \
         patch('core.download_plugins.usenet.collect_audio_after_extraction',
               return_value=[Path('/done/track1.flac')]):
        plugin._download_thread(download_id, 'http://x/y.nzb')
    return plugin.active_downloads[download_id]


def test_usenet_thread_waits_out_completed_no_path_then_finalizes(tmp_path: Path) -> None:
    """Per-track sibling of the #721 bundle fix. SAB flips History to
    'completed' before writing ``storage`` — the thread must NOT error
    on the first such read. It waits out the completed-no-path window;
    when the path lands it finalizes as Succeeded."""
    plugin = UsenetDownloadPlugin()
    statuses = [
        UsenetStatus(id='job1', name='A', state='downloading', progress=0.6,
                     size=100, downloaded=60, download_speed=10),
        UsenetStatus(id='job1', name='A', state='completed', progress=1.0,
                     size=100, downloaded=100, download_speed=0, save_path=None),
        UsenetStatus(id='job1', name='A', state='completed', progress=1.0,
                     size=100, downloaded=100, download_speed=0, save_path=None),
        UsenetStatus(id='job1', name='A', state='completed', progress=1.0,
                     size=100, downloaded=100, download_speed=0,
                     save_path='/done/album'),
    ]
    row = _drive_download_thread(plugin, statuses)
    assert row['state'] == 'Completed, Succeeded'
    assert row['progress'] == 100.0
    assert row['file_path'] == str(Path('/done/track1.flac'))


def test_usenet_thread_falls_back_to_incomplete_path_when_storage_never_lands() -> None:
    """If ``storage`` never lands but SAB exposed an ``incomplete_path``
    (files physically on disk), the thread recovers via the in-progress
    dir as a last resort rather than erroring a completed download."""
    plugin = UsenetDownloadPlugin()
    completed_no_path = UsenetStatus(
        id='job1', name='A', state='completed', progress=1.0,
        size=100, downloaded=100, download_speed=0,
        save_path=None, incomplete_path='/sab/incomplete/A',
    )
    # Window of 10s / 2s interval = 5 polls, floored at the miss
    # threshold; supply plenty so the fallback fires.
    row = _drive_download_thread(plugin, [completed_no_path] * 12)
    assert row['state'] == 'Completed, Succeeded'
    assert row['audio_files'] == [str(Path('/done/track1.flac'))]


def test_usenet_thread_errors_when_completed_with_no_path_at_all() -> None:
    """No final save_path AND no incomplete_path → there's nothing to
    scan, so the thread errors (rather than spinning or finalizing a
    phantom path)."""
    plugin = UsenetDownloadPlugin()
    completed_no_path = UsenetStatus(
        id='job1', name='A', state='completed', progress=1.0,
        size=100, downloaded=100, download_speed=0, save_path=None,
    )
    row = _drive_download_thread(plugin, [completed_no_path] * 12)
    assert row['state'] == 'Completed, Errored'
    assert 'save_path' in (row['error'] or '').lower()


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


def test_usenet_reload_settings_refreshes_cached_prowlarr_config(monkeypatch) -> None:
    """Settings saves must update the plugin's held ProwlarrClient.

    The active usenet adapter is rebuilt from config on each call, but
    ProwlarrClient is cached inside the plugin. This is the path that
    used to require a process restart after entering Prowlarr settings.
    """
    settings = {
        'prowlarr.url': '',
        'prowlarr.api_key': '',
    }
    monkeypatch.setattr(
        'core.prowlarr_client.config_manager.get',
        lambda key, default=None: settings.get(key, default),
    )

    plugin = UsenetDownloadPlugin()
    assert plugin._prowlarr.is_configured() is False

    settings.update({
        'prowlarr.url': 'http://prowlarr:9696',
        'prowlarr.api_key': 'secret',
    })
    plugin.reload_settings()

    assert plugin._prowlarr.is_configured() is True


def test_plugins_conform_to_protocol() -> None:
    from core.download_plugins.base import DownloadSourcePlugin
    assert isinstance(TorrentDownloadPlugin(), DownloadSourcePlugin)
    assert isinstance(UsenetDownloadPlugin(), DownloadSourcePlugin)


# ---------------------------------------------------------------------------
# Registry — both should register cleanly
# ---------------------------------------------------------------------------


def test_torrent_album_pick_prefers_seeded_flac(tmp_path: Path) -> None:
    """Album bundle picker prefers high-seeded FLAC over low-seeded MP3
    of comparable size — protects against picking a dead torrent."""
    from core.download_plugins.album_bundle import pick_best_album_release
    from core.download_plugins.torrent import _guess_quality_from_title
    flac = _make_torrent_result(title='Kendrick Lamar - GNX [FLAC]', size=400_000_000, seeders=120)
    mp3 = _make_torrent_result(title='Kendrick Lamar - GNX [MP3 320]', size=120_000_000, seeders=5, guid='guid-2')
    picked = pick_best_album_release([flac, mp3], _guess_quality_from_title)
    assert picked is flac


def test_torrent_album_pick_drops_too_small() -> None:
    """Single-track torrents (~10 MB) shouldn't be picked when the user
    is downloading a whole album — the size floor (40 MB) catches them."""
    from core.download_plugins.album_bundle import pick_best_album_release
    from core.download_plugins.torrent import _guess_quality_from_title
    single = _make_torrent_result(title='Kendrick Lamar - HUMBLE', size=10_000_000, seeders=500)
    album = _make_torrent_result(title='Kendrick Lamar - DAMN [MP3]', size=120_000_000, seeders=50, guid='guid-2')
    picked = pick_best_album_release([single, album], _guess_quality_from_title)
    assert picked is album


def test_torrent_album_pick_falls_back_when_all_outside_size_range() -> None:
    """If every candidate is below the floor (e.g. all results are
    singles), pick the most-seeded one rather than returning None —
    user still wants a download even if it's a track torrent."""
    from core.download_plugins.album_bundle import pick_best_album_release
    from core.download_plugins.torrent import _guess_quality_from_title
    small_a = _make_torrent_result(title='X [MP3]', size=8_000_000, seeders=5)
    small_b = _make_torrent_result(title='Y [MP3]', size=9_000_000, seeders=80, guid='guid-2')
    picked = pick_best_album_release([small_a, small_b], _guess_quality_from_title)
    assert picked is small_b


def test_unique_staging_path_handles_collision(tmp_path: Path) -> None:
    from core.download_plugins.album_bundle import unique_staging_path
    src = tmp_path / 'src' / 'track.flac'
    src.parent.mkdir()
    src.write_bytes(b'fLaC')
    dest_dir = tmp_path / 'staging'
    dest_dir.mkdir()
    # First call returns the natural name.
    first = unique_staging_path(dest_dir, src)
    assert first == dest_dir / 'track.flac'
    first.write_bytes(b'fLaC')
    # Second call picks a non-colliding suffix.
    second = unique_staging_path(dest_dir, src)
    assert second == dest_dir / 'track_1.flac'


def test_torrent_album_to_staging_short_circuits_when_not_configured() -> None:
    """The gate must refuse to operate when Prowlarr isn't set up —
    every later call would hit the network with empty creds."""
    plugin = TorrentDownloadPlugin()
    with patch.object(plugin, 'is_configured', return_value=False):
        outcome = plugin.download_album_to_staging('GNX', 'Kendrick Lamar', '/tmp/staging')
    assert outcome['success'] is False
    assert 'not configured' in outcome['error'].lower()


def test_torrent_album_to_staging_ignores_candidates_without_download_url(tmp_path: Path) -> None:
    plugin = TorrentDownloadPlugin()
    fake_adapter = MagicMock()
    fake_adapter.is_configured.return_value = True
    with patch.object(plugin, 'is_configured', return_value=True), \
         patch.object(plugin._prowlarr, 'search', new=AsyncMock(return_value=[
             _make_torrent_result(download_url=None, magnet_uri=None),
         ])), \
         patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=fake_adapter):
        outcome = plugin.download_album_to_staging('GNX', 'Kendrick Lamar', str(tmp_path))

    assert outcome['success'] is False
    assert 'No torrent results' in outcome['error']
    # Regression (Cezar): "no results" must be fallback-eligible so a
    # torrent-first hybrid returns to the per-track flow (next source)
    # instead of the dispatch marking the batch failed and freezing at
    # "Torrent searching for release 0%".
    assert outcome.get('fallback') is True
    fake_adapter.add_torrent.assert_not_called()


def test_torrent_album_to_staging_no_results_flags_fallback(tmp_path: Path) -> None:
    """Empty Prowlarr search → fallback-eligible failure, not terminal."""
    plugin = TorrentDownloadPlugin()
    fake_adapter = MagicMock()
    fake_adapter.is_configured.return_value = True
    with patch.object(plugin, 'is_configured', return_value=True), \
         patch.object(plugin._prowlarr, 'search', new=AsyncMock(return_value=[])), \
         patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=fake_adapter):
        outcome = plugin.download_album_to_staging('GNX', 'Kendrick Lamar', str(tmp_path))
    assert outcome['success'] is False
    assert 'No torrent results' in outcome['error']
    assert outcome.get('fallback') is True
    fake_adapter.add_torrent.assert_not_called()


def test_usenet_album_to_staging_no_results_flags_fallback(tmp_path: Path) -> None:
    """Same contract for usenet: an empty search must fall back to the
    per-track flow rather than hard-failing the album batch."""
    plugin = UsenetDownloadPlugin()
    fake_adapter = MagicMock()
    fake_adapter.is_configured.return_value = True
    with patch.object(plugin, 'is_configured', return_value=True), \
         patch.object(plugin._prowlarr, 'search', new=AsyncMock(return_value=[])), \
         patch('core.download_plugins.usenet.get_active_usenet_adapter', return_value=fake_adapter):
        outcome = plugin.download_album_to_staging('GNX', 'Kendrick Lamar', str(tmp_path))
    assert outcome['success'] is False
    assert 'No usenet results' in outcome['error']
    assert outcome.get('fallback') is True
    fake_adapter.add_nzb.assert_not_called()


def test_registry_includes_torrent_and_usenet() -> None:
    """The registry decides what shows up in the orchestrator's
    iteration helpers. If we forget to register a new plugin the
    download source dropdown will silently no-op."""
    from core.download_plugins.registry import build_default_registry
    registry = build_default_registry()
    names = registry.names()
    assert 'torrent' in names
    assert 'usenet' in names


# ---------------------------------------------------------------------------
# Stalled-torrent handling (noldevin) — the _handle_stalled action path
# ---------------------------------------------------------------------------


def test_handle_stalled_abandon_removes_and_fails():
    plugin = TorrentDownloadPlugin()
    with plugin._lock:
        plugin.active_downloads['d1'] = {'state': 'InProgress, Downloading', 'progress': 0.0}

    adapter = MagicMock()
    adapter.remove = AsyncMock(return_value=True)
    adapter.pause = AsyncMock(return_value=True)

    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=adapter), \
         patch('core.download_plugins.torrent.get_stall_timeout', return_value=600):
        plugin._handle_stalled('d1', 'HASH123', 'abandon')

    adapter.remove.assert_called_once()
    assert adapter.remove.call_args.kwargs.get('delete_files') is True  # partial junk removed
    adapter.pause.assert_not_called()
    row = plugin.active_downloads['d1']
    assert row['state'] == 'Completed, Errored'
    assert 'stalled' in (row.get('error') or '').lower()
    assert 'removed' in (row.get('error') or '').lower()


def test_handle_stalled_pause_pauses_and_fails():
    plugin = TorrentDownloadPlugin()
    with plugin._lock:
        plugin.active_downloads['d2'] = {'state': 'InProgress, Downloading', 'progress': 0.0}

    adapter = MagicMock()
    adapter.remove = AsyncMock(return_value=True)
    adapter.pause = AsyncMock(return_value=True)

    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=adapter), \
         patch('core.download_plugins.torrent.get_stall_timeout', return_value=600):
        plugin._handle_stalled('d2', 'HASH456', 'pause')

    adapter.pause.assert_called_once()
    adapter.remove.assert_not_called()                 # data left for the user
    row = plugin.active_downloads['d2']
    assert row['state'] == 'Completed, Errored'
    assert 'paused' in (row.get('error') or '').lower()


def test_handle_stalled_survives_adapter_error():
    plugin = TorrentDownloadPlugin()
    with plugin._lock:
        plugin.active_downloads['d3'] = {'state': 'InProgress, Downloading'}

    adapter = MagicMock()
    adapter.remove = AsyncMock(side_effect=RuntimeError("client down"))

    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=adapter), \
         patch('core.download_plugins.torrent.get_stall_timeout', return_value=600):
        plugin._handle_stalled('d3', 'HASH789', 'abandon')   # must not raise

    # Download still fails cleanly even when the client call blew up.
    assert plugin.active_downloads['d3']['state'] == 'Completed, Errored'
