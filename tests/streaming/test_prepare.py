"""Tests for core/streaming/prepare.py — stream-prep worker."""

from __future__ import annotations

import threading

import pytest

from core.streaming import prepare as sp


class _FakeSoulseek:
    """Minimal download_orchestrator stub for the stream-prep worker."""

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
        download_orchestrator=soulseek or _FakeSoulseek(),
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
    """download_orchestrator.download() returning None → state.error."""
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


# ---------------------------------------------------------------------------
# Real StreamSession compatibility (player-revamp Phase 3 wiring)
# ---------------------------------------------------------------------------

def test_worker_drives_a_real_stream_session(tmp_path):
    """web_server.py now binds stream_state to a StreamStateStore session
    (not a bare dict). Prove the prepare worker drives that real object
    correctly end-to-end through the deps proxy — the actual production type."""
    from core.streaming.state import StreamStateStore

    session = StreamStateStore().get()   # the real production object
    sk = _FakeSoulseek(download_id=None)  # early error exit is enough to mutate state
    deps = _build_deps(soulseek=sk, project_root=str(tmp_path), state=session)

    sp.prepare_stream_task({'username': 'u', 'filename': 'song.flac', 'size': 1}, deps)

    # Worker mutated the SAME session via update()/[k]= — proves dict-compat.
    assert session['status'] == 'error'
    assert 'Failed to initiate' in session['error_message']
    assert session['track_info'] == {'username': 'u', 'filename': 'song.flac', 'size': 1}


# ---------------------------------------------------------------------------
# Observability: prep logs must actually reach app.log
# ---------------------------------------------------------------------------

def test_prepare_logger_is_in_soulsync_namespace():
    """Handlers only attach to the soulsync.* hierarchy. A bare
    getLogger(__name__) gave this module a 'core.streaming.prepare' logger with
    no handler — every prep log (including failures) vanished, which made the
    broken-stream report undebuggable from app.log. Lock the namespace."""
    assert sp.logger.name.startswith('soulsync.'), (
        f"prepare logger '{sp.logger.name}' is outside the soulsync.* namespace "
        "— its output never reaches app.log"
    )


# ---------------------------------------------------------------------------
# Deezer streaming resolution & cleanup
# ---------------------------------------------------------------------------

def test_deezer_stream_uses_status_file_path_directly(tmp_path):
    """When download_status has file_path, prepare_stream_task uses it without disk walk."""
    download_path = tmp_path / 'downloads'
    download_path.mkdir()
    deezer_file = download_path / 'Artist - Song.flac'
    deezer_file.write_bytes(b'audio-bytes')

    cancelled = []

    class _OrchestratorWithCancel:
        async def download(self, username, filename, size):
            return 'dz-123'

        async def get_all_downloads(self):
            return []

        async def cancel_download(self, download_id, username, remove=True):
            cancelled.append((download_id, username, remove))
            return True

    download_status = {
        'id': 'dz-123',
        'state': 'Completed, Succeeded',
        'percentComplete': 100,
        'size': 11,
        'bytesTransferred': 11,
        'file_path': str(deezer_file),
    }

    deps = _build_deps(
        soulseek=_OrchestratorWithCancel(),
        project_root=str(tmp_path),
        find_streaming_result=download_status,
        find_downloaded_result=None,  # Not used because file_path was present
    )

    track_data = {'username': 'deezer_dl', 'filename': '99999||Artist - Song', 'size': 11}
    sp.prepare_stream_task(track_data, deps)

    assert deps._state['status'] == 'ready'
    assert deps._state['progress'] == 100
    assert (tmp_path / 'Stream' / 'Artist - Song.flac').exists()
    assert cancelled == [('dz-123', 'deezer_dl', True)]


def test_find_downloaded_file_deezer(tmp_path):
    """web_server._find_downloaded_file finds Deezer files with encoded id||title format."""
    from web_server import _find_downloaded_file

    dl_dir = tmp_path / 'downloads'
    dl_dir.mkdir()
    song_file = dl_dir / 'Artist - Cool Song.flac'
    song_file.write_bytes(b'x' * 2048)

    track_data = {'username': 'deezer_dl', 'filename': '123456||Artist - Cool Song'}
    found = _find_downloaded_file(str(dl_dir), track_data)
    assert found == str(song_file)


def test_find_streaming_download_deezer_alias_match():
    """web_server._find_streaming_download_in_all_downloads matches deezer vs deezer_dl."""
    from web_server import _find_streaming_download_in_all_downloads
    from core.download_plugins.types import DownloadStatus

    status = DownloadStatus(
        id='dz-1',
        filename='12345||Artist - Title',
        username='deezer_dl',
        state='Completed, Succeeded',
        progress=100.0,
        size=5000,
        transferred=5000,
        speed=1000,
        file_path='/downloads/Artist - Title.mp3',
    )

    # Target uses 'deezer', status uses 'deezer_dl'
    matched = _find_streaming_download_in_all_downloads(
        [status],
        {'username': 'deezer', 'filename': '12345||Artist - Title'},
    )
    assert matched is not None
    assert matched['id'] == 'dz-1'
    assert matched['file_path'] == '/downloads/Artist - Title.mp3'
    assert matched['percentComplete'] == 100.0

