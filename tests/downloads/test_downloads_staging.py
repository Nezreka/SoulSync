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
    get_batch_field=None,
):
    post_process_calls = post_process_calls if post_process_calls is not None else []
    deps = ds.StagingDeps(
        config_manager=_FakeConfig(transfer_path),
        matching_engine=_FakeMatchingEngine(),
        get_staging_file_cache=lambda batch_id: staging_files or [],
        docker_resolve_path=lambda p: p,  # passthrough
        post_process_matched_download_with_verification=lambda *a, **kw: post_process_calls.append((a, kw)),
        get_batch_field=get_batch_field,
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


def test_private_album_bundle_staging_source_is_removed_after_claim(tmp_path):
    src_file = tmp_path / 'private' / 'Hello.flac'
    src_file.parent.mkdir()
    src_file.write_bytes(b'fake audio')

    transfer_dir = tmp_path / 'transfer'

    def get_batch_field(_batch_id, field):
        if field == 'album_bundle_source':
            return 'torrent'
        if field == 'album_bundle_private_staging':
            return True
        return None

    deps = _build_deps(
        transfer_path=str(transfer_dir),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Hello', 'artist': 'Artist One'},
        ],
        get_batch_field=get_batch_field,
    )
    _seed_task('t_private')

    result = ds.try_staging_match('t_private', 'b_private', _Track(), deps)

    assert result is True
    assert (transfer_dir / 'Hello.flac').exists()
    assert not src_file.exists()
    assert download_tasks['t_private']['username'] == 'torrent'


def test_public_staging_source_is_kept_after_match(tmp_path):
    src_file = tmp_path / 'staging' / 'Hello.flac'
    src_file.parent.mkdir()
    src_file.write_bytes(b'fake audio')

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Hello', 'artist': 'Artist One'},
        ],
    )
    _seed_task('t_public')

    result = ds.try_staging_match('t_public', 'b_public', _Track(), deps)

    assert result is True
    assert src_file.exists()


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


def test_staging_context_falls_back_to_matched_file_track_number(tmp_path):
    """Album-bundle staging can recover numbering from the selected audio file."""
    src_file = tmp_path / 'staging' / '03 - Backseat Freestyle.flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {
                'full_path': str(src_file),
                'title': 'Backseat Freestyle',
                'artist': 'Kendrick Lamar',
                'track_number': 3,
                'disc_number': 1,
            },
        ],
    )
    _seed_task('t6b', track_info={
        '_is_explicit_album_download': True,
        '_explicit_album_context': {'id': 'alb', 'name': 'good kid, m.A.A.d city (Deluxe)'},
        '_explicit_artist_context': {'id': 'art', 'name': 'Kendrick Lamar'},
    })

    ds.try_staging_match(
        't6b', 'b1',
        _Track(name='Backseat Freestyle', artists=['Kendrick Lamar']),
        deps,
    )

    ctx = matched_downloads_context['staging_t6b']
    assert ctx['original_search_result']['track_number'] == 3
    assert ctx['original_search_result']['disc_number'] == 1


def test_private_album_bundle_staging_overrides_default_track_info_number(tmp_path):
    """Private release staging trusts the selected file number over weak task defaults."""
    src_file = tmp_path / 'staging' / '04-kendrick_lamar-the_art_of_peer_pressure.flac'
    src_file.parent.mkdir()
    src_file.touch()

    def get_batch_field(_batch_id, field):
        if field == 'album_bundle_source':
            return 'torrent'
        if field == 'album_bundle_private_staging':
            return True
        return None

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {
                'full_path': str(src_file),
                'title': 'The Art of Peer Pressure',
                'artist': 'Kendrick Lamar',
            },
        ],
        get_batch_field=get_batch_field,
    )
    _seed_task('t6c', track_info={
        '_is_explicit_album_download': True,
        '_explicit_album_context': {'id': 'alb', 'name': 'good kid, m.A.A.d city (Deluxe)'},
        '_explicit_artist_context': {'id': 'art', 'name': 'Kendrick Lamar'},
        'track_number': 1,
    })

    ds.try_staging_match(
        't6c', 'b1',
        _Track(name='The Art of Peer Pressure', artists=['Kendrick Lamar']),
        deps,
    )

    ctx = matched_downloads_context['staging_t6c']
    assert ctx['track_info']['track_number'] == 4
    assert ctx['original_search_result']['track_number'] == 4
    assert ctx['original_search_result']['username'] == 'torrent'
    assert ctx['original_search_result']['filename'] == str(src_file)


def test_private_album_bundle_staging_keeps_task_number_when_file_has_no_number(tmp_path):
    """Private release staging must not turn every unnumbered release file into track 1."""
    src_file = tmp_path / 'staging' / 'Katy Perry - Firework.flac'
    src_file.parent.mkdir()
    src_file.touch()

    def get_batch_field(_batch_id, field):
        if field == 'album_bundle_source':
            return 'soulseek'
        if field == 'album_bundle_private_staging':
            return True
        return None

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {
                'full_path': str(src_file),
                'title': 'Firework',
                'artist': 'Katy Perry',
            },
        ],
        get_batch_field=get_batch_field,
    )
    _seed_task('t6d', track_info={
        '_is_explicit_album_download': True,
        '_explicit_album_context': {'id': 'alb', 'name': 'Teenage Dream: The Complete Confection'},
        '_explicit_artist_context': {'id': 'art', 'name': 'Katy Perry'},
        'track_number': 4,
        'disc_number': 1,
    })

    ds.try_staging_match(
        't6d', 'b1',
        _Track(name='Firework', artists=['Katy Perry']),
        deps,
    )

    ctx = matched_downloads_context['staging_t6d']
    assert ctx['track_info']['track_number'] == 4
    assert ctx['original_search_result']['track_number'] == 4


def test_staging_title_match_accepts_feature_suffix_from_release_file(tmp_path):
    """Album releases can include featured artists in filenames."""
    src_file = tmp_path / 'staging' / '05-kendrick_lamar-money_trees_(feat._jay_rock).flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {
                'full_path': str(src_file),
                'title': 'money_trees_(feat._jay_rock)',
                'artist': 'Kendrick Lamar',
                'track_number': 5,
            },
        ],
    )
    _seed_task('t_feature', track_info={
        '_is_explicit_album_download': True,
        '_explicit_album_context': {'id': 'alb', 'name': 'good kid, m.A.A.d city (Deluxe)'},
        '_explicit_artist_context': {'id': 'art', 'name': 'Kendrick Lamar'},
    })

    result = ds.try_staging_match(
        't_feature', 'b1',
        _Track(name='Money Trees', artists=['Kendrick Lamar']),
        deps,
    )

    assert result is True
    assert matched_downloads_context['staging_t_feature']['track_info']['track_number'] == 5


def test_staging_title_match_accepts_bonus_track_against_release_file(tmp_path):
    """Expected bonus labels should not block matching the actual release file."""
    src_file = tmp_path / 'staging' / '13-kendrick_lamar-the_recipe_(feat._dr._dre).flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {
                'full_path': str(src_file),
                'title': 'the_recipe_(feat._dr._dre)',
                'artist': 'Kendrick Lamar',
                'track_number': 13,
            },
        ],
    )
    _seed_task('t_bonus', track_info={
        '_is_explicit_album_download': True,
        '_explicit_album_context': {'id': 'alb', 'name': 'good kid, m.A.A.d city (Deluxe)'},
        '_explicit_artist_context': {'id': 'art', 'name': 'Kendrick Lamar'},
    })

    result = ds.try_staging_match(
        't_bonus', 'b1',
        _Track(name='The Recipe (Bonus Track)', artists=['Kendrick Lamar']),
        deps,
    )

    assert result is True
    assert matched_downloads_context['staging_t_bonus']['track_info']['track_number'] == 13


def test_staging_title_match_keeps_wrong_versions_separate(tmp_path):
    """Do not strip remix/extended wording when matching staged release files."""
    src_file = tmp_path / 'staging' / '17-kendrick_lamar-swimming_pools_(drank)_(black_hippy_remix).flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {
                'full_path': str(src_file),
                'title': 'swimming_pools_(drank)_(black_hippy_remix)',
                'artist': 'Kendrick Lamar',
                'track_number': 17,
            },
        ],
    )
    _seed_task('t_wrong_version')

    result = ds.try_staging_match(
        't_wrong_version', 'b1',
        _Track(name='Swimming Pools (Drank) (Extended Version)', artists=['Kendrick Lamar']),
        deps,
    )

    assert result is False
    assert 'staging_t_wrong_version' not in matched_downloads_context


def test_staging_title_match_handles_untagged_release_filename(tmp_path):
    """Album-bundle slskd downloads often arrive without ID3 tags.

    When that happens the staging cache falls back to the file stem
    for the title (e.g. 'Kendrick Lamar - GNX - 03 - Reincarnated').
    The full stem is too noisy to fuzzy-match against the clean
    Spotify title at the 0.80 threshold, so the variant generator
    pulls out the trailing-title segment when a track-number block
    is present between ' - ' delimiters.
    """
    src_file = tmp_path / 'staging' / 'Kendrick Lamar - GNX - 03 - Reincarnated.flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {
                'full_path': str(src_file),
                'title': 'Kendrick Lamar - GNX - 03 - Reincarnated',
                'artist': 'Kendrick Lamar',
                'track_number': 3,
            },
        ],
    )
    _seed_task('t_untagged', track_info={
        '_is_explicit_album_download': True,
        '_explicit_album_context': {'id': 'alb', 'name': 'GNX'},
        '_explicit_artist_context': {'id': 'art', 'name': 'Kendrick Lamar'},
    })

    result = ds.try_staging_match(
        't_untagged', 'b1',
        _Track(name='Reincarnated', artists=['Kendrick Lamar']),
        deps,
    )

    assert result is True
    assert matched_downloads_context['staging_t_untagged']['track_info']['track_number'] == 3


def test_staging_title_match_keeps_dash_titles_intact(tmp_path):
    """The trailing-title variant must not fire when there's no track-number
    segment — otherwise a legit title like 'Hold Me - Live' would generate
    a 'Live' variant and false-match unrelated 'Live' stems on disk.
    """
    src_file = tmp_path / 'staging' / 'Live.flac'
    src_file.parent.mkdir()
    src_file.touch()

    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'),
        staging_files=[
            {'full_path': str(src_file), 'title': 'Live', 'artist': 'Other Artist'},
        ],
    )
    _seed_task('t_dash')

    result = ds.try_staging_match(
        't_dash', 'b1',
        _Track(name='Hold Me - Live', artists=['Some Artist']),
        deps,
    )

    assert result is False
    assert 'staging_t_dash' not in matched_downloads_context


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
