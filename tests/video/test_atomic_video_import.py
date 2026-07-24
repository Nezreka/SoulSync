"""Video files land in the library atomically + size-verified (Boulder).

The importer copied straight to the FINAL library filename
(``shutil.copy2(src, final.mkv)``), so for the whole multi-GB copy over SMB
the Plex/Jellyfin-watched folder contained a growing partial file at its
real name — servers indexed/analyzed truncated data ("skips like it's
corrupted"), and an interrupted copy left a permanently truncated file that
looked complete. The music side has had the cure for ages
(``atomic_copy_to_staging``); ``youtube_download`` had it too — the main
movie/TV importer and the monitor's fallback mover were the holdouts.

Contract: copy to ``<dest>.tmp.<random>`` in the destination dir → verify
the byte count → ``os.replace`` to the final name. Nothing partial is ever
visible at a media extension; a silent short copy is refused; a move only
removes its source after the destination verified.

All tmp_path — no network, no services.
"""

from __future__ import annotations

import errno
import os

import pytest

from core.video import importer as imp
from core.video.download_monitor import _move
from core.video.importer import atomic_verified_copy, atomic_verified_move, real_fs


def _mk(path, data=b"x" * 4096):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _no_tmp_leftovers(directory):
    return [n for n in os.listdir(directory) if ".tmp." in n] == []


def test_copy_lands_full_content_and_leaves_no_temp(tmp_path):
    src = _mk(tmp_path / "dl" / "movie.mkv", b"m" * 10_000)
    dst = tmp_path / "lib" / "Movie (2026)" / "Movie (2026) 1080p.mkv"
    dst.parent.mkdir(parents=True)
    atomic_verified_copy(str(src), str(dst))
    assert dst.read_bytes() == b"m" * 10_000
    assert src.exists()                       # copy keeps the source
    assert _no_tmp_leftovers(dst.parent)


def test_short_copy_is_refused_and_never_reaches_the_final_name(tmp_path, monkeypatch):
    """An SMB hiccup that truncates without raising must not be promoted."""
    src = _mk(tmp_path / "movie.mkv", b"m" * 10_000)
    dst = tmp_path / "lib" / "movie.mkv"
    dst.parent.mkdir(parents=True)

    def short_copy2(s, d):
        with open(d, "wb") as f:
            f.write(b"m" * 1_000)             # silently short

    monkeypatch.setattr(imp.shutil, "copy2", short_copy2)
    with pytest.raises(OSError, match="short copy"):
        atomic_verified_copy(str(src), str(dst))
    assert not dst.exists()                   # nothing at the final name
    assert _no_tmp_leftovers(dst.parent)      # partial cleaned up


def test_interrupted_copy_leaves_nothing_at_the_final_name(tmp_path, monkeypatch):
    src = _mk(tmp_path / "movie.mkv")
    dst = tmp_path / "lib" / "movie.mkv"
    dst.parent.mkdir(parents=True)

    def exploding_copy2(s, d):
        with open(d, "wb") as f:
            f.write(b"partial")
        raise OSError("connection reset")

    monkeypatch.setattr(imp.shutil, "copy2", exploding_copy2)
    with pytest.raises(OSError, match="connection reset"):
        atomic_verified_copy(str(src), str(dst))
    assert not dst.exists()
    assert _no_tmp_leftovers(dst.parent)


def test_move_same_filesystem_is_a_rename(tmp_path):
    src = _mk(tmp_path / "dl" / "ep.mkv", b"e" * 5_000)
    dst = tmp_path / "lib" / "Show" / "S01E01.mkv"
    atomic_verified_move(str(src), str(dst))  # makedirs is its job
    assert dst.read_bytes() == b"e" * 5_000
    assert not src.exists()                   # move relocates


def test_move_cross_device_verifies_before_removing_source(tmp_path, monkeypatch):
    src = _mk(tmp_path / "dl" / "ep.mkv", b"e" * 5_000)
    dst = tmp_path / "lib" / "S01E01.mkv"

    real_replace = os.replace

    def exdev_for_direct_move(s, d):
        if str(s) == str(src):                # only the direct src→dst rename fails
            raise OSError(errno.EXDEV, "cross-device link")
        return real_replace(s, d)             # the tmp→dst promotion still works

    monkeypatch.setattr(imp.os, "replace", exdev_for_direct_move)
    atomic_verified_move(str(src), str(dst))
    assert dst.read_bytes() == b"e" * 5_000
    assert not src.exists()
    assert _no_tmp_leftovers(dst.parent)


def test_cross_device_failed_copy_keeps_the_source(tmp_path, monkeypatch):
    """The only good copy must survive a failed cross-device move."""
    src = _mk(tmp_path / "dl" / "ep.mkv", b"e" * 5_000)
    dst = tmp_path / "lib" / "S01E01.mkv"

    def always_exdev(s, d):
        raise OSError(errno.EXDEV, "cross-device link")

    def broken_copy2(s, d):
        raise OSError("no space left on device")

    monkeypatch.setattr(imp.os, "replace", always_exdev)
    monkeypatch.setattr(imp.shutil, "copy2", broken_copy2)
    with pytest.raises(OSError, match="no space"):
        atomic_verified_move(str(src), str(dst))
    assert src.exists()                       # source untouched
    assert not dst.exists()


def test_move_non_exdev_errors_propagate(tmp_path, monkeypatch):
    """A real failure (perms/missing) must not silently fall into copy mode."""
    src = _mk(tmp_path / "ep.mkv")
    dst = tmp_path / "lib" / "S01E01.mkv"

    def eacces(s, d):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(imp.os, "replace", eacces)
    with pytest.raises(OSError, match="permission denied"):
        atomic_verified_move(str(src), str(dst))
    assert src.exists()


def test_real_fs_facade_routes_through_the_atomic_helpers(tmp_path, monkeypatch):
    """run_import's production fs must get the guarantee, not bare shutil."""
    fs = real_fs()
    src = _mk(tmp_path / "a.mkv", b"a" * 2_000)
    dst = tmp_path / "lib" / "a.mkv"
    dst.parent.mkdir(parents=True)
    fs.copy(str(src), str(dst))
    assert dst.read_bytes() == b"a" * 2_000

    # The short-copy refusal proves the facade goes through verification.
    def short_copy2(s, d):
        with open(d, "wb") as f:
            f.write(b"a")

    monkeypatch.setattr(imp.shutil, "copy2", short_copy2)
    with pytest.raises(OSError, match="short copy"):
        fs.copy(str(src), str(tmp_path / "lib" / "b.mkv"))


def test_download_monitor_mover_is_atomic_too(tmp_path):
    src = _mk(tmp_path / "dl" / "m.mkv", b"m" * 3_000)
    dst = tmp_path / "lib" / "Movies" / "m.mkv"
    _move(str(src), str(dst))
    assert dst.read_bytes() == b"m" * 3_000
    assert not src.exists()
    assert _no_tmp_leftovers(dst.parent)
