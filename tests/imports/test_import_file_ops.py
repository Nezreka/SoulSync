import sys
import types
from pathlib import Path

from core.imports.file_ops import (
    cleanup_empty_directories,
    safe_move_file,
)
from core.imports.filename import (
    extract_explicit_track_number,
    extract_track_number_from_filename,
)
from core.imports.staging import read_staging_file_metadata


def test_extract_track_number_from_filename_handles_common_patterns():
    assert extract_track_number_from_filename("01 - Song.mp3") == 1
    assert extract_track_number_from_filename("1-03 - Song.mp3") == 3
    # Bare filename keeps the auto-import-friendly default of 1 — there's
    # no upstream metadata to recover from in that flow.
    assert extract_track_number_from_filename("Artist - Song.mp3") == 1


def test_extract_explicit_track_number_returns_zero_when_no_prefix():
    """Staging readers need to distinguish 'track 1' from 'unknown'.

    Pinned because:
    - the legacy extractor defaults to 1 (auto-import semantics),
    - staging file scanners that conflate the two end up writing every
      file in an untagged album bundle to track_number=1.
    """
    # Bare titles with no numeric prefix → 0 (unknown).
    assert extract_explicit_track_number("Artist - Song.mp3") == 0
    assert extract_explicit_track_number("Cha-La Head-Cha-La.flac") == 0
    assert extract_explicit_track_number("") == 0
    # Real prefixes still parse correctly.
    assert extract_explicit_track_number("01 - Song.mp3") == 1
    assert extract_explicit_track_number("(03) Song.mp3") == 3
    # Disc-track format requires a separator after the track number.
    assert extract_explicit_track_number("1-07 - Song.mp3") == 7


def test_safe_move_file_replaces_existing_destination(tmp_path):
    src = tmp_path / "source.flac"
    dst_dir = tmp_path / "dest"
    dst_dir.mkdir()
    dst = dst_dir / "track.flac"

    src.write_text("new")
    dst.write_text("old")

    safe_move_file(src, dst)

    assert not src.exists()
    assert dst.read_text() == "new"


def test_safe_move_failure_preserves_existing_destination_and_source(tmp_path, monkeypatch):
    src = tmp_path / "source.flac"
    dst = tmp_path / "track.flac"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")

    monkeypatch.setattr(_fo.os, "replace", lambda *_args: (_ for _ in ()).throw(
        OSError(errno.ENOSPC, "No space left on device")
    ))

    with pytest.raises(OSError, match="No space"):
        safe_move_file(src, dst)

    assert src.read_bytes() == b"new"
    assert dst.read_bytes() == b"old"


def test_cleanup_empty_directories_removes_nested_empty_paths(tmp_path):
    download_root = tmp_path / "downloads"
    nested_dir = download_root / "Artist" / "Album"
    nested_dir.mkdir(parents=True)
    moved_file_path = nested_dir / "track.flac"

    cleanup_empty_directories(str(download_root), str(moved_file_path))

    assert not nested_dir.exists()
    assert not (download_root / "Artist").exists()
    assert download_root.exists()


def test_read_staging_file_metadata_reads_tags(monkeypatch, tmp_path):
    file_path = tmp_path / "Song One.flac"
    file_path.write_text("fake")

    class DummyTags:
        def __init__(self):
            self.values = {
                "title": ["Song One"],
                "artist": ["Artist One"],
                "albumartist": ["Album Artist"],
                "album": ["Album One"],
                "tracknumber": ["03/12"],
                "discnumber": ["2/3"],
            }

        def get(self, key, default=None):
            return self.values.get(key, default)

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: DummyTags()
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata == {
        "title": "Song One",
        "artist": "Artist One",
        "albumartist": "Album Artist",
        "album": "Album One",
        "track_number": 3,
        "disc_number": 2,
    }


def test_read_staging_file_metadata_falls_back_to_filename_track_number(monkeypatch, tmp_path):
    file_path = tmp_path / "07 - Song Two.flac"
    file_path.write_text("fake")

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: None
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata["title"] == "07 - Song Two"
    assert metadata["track_number"] == 7
    assert metadata["disc_number"] == 1


def test_read_staging_file_metadata_returns_zero_track_when_unknown(monkeypatch, tmp_path):
    """Bare filename + no tags → track_number=0, not 1.

    Pre-fix this returned 1 because the filename extractor's default
    was 1. The bug caused every untagged file in an album-bundle
    download to land in the staging cache with track_number=1, which
    then short-circuited the downstream resolution chain that should
    have picked up the real number from track_info.
    """
    file_path = tmp_path / "Cha-La Head-Cha-La.flac"
    file_path.write_text("fake")

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: None
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata["track_number"] == 0


def test_read_staging_file_metadata_uses_filename_fallbacks_when_tags_are_invalid(monkeypatch, tmp_path):
    file_path = tmp_path / "02 - Song Three.flac"
    file_path.write_text("fake")

    class DummyTags:
        def __init__(self):
            self.values = {
                "title": [""],
                "artist": "Artist One",
                "albumartist": "",
                "album": ["Album One"],
                "tracknumber": ["not-a-number"],
                "discnumber": ["bad/disc"],
            }

        def get(self, key, default=None):
            return self.values.get(key, default)

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: DummyTags()
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata == {
        "title": "02 - Song Three",
        "artist": "Artist One",
        "albumartist": "Artist One",
        "album": "Album One",
        "track_number": 2,
        "disc_number": 1,
    }


# ── atomic cross-filesystem move (Jellyfin null-disc mitigation) ──────────────
import errno  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402

import pytest  # noqa: E402

from core.imports import file_ops as _fo  # noqa: E402
from core.imports.file_ops import _atomic_cross_device_move  # noqa: E402


def test_same_fs_move_moves_and_removes_source(tmp_path):
    src = tmp_path / "s.flac"
    src.write_text("hello")
    dst = tmp_path / "lib" / "t.flac"          # parent created by safe_move_file
    safe_move_file(src, dst)
    assert dst.read_text() == "hello"
    assert not src.exists()


def test_cross_device_move_routes_to_atomic_and_completes(tmp_path, monkeypatch):
    # Simulate a cross-filesystem move: the same-fs os.replace raises EXDEV, and the
    # atomic helper's temp->dst replace (same fs) succeeds. The file must complete and
    # no partial temp file may be left at the final name's directory.
    src = tmp_path / "s.flac"
    src.write_text("payload")
    dstdir = tmp_path / "lib"
    dstdir.mkdir()
    dst = dstdir / "t.flac"

    real_replace = os.replace

    def fake_replace(a, b):
        if str(a) == str(src):                  # the cross-fs move attempt
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(a, b)               # the temp -> dst rename (same fs)

    monkeypatch.setattr(_fo.os, "replace", fake_replace)
    safe_move_file(src, dst)

    assert dst.read_text() == "payload"
    assert not src.exists()
    assert not list(dstdir.glob(".*ssync-tmp"))   # complete file only, no leftover temp


def test_cross_device_move_atomically_replaces_existing_destination(tmp_path, monkeypatch):
    src = tmp_path / "s.flac"
    src.write_bytes(b"new payload")
    dst = tmp_path / "lib" / "t.flac"
    dst.parent.mkdir()
    dst.write_bytes(b"old payload")
    real_replace = os.replace

    def fake_replace(a, b):
        if Path(a) == src:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(a, b)

    monkeypatch.setattr(_fo.os, "replace", fake_replace)
    safe_move_file(src, dst)

    assert not src.exists()
    assert dst.read_bytes() == b"new payload"


def test_cross_device_copy_failure_keeps_old_destination(tmp_path, monkeypatch):
    src = tmp_path / "s.flac"
    src.write_bytes(b"new payload")
    dst = tmp_path / "lib" / "t.flac"
    dst.parent.mkdir()
    dst.write_bytes(b"old payload")

    monkeypatch.setattr(_fo.shutil, "copyfileobj", lambda *_args: (_ for _ in ()).throw(
        OSError(errno.ENOSPC, "No space left on device")
    ))

    with pytest.raises(OSError, match="No space"):
        _atomic_cross_device_move(src, dst)

    assert src.read_bytes() == b"new payload"
    assert dst.read_bytes() == b"old payload"
    assert not list(dst.parent.glob(".*ssync-tmp"))


def test_atomic_helper_completes_and_cleans_temp(tmp_path):
    src = tmp_path / "s.flac"
    src.write_text("payload")
    dstdir = tmp_path / "d"
    dstdir.mkdir()
    dst = dstdir / "t.flac"
    _atomic_cross_device_move(src, dst)
    assert dst.read_text() == "payload"
    assert not src.exists()
    assert not list(dstdir.glob(".*ssync-tmp"))


def _publish(tmp_path, *, src_mode, dir_mode, cross_device=True):
    """Move one file into a library folder of ``dir_mode`` and report its mode."""
    src = tmp_path / "s.flac"
    src.write_text("payload")
    os.chmod(src, src_mode)
    dstdir = tmp_path / "d"
    dstdir.mkdir()
    os.chmod(dstdir, dir_mode)
    dst = dstdir / "t.flac"

    if cross_device:
        _atomic_cross_device_move(src, dst)
    else:
        _fo.safe_move_file(src, dst)
    return stat.S_IMODE(dst.stat().st_mode)


@pytest.mark.parametrize("cross_device", [True, False])
@pytest.mark.parametrize("mode", [0o644, 0o664])
def test_move_keeps_an_already_shareable_source_mode(tmp_path, mode, cross_device):
    # PR #1121 review: tempfile.mkstemp creates at 0600 by design and os.replace
    # preserves that mode, so a cross-device import (docker downloads-volume ->
    # library-volume) landed files no other user could read — Plex/Jellyfin run
    # as a different user. An import never NARROWS a file that was already
    # readable.
    assert _publish(tmp_path, src_mode=mode, dir_mode=0o755,
                    cross_device=cross_device) == mode


@pytest.mark.parametrize("cross_device", [True, False])
def test_move_widens_a_private_staging_mode_to_the_library_audience(
    tmp_path, cross_device,
):
    """Copying the SOURCE's mode only fixes the reported bug when the download
    client happened to write a permissive one. slskd/SABnzbd in a container
    with umask 077 writes its downloads 0600, so inheriting that publishes a
    library file the media server still cannot read — the exact symptom. The
    destination DIRECTORY is the statement of who the library is for."""
    assert _publish(tmp_path, src_mode=0o600, dir_mode=0o755,
                    cross_device=cross_device) == 0o644


@pytest.mark.parametrize("cross_device", [True, False])
def test_move_follows_a_group_writable_library(tmp_path, cross_device):
    assert _publish(tmp_path, src_mode=0o600, dir_mode=0o775,
                    cross_device=cross_device) == 0o664


@pytest.mark.parametrize("cross_device", [True, False])
def test_move_does_not_widen_past_a_private_library(tmp_path, cross_device):
    """A 0700 library says only this user gets in — nobody can traverse the
    directory anyway, so there is nothing to widen for."""
    assert _publish(tmp_path, src_mode=0o600, dir_mode=0o700,
                    cross_device=cross_device) == 0o600


def test_atomic_helper_keeps_permissions_when_copystat_fails(tmp_path, monkeypatch):
    # copystat also copies timestamps/xattrs; a filesystem that rejects those
    # must not cost the file its mode and leave it 0600 (mkstemp's default).
    src = tmp_path / "s.flac"
    src.write_text("payload")
    os.chmod(src, 0o644)
    dstdir = tmp_path / "d"
    dstdir.mkdir()
    os.chmod(dstdir, 0o755)
    dst = dstdir / "t.flac"

    monkeypatch.setattr(_fo.shutil, "copystat", lambda *_a, **_k: (_ for _ in ()).throw(
        OSError("utime not supported")
    ))
    _atomic_cross_device_move(src, dst)

    assert stat.S_IMODE(dst.stat().st_mode) == 0o644


def test_atomic_helper_cleans_temp_and_keeps_source_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "s.flac"
    src.write_text("payload")
    dstdir = tmp_path / "d"
    dstdir.mkdir()
    dst = dstdir / "t.flac"

    def boom(_a, _b):
        raise OSError("replace failed")

    monkeypatch.setattr(_fo.os, "replace", boom)
    with pytest.raises(OSError):
        _atomic_cross_device_move(src, dst)

    assert src.exists()                           # source preserved on failure
    assert not dst.exists()                       # no partial final file
    assert not list(dstdir.glob(".*ssync-tmp"))   # temp cleaned up


# ── #941: create_lossy_copy now accepts all lossless sources + never overwrites them ──

import core.imports.file_ops as _fo


def _enable_lossy(monkeypatch, codec="mp3", bitrate="320"):
    cfg = {"lossy_copy.enabled": True, "lossy_copy.codec": codec,
           "lossy_copy.bitrate": bitrate, "lossy_copy.delete_original": False}
    monkeypatch.setattr(_fo.config_manager, "get", lambda k, d=None: cfg.get(k, d))
    monkeypatch.setattr(_fo, "get_audio_quality_string", lambda _p: None)


def test_create_lossy_copy_rejects_non_lossless(monkeypatch, tmp_path):
    _enable_lossy(monkeypatch)
    src = tmp_path / "song.mp3"
    src.write_bytes(b"id3")
    assert _fo.create_lossy_copy(str(src)) is None   # lossy input → nothing to do


def test_create_lossy_copy_now_accepts_wav(monkeypatch, tmp_path):
    """Was FLAC-only; a WAV must now pass the gate and convert (#941)."""
    _enable_lossy(monkeypatch, codec="mp3")
    monkeypatch.setattr(_fo.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("mutagen.File", lambda *_a, **_k: None)  # skip tag write

    seen = {}

    def _fake_run(cmd, **_kw):
        seen["cmd"] = cmd
        open(cmd[-1], "wb").write(b"fake-mp3")   # ffmpeg "writes" the output
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(_fo.subprocess, "run", _fake_run)

    src = tmp_path / "song.wav"
    src.write_bytes(b"RIFF....WAVE")
    out = _fo.create_lossy_copy(str(src))
    assert out and out.endswith(".mp3")          # WAV passed the gate + converted
    assert str(src) in seen["cmd"]               # ffmpeg got the .wav input


def test_create_lossy_copy_skips_when_output_would_overwrite_source(monkeypatch, tmp_path):
    """REGRESSION: .m4a ALAC source + AAC codec → output is the same .m4a path.
    ffmpeg (-y) must NEVER run, or it would destroy the original lossless file."""
    _enable_lossy(monkeypatch, codec="aac", bitrate="256")
    monkeypatch.setattr(_fo, "m4a_codec", lambda _p: "alac")   # source IS ALAC (lossless)

    ran = {"called": False}
    monkeypatch.setattr(_fo.subprocess, "run",
                        lambda *_a, **_k: ran.__setitem__("called", True))

    src = tmp_path / "track.m4a"
    src.write_bytes(b"....ALAC....")
    out = _fo.create_lossy_copy(str(src))
    assert out is None                 # skipped — output would overwrite source
    assert ran["called"] is False      # the original was never touched
