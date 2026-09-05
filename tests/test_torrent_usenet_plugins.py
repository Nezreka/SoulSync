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
    # Prowlarr did not supply a codec here; do not invent MP3.
    assert _guess_quality_from_title('Just A Title') == 'unknown'
    assert _guess_quality_from_title('') == 'unknown'


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


def test_torrent_project_results_prefers_the_torrent_url_over_the_magnet() -> None:
    """#1139 reversed this. A magnet hands the client an info-hash and nothing
    else — it has to find the swarm itself, and a client without working
    DHT/PEX (or a release with no live peers) parks on "downloading metadata"
    indefinitely. The http link lets SoulSync fetch the real .torrent
    server-side and push the file, which is what Sonarr/Radarr do.

    The magnet is not thrown away: it rides along as the fallback for when
    that fetch fails, which is what the pair encoding below is for."""
    from core.download_plugins.candidate_store import get_candidate_store
    from core.download_plugins.torrent import _decode_candidate
    plugin = TorrentDownloadPlugin()
    magnet = 'magnet:?xt=urn:btih:abc'
    results = [_make_torrent_result(magnet_uri=magnet, download_url='https://x/y.torrent')]
    tracks, _ = plugin._project_results(results)
    token, _ = _decode_filename(tracks[0].filename)
    url, fallback = _decode_candidate(get_candidate_store().resolve(token))
    assert url == 'https://x/y.torrent'
    assert fallback == magnet


def test_torrent_project_results_still_uses_the_magnet_when_it_is_all_there_is() -> None:
    from core.download_plugins.candidate_store import get_candidate_store
    from core.download_plugins.torrent import _decode_candidate
    plugin = TorrentDownloadPlugin()
    magnet = 'magnet:?xt=urn:btih:def'
    results = [_make_torrent_result(magnet_uri=magnet, download_url=None)]
    tracks, _ = plugin._project_results(results)
    token, _ = _decode_filename(tracks[0].filename)
    url, fallback = _decode_candidate(get_candidate_store().resolve(token))
    assert url == magnet
    # No second link to fall back to, and the magnet must not be its own
    # fallback — add_torrent_smart would otherwise retry the identical add.
    assert fallback is None


def test_torrent_project_results_encodes_token_and_title_in_filename() -> None:
    """P0-03: the filename that reaches the browser carries an opaque
    candidate token — never the indexer download URL (may embed API keys)."""
    from core.download_plugins.candidate_store import get_candidate_store
    plugin = TorrentDownloadPlugin()
    tracks, _ = plugin._project_results([_make_torrent_result()])
    token, display = _decode_filename(tracks[0].filename)
    assert 'https://x/y.torrent' not in tracks[0].filename
    assert get_candidate_store().is_token(token)
    from core.download_plugins.torrent import _decode_candidate
    assert _decode_candidate(get_candidate_store().resolve(token))[0] == 'https://x/y.torrent'
    assert get_candidate_store().resolve_with_metadata(token)[1] == {
        'categories': [3040],
    }
    assert display == 'Danny Brown - Atrocity Exhibition [FLAC]'


def test_torrent_project_falls_back_to_placeholder_when_title_lacks_dash() -> None:
    """When the title has no 'Artist -' prefix we'd auto-parse the
    filename (which starts with the indexer download URL) and end
    up showing the URL in the UI's 'by' field. Pre-filling artist
    with a generic placeholder avoids that — the indexer name (e.g.
    "NZBGeek") must NOT be used as a stand-in artist: it's a source,
    not a performer, and showing it as one is misleading."""
    plugin = TorrentDownloadPlugin()
    tracks, _ = plugin._project_results([_make_torrent_result(title='JustATitle')])
    assert tracks[0].artist == 'Unknown Artist'
    # The indexer name only belongs in source metadata, never the artist.
    assert tracks[0].artist != 'Indexer'
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


def test_torrent_project_results_carries_rich_title_quality() -> None:
    plugin = TorrentDownloadPlugin()
    tracks, _ = plugin._project_results([
        _make_torrent_result(
            title='Danny Brown - Atrocity Exhibition [FLAC 24-96]',
        )
    ])

    assert tracks[0].quality == 'flac'
    assert tracks[0].sample_rate == 96_000
    assert tracks[0].bit_depth == 24
    assert tracks[0]._source_metadata['release_title'].endswith('[FLAC 24-96]')
    assert tracks[0]._source_metadata['categories'] == [3040]


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


def _inflight_row():
    return {
        'id': 'dl-1', 'filename': 'x', 'username': 'torrent',
        'display_name': 'X', 'state': 'InProgress, Downloading',
        'progress': 50.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'torrent_hash': 'h1', 'error': None,
    }


def test_torrent_finalize_walks_only_this_torrents_release_folder(tmp_path: Path) -> None:
    """save_path is the client's save DIRECTORY — on a shared root, walking
    it whole donated the 'first audio file' of some OTHER torrent. With the
    torrent's name known, only <save_path>/<name> is walked."""
    plugin = TorrentDownloadPlugin()
    plugin.active_downloads['dl-1'] = _inflight_row()
    other = tmp_path / 'Another Album [FLAC]'
    other.mkdir()
    (other / 'aaa-other.mp3').write_bytes(b'ID3')          # sorts first in a full walk
    mine = tmp_path / 'My Release'
    mine.mkdir()
    (mine / 'track.mp3').write_bytes(b'ID3')
    plugin._finalize_download('dl-1', str(tmp_path), torrent_name='My Release')
    row = plugin.active_downloads['dl-1']
    assert row['state'] == 'Completed, Succeeded'
    assert row['file_path'].endswith('track.mp3')          # never the other torrent's file


def test_torrent_finalize_single_file_torrent_still_walks_the_save_dir(tmp_path: Path) -> None:
    # A single-FILE torrent's name points at the file itself; the audio
    # walker only walks directories, so the narrowing must not apply.
    plugin = TorrentDownloadPlugin()
    plugin.active_downloads['dl-1'] = _inflight_row()
    (tmp_path / 'Artist - Song.mp3').write_bytes(b'ID3')
    plugin._finalize_download('dl-1', str(tmp_path), torrent_name='Artist - Song.mp3')
    row = plugin.active_downloads['dl-1']
    assert row['state'] == 'Completed, Succeeded'
    assert row['file_path'].endswith('Artist - Song.mp3')


def test_torrent_finalize_rescues_a_wrong_reported_mount(tmp_path: Path, monkeypatch) -> None:
    """TheHomeGuy: qBittorrent reports '/downloads' (its container view); a
    same-named dir exists here but is empty, while the release actually
    landed under the configured download root."""
    from core.download_plugins import album_bundle as ab
    wrong = tmp_path / 'downloads'
    wrong.mkdir()
    real_root = tmp_path / 'real'
    release = real_root / 'My Release'
    release.mkdir(parents=True)
    (release / 'track.mp3').write_bytes(b'ID3')
    monkeypatch.setattr(ab, 'config_manager', type('C', (), {
        'get': staticmethod(lambda key, default=None: str(real_root)
                            if key == 'soulseek.download_path' else default)})())
    plugin = TorrentDownloadPlugin()
    plugin.active_downloads['dl-1'] = _inflight_row()
    plugin._finalize_download('dl-1', str(wrong), torrent_name='My Release')
    row = plugin.active_downloads['dl-1']
    assert row['state'] == 'Completed, Succeeded'
    assert row['file_path'] == str(release / 'track.mp3')


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


def test_usenet_project_encodes_token_in_filename() -> None:
    """P0-03: the browser sees an opaque candidate token, never the NZB URL."""
    from core.download_plugins.candidate_store import get_candidate_store
    plugin = UsenetDownloadPlugin()
    tracks, _ = plugin._project_results([_make_usenet_result()])
    token, display = _decode_filename(tracks[0].filename)
    assert 'https://x/y.nzb' not in tracks[0].filename
    assert get_candidate_store().resolve(token) == 'https://x/y.nzb'
    assert get_candidate_store().resolve_with_metadata(token)[1] == {
        'categories': [3010],
    }
    assert display == 'Some Artist - Some Album'
    # Artist + title should be parsed out, not auto-extracted from filename.
    assert tracks[0].artist == 'Some Artist'
    assert tracks[0].title == 'Some Album'
    assert tracks[0]._source_metadata['release_title'] == 'Some Artist - Some Album'
    # The helper's category is Audio/MP3, structured quality evidence even when
    # the title is bare; bitrate remains unknown until title/file says it.
    assert tracks[0].quality == 'mp3'
    assert tracks[0].bitrate is None
    assert tracks[0]._source_metadata['categories'] == [3010]


def test_usenet_project_falls_back_to_placeholder_when_title_lacks_dash() -> None:
    """Sibling of the torrent-plugin regression: an indexer name (e.g.
    "NZBGeek") must never stand in for the artist when the release title
    has no 'Artist - Title' separator."""
    plugin = UsenetDownloadPlugin()
    tracks, _ = plugin._project_results(
        [_make_usenet_result(title='JustATitle', indexer_name='NZBGeek')]
    )
    assert tracks[0].artist == 'Unknown Artist'
    assert tracks[0].artist != 'NZBGeek'


def test_usenet_project_results_carries_lossy_bitrate() -> None:
    plugin = UsenetDownloadPlugin()
    tracks, _ = plugin._project_results([
        _make_usenet_result(title='Some Artist - Some Album [MP3 320]')
    ])

    assert tracks[0].quality == 'mp3'
    assert tracks[0].bitrate == 320
    assert tracks[0]._source_metadata['release_title'].endswith('[MP3 320]')


def test_usenet_download_uses_the_assigned_profile_before_grab() -> None:
    from core.download_plugins.candidate_store import get_candidate_store

    plugin = UsenetDownloadPlugin()
    plugin.is_configured = lambda: True
    token = get_candidate_store().put('https://x/release.nzb')
    filename = f'{token}{_FILENAME_SEP}Artist - Album [MP3 320]'

    with patch(
        'core.download_plugins.usenet.profile_allowed_formats',
        return_value={'flac'},
    ) as policy:
        result = _run(plugin.download(
            'usenet',
            filename,
            quality_profile_id=88,
        ))

    assert result is None
    policy.assert_called_once_with(88)


def test_usenet_download_honors_category_quality_for_a_bare_title() -> None:
    plugin = UsenetDownloadPlugin()
    plugin.is_configured = lambda: True
    tracks, _albums = plugin._project_results([_make_usenet_result()])

    with patch(
        'core.download_plugins.usenet.profile_allowed_formats',
        return_value={'mp3'},
    ), patch.object(plugin, '_download_thread', lambda *args, **kwargs: None):
        result = _run(plugin.download(
            'usenet',
            tracks[0].filename,
            quality_profile_id=89,
        ))

    assert result is not None


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


def _drive_download_thread(plugin, statuses, *, window_seconds=10.0, snapshot=None):
    """Run ``_download_thread`` end-to-end against a scripted adapter.

    ``statuses`` is the sequence of ``UsenetStatus`` reads the poll loop
    will see (one per poll). Returns the finished active_downloads row.

    ``snapshot`` stubs the P2-21 incomplete_path stability check
    (``snapshot_incomplete_path``) — a real filesystem probe that would
    otherwise return ``None`` for the fake ``/sab/...`` test paths and
    make the fallback branch un-testable. Defaults to a fixed value so
    two consecutive polls always look "stable" once reached; pass a
    ``side_effect`` list/callable to simulate a still-changing path."""
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
    snapshot_patch_kwargs = {'return_value': ('fixed', 1, 0.0)} if snapshot is None else snapshot
    with patch('core.download_plugins.usenet.get_active_usenet_adapter', return_value=adapter), \
         patch('core.download_plugins.usenet.run_async', side_effect=lambda x: x), \
         patch('core.download_plugins.usenet.get_completed_no_path_window_seconds',
               return_value=window_seconds), \
         patch('core.download_plugins.usenet.time', clock), \
         patch('core.download_plugins.usenet.snapshot_incomplete_path',
               **snapshot_patch_kwargs), \
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
    (files physically on disk) whose fingerprint has stopped changing
    (P2-21), the thread recovers via the in-progress dir as a last resort
    rather than erroring a completed download."""
    plugin = UsenetDownloadPlugin()
    completed_no_path = UsenetStatus(
        id='job1', name='A', state='completed', progress=1.0,
        size=100, downloaded=100, download_speed=0,
        save_path=None, incomplete_path='/sab/incomplete/A',
    )
    # Window of 10s / 2s interval = 5 polls, floored at the miss
    # threshold; supply plenty so the fallback (plus one extra
    # stability-confirmation poll) fires.
    row = _drive_download_thread(plugin, [completed_no_path] * 12)
    assert row['state'] == 'Completed, Succeeded'
    assert row['audio_files'] == [str(Path('/done/track1.flac'))]


def test_usenet_thread_waits_for_incomplete_path_to_stop_changing() -> None:
    """P2-21: the window elapsing alone must not trigger the fallback —
    while the client is still writing into ``incomplete_path`` (fingerprint
    keeps changing), the thread keeps polling instead of finalizing a
    possibly-partial directory. Once it stabilizes, it recovers."""
    plugin = UsenetDownloadPlugin()
    completed_no_path = UsenetStatus(
        id='job1', name='A', state='completed', progress=1.0,
        size=100, downloaded=100, download_speed=0,
        save_path=None, incomplete_path='/sab/incomplete/A',
    )
    growing = iter([1, 2, 3, 4, 4, 4, 4, 4])
    row = _drive_download_thread(
        plugin, [completed_no_path] * 20,
        snapshot={'side_effect': lambda path: (next(growing, 4), 1, 0.0)},
    )
    assert row['state'] == 'Completed, Succeeded'
    assert row['audio_files'] == [str(Path('/done/track1.flac'))]


def test_usenet_thread_resolves_incomplete_path_before_stability_check() -> None:
    """P2-21 follow-up: a client-container incomplete_path unreadable from
    here must be remapped via resolve_reported_save_path BEFORE the
    stability snapshot — the same fix applied to poll_album_download's
    stability gate — otherwise a split-container SAB/NZBGet mount can
    never stabilize and the download hangs until the outer deadline."""
    plugin = UsenetDownloadPlugin()
    completed_no_path = UsenetStatus(
        id='job1', name='A', state='completed', progress=1.0,
        size=100, downloaded=100, download_speed=0,
        save_path=None, incomplete_path='/client-container/incomplete/A',
    )
    with patch(
        'core.download_plugins.usenet.resolve_reported_save_path',
        side_effect=lambda p, *a, **kw: (
            '/local/incomplete/A' if p == '/client-container/incomplete/A' else p
        ),
    ):
        row = _drive_download_thread(
            plugin, [completed_no_path] * 12,
            snapshot={
                'side_effect': lambda path: (
                    (100, 3, 1.0) if path == '/local/incomplete/A' else None
                ),
            },
        )
    assert row['state'] == 'Completed, Succeeded'
    assert row['audio_files'] == [str(Path('/done/track1.flac'))]


def test_usenet_thread_gives_up_when_incomplete_path_is_never_readable() -> None:
    """Sibling of the same cap added to ``poll_album_download``: if
    ``incomplete_path`` can never be read at all (e.g. no
    ``usenet_path_mappings`` entry for a split-container deployment, not
    just an unresolved-but-real path), the stability snapshot returns
    ``None`` on every single poll and can never stabilize. After a
    handful of consecutive unreadable polls the thread must give up with
    the same error the pre-stability-gate code used, rather than quietly
    retrying for the full outer deadline (6h)."""
    plugin = UsenetDownloadPlugin()
    completed_no_path = UsenetStatus(
        id='job1', name='A', state='completed', progress=1.0,
        size=100, downloaded=100, download_speed=0,
        save_path=None, incomplete_path='/sab/incomplete/A',
    )
    row = _drive_download_thread(
        plugin, [completed_no_path] * 30,
        snapshot={'return_value': None},
    )
    assert row['state'] == 'Completed, Errored'
    assert 'never reported a save_path' in (row['error'] or '').lower()


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


@pytest.mark.parametrize('module, plugin_factory, adapter_patch, make_result', [
    ('core.download_plugins.torrent', lambda: TorrentDownloadPlugin(),
     'core.download_plugins.torrent.get_active_torrent_adapter',
     _make_torrent_result),
    ('core.download_plugins.usenet', lambda: UsenetDownloadPlugin(),
     'core.download_plugins.usenet.get_active_usenet_adapter',
     _make_usenet_result),
])
def test_album_duration_reaches_the_release_picker(module, plugin_factory,
                                                   adapter_patch, make_result,
                                                   tmp_path: Path) -> None:
    """The size gate is useless if the duration stops at the plugin boundary.

    Both album plugins must hand ``expected_duration_seconds`` to
    ``pick_best_album_release``; without it the gate silently has no opinion,
    which looks identical to "every release was plausible".
    """
    plugin = plugin_factory()
    fake_adapter = MagicMock()
    fake_adapter.is_configured.return_value = True
    seen = {}

    def _capture(*_args, **kwargs):
        seen.update(kwargs)
        return None

    with patch.object(plugin, 'is_configured', return_value=True), \
         patch.object(plugin._prowlarr, 'search',
                      new=AsyncMock(return_value=[make_result()])), \
         patch(f'{module}.pick_best_album_release', side_effect=_capture), \
         patch(adapter_patch, return_value=fake_adapter):
        plugin.download_album_to_staging(
            'GNX', 'Kendrick Lamar', str(tmp_path),
            expected_duration_seconds=2700,
        )

    assert seen.get('expected_duration_seconds') == 2700


@pytest.mark.parametrize('title, size, projected', [
    ('Danny Brown - Atrocity Exhibition [FLAC] (sample)', 15_000_000, False),
    # "Sample" is only evidence together with a size no album can have.
    ('Danny Brown - Atrocity Exhibition [FLAC] sample', 500_000_000, True),
    ('Danny Brown - Sample Text [FLAC]', 500_000_000, True),
    # A small release that never claims to be a sample is somebody's single.
    ('Danny Brown - Atrocity Exhibition [FLAC]', 15_000_000, True),
])
def test_sample_releases_never_become_candidates(title, size, projected) -> None:
    """Lidarr's NotSampleSpecification, at the projection boundary.

    The album picker's 40 MB floor already refuses these, but the per-track
    lane had no floor at all, so a 15 MB "sample" was a candidate like any
    other — and it passes a FLAC profile, because it really is FLAC.
    """
    plugin = TorrentDownloadPlugin()
    tracks, _albums = plugin._project_results([
        _make_torrent_result(title=title, size=size),
    ])

    assert bool(tracks) is projected
