"""Regression tests for lossy_copy.delete_original honoring.

Discord-reported (CAL): with ``lossy_copy.enabled=True``,
``lossy_copy.delete_original=True``, and ``codec=mp3``, downloads
ended up with BOTH the original FLAC AND the converted MP3 in the
target folder. The setting was being read by the pre-move source-
vanished check at ``core/imports/pipeline.py`` but never acted on
during the actual conversion step. Result: a "lossy-only" library
ended up dual-format on every import.

These tests pin the behavior so the regression doesn't return — they
exercise ``create_lossy_copy`` directly with ffmpeg stubbed via
monkeypatch, asserting the original is deleted only when the setting
is enabled and the conversion succeeded.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.imports import file_ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_flac(tmp_path: Path) -> Path:
    """A placeholder FLAC file — content doesn't matter, ffmpeg call is stubbed."""
    src = tmp_path / "01 - Track.flac"
    src.write_bytes(b"FAKE-FLAC-CONTENT")
    return src


def _stub_config(monkeypatch, **overrides):
    """Patch the file_ops module's config_manager so each test controls
    only the keys it cares about."""
    defaults = {
        "lossy_copy.enabled": True,
        "lossy_copy.codec": "mp3",
        "lossy_copy.bitrate": "320",
        "lossy_copy.delete_original": False,
    }
    defaults.update(overrides)

    fake_cfg = MagicMock()
    fake_cfg.get.side_effect = lambda key, default=None: defaults.get(key, default)
    monkeypatch.setattr(file_ops, "config_manager", fake_cfg)
    return defaults


def _stub_ffmpeg_success(monkeypatch, fake_flac: Path):
    """Stub shutil.which to report ffmpeg available + subprocess.run to
    write a fake MP3 to out_path and return success."""
    monkeypatch.setattr(file_ops.shutil, "which", lambda _: "/fake/ffmpeg")

    def _fake_run(cmd, **_kwargs):
        # cmd[-1] is the out_path (per ffmpeg invocation in create_lossy_copy)
        out_path = cmd[-1]
        Path(out_path).write_bytes(b"FAKE-MP3-CONTENT")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(file_ops.subprocess, "run", _fake_run)

    # Skip the mutagen tagging step — file is fake bytes, mutagen would
    # raise. Accepting the silent-fail path is fine here; tests assert
    # on file presence, not tag content.
    monkeypatch.setattr(
        "mutagen.File",
        lambda _path: None,
        raising=False,
    )


def _stub_ffmpeg_failure(monkeypatch):
    """Stub ffmpeg to return non-zero so the conversion path bails out."""
    monkeypatch.setattr(file_ops.shutil, "which", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr(
        file_ops.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stderr="fake ffmpeg error", stdout=""),
    )


# ---------------------------------------------------------------------------
# delete_original honored after successful conversion
# ---------------------------------------------------------------------------


class TestDeleteOriginalHonored:
    def test_original_flac_removed_when_setting_enabled(self, monkeypatch, fake_flac: Path):
        _stub_config(monkeypatch, **{"lossy_copy.delete_original": True})
        _stub_ffmpeg_success(monkeypatch, fake_flac)

        out_path = file_ops.create_lossy_copy(str(fake_flac))

        assert out_path is not None
        assert out_path.endswith(".mp3")
        assert Path(out_path).exists(), "MP3 should have been written"
        assert not fake_flac.exists(), \
            "Original FLAC must be removed when lossy_copy.delete_original=True"

    def test_original_flac_kept_when_setting_disabled(self, monkeypatch, fake_flac: Path):
        _stub_config(monkeypatch, **{"lossy_copy.delete_original": False})
        _stub_ffmpeg_success(monkeypatch, fake_flac)

        out_path = file_ops.create_lossy_copy(str(fake_flac))

        assert out_path is not None
        assert Path(out_path).exists()
        assert fake_flac.exists(), \
            "Original FLAC must survive when lossy_copy.delete_original=False"

    def test_default_is_keep_original(self, monkeypatch, fake_flac: Path):
        """When the user never set the option, default = keep original.
        Defensive: a missing config value must not silently drop files."""
        # Don't override delete_original — picks up the default in _stub_config (False)
        _stub_config(monkeypatch)
        _stub_ffmpeg_success(monkeypatch, fake_flac)

        file_ops.create_lossy_copy(str(fake_flac))
        assert fake_flac.exists()


# ---------------------------------------------------------------------------
# delete_original NOT triggered when conversion fails
# ---------------------------------------------------------------------------


class TestDeleteOriginalSkippedOnFailure:
    def test_original_kept_when_ffmpeg_fails(self, monkeypatch, fake_flac: Path):
        """If ffmpeg returns non-zero, the conversion is treated as failed
        and the original must NOT be deleted (would leave the user with
        no audio file at all)."""
        _stub_config(monkeypatch, **{"lossy_copy.delete_original": True})
        _stub_ffmpeg_failure(monkeypatch)

        out_path = file_ops.create_lossy_copy(str(fake_flac))

        assert out_path is None, "Conversion failure must return None"
        assert fake_flac.exists(), \
            "Original FLAC must survive a failed conversion regardless of delete_original"

    def test_original_kept_when_lossy_copy_disabled(self, monkeypatch, fake_flac: Path):
        """The function early-returns when lossy_copy.enabled=False — so
        delete_original cannot fire even if it's enabled."""
        _stub_config(monkeypatch, **{
            "lossy_copy.enabled": False,
            "lossy_copy.delete_original": True,
        })
        _stub_ffmpeg_success(monkeypatch, fake_flac)

        result = file_ops.create_lossy_copy(str(fake_flac))
        assert result is None
        assert fake_flac.exists()


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


class TestDeleteOriginalDefensive:
    def test_does_not_crash_when_original_already_gone(self, monkeypatch, fake_flac: Path):
        """If something else (concurrent worker, dedup cleanup) removed
        the original between conversion and deletion, that's not an
        error — we just got our wish slightly early."""
        _stub_config(monkeypatch, **{"lossy_copy.delete_original": True})
        _stub_ffmpeg_success(monkeypatch, fake_flac)

        original_remove = os.remove

        def _remove_after_unlinking_first(path, *args, **kwargs):
            # Simulate the source being gone before the delete call: pre-
            # remove on first call, then defer to real os.remove.
            if Path(path) == fake_flac and fake_flac.exists():
                fake_flac.unlink()
                # Now raise FileNotFoundError as os.remove would on a missing path
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return original_remove(path, *args, **kwargs)

        monkeypatch.setattr(file_ops.os, "remove", _remove_after_unlinking_first)

        out_path = file_ops.create_lossy_copy(str(fake_flac))
        assert out_path is not None
        assert Path(out_path).exists()
        assert not fake_flac.exists()

    def test_handles_oserror_during_delete_without_propagating(self, monkeypatch, fake_flac: Path):
        """If the actual unlink fails (permission error, locked file, FS
        full), the conversion is still considered successful — the lossy
        copy already exists. We log the error but return the out_path so
        the import pipeline can continue. The original is left in place
        for the user to clean up manually."""
        _stub_config(monkeypatch, **{"lossy_copy.delete_original": True})
        _stub_ffmpeg_success(monkeypatch, fake_flac)

        def _failing_remove(path, *args, **kwargs):
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.setattr(file_ops.os, "remove", _failing_remove)

        out_path = file_ops.create_lossy_copy(str(fake_flac))
        assert out_path is not None, "Failed delete must not break conversion return value"
        assert Path(out_path).exists()
        assert fake_flac.exists(), "Failed delete leaves the original in place"
