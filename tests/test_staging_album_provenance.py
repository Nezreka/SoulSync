"""Tests for the album-bundle provenance override in
``core/downloads/staging.py``.

Verifies that when ``StagingDeps.get_batch_field`` returns a source
override (i.e. the batch was populated by the torrent / usenet
album-bundle flow), the staging matcher records that source on the
task instead of the generic 'staging' username. Provenance recording
downstream uses ``task['username']`` to set ``source_service`` on
the persisted download row — so this is the single point that
controls whether the history modal shows 'Torrent' / 'Usenet' vs
'Staging' / 'Soulseek'.

Mocks the rest of StagingDeps so the test doesn't touch the
filesystem, AcoustID, or post-processing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.downloads.staging import StagingDeps, try_staging_match
from core.runtime_state import download_tasks, tasks_lock


def _make_deps(staging_file, transfer_dir, batch_field_value=None):
    """Build a StagingDeps with mocked collaborators. ``batch_field_value``
    is what get_batch_field returns for ``album_bundle_source`` — None
    means no override (generic staging match), 'torrent' / 'usenet'
    means the album-bundle flow seeded the staging folder."""
    me = MagicMock()
    me.normalize_string.side_effect = lambda s: (s or '').lower().strip()
    config = MagicMock()
    config.get.return_value = transfer_dir
    return StagingDeps(
        config_manager=config,
        matching_engine=me,
        get_staging_file_cache=lambda _b: [staging_file],
        docker_resolve_path=lambda p: p,
        post_process_matched_download_with_verification=lambda *a, **kw: None,
        get_batch_field=(lambda _b, _f: batch_field_value) if batch_field_value is not None else (lambda _b, _f: None),
    )


def _seed_task(task_id: str, track_name: str, track_artist: str) -> None:
    """Register a task in the runtime_state dict so try_staging_match
    has something to mark complete."""
    with tasks_lock:
        download_tasks[task_id] = {
            'status': 'searching',
            'track_info': {
                'name': track_name,
                'artists': [{'name': track_artist}],
                '_is_explicit_album_download': False,
            },
        }


def _cleanup_task(task_id: str) -> None:
    with tasks_lock:
        download_tasks.pop(task_id, None)


def test_staging_match_uses_torrent_override_when_present(tmp_path) -> None:
    src = tmp_path / 'staging' / 'gnx_track_01.flac'
    src.parent.mkdir()
    src.write_bytes(b'fLaC')
    transfer = tmp_path / 'transfer'
    deps = _make_deps(
        staging_file={'full_path': str(src), 'title': 'Luther', 'artist': 'Kendrick Lamar'},
        transfer_dir=str(transfer),
        batch_field_value='torrent',
    )
    track = SimpleNamespace(name='Luther', artists=['Kendrick Lamar'])
    task_id = 'test_task_torrent_override'
    _seed_task(task_id, 'Luther', 'Kendrick Lamar')
    try:
        ok = try_staging_match(task_id, 'batch_x', track, deps)
        assert ok is True
        with tasks_lock:
            row = download_tasks[task_id]
            assert row['username'] == 'torrent', \
                f"Expected provenance override 'torrent', got {row['username']!r}"
            assert row['staging_match'] is True
    finally:
        _cleanup_task(task_id)


def test_staging_match_uses_usenet_override_when_present(tmp_path) -> None:
    src = tmp_path / 'staging' / 'gnx_track_01.flac'
    src.parent.mkdir()
    src.write_bytes(b'fLaC')
    transfer = tmp_path / 'transfer'
    deps = _make_deps(
        staging_file={'full_path': str(src), 'title': 'Luther', 'artist': 'Kendrick Lamar'},
        transfer_dir=str(transfer),
        batch_field_value='usenet',
    )
    track = SimpleNamespace(name='Luther', artists=['Kendrick Lamar'])
    task_id = 'test_task_usenet_override'
    _seed_task(task_id, 'Luther', 'Kendrick Lamar')
    try:
        try_staging_match(task_id, 'batch_x', track, deps)
        with tasks_lock:
            assert download_tasks[task_id]['username'] == 'usenet'
    finally:
        _cleanup_task(task_id)


def test_staging_match_falls_back_to_staging_without_override(tmp_path) -> None:
    """When no batch override is present (manual file drop, or
    batch has no album_bundle_source field), the staging matcher
    uses the historical 'staging' username."""
    src = tmp_path / 'staging' / 'gnx_track_01.flac'
    src.parent.mkdir()
    src.write_bytes(b'fLaC')
    transfer = tmp_path / 'transfer'
    deps = _make_deps(
        staging_file={'full_path': str(src), 'title': 'Luther', 'artist': 'Kendrick Lamar'},
        transfer_dir=str(transfer),
        batch_field_value=None,
    )
    track = SimpleNamespace(name='Luther', artists=['Kendrick Lamar'])
    task_id = 'test_task_no_override'
    _seed_task(task_id, 'Luther', 'Kendrick Lamar')
    try:
        try_staging_match(task_id, 'batch_x', track, deps)
        with tasks_lock:
            assert download_tasks[task_id]['username'] == 'staging'
    finally:
        _cleanup_task(task_id)


def test_staging_match_handles_missing_batch_field_callable(tmp_path) -> None:
    """Backward compat: callers that build StagingDeps without
    supplying ``get_batch_field`` (it defaults to None) still
    work — staging matcher falls back to the 'staging' username."""
    src = tmp_path / 'staging' / 'gnx_track_01.flac'
    src.parent.mkdir()
    src.write_bytes(b'fLaC')
    transfer = tmp_path / 'transfer'

    me = MagicMock()
    me.normalize_string.side_effect = lambda s: (s or '').lower().strip()
    config = MagicMock()
    config.get.return_value = str(transfer)
    deps = StagingDeps(
        config_manager=config,
        matching_engine=me,
        get_staging_file_cache=lambda _b: [{'full_path': str(src), 'title': 'Luther', 'artist': 'Kendrick Lamar'}],
        docker_resolve_path=lambda p: p,
        post_process_matched_download_with_verification=lambda *a, **kw: None,
        # get_batch_field omitted — defaults to None
    )
    track = SimpleNamespace(name='Luther', artists=['Kendrick Lamar'])
    task_id = 'test_task_no_accessor'
    _seed_task(task_id, 'Luther', 'Kendrick Lamar')
    try:
        try_staging_match(task_id, 'batch_x', track, deps)
        with tasks_lock:
            assert download_tasks[task_id]['username'] == 'staging'
    finally:
        _cleanup_task(task_id)


def test_staging_match_swallows_accessor_exception(tmp_path) -> None:
    """If the injected accessor raises (e.g. the batch was deleted
    mid-process), the staging matcher should fall back to 'staging'
    rather than failing the whole match."""
    src = tmp_path / 'staging' / 'gnx_track_01.flac'
    src.parent.mkdir()
    src.write_bytes(b'fLaC')
    transfer = tmp_path / 'transfer'

    def _boom(_b, _f):
        raise RuntimeError("batch went away")

    me = MagicMock()
    me.normalize_string.side_effect = lambda s: (s or '').lower().strip()
    config = MagicMock()
    config.get.return_value = str(transfer)
    deps = StagingDeps(
        config_manager=config,
        matching_engine=me,
        get_staging_file_cache=lambda _b: [{'full_path': str(src), 'title': 'Luther', 'artist': 'Kendrick Lamar'}],
        docker_resolve_path=lambda p: p,
        post_process_matched_download_with_verification=lambda *a, **kw: None,
        get_batch_field=_boom,
    )
    track = SimpleNamespace(name='Luther', artists=['Kendrick Lamar'])
    task_id = 'test_task_accessor_raises'
    _seed_task(task_id, 'Luther', 'Kendrick Lamar')
    try:
        try_staging_match(task_id, 'batch_x', track, deps)
        with tasks_lock:
            assert download_tasks[task_id]['username'] == 'staging'
    finally:
        _cleanup_task(task_id)
