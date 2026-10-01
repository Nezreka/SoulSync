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
        config_manager_=_FakeConfig([str(music)]), warm=False
    )
    yield str(track_file)


def test_resolve_audio_path_translates_container_path(configured_worker):
    real_file = configured_worker
    stored = "/mnt/musicBackup/Virtual Mage/Virtual Mage - Aether/01 - Aether.flac"
    assert not os.path.isfile(stored)  # container path: not literal here
    assert sample_worker.resolve_audio_path(stored) == real_file


def test_resolve_audio_path_raw_path_still_wins(configured_worker, tmp_path):
    direct = tmp_path / "direct.flac"
    direct.write_bytes(b"x")
    assert sample_worker.resolve_audio_path(str(direct)) == str(direct)


def test_resolve_audio_path_none_when_nothing_matches(tmp_path):
    """no base dir holds the file: honest miss."""
    sample_worker.configure(config_manager_=_FakeConfig([]), warm=False)
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

    sample_worker.configure(config_manager_=_FakeConfig([]), warm=False)
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


# the playback resolver web_server injects wins: sample studio must find a
# file wherever the player finds it.


def test_injected_playback_resolver_wins(tmp_path):
    real = tmp_path / "elsewhere" / "01 - Aether.flac"
    real.parent.mkdir()
    real.write_bytes(b"x")
    seen = []

    def playback_resolver(path):
        seen.append(path)
        return str(real)

    sample_worker.configure(
        config_manager_=_FakeConfig([]), resolve_path_fn=playback_resolver, warm=False
    )
    stored = "/mnt/musicBackup/Virtual Mage/Aether/01 - Aether.flac"
    assert sample_worker.resolve_audio_path(stored) == str(real)
    assert seen == [stored]


def test_injected_resolver_miss_falls_back_to_shared(configured_worker):
    sample_worker.configure(
        config_manager_=sample_worker._config_manager,
        resolve_path_fn=lambda p: "/nope/not/here.flac",
        warm=False,
    )
    stored = "/mnt/musicBackup/Virtual Mage/Virtual Mage - Aether/01 - Aether.flac"
    assert sample_worker.resolve_audio_path(stored) == configured_worker


def test_injected_resolver_crash_falls_back_to_shared(configured_worker):
    def boom(path):
        raise OSError("stale nfs handle")

    sample_worker.configure(
        config_manager_=sample_worker._config_manager, resolve_path_fn=boom, warm=False
    )
    stored = "/mnt/musicBackup/Virtual Mage/Virtual Mage - Aether/01 - Aether.flac"
    assert sample_worker.resolve_audio_path(stored) == configured_worker


def test_unconfigured_worker_reads_global_config(tmp_path, monkeypatch):
    """a boot path that never called configure() still resolves against the
    global config instead of silently having no base dirs."""
    import core.settings as settings

    music = tmp_path / "music"
    track = music / "A" / "B" / "01.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"x")
    monkeypatch.setattr(settings, "config_manager", _FakeConfig([str(music)]))
    monkeypatch.setattr(sample_worker, "_config_manager", None)
    assert sample_worker.resolve_audio_path("/data/music/A/B/01.flac") == str(track)
