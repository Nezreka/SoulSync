"""Integration tests for RuTracker-shaped torrent album-bundle downloads.

These tests keep the real torrent album-bundle code in the loop and fake only
external boundaries (Prowlarr, torrent client, tagging).
They encode two real RuTracker naming patterns reported by users:

- artist-prefixed, unnumbered files: ``Nosferatu_-_Beaver_Cleaver.flac``
- compilation files: ``01 - Artist - Title.flac``

Both are marked strict-xfail while they document the current matcher gap. When
the album-bundle matcher learns those shapes, the XPASS will fail and remind us
to remove the marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.downloads import album_bundle_dispatch as dispatch
from core.downloads import staging as ds
from core.download_plugins.torrent import TorrentDownloadPlugin
from core.prowlarr_client import ProwlarrSearchResult
from core.runtime_state import download_tasks, matched_downloads_context
from core.torrent_clients.base import TorrentStatus


@pytest.fixture(autouse=True)
def reset_runtime_state():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


@dataclass
class _Track:
    name: str
    artists: list[str]
    album: str
    track_number: int
    disc_number: int = 1


class _FakeMatchingEngine:
    @staticmethod
    def normalize_string(value: str) -> str:
        import re

        value = (value or '').replace('_', ' ').lower().strip()
        value = re.sub(r'[^a-z0-9]+', ' ', value)
        return re.sub(r'\s+', ' ', value).strip()


class _FakeConfig:
    def __init__(self, transfer_path: Path):
        self.transfer_path = str(transfer_path)

    def get(self, key: str, default: Any = None) -> Any:
        if key == 'soulseek.transfer_path':
            return self.transfer_path
        return default


class _BatchState:
    def __init__(self):
        self.rows: dict[str, dict[str, Any]] = {}

    def update_fields(self, batch_id: str, fields: dict) -> None:
        self.rows.setdefault(batch_id, {}).update(fields)


    def mark_failed(self, batch_id: str, error: str) -> None:
        self.update_fields(batch_id, {
            'phase': 'failed',
            'error': error,
            'album_bundle_state': 'failed',
        })


class _FakeTorrentAdapter:
    def __init__(self, save_path: Path):
        self.save_path = str(save_path)
        self.added_urls: list[str] = []

    def is_configured(self) -> bool:
        return True

    async def add_torrent(self, url_or_magnet: str, category: str = "soulsync", save_path: str | None = None) -> str:
        self.added_urls.append(url_or_magnet)
        return 'FAKEHASH'

    async def get_status(self, torrent_id: str) -> TorrentStatus:
        return TorrentStatus(
            id=torrent_id,
            name='rutracker-release',
            state='seeding',
            progress=1.0,
            size=123_456_789,
            downloaded=123_456_789,
            download_speed=0,
            upload_speed=0,
            seeders=10,
            save_path=self.save_path,
        )


def _make_rutracker_result(title: str, *, size: int = 500_000_000) -> ProwlarrSearchResult:
    return ProwlarrSearchResult(
        guid=f'https://rutracker.org/forum/viewtopic.php?t={abs(hash(title))}',
        title=title,
        indexer_id=350,
        indexer_name='RuTracker',
        protocol='torrent',
        download_url='https://prowlarr.example/download.torrent',
        magnet_uri='magnet:?xt=urn:btih:FAKEHASH',
        info_url='https://rutracker.org/forum/viewtopic.php?t=3503447',
        size=size,
        seeders=25,
        leechers=1,
        grabs=100,
        categories=[3040],
        raw={},
    )


def _write_downloaded_files(download_dir: Path, filenames: list[str]) -> None:
    download_dir.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (download_dir / name).write_bytes(b'fake flac bytes')


def _seed_task(task_id: str, track: _Track, album_name: str, artist_name: str) -> None:
    download_tasks[task_id] = {
        'status': 'searching',
        'track_info': {
            '_is_explicit_album_download': True,
            '_explicit_album_context': {
                'id': 'album-id',
                'name': album_name,
                'total_tracks': 0,
                'total_discs': 1,
                'artists': [{'name': artist_name}],
            },
            '_explicit_artist_context': {'id': 'artist-id', 'name': artist_name},
            'track_number': track.track_number,
            'disc_number': track.disc_number,
        },
        'used_sources': set(),
        'download_id': None,
    }


def _staging_cache_from_dir(staging_dir: str) -> list[dict[str, Any]]:
    files = sorted(Path(staging_dir).glob('*.flac'))
    return [
        {
            'full_path': str(path),
            # Simulate the real weak fallback for untagged release files: title
            # is the filename stem, not clean track metadata.
            'title': path.stem,
            'artist': '',
        }
        for path in files
    ]


def _run_album_bundle_then_claim_tracks(
    *,
    tmp_path: Path,
    album_name: str,
    artist_name: str,
    release_title: str,
    filenames: list[str],
    tracks: list[_Track],
) -> list[tuple[str, tuple, dict]]:
    state = _BatchState()
    batch_id = 'rutracker_batch'
    post_process_calls: list[tuple[str, tuple, dict]] = []
    download_dir = tmp_path / 'torrent_download'
    _write_downloaded_files(download_dir, filenames)
    plugin = TorrentDownloadPlugin()
    adapter = _FakeTorrentAdapter(download_dir)
    search = AsyncMock(return_value=[_make_rutracker_result(release_title)])

    with patch.object(plugin, 'is_configured', return_value=True), \
         patch.object(plugin._prowlarr, 'search', new=search), \
         patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=adapter):
        engaged = dispatch.try_dispatch(
            batch_id=batch_id,
            is_album=True,
            album_context={'name': album_name},
            artist_context={'name': artist_name},
            config_get=lambda key, default=None: str(tmp_path / 'bundle_staging') if key == 'download_source.album_bundle_staging_path' else default,
            plugin_resolver=lambda mode: plugin if mode == 'torrent' else None,
            state=state,
            source_override='torrent',
        )

    # Success returns False by design: dispatch staged files, then falls through
    # so per-track workers can claim them from private staging.
    assert engaged is False
    assert state.rows[batch_id]['album_bundle_state'] == 'staged'
    assert adapter.added_urls == ['magnet:?xt=urn:btih:FAKEHASH']
    search.assert_awaited_once()
    staging_dir = state.rows[batch_id]['album_bundle_staging_path']

    deps = ds.StagingDeps(
        config_manager=_FakeConfig(tmp_path / 'transfer'),
        matching_engine=_FakeMatchingEngine(),
        get_staging_file_cache=lambda _batch_id: _staging_cache_from_dir(staging_dir),
        docker_resolve_path=lambda path: path,
        post_process_matched_download_with_verification=lambda *args, **kwargs: post_process_calls.append((args[0], args, kwargs)),
        get_batch_field=lambda _batch_id, field: state.rows[batch_id].get(field),
    )

    for index, track in enumerate(tracks, start=1):
        task_id = f'track_{index:02d}'
        _seed_task(task_id, track, album_name, artist_name)
        ds.try_staging_match(task_id, batch_id, track, deps)

    return post_process_calls


def test_rutracker_artist_prefixed_unnumbered_album_files_claim_all_tracks(tmp_path):
    # Real-world source: https://rutracker.org/forum/viewtopic.php?t=3503447
    # Title: (Hardcore, Gabber) Nosferatu - Never Met Equals [WEB] - 2006, FLAC (tracks) lossless
    calls = _run_album_bundle_then_claim_tracks(
        tmp_path=tmp_path,
        album_name='Never Met Equals',
        artist_name='Nosferatu',
        release_title='(Hardcore, Gabber) Nosferatu - Never Met Equals [WEB] - 2006, FLAC (tracks) lossless',
        filenames=[
            'Nosferatu_-_Beaver_Cleaver.flac',
            'Nosferatu_-_Disorder_Of_The_Mind.flac',
            'Nosferatu_-_Knock_Out.flac',
            'Nosferatu_-_Underground_Stream_(Dione_remix).flac',
        ],
        tracks=[
            _Track('Beaver Cleaver', ['Nosferatu'], 'Never Met Equals', 1),
            _Track('Knock Out', ['Nosferatu'], 'Never Met Equals', 2),
            _Track('Disorder Of The Mind', ['Nosferatu'], 'Never Met Equals', 3),
            _Track('The Underground Stream (Dione Remix)', ['Nosferatu'], 'Never Met Equals', 4),
        ],
    )

    assert len(calls) == 4
    assert [matched_downloads_context[f'staging_track_{i:02d}']['track_info']['track_number'] for i in range(1, 5)] == [1, 2, 3, 4]


def test_rutracker_compilation_album_files_claim_duplicate_title_tracks_by_number(tmp_path):
    # Real-world source: https://rutracker.org/forum/viewtopic.php?t=1599889
    # Title: (Happy Hardcore) VA - Happy Hardcore vol.1 - 1997, FLAC (tracks+.cue), lossless
    calls = _run_album_bundle_then_claim_tracks(
        tmp_path=tmp_path,
        album_name='Happy Hardcore vol.1',
        artist_name='Various Artists',
        release_title='(Happy Hardcore) VA - Happy Hardcore vol.1 - 1997, FLAC (tracks+.cue), lossless',
        filenames=[
            '01 - 4 Tune Fairytales - Take Me 2 Wonderland (Extended Mix).flac',
            '02 - Mindtrust - The Key To Your Heart (Extended Mix).flac',
            '03 - Critical Mass - Happy Generation (Trimix).flac',
            '14 - Mindtrust - The Key To Your Heart (Extended Mix).flac',
            '16 - Critical Mass - Happy Generation (Deaz. D. Remix).flac',
        ],
        tracks=[
            _Track('Take Me 2 Wonderland (Extended Mix)', ['4 Tune Fairytales'], 'Happy Hardcore vol.1', 1),
            _Track('The Key To Your Heart (Extended Mix)', ['Mindtrust'], 'Happy Hardcore vol.1', 2),
            _Track('Happy Generation (Trimix)', ['Critical Mass'], 'Happy Hardcore vol.1', 3),
            _Track('The Key To Your Heart (Extended Mix)', ['Mindtrust'], 'Happy Hardcore vol.1', 14),
            _Track('Happy Generation (Deaz. D. Remix)', ['Critical Mass'], 'Happy Hardcore vol.1', 16),
        ],
    )

    assert len(calls) == 5
    assert [matched_downloads_context[f'staging_track_{i:02d}']['track_info']['track_number'] for i in range(1, 6)] == [1, 2, 3, 14, 16]
