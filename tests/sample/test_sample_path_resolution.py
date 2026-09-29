"""Issue 1: container-style stored paths must resolve via the shared library
path resolver — with the config manager injected.

Root cause: both ``core/sample/worker.py::_resolve_existing_path`` and the
inline copy in ``api/sample.py::_resolve_source_path`` called
``resolve_library_file_path(stored)`` WITHOUT a config manager, so the
resolver had no base directories to walk against and the fallback was dead.
On a native install whose DB holds Docker-style paths
(``/mnt/musicBackup/…``), every track reported "not reachable on disk".

Covered here:
- ``resolve_audio_path`` translates a container-style stored path against
  the configured ``library.music_paths`` (the Broque case).
- Without an injected config manager it still returns None (honest miss).
- ``_resolve_source_path`` funnels through the same resolver and its
  FILE_MISSING error names the stored path.
"""

import os

import pytest

import api.sample as sample_api
from core.sample import worker as sample_worker


class _FakeConfig:
    def __init__(self, music_paths):
        self._music_paths = music_paths

    def get(self, key, default=None):
        if key == "library.music_paths":
            return self._music_paths
        return default


@pytest.fixture
def configured_worker(tmp_path):
    """A music folder on disk + worker configured to know about it."""
    music = tmp_path / "musicBackup"
    track_file = music / "Virtual Mage" / "Virtual Mage - Aether" / "01 - Aether.flac"
    track_file.parent.mkdir(parents=True)
    track_file.write_bytes(b"fake flac")
    sample_worker.configure(
        config_manager_=_FakeConfig([str(music)])
    )
    yield str(track_file)
    sample_worker.configure(config_manager_=None)


def test_resolve_audio_path_translates_container_path(configured_worker):
    real_file = configured_worker
    stored = "/mnt/musicBackup/Virtual Mage/Virtual Mage - Aether/01 - Aether.flac"
    assert not os.path.isfile(stored)  # container path: not literal here
    assert sample_worker.resolve_audio_path(stored) == real_file


def test_resolve_audio_path_raw_path_still_wins(configured_worker, tmp_path):
    direct = tmp_path / "direct.flac"
    direct.write_bytes(b"x")
    assert sample_worker.resolve_audio_path(str(direct)) == str(direct)


def test_resolve_audio_path_none_without_config_manager(tmp_path):
    """No injected config: the resolver has no base dirs, honest miss."""
    sample_worker.configure(config_manager_=None)
    assert sample_worker.resolve_audio_path(
        "/mnt/musicBackup/Artist/Album/01.flac"
    ) is None


def test_resolve_source_path_uses_translation(configured_worker, monkeypatch):
    from core.sample import store as sample_store

    stored = "/mnt/musicBackup/Virtual Mage/Virtual Mage - Aether/01 - Aether.flac"
    monkeypatch.setattr(
        sample_store, "get_track_file_path", lambda track_id: stored
    )
    assert sample_api._resolve_source_path(99, None) == configured_worker


def test_resolve_source_path_untranslatable_is_honest(monkeypatch):
    from core.sample import store as sample_store

    sample_worker.configure(config_manager_=None)
    stored = "/mnt/musicBackup/Artist/Album/01.flac"
    monkeypatch.setattr(
        sample_store, "get_track_file_path", lambda track_id: stored
    )
    with pytest.raises(sample_api.SampleHttpError) as exc_info:
        sample_api._resolve_source_path(99, None)
    assert exc_info.value.code == "FILE_MISSING"
    assert exc_info.value.status == 409
    # the stored path is named so the user can see what failed to translate
    assert stored in exc_info.value.message
