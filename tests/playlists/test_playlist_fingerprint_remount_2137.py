"""#2137: playlist ownership has to survive a remount.

a remounted fat/exfat drive or network share hands every file a new inode,
device and ctime. the copy (and the link) is still the one soulsync wrote,
so it stays owned and gets pruned when it leaves the playlist. an edited
copy is still preserved.
"""

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from core.playlists.managed_artifacts import (
    MANIFEST_DIR, artifact_fingerprint, entry_is_owned, load_manifest,
)
from core.playlists.materialize import rebuild_playlist_folder


def _audio(path, data=b"original audio"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _remount(root: Path) -> None:
    """same path, same bytes and mtimes, new inodes and ctimes."""
    moved = root.parent / (root.name + "-remount")
    shutil.copytree(root, moved, symlinks=True, copy_function=shutil.copy2)
    shutil.rmtree(root)
    moved.rename(root)


def _manifest_files(root: Path):
    return list((root / MANIFEST_DIR).glob("*.json"))


@pytest.mark.parametrize("mode", ["copy", "symlink"])
def test_entries_stay_owned_after_a_remount(tmp_path, mode):
    """#2137: a rebuild after a remount must not call every entry changed."""
    keep = _audio(tmp_path / "Music" / "keep.flac", b"keep audio")
    gone = _audio(tmp_path / "Music" / "gone.flac", b"gone audio")
    root = tmp_path / "Playlists"
    first = rebuild_playlist_folder(str(root), "Mix", [keep, gone], mode)
    pdir = Path(first.playlist_dir)
    before = os.lstat(pdir / "keep.flac").st_ino

    _remount(root)
    assert os.lstat(pdir / "keep.flac").st_ino != before

    summary = rebuild_playlist_folder(str(root), "Mix", [keep], mode)
    assert summary.failed == 0, summary.errors
    assert summary.removed_stale == 1
    assert not os.path.lexists(pdir / "gone.flac")
    assert "keep.flac" in load_manifest(str(root), str(pdir))["entries"]


def test_edited_copy_is_still_preserved_after_a_remount(tmp_path):
    """#2137: dropping inode/ctime must not lose the real protection."""
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    first = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original = copied.stat()
    copied.write_bytes(b"user edit!!!!!")  # same length as the original
    os.utime(copied, ns=(original.st_atime_ns, original.st_mtime_ns))
    _remount(root)

    summary = rebuild_playlist_folder(str(root), "Mix", [], "copy")
    assert copied.read_bytes() == b"user edit!!!!!"
    assert summary.removed_stale == 0


def test_permission_bits_do_not_change_ownership(tmp_path):
    """#2137: fat mount options (fmask/umask) can change mode bits on remount."""
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    first = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    os.chmod(copied, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    assert rebuild_playlist_folder(str(root), "Mix", [], "copy").removed_stale == 1


def _legacy_fingerprint(path):
    """the pre #2137 shape, built the way the old code did."""
    st = os.lstat(path)
    old = {"device": st.st_dev, "inode": st.st_ino, "mode": st.st_mode,
           "size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}
    if stat.S_ISLNK(st.st_mode):
        old["target"] = os.readlink(path)
    else:
        old["sha256"] = artifact_fingerprint(path)["sha256"]
    return old


def _rewrite_as_legacy(root: Path, pdir: Path) -> None:
    [path] = _manifest_files(root)
    value = json.loads(path.read_text(encoding="utf-8"))
    for name, record in value["entries"].items():
        record["fingerprint"] = _legacy_fingerprint(pdir / name)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("mode", ["copy", "symlink"])
def test_old_manifests_keep_owning_their_entries_after_upgrade_and_remount(tmp_path, mode):
    """#2137: a manifest written before the fix must not make every entry look changed."""
    keep = _audio(tmp_path / "Music" / "keep.flac", b"keep audio")
    gone = _audio(tmp_path / "Music" / "gone.flac", b"gone audio")
    root = tmp_path / "Playlists"
    pdir = Path(rebuild_playlist_folder(str(root), "Mix", [keep, gone], mode).playlist_dir)
    _rewrite_as_legacy(root, pdir)
    _remount(root)

    summary = rebuild_playlist_folder(str(root), "Mix", [keep], mode)
    assert summary.failed == 0, summary.errors
    assert summary.removed_stale == 1
    # the manifest is written back in the remount-safe shape
    [path] = _manifest_files(root)
    record = json.loads(path.read_text(encoding="utf-8"))["entries"]["keep.flac"]
    assert "inode" not in record["fingerprint"] and "device" not in record["fingerprint"]
    assert record["fingerprint"] == artifact_fingerprint(str(pdir / "keep.flac"))


def test_old_manifest_still_protects_an_edited_copy(tmp_path):
    """#2137: the legacy shape is read by its sha256, so an edit is still an edit."""
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    pdir = Path(rebuild_playlist_folder(str(root), "Mix", [source], "copy").playlist_dir)
    _rewrite_as_legacy(root, pdir)
    (pdir / "song.flac").write_bytes(b"user edit!!!!!")

    summary = rebuild_playlist_folder(str(root), "Mix", [], "copy")
    assert (pdir / "song.flac").read_bytes() == b"user edit!!!!!"
    assert summary.removed_stale == 0


def test_stable_fingerprint_grants_nothing_for_partial_records(tmp_path):
    """#2137: a legacy file record without a digest, or junk, owns nothing."""
    from core.playlists.managed_artifacts import stable_fingerprint
    target = Path(_audio(tmp_path / "a.flac"))
    assert stable_fingerprint(None) is None
    assert stable_fingerprint({"mode": stat.S_IFREG | 0o644, "size": 5}) is None
    assert stable_fingerprint({"kind": "file", "sha256": "x"}) is None
    assert not entry_is_owned(str(target), {"fingerprint": {"mode": stat.S_IFREG | 0o644}})
    link = tmp_path / "link.flac"
    link.symlink_to("a.flac")
    # a link retargeted by the user is not ours
    assert not entry_is_owned(str(link), {"fingerprint": {"kind": "symlink", "target": "b.flac"}})
    assert entry_is_owned(str(link), {"fingerprint": {"kind": "symlink", "target": "a.flac"}})
