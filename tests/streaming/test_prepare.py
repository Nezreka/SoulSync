"""Tests for core/streaming/prepare.py — stream-prep worker."""

from __future__ import annotations

import threading

import pytest

from core.streaming import prepare as sp


class _FakeSoulseek:
    """Minimal soulseek_client stub for the stream-prep worker."""

    def __init__(self, *, download_id='dl-1', all_downloads=None):
        self._download_id = download_id
        self._all_downloads = all_downloads if all_downloads is not None else []

    async def download(self, username, filename, size):
        return self._download_id

    async def get_all_downloads(self):
        return self._all_downloads

    async def signal_download_completion(self, download_id, username, remove=True):
        return True


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Keep stream-prep tests fast while still exercising the polling branches."""
    monkeypatch.setattr(sp.time, 'sleep', lambda *_args, **_kwargs: None)


def _build_deps(
    *,
    state=None,
    soulseek=None,
    project_root='/tmp/proj',
    find_streaming_result=None,
    find_downloaded_result=None,
):
    state = state if state is not None else {}
    deps = sp.PrepareStreamDeps(
        config_manager=type('C', (), {'get': lambda self, k, d=None: d})(),
        soulseek_client=soulseek or _FakeSoulseek(),
        stream_lock=threading.Lock(),
        project_root=project_root,
        docker_resolve_path=lambda p: p,
        find_streaming_download_in_all_downloads=lambda all_dl, td: find_streaming_result,
        find_downloaded_file=lambda dl_path, td: find_downloaded_result,
        extract_filename=lambda fp: __import__('os').path.basename(fp),
        cleanup_empty_directories=lambda dl_path, found_file: None,
        _get_stream_state=lambda: state,
        _set_stream_state=lambda v: state.clear() or state.update(v),
    )
    deps._state = state
    return deps


# ---------------------------------------------------------------------------
# Initial state setup
# ---------------------------------------------------------------------------

def test_state_starts_loading_with_track_info(tmp_path):
    """First action sets state to 'loading' with the track_info."""
    sk = _FakeSoulseek(download_id=None)  # forces an early "Failed to initiate" exit
    deps = _build_deps(soulseek=sk, project_root=str(tmp_path))

    track_data = {'username': 'u', 'filename': 'song.flac', 'size': 1000}
    sp.prepare_stream_task(track_data, deps)

    # First mutation set status='loading', track_info=track_data
    # Then early exit because download() returned None — state ends up 'error'
    assert deps._state['status'] == 'error'
    assert 'Failed to initiate' in deps._state['error_message']


def test_stream_folder_created(tmp_path):
    """Stream/ subfolder is created under project_root."""
    sk = _FakeSoulseek(download_id=None)
    deps = _build_deps(soulseek=sk, project_root=str(tmp_path))

    sp.prepare_stream_task({'username': 'u', 'filename': 'x', 'size': 0}, deps)

    assert (tmp_path / 'Stream').is_dir()


def test_stream_folder_cleared_before_download(tmp_path):
    """Existing files in Stream/ are removed before each prepare."""
    stream_dir = tmp_path / 'Stream'
    stream_dir.mkdir()
    old_file = stream_dir / 'old.flac'
    old_file.write_bytes(b'old data')
    assert old_file.exists()

    sk = _FakeSoulseek(download_id=None)
    deps = _build_deps(soulseek=sk, project_root=str(tmp_path))
    sp.prepare_stream_task({'username': 'u', 'filename': 'x', 'size': 0}, deps)

    # Old file gone (cleared at start of prep)
    assert not old_file.exists()


# ---------------------------------------------------------------------------
# Download initiation failure
# ---------------------------------------------------------------------------

def test_download_returns_none_marks_error(tmp_path):
    """soulseek_client.download() returning None → state.error."""
    sk = _FakeSoulseek(download_id=None)
    deps = _build_deps(soulseek=sk, project_root=str(tmp_path))

    sp.prepare_stream_task({'username': 'u', 'filename': 'x', 'size': 0}, deps)

    assert deps._state['status'] == 'error'


# ---------------------------------------------------------------------------
# Successful completion
# ---------------------------------------------------------------------------

def test_completed_download_moves_to_stream_and_marks_ready(tmp_path):
    """When the polled status reports succeeded + bytes match, file moved + state ready."""
    download_path = tmp_path / 'downloads'
    download_path.mkdir()
    src_file = download_path / 'song.flac'
    src_file.write_bytes(b'audio')

    download_status = {
        'id': 'dl-99',
        'state': 'Succeeded',
        'percentComplete': 100,
        'size': 5,
        'bytesTransferred': 5,
    }
    sk = _FakeSoulseek(download_id='dl-99', all_downloads=['stub'])
    deps = _build_deps(
        soulseek=sk,
        project_root=str(tmp_path),
        find_streaming_result=download_status,
        find_downloaded_result=str(src_file),
    )
    deps.config_manager = type('C', (), {
        'get': lambda self, k, d=None: str(download_path) if k == 'soulseek.download_path' else d,
    })()

    sp.prepare_stream_task(
        {'username': 'u', 'filename': 'song.flac', 'size': 5},
        deps,
    )

    assert deps._state['status'] == 'ready'
    assert deps._state['progress'] == 100
    assert (tmp_path / 'Stream' / 'song.flac').exists()
    assert deps._state['file_path'] == str(tmp_path / 'Stream' / 'song.flac')


def test_succeeded_state_with_partial_bytes_keeps_polling(tmp_path):
    """If state is 'Succeeded' but bytes < size, marks _incomplete_warned and continues."""
    download_status = {
        'id': 'dl-99',
        'state': 'Succeeded',
        'percentComplete': 100,
        'size': 100,
        'bytesTransferred': 50,  # incomplete
    }
    sk = _FakeSoulseek(download_id='dl-99', all_downloads=['stub'])
    deps = _build_deps(
        soulseek=sk,
        project_root=str(tmp_path),
        find_streaming_result=download_status,
    )

    # Force quick exit by capping the loop with no further state change
    # Worker times out via max_wait_time in real code — we just verify state didn't go ready
    sp.prepare_stream_task({'username': 'u', 'filename': 'x', 'size': 100}, deps)

    # Should NOT have gone to 'ready' because bytes were incomplete
    assert deps._state['status'] != 'ready'
