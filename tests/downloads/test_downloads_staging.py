"""Tests for core/downloads/staging.py — staging-folder match shortcut."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from core.downloads import staging as ds
from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
)


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


@dataclass
class _Track:
    name: str = 'Hello'
    artists: list = None
    album: str = 'Album'

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Artist One']


class _FakeMatchingEngine:
    @staticmethod
    def normalize_string(s):
        return (s or '').lower().strip()


class _FakeConfig:
    def __init__(self, transfer_path):
        self._transfer_path = transfer_path

    def get(self, key, default=None):
        if key == 'soulseek.transfer_path':
            return self._transfer_path
        return default


def _build_deps(
    *,
    transfer_path,
    staging_files=None,
    post_process_calls=None,
):
    post_process_calls = post_process_calls if post_process_calls is not None else []
    deps = ds.StagingDeps(
        config_manager=_FakeConfig(transfer_path),
        matching_engine=_FakeMatchingEngine(),
        get_staging_file_cache=lambda batch_id: staging_files or [],
        docker_resolve_path=lambda p: p,  # passthrough
        post_process_matched_download_with_verification=lambda *a, **kw: post_process_calls.append((a, kw)),
    )
    deps._post_process_calls = post_process_calls
    return deps


def _seed_task(task_id, *, track_info=None):
    download_tasks[task_id] = {
        'status': 'searching',
        'track_info': track_info or {},
        'used_sources': set(),
        'download_id': None,
    }


# ---------------------------------------------------------------------------
# No staging files / no match
# ---------------------------------------------------------------------------

def test_no_staging_files_returns_false(tmp_path):
    deps = _build_deps(transfer_path=str(tmp_path), staging_files=[])
    _seed_task('t1')

    result = ds.try_staging_match('t1', 'b1', _Track(), deps)

    assert result is False


def test_no_track_title_returns_false(tmp_path):
    deps = _build_deps(transfer_path=str(tmp_path), staging_files=[
        {'full_path': str(tmp_path / 'src.flac'), 'title': 'Hello', 'artist': 'Artist One'},
    ])
    _seed_task('t2')

    track = _Track(name='')
    result = ds.try_staging_match('t2', 'b1', track, deps)

    assert result is False


def test_low_confidence_match_returns_false(tmp_path):
    """Match below 0.75 combined score → fall through."""
    deps = _build_deps(transfer_path=str(tmp_path), staging_files=[
        {'full_path': str(tmp_path / 'src.flac'),
         'title': 'Completely Different Song',
         'artist': 'Different Artist'},
    ])
    _seed_task('t3')

    result = ds.try_staging_match('t3', 'b1', _Track(name='Hello'), deps)

    assert result is False


# ---------------------------------------------------------------------------
# High-confidence match — file copy + post-processing
# ---------------------------------------------------------------------------

def test_exact_match_copies_to_transfer_and_marks_post_processing(tmp_path):
    """High-confidence match → file copied, task → post_processing, post-proc invoked."""
    src_file = tmp_path / 'staging' / 'Hello.flac'
    src_file.parent.mkdir()
    src_file.write_bytes(b'fake audio')

    transfer_dir = tmp_path / 'transfer'

    deps = _build_deps(
        transfer_path=str(transfer_dir),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Hello', 'artist': 'Artist One'},
        ],
    )
    _seed_task('t4')

    result = ds.try_staging_match('t4', 'b1', _Track(), deps)

    assert result is True
    # File copied
    assert (transfer_dir / 'Hello.flac').exists()
    # Task transitioned to post_processing
    assert download_tasks['t4']['status'] == 'post_processing'
    assert download_tasks['t4']['username'] == 'staging'
    assert download_tasks['t4']['staging_match'] is True
    # Post-processing invoked
    assert len(deps._post_process_calls) == 1
    args, _ = deps._post_process_calls[0]
    context_key = args[0]
    assert context_key == 'staging_t4'


def test_existing_file_in_transfer_gets_staging_suffix(tmp_path):
    """If destination already exists, suffix '_staging' added to avoid overwrite."""
    src_file = tmp_path / 'staging' / 'Hello.flac'
    src_file.parent.mkdir()
    src_file.write_bytes(b'new audio')

    transfer_dir = tmp_path / 'transfer'
    transfer_dir.mkdir()
    # Existing file with same name in transfer dir
    (transfer_dir / 'Hello.flac').write_bytes(b'old audio')

    deps = _build_deps(
        transfer_path=str(transfer_dir),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Hello', 'artist': 'Artist One'},
        ],
    )
    _seed_task('t5')

    result = ds.try_staging_match('t5', 'b1', _Track(), deps)

    assert result is True
    # Original file untouched
    assert (transfer_dir / 'Hello.flac').read_bytes() == b'old audio'
    # New file has _staging suffix
    assert (transfer_dir / 'Hello_staging.flac').exists()
    assert (transfer_dir / 'Hello_staging.flac').read_bytes() == b'new audio'


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def test_explicit_album_context_uses_real_data(tmp_path):
    """track_info with _is_explicit_album_download=True copies real album/artist context."""
    src_file = tmp_path / 'staging' / 'Hello.flac'
    src_file.parent.mkdir()
    src_file.touch()

    explicit_album = {'id': 'alb-real', 'name': 'Real Album', 'release_date': '2024-05-05',
                      'total_tracks': 12, 'total_discs': 2, 'album_type': 'album',
                      'image_url': 'http://img/a.jpg'}
    explicit_artist = {'id': 'art-real', 'name': 'Real Artist'}

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Hello', 'artist': 'Real Artist'},
        ],
    )
    _seed_task('t6', track_info={
        '_is_explicit_album_download': True,
        '_explicit_album_context': explicit_album,
        '_explicit_artist_context': explicit_artist,
        'track_number': 5,
        'disc_number': 2,
    })

    ds.try_staging_match('t6', 'b1', _Track(name='Hello', artists=['Real Artist']), deps)

    ctx = matched_downloads_context['staging_t6']
    assert ctx['spotify_album']['id'] == 'alb-real'
    assert ctx['spotify_album']['total_discs'] == 2
    assert ctx['spotify_artist']['id'] == 'art-real'
    assert ctx['is_album_download'] is True
    assert ctx['has_clean_spotify_data'] is True
    assert ctx['staging_source'] is True


def test_fallback_context_synthesizes_from_track(tmp_path):
    """Without explicit context, synthesizes spotify_artist/album from the track."""
    src_file = tmp_path / 'staging' / 'Hello.flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Hello', 'artist': 'Artist One'},
        ],
    )
    _seed_task('t7')

    ds.try_staging_match('t7', 'b1', _Track(name='Hello', album='Some Album'), deps)

    ctx = matched_downloads_context['staging_t7']
    assert ctx['spotify_artist']['id'] == 'staging'
    assert ctx['spotify_artist']['name'] == 'Artist One'
    assert ctx['spotify_album']['id'] == 'staging'
    assert ctx['spotify_album']['name'] == 'Some Album'
    assert ctx['is_album_download'] is True  # album differs from title


def test_album_same_as_title_not_treated_as_album(tmp_path):
    """When track album == title, is_album_download stays False."""
    src_file = tmp_path / 'staging' / 'Hello.flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Hello', 'artist': 'Artist One'},
        ],
    )
    _seed_task('t8')

    # album == name → single-track release pattern
    ds.try_staging_match('t8', 'b1', _Track(name='Hello', album='Hello'), deps)

    ctx = matched_downloads_context['staging_t8']
    assert ctx['is_album_download'] is False


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_copy_failure_returns_false(tmp_path):
    """If shutil.copy2 raises (e.g., source vanished), returns False, no post-proc invoked."""
    # Source path that doesn't exist → copy2 raises FileNotFoundError
    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {'full_path': str(tmp_path / 'staging' / 'missing.flac'),
             'title': 'Hello', 'artist': 'Artist One'},
        ],
    )
    _seed_task('t9')

    result = ds.try_staging_match('t9', 'b1', _Track(), deps)

    assert result is False
    assert deps._post_process_calls == []
