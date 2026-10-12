"""Tests for audiobook import staging: build where media servers can't see,
reveal atomically.

The M4B merge deletes its source chapters after folding them. Filing those
chapters into the watched library first lets Plex ingest files that vanish
moments later (ghost entries + a manual rescan). Staging the whole book
folder outside the library and revealing it in one atomic operation means
scanners only ever see complete books — the folder-level version of the
music side's atomic_copy_to_staging.
"""

import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.audiobook_download_monitor import _nudge_media_server_scan, _organize
from core.audiobook_organizer import (
    _sweep_stale_staging,
    new_staging_dir,
    reveal_staged_book,
    staging_base,
    write_release_marker,
)


def _book(**overrides):
    payload = {
        "title": "The Book",
        "author_names": ["The Author"],
        "narrator_names": [],
        "series": [],
    }
    payload.update(overrides)
    return payload


def _row(**overrides):
    payload = {
        "download_id": "d1",
        "asin": "B1",
        "title": "The Book",
        "author": "The Author",
        "source": "torrent",
        "client_id": "hash-1",
        "release_title": "The Author - The Book",
    }
    payload.update(overrides)
    return payload


def _download_with_chapters(parent: Path, count: int = 3) -> Path:
    src = parent / "download"
    src.mkdir(parents=True)
    for i in range(1, count + 1):
        (src / f"chapter{i:02d}.mp3").write_bytes(b"fake audio %d" % i)
    (src / "cover.jpg").write_bytes(b"fake cover")
    return src


def _patch_settings(monkeypatch):
    monkeypatch.setattr("core.settings.config_manager.get", lambda key, default=None: True)


# ---------------------------------------------------------------------------
# staging_base / new_staging_dir
# ---------------------------------------------------------------------------

def test_staging_base_is_a_hidden_sibling_of_the_library(tmp_path):
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    base = staging_base(str(lib))
    assert base is not None
    assert base.parent == lib.parent
    assert base.name.startswith(".")
    assert base.is_dir()


def test_staging_base_returns_none_when_it_cannot_be_created(tmp_path, monkeypatch):
    lib = tmp_path / "audiobooks"
    lib.mkdir()

    real_mkdir = Path.mkdir

    def boom(self, *args, **kwargs):
        if str(self).endswith(".soulsync-audiobook-staging"):
            raise OSError("read-only")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", boom)
    assert staging_base(str(lib)) is None


def test_staging_base_falls_back_inside_library_root(tmp_path, monkeypatch):
    """Docker top-level mount (/audiobooks) as a non-root user: creating the
    sibling under / raises PermissionError, so staging falls back to inside
    the library root (dot-prefixed: scanners skip it)."""
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    sibling = tmp_path / ".soulsync-audiobook-staging"
    real_mkdir = Path.mkdir

    def boom(self, *args, **kwargs):
        if self == sibling:
            raise PermissionError("read-only /")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", boom)
    base = staging_base(str(lib))
    assert base == lib / ".soulsync-audiobook-staging"
    assert base.is_dir()


def test_sweep_only_removes_book_prefixed_dirs(tmp_path):
    base = tmp_path / ".soulsync-audiobook-staging"
    base.mkdir()
    keep = base / "user-notes"
    keep.mkdir()
    stale = base / "book-abc123"
    stale.mkdir()
    ancient = time.time() - 25 * 3600
    os.utime(keep, (ancient, ancient))
    os.utime(stale, (ancient, ancient))

    _sweep_stale_staging(base)

    assert keep.is_dir()
    assert not stale.exists()


def test_reveal_refuses_when_staged_book_is_the_stage_root(tmp_path):
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    base = staging_base(str(lib))
    staged = new_staging_dir(base)

    result = reveal_staged_book(staged, staged, str(lib), release_id="r1")

    assert result["ok"] is False
    assert result["path"] == ""
    assert "staging root" in result["error"]
    assert list(lib.iterdir()) == []  # the library root itself is untouched


def test_reveal_copies_across_filesystems(tmp_path):
    """Docker -v / NAS mounts: staging and library on different devices.
    The whole-folder rename raises EXDEV, so the book is copied file by
    file — each still appearing atomically via temp name + rename."""
    shm = Path("/dev/shm")
    if not shm.is_dir():
        pytest.skip("no second filesystem available")
    if os.stat(shm).st_dev == os.stat(tmp_path).st_dev:
        pytest.skip("same filesystem, cannot exercise EXDEV")
    lib = tmp_path / "audiobooks"
    base = staging_base(str(shm / "staging-test"))
    assert base is not None
    staged = new_staging_dir(base)
    book_dir = staged / "The Author" / "The Book"
    book_dir.mkdir(parents=True)
    (book_dir / "The Book.m4b").write_bytes(b"fake-m4b")
    write_release_marker(book_dir, "release-1")

    revealed = reveal_staged_book(book_dir, staged, str(lib), release_id="release-1")

    assert revealed["ok"] is True
    assert revealed["path"] == str(lib / "The Author" / "The Book")
    assert (lib / "The Author" / "The Book" / "The Book.m4b").is_file()
    # Staged sources are consumed by the copy-reveal.
    assert (book_dir / "The Book.m4b").exists() is False
    shutil.rmtree(base, ignore_errors=True)


def test_staging_base_resolves_symlinked_library_roots(tmp_path):
    """A library root symlinked elsewhere stages next to the resolved
    location, so the same-filesystem check sees the real layout."""
    real = tmp_path / "real-books"
    real.mkdir()
    linkdir = tmp_path / "linkdir"
    linkdir.mkdir()
    link = linkdir / "audiobooks"
    link.symlink_to(real, target_is_directory=True)
    base = staging_base(str(link))
    assert base is not None
    assert base.parent == tmp_path  # next to real-books, not next to the link
    assert base.is_dir()
    first = new_staging_dir(base)
    second = new_staging_dir(base)
    assert first is not None and second is not None
    assert first != second
    assert first.is_dir() and second.is_dir()


def test_stale_staging_dirs_are_swept_but_fresh_ones_survive(tmp_path):
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    base = tmp_path / ".soulsync-audiobook-staging"
    base.mkdir()
    stale = base / "book-old"
    stale.mkdir()
    fresh = base / "book-new"
    fresh.mkdir()
    old = time.time() - (25 * 3600)
    os.utime(stale, (old, old))

    staging_base(str(lib))

    assert not stale.exists()
    assert fresh.is_dir()


# ---------------------------------------------------------------------------
# reveal_staged_book
# ---------------------------------------------------------------------------

def _staged_book(base: Path, *segments: str, files=("a.mp3", "b.mp3")) -> Path:
    book = base.joinpath(*segments)
    book.mkdir(parents=True)
    for name in files:
        (book / name).write_bytes(b"x")
    write_release_marker(book, "rel-1")
    return book


def test_reveal_moves_the_whole_folder_atomically(tmp_path):
    lib = tmp_path / "audiobooks"
    stage = tmp_path / "staging"
    staged = _staged_book(stage, "The Author", "The Book")

    result = reveal_staged_book(staged, stage, str(lib), release_id="rel-1")

    assert result["ok"] is True
    dest = lib / "The Author" / "The Book"
    assert result["path"] == str(dest)
    assert dest.is_dir()
    assert (dest / "a.mp3").is_file()
    assert not staged.exists()
    # The release marker moved along, so the claim survives the reveal.
    assert (dest / ".soulsync-release").is_file()


def test_reveal_refuses_a_different_release(tmp_path):
    lib = tmp_path / "audiobooks"
    dest = lib / "The Author" / "The Book"
    dest.mkdir(parents=True)
    write_release_marker(dest, "other-release")
    stage = tmp_path / "staging"
    staged = _staged_book(stage, "The Author", "The Book")

    result = reveal_staged_book(staged, stage, str(lib), release_id="rel-1")

    assert result["ok"] is False
    assert result["path"] == ""
    assert "different release" in result["error"]
    # Nothing was moved: the staged book is untouched.
    assert staged.is_dir()
    assert (staged / "a.mp3").is_file()


def test_reveal_allows_the_same_release_again(tmp_path):
    lib = tmp_path / "audiobooks"
    dest = lib / "The Author" / "The Book"
    dest.mkdir(parents=True)
    write_release_marker(dest, "rel-1")
    stage = tmp_path / "staging"
    staged = _staged_book(stage, "The Author", "The Book")

    result = reveal_staged_book(staged, stage, str(lib), release_id="rel-1")

    assert result["ok"] is True
    assert (lib / "The Author" / "The Book" / "a.mp3").is_file()


def test_reveal_falls_back_to_per_file_moves_when_destination_exists(tmp_path):
    lib = tmp_path / "audiobooks"
    dest = lib / "The Author" / "The Book"
    dest.mkdir(parents=True)
    (dest / "existing.mp3").write_bytes(b"old")
    stage = tmp_path / "staging"
    staged = _staged_book(stage, "The Author", "The Book")

    result = reveal_staged_book(staged, stage, str(lib), release_id="rel-1")

    assert result["ok"] is True
    assert (dest / "a.mp3").is_file()
    assert (dest / "existing.mp3").is_file()  # untouched


def test_reveal_rejects_a_book_outside_the_staging_root(tmp_path):
    lib = tmp_path / "audiobooks"
    elsewhere = tmp_path / "elsewhere" / "Book"
    elsewhere.mkdir(parents=True)
    stage = tmp_path / "staging"

    result = reveal_staged_book(elsewhere, stage, str(lib))

    assert result["ok"] is False


# ---------------------------------------------------------------------------
# _organize end to end
# ---------------------------------------------------------------------------

def _patch_organize_env(monkeypatch, lib: Path, book):
    monkeypatch.setattr("core.audiobook_download_monitor._book_for", lambda row: book)
    monkeypatch.setattr("core.audiobook_organizer.library_root", lambda: str(lib))
    monkeypatch.setattr(
        "core.audiobook_organizer.configured_template", lambda: "$author/$title")
    monkeypatch.setattr(
        "core.audiobook_m4b_merge.merge_book_files",
        lambda folder, files, book: files,  # merge disabled: passthrough
    )
    monkeypatch.setattr(
        "core.audiobook_post_processor.post_process_book",
        lambda folder, book, files=None: {"tagged": 0},
    )
    _patch_settings(monkeypatch)
    return patch("core.audiobook_download_monitor._request_scan_nudge")


def test_organize_stages_then_reveals_final_book_only(tmp_path, monkeypatch):
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    src = _download_with_chapters(tmp_path)
    book = _book()

    nudge = _patch_organize_env(monkeypatch, lib, book)
    with nudge as nudge_mock:
        result = _organize(str(src), _row())

    assert result["ok"] is True
    dest = lib / "The Author" / "The Book"
    assert result["path"] == str(dest)
    assert (dest / "01 - chapter01.mp3").is_file()
    assert (dest / "cover.jpg").is_file()  # sidecars ride along
    # Sources were copied, never moved: the download is intact for retries.
    assert (src / "chapter01.mp3").is_file()
    # No staging leftovers.
    staging = lib.parent / ".soulsync-audiobook-staging"
    assert not any(staging.iterdir())
    # The import nudged the media server once.
    nudge_mock.assert_called_once_with()


def test_organize_with_merge_reveals_only_the_m4b(tmp_path, monkeypatch):
    """The reported bug: chapters must never sit in the watched library."""
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    src = _download_with_chapters(tmp_path)
    book = _book()

    def fake_merge(folder, files, book):
        folder = Path(folder)
        out = folder / "The Book.m4b"
        out.write_bytes(b"merged")
        for f in files:
            Path(f).unlink(missing_ok=True)
        return [str(out)]

    _patch_organize_env(monkeypatch, lib, book)
    monkeypatch.setattr("core.audiobook_m4b_merge.merge_book_files", fake_merge)

    result = _organize(str(src), _row())

    assert result["ok"] is True
    dest = lib / "The Author" / "The Book"
    assert (dest / "The Book.m4b").is_file()
    assert not list(dest.glob("*.mp3")), "chapter files leaked into the library"
    assert result["path"] == str(dest)
    assert str(dest / "The Book.m4b") in result["files"]


def test_organize_falls_back_to_direct_filing_without_staging(tmp_path, monkeypatch):
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    src = _download_with_chapters(tmp_path)
    book = _book()

    _patch_organize_env(monkeypatch, lib, book)
    monkeypatch.setattr("core.audiobook_organizer.staging_base", lambda root: None)

    result = _organize(str(src), _row())

    assert result["ok"] is True
    dest = lib / "The Author" / "The Book"
    assert result["path"] == str(dest)
    assert (dest / "01 - chapter01.mp3").is_file()


def test_organize_fails_cleanly_when_the_reveal_is_refused(tmp_path, monkeypatch):
    lib = tmp_path / "audiobooks"
    dest = lib / "The Author" / "The Book"
    dest.mkdir(parents=True)
    write_release_marker(dest, "other-release")
    src = _download_with_chapters(tmp_path)
    book = _book()

    _patch_organize_env(monkeypatch, lib, book)

    result = _organize(str(src), _row())

    assert result["ok"] is False
    assert result["path"] == ""
    assert "different release" in result["error"]
    # The refused book never reaches the library...
    assert (dest / "01 - chapter01.mp3").exists() is False
    # ...and the download sources are intact for a retry.
    assert (src / "chapter01.mp3").is_file()


def test_organize_refuses_different_release_before_any_work(tmp_path, monkeypatch):
    """The one-book-one-release rule fails fast against the real destination:
    no copy, no merge encode — the reveal used to refuse only after the
    full merge."""
    lib = tmp_path / "audiobooks"
    dest = lib / "The Author" / "The Book"
    dest.mkdir(parents=True)
    write_release_marker(dest, "other-release")
    src = _download_with_chapters(tmp_path)
    book = _book()

    nudge = _patch_organize_env(monkeypatch, lib, book)
    with (nudge,
          patch("core.audiobook_organizer.organize_download") as org_mock,
          patch("core.audiobook_m4b_merge.merge_book_files") as merge_mock):
        result = _organize(str(src), _row())

    assert result["ok"] is False
    assert result["path"] == ""
    assert "different release" in result["error"]
    org_mock.assert_not_called()
    merge_mock.assert_not_called()
    # Staging was never even set up.
    assert (tmp_path / ".soulsync-audiobook-staging").exists() is False


# ---------------------------------------------------------------------------
# scan nudge
# ---------------------------------------------------------------------------

def test_nudge_triggers_a_scan_on_the_active_client():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.is_connected.return_value = True
    engine = MagicMock()
    engine.active_client.return_value = client

    with patch("core.media_server.engine.get_media_server_engine", return_value=engine):
        _nudge_media_server_scan()

    client.trigger_library_scan.assert_called_once_with()


def test_nudge_is_silent_without_a_client():
    with patch("core.media_server.engine.get_media_server_engine",
               side_effect=RuntimeError("no engine")):
        _nudge_media_server_scan()  # must not raise


def test_nudge_skips_a_disconnected_client():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.is_connected.return_value = False
    engine = MagicMock()
    engine.active_client.return_value = client

    with patch("core.media_server.engine.get_media_server_engine", return_value=engine):
        _nudge_media_server_scan()

    client.trigger_library_scan.assert_not_called()
