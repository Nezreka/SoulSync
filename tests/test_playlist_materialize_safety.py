"""Real-filesystem regressions for playlist ownership and output identity."""

import os
import json
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

from core.playlists.materialize import materialize_one, rebuild_playlist_folder
from core.playlists.materialize_service import (
    materialize_playlist_from_batch, rebuild_mirrored_playlist_if_organized,
    reconcile_batch_playlists,
)


def _audio(path, data=b"original audio"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize("mode", ["symlink", "copy"])
def test_overlapping_historical_playlist_keeps_originals_and_m3u(tmp_path, mode):
    root = tmp_path / "Playlists"
    current = root / "Mix" / "A.flac"
    removed = root / "Mix" / "Removed.flac"
    m3u = root / "Mix" / "Mix.m3u"
    _audio(current)
    _audio(removed, b"historical original")
    _audio(m3u, b"A.flac\nRemoved.flac\n")
    summary = rebuild_playlist_folder(str(root), "Mix", [str(current)], mode)
    assert not current.is_symlink()
    assert current.read_bytes() == b"original audio"
    assert removed.read_bytes() == b"historical original"
    assert m3u.read_bytes() == b"A.flac\nRemoved.flac\n"
    assert summary.removed_stale == 0


def test_hardlinked_source_destination_is_never_unlinked(tmp_path):
    source = Path(_audio(tmp_path / "Music" / "song.flac"))
    destination = tmp_path / "Playlists" / "Mix" / "song.flac"
    destination.parent.mkdir(parents=True)
    os.link(source, destination)
    inode = destination.stat().st_ino
    assert materialize_one(str(source), str(destination)) == "unchanged"
    assert not destination.is_symlink()
    assert destination.stat().st_ino == inode


@pytest.mark.parametrize("mode", ["symlink", "copy"])
def test_unowned_destination_collision_preserves_bytes(tmp_path, mode):
    source = _audio(tmp_path / "Music" / "song.flac")
    destination = tmp_path / "Playlists" / "Mix" / "song.flac"
    _audio(destination, b"irreplaceable original")
    summary = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Mix", [source], mode)
    assert not destination.is_symlink()
    assert destination.read_bytes() == b"irreplaceable original"
    assert summary.failed == 1


@pytest.mark.parametrize("membership", [[], ["song.flac"]])
def test_edited_managed_copy_is_never_pruned_or_replaced(tmp_path, membership):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original_stat = copied.stat()
    copied.write_bytes(b"modified audio")  # same byte length as original
    os.utime(copied, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    summary = rebuild_playlist_folder(root, "Mix", [source] if membership else [], "copy")
    assert copied.read_bytes() == b"modified audio"
    assert summary.removed_stale == 0


def test_pruning_keeps_foreign_entries_but_removes_unchanged_managed_copy(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    foreign = Path(first.playlist_dir) / "foreign.flac"
    _audio(foreign, b"not ours")
    summary = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert summary.removed_stale == 1
    assert not (Path(first.playlist_dir) / "song.flac").exists()
    assert foreign.read_bytes() == b"not ours"


def test_playlist_directory_symlink_does_not_allow_external_writes(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    root.mkdir()
    outside = tmp_path / "Originals"
    _audio(outside / "precious.flac", b"never delete")
    (root / "Mix").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        rebuild_playlist_folder(str(root), "Mix", [source])
    assert (outside / "precious.flac").read_bytes() == b"never delete"
    assert not (outside / "song.flac").exists()


def test_dest_name_cannot_escape_playlist_directory(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    result = rebuild_playlist_folder(str(root), "Mix", [source], "copy", dest_names=["../../escaped.flac"])
    assert not (tmp_path / "escaped.flac").exists()
    assert all(p.parent == Path(result.playlist_dir) for p in Path(result.playlist_dir).iterdir())


class _Cfg:
    def __init__(self, root):
        self.values = {"playlists.materialize_path": str(root), "playlists.materialize_mode": "copy"}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _PlaylistDB:
    def __init__(self, playlists, paths):
        self.playlists = playlists
        self.paths = paths

    def get_mirrored_playlist(self, playlist_id, profile_id=None):
        return next((pl for pl in self.playlists if pl["id"] == playlist_id
                     and (profile_id is None or pl["profile_id"] == profile_id)), None)

    def get_mirrored_playlists(self, profile_id=1):
        return [pl for pl in self.playlists if pl["profile_id"] == profile_id]

    def resolve_mirrored_playlist(self, ref, profile_id=1, *, default_source="spotify"):
        return next((pl for pl in self.get_mirrored_playlists(profile_id)
                     if str(ref) in (str(pl["id"]), pl["source_playlist_id"])
                     and pl["source"] == default_source), None)

    def get_mirrored_playlist_tracks(self, playlist_id):
        return [{"track_name": str(playlist_id), "artist_name": "Artist"}]

    def check_track_exists(self, title, artist, confidence_threshold=0.7):
        return type("Track", (), {"file_path": self.paths[int(title)]})(), 1.0


def _playlist(pid, profile, source="spotify", ref=None):
    return {"id": pid, "profile_id": profile, "source": source,
            "source_playlist_id": ref or f"upstream-{pid}", "name": "Favorites",
            "organize_by_playlist": True}


def test_same_name_playlists_are_isolated_by_owner_source_and_identity(tmp_path):
    playlists = [_playlist(1, 2), _playlist(2, 3, ref="upstream-1"), _playlist(3, 2),
                 _playlist(4, 2, "deezer", ref="upstream-1")]
    paths = {pl["id"]: _audio(tmp_path / "Music" / f"{pl['id']}.flac") for pl in playlists}
    db, cfg = _PlaylistDB(playlists, paths), _Cfg(tmp_path / "Playlists")
    summaries = [rebuild_mirrored_playlist_if_organized(db, cfg, pl["id"], profile_id=pl["profile_id"])
                 for pl in playlists]
    assert len({s.playlist_dir for s in summaries}) == 4
    assert all(s.removed_stale == 0 for s in summaries)
    for pl, summary in zip(playlists, summaries):
        assert (Path(summary.playlist_dir) / f"{pl['id']}.flac").read_bytes() == b"original audio"


@pytest.mark.parametrize("source_ref", [None, "upstream-1"])
def test_batch_completion_and_mirror_use_same_destination_without_mirror_id(tmp_path, source_ref):
    pl = _playlist(1, 2)
    source = _audio(tmp_path / "Music" / "1.flac")
    db, cfg = _PlaylistDB([pl], {1: source}), _Cfg(tmp_path / "Playlists")
    batch = {"profile_id": 2, "playlist_folder_mode": True, "playlist_name": "Favorites",
             "batch_source": "spotify", "analysis_results": [{"found": True, "matched_file_path": source}],
             "queue": []}
    if source_ref:
        batch["source_playlist_ref"] = source_ref
    results = reconcile_batch_playlists(db, batch, {}, cfg)
    mirror = rebuild_mirrored_playlist_if_organized(db, cfg, 1, profile_id=2)
    assert len(results) == 1
    assert results[0][1].playlist_dir == mirror.playlist_dir
    assert (Path(mirror.playlist_dir) / "1.flac").exists()


def test_batch_source_ref_uses_same_identity_as_later_mirror(tmp_path):
    pl = _playlist(1, 2)
    source = _audio(tmp_path / "Music" / "1.flac")
    db, cfg = _PlaylistDB([pl], {1: source}), _Cfg(tmp_path / "Playlists")
    batch = {"profile_id": 2, "playlist_folder_mode": True, "playlist_name": "Favorites",
             "batch_source": "spotify", "source_playlist_ref": "upstream-1",
             "analysis_results": [{"found": True, "matched_file_path": source}], "queue": []}
    first = materialize_playlist_from_batch(batch, {}, cfg)
    second = rebuild_mirrored_playlist_if_organized(db, cfg, 1, profile_id=2)
    assert first.playlist_dir == second.playlist_dir
    # one folder for the batch and the later mirror rebuild, named after the
    # playlist as in 3.6.1 (the stable identity is in the manifest)
    assert Path(first.playlist_dir).name == "Favorites"


def test_managed_artifact_reused_as_a_source_cannot_be_pruned(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    original = rebuild_playlist_folder(root, "Mix", [source], "copy")
    reused_source = Path(original.playlist_dir) / "song.flac"
    summary = rebuild_playlist_folder(root, "Mix", [str(reused_source)], dest_names=["renamed.flac"])
    assert reused_source.read_bytes() == b"original audio"
    assert (Path(summary.playlist_dir) / "renamed.flac").read_bytes() == b"original audio"
    assert summary.removed_stale == 0


def test_unknown_same_name_batches_cannot_replace_each_others_copy(tmp_path):
    first = _audio(tmp_path / "Music" / "one" / "song.flac", b"first original")
    second = _audio(tmp_path / "Music" / "two" / "song.flac", b"second original")
    cfg = _Cfg(tmp_path / "Playlists")
    batch = {"profile_id": 2, "playlist_folder_mode": True, "playlist_name": "Unknown",
             "analysis_results": [{"found": True, "matched_file_path": first}]}
    summary = materialize_playlist_from_batch(batch, {}, cfg)
    batch["analysis_results"] = [{"found": True, "matched_file_path": second}]
    later = materialize_playlist_from_batch(batch, {}, cfg)
    assert (Path(summary.playlist_dir) / "song.flac").read_bytes() == b"first original"
    assert later.failed == 1


def test_manifest_records_only_successfully_created_artifacts_and_readable_identity(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    pl = _playlist(1, 2)
    result = rebuild_mirrored_playlist_if_organized(_PlaylistDB([pl], {1: source}), _Cfg(root), 1, profile_id=2)
    manifests = list((root / ".soulsync-playlist-manifests").glob("*.json"))
    assert len(manifests) == 1
    value = json.loads(manifests[0].read_text())
    assert value["folder"] == Path(result.playlist_dir).name
    assert value["playlist"]["playlist_name"] == "Favorites"
    assert value["playlist"]["profile_id"] == 2
    assert value["playlist"]["source"] == "spotify"
    assert value["playlist"]["source_playlist_id"] == "upstream-1"
    assert value["entries"]["song.flac"]["source"] == source

    def fail_copy(source, destination):
        Path(destination).write_bytes(b"partial")
        raise OSError("disk full")

    failed = rebuild_playlist_folder(str(root), "Fail", [source], "copy", copy_fn=fail_copy)
    assert failed.failed == 1
    assert list(Path(failed.playlist_dir).iterdir()) == []
    failed_manifest = next(json.loads(p.read_text()) for p in (root / ".soulsync-playlist-manifests").glob("*.json")
                           if json.loads(p.read_text())["folder"] == "Fail")
    assert failed_manifest["entries"] == {}


def test_interrupted_manifest_update_keeps_previous_complete_manifest(tmp_path, monkeypatch):
    from core.playlists import managed_artifacts

    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    first = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    manifest = next((root / ".soulsync-playlist-manifests").glob("*.json"))
    before = manifest.read_bytes()
    real_replace = managed_artifacts.os.replace

    def interrupt(source, destination):
        if str(destination).endswith(".json"):
            raise OSError("interrupted before manifest rename")
        return real_replace(source, destination)

    monkeypatch.setattr(managed_artifacts.os, "replace", interrupt)
    with pytest.raises(OSError):
        rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    assert manifest.read_bytes() == before
    assert (Path(first.playlist_dir) / "song.flac").read_bytes() == b"original audio"
    assert not list(manifest.parent.glob(".pending-*"))


def test_failed_owned_replacement_keeps_previous_copy(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    Path(source).write_bytes(b"new tags and new data")

    def fail_copy(source, destination):
        Path(destination).write_bytes(b"partial")
        raise OSError("disk full")

    later = rebuild_playlist_folder(root, "Mix", [source], "copy", copy_fn=fail_copy)
    assert later.failed == 1
    assert (Path(first.playlist_dir) / "song.flac").read_bytes() == b"original audio"


def test_reorganize_repairs_only_unchanged_owned_links_and_preserves_copy_ownership(tmp_path):
    from core.playlists.materialize import update_materialized_paths

    source = _audio(tmp_path / "Music" / "old.flac")
    unrelated = _audio(tmp_path / "Music" / "unrelated.flac", b"user-selected audio")
    root = str(tmp_path / "Playlists")
    owned = rebuild_playlist_folder(root, "Owned", [source])
    edited = rebuild_playlist_folder(root, "Edited", [source])
    copied = rebuild_playlist_folder(root, "Copied", [source], "copy")
    edited_link = Path(edited.playlist_dir) / "old.flac"
    edited_link.unlink()
    edited_link.symlink_to(unrelated)
    unowned = Path(owned.playlist_dir) / "foreign.flac"
    unowned.symlink_to(source)
    moved = tmp_path / "Music" / "renamed.flac"
    Path(source).rename(moved)
    assert update_materialized_paths(root, {source: str(moved)}) == 1
    assert (Path(owned.playlist_dir) / "old.flac").resolve() == moved
    assert edited_link.resolve() == Path(unrelated)
    assert os.readlink(unowned) == source
    assert (Path(copied.playlist_dir) / "old.flac").read_bytes() == b"original audio"
    # Repairs update fingerprints, so the next membership prune still owns it.
    assert rebuild_playlist_folder(root, "Owned", []).removed_stale == 1
    assert rebuild_playlist_folder(root, "Edited", []).removed_stale == 0


def test_manifest_directory_symlink_is_refused(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    root.mkdir()
    external = tmp_path / "external-manifests"
    external.mkdir()
    (root / ".soulsync-playlist-manifests").symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        rebuild_playlist_folder(str(root), "Mix", [source])
    assert list(external.iterdir()) == []
    assert not (root / "Mix" / "song.flac").exists()


def test_same_size_mtime_edit_survives_on_filesystems_with_creation_only_ctime(tmp_path, monkeypatch):
    # ctime no longer feeds ownership at all; this now pins that the sha256
    # alone catches a same-size, mtime-restored edit on such a filesystem.
    from core.playlists import managed_artifacts

    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original = copied.stat()
    copied.write_bytes(b"modified audio")
    os.utime(copied, ns=(original.st_atime_ns, original.st_mtime_ns))
    real_lstat = managed_artifacts.os.lstat

    def creation_ctime(path, *args, **kwargs):
        value = real_lstat(path, *args, **kwargs)
        if str(path) == str(copied):
            return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino, st_mode=value.st_mode,
                                   st_size=value.st_size, st_mtime_ns=value.st_mtime_ns,
                                   st_ctime_ns=original.st_ctime_ns)
        return value

    monkeypatch.setattr(managed_artifacts.os, "lstat", creation_ctime)
    summary = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert copied.read_bytes() == b"modified audio"
    assert summary.removed_stale == 0


def test_user_edit_during_copy_preparation_cannot_be_replaced(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    destination = Path(first.playlist_dir) / "song.flac"
    Path(source).write_bytes(b"new source bytes")

    def edit_during_copy(source, temporary):
        destination.write_bytes(b"concurrent user edit")
        shutil.copy2(source, temporary)

    summary = rebuild_playlist_folder(root, "Mix", [source], "copy", copy_fn=edit_during_copy)
    assert destination.read_bytes() == b"concurrent user edit"
    assert summary.failed == 1


def test_explicit_batch_source_overrides_legacy_id_prefix_heuristic(tmp_path):
    playlists = [_playlist(1, 2, "spotify", "1000"), _playlist(2, 2, "deezer", "1000")]
    paths = {1: _audio(tmp_path / "Music" / "spotify.flac"),
             2: _audio(tmp_path / "Music" / "deezer.flac")}
    db, cfg = _PlaylistDB(playlists, paths), _Cfg(tmp_path / "Playlists")
    batch = {"profile_id": 2, "playlist_folder_mode": True, "playlist_name": "Favorites",
             "batch_source": "spotify", "playlist_source": "deezer", "playlist_id": "1000", "queue": []}
    results = reconcile_batch_playlists(db, batch, {}, cfg)
    correct = rebuild_mirrored_playlist_if_organized(db, cfg, 2, profile_id=2)
    assert results[0][1].playlist_dir == correct.playlist_dir
    assert (Path(results[0][1].playlist_dir) / "deezer.flac").exists()


def test_explicit_mirror_id_is_not_interpreted_as_an_upstream_numeric_id(tmp_path):
    playlists = [_playlist(1, 2, ref="2"), _playlist(2, 2, ref="different-source-id")]
    paths = {1: _audio(tmp_path / "Music" / "wrong.flac"),
             2: _audio(tmp_path / "Music" / "correct.flac")}
    db, cfg = _PlaylistDB(playlists, paths), _Cfg(tmp_path / "Playlists")
    batch = {"profile_id": 2, "playlist_folder_mode": True, "playlist_name": "Favorites",
             "mirrored_playlist_id": 2, "batch_source": "spotify", "queue": []}
    results = reconcile_batch_playlists(db, batch, {}, cfg)
    mirror = rebuild_mirrored_playlist_if_organized(db, cfg, 2, profile_id=2)
    assert results[0][1].playlist_dir == mirror.playlist_dir
    assert (Path(results[0][1].playlist_dir) / "correct.flac").exists()


def test_unchanged_result_does_not_adopt_user_edit_after_initial_ownership_check(tmp_path, monkeypatch):
    from core.playlists import materialize

    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    destination = Path(first.playlist_dir) / "song.flac"
    real_check = materialize._copy_is_current

    def edit_after_freshness_check(real, dest):
        result = real_check(real, dest)
        destination.write_bytes(b"user edits after initial check")
        return result

    monkeypatch.setattr(materialize, "_copy_is_current", edit_after_freshness_check)
    rebuild_playlist_folder(root, "Mix", [source], "copy")
    summary = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert destination.read_bytes() == b"user edits after initial check"
    assert summary.removed_stale == 0


def _settled_fingerprint(path):
    """Fingerprint a just-created file: its timestamps can still settle
    during its first reads on some filesystems (observed on this VM's),
    which artifact_fingerprint's race check rightly reads as a change and
    answers None - so read once to settle, then require an answer."""
    from core.playlists.managed_artifacts import artifact_fingerprint

    artifact_fingerprint(path)
    result = artifact_fingerprint(path) or artifact_fingerprint(path)
    assert result is not None
    return result


def _shifted_stat(value):
    return SimpleNamespace(
        st_dev=value.st_dev + 9999, st_ino=value.st_ino + 12345,
        st_mode=value.st_mode, st_nlink=value.st_nlink, st_uid=value.st_uid,
        st_gid=value.st_gid, st_size=value.st_size,
        st_atime=value.st_atime, st_mtime=value.st_mtime, st_ctime=value.st_ctime,
        st_atime_ns=value.st_atime_ns, st_mtime_ns=value.st_mtime_ns,
        st_ctime_ns=value.st_ctime_ns + 1000000,
        st_blksize=getattr(value, "st_blksize", 0), st_blocks=getattr(value, "st_blocks", 0),
        st_flags=getattr(value, "st_flags", 0), st_rdev=getattr(value, "st_rdev", 0),
        st_file_attributes=getattr(value, "st_file_attributes", 0),
        st_reparse_tag=getattr(value, "st_reparse_tag", 0),
        st_birthtime=getattr(value, "st_birthtime", 0))


def _remount_shift(monkeypatch):
    """Simulate a remount: patching managed_artifacts.os patches the global
    os module, so every lstat/fstat in the process reports a different
    device, inode and ctime - a whole-volume identity change, consistently
    across lstat and fstat so the fingerprint's internal identity check
    still passes. That reach includes os.path.islink/lexists in callers,
    which read the same patched lstat (only st_mode, which is preserved)."""
    from core.playlists import managed_artifacts

    real_lstat = managed_artifacts.os.lstat
    real_fstat = managed_artifacts.os.fstat
    monkeypatch.setattr(managed_artifacts.os, "lstat",
                        lambda path, *a, **k: _shifted_stat(real_lstat(path, *a, **k)))
    monkeypatch.setattr(managed_artifacts.os, "fstat",
                        lambda fd, *a, **k: _shifted_stat(real_fstat(fd, *a, **k)))


def _manifest_for(root, folder):
    for path in (Path(root) / ".soulsync-playlist-manifests").glob("*.json"):
        value = json.loads(path.read_text())
        if value.get("folder") == folder:
            return path, value
    raise AssertionError(f"no manifest for {folder}")


def _make_legacy_fingerprint(root, folder, name):
    path, value = _manifest_for(root, folder)
    fingerprint = value["entries"][name]["fingerprint"]
    fingerprint.update({"device": 424242, "inode": 989898, "ctime_ns": 111})
    path.write_text(json.dumps(value))


def test_copy_ownership_survives_remount_identity_change(tmp_path, monkeypatch):
    from core.playlists.managed_artifacts import load_manifest

    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    _remount_shift(monkeypatch)
    summary = rebuild_playlist_folder(root, "Mix", [source], "copy")
    assert summary.failed == 0
    assert "song.flac" in load_manifest(root, first.playlist_dir)["entries"]
    os.chmod(copied, 0o600 if (copied.stat().st_mode & 0o7777) != 0o600 else 0o644)
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not copied.exists()


def test_legacy_manifest_fingerprint_with_identity_keys_still_owned(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    first = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    _make_legacy_fingerprint(root, "Mix", "song.flac")
    summary = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    assert summary.failed == 0
    # No migration: a verified-unchanged record keeps its legacy keys; the
    # comparator ignoring them is what keeps it owned.
    _, value = _manifest_for(root, "Mix")
    assert "device" in value["entries"]["song.flac"]["fingerprint"]
    pruned = rebuild_playlist_folder(str(root), "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not (Path(first.playlist_dir) / "song.flac").exists()


def test_legacy_owned_copy_can_be_replaced_after_source_change(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    first = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    _make_legacy_fingerprint(root, "Mix", "song.flac")
    Path(source).write_bytes(b"new source bytes, longer than before")
    summary = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    assert summary.failed == 0
    assert summary.copied == 1
    assert (Path(first.playlist_dir) / "song.flac").read_bytes() == b"new source bytes, longer than before"


def test_remount_does_not_relax_content_check(tmp_path, monkeypatch):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original_stat = copied.stat()
    assert len(b"modified audio") == len(b"original audio")
    copied.write_bytes(b"modified audio")  # same byte length as original
    os.utime(copied, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    _remount_shift(monkeypatch)
    summary = rebuild_playlist_folder(root, "Mix", [source], "copy")
    assert summary.failed == 1
    assert any("Managed playlist destination was changed" in error for error in summary.errors)
    assert copied.read_bytes() == b"modified audio"
    # No prune leg here: the failed rebuild above already popped the record,
    # so a following prune rebuild would evaluate nothing. Prune-under-edit
    # is covered by test_remount_prune_first_keeps_edited_copy.


def test_symlink_ownership_survives_device_change(tmp_path, monkeypatch):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source])
    link = Path(first.playlist_dir) / "song.flac"
    assert link.is_symlink()
    _remount_shift(monkeypatch)
    pruned = rebuild_playlist_folder(root, "Mix", [])
    assert pruned.removed_stale == 1
    assert not os.path.lexists(link)
    # A retargeted link not being owned is pinned in isolation by
    # test_symlink_target_is_the_proof_when_size_and_mtime_match.


def test_fingerprint_missing_required_key_is_not_owned(tmp_path):
    from core.playlists.managed_artifacts import entry_is_owned, load_manifest

    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    built = rebuild_playlist_folder(str(root), "Mix", [source], "copy")
    path, value = _manifest_for(root, "Mix")
    del value["entries"]["song.flac"]["fingerprint"]["sha256"]
    path.write_text(json.dumps(value))
    record = load_manifest(str(root), built.playlist_dir)["entries"]["song.flac"]
    dest = Path(built.playlist_dir) / "song.flac"
    assert not entry_is_owned(str(dest), record)
    pruned = rebuild_playlist_folder(str(root), "Mix", [], "copy")
    assert pruned.removed_stale == 0
    assert dest.read_bytes() == b"original audio"

    linked = rebuild_playlist_folder(str(root), "Links", [source])
    path, value = _manifest_for(root, "Links")
    del value["entries"]["song.flac"]["fingerprint"]["target"]
    path.write_text(json.dumps(value))
    record = load_manifest(str(root), linked.playlist_dir)["entries"]["song.flac"]
    link = Path(linked.playlist_dir) / "song.flac"
    assert not entry_is_owned(str(link), record)
    pruned = rebuild_playlist_folder(str(root), "Links", [])
    assert pruned.removed_stale == 0
    assert link.is_symlink()


def test_fingerprint_shape_is_remount_stable_only(tmp_path):
    from core.playlists.managed_artifacts import artifact_fingerprint

    regular = tmp_path / "song.flac"
    regular.write_bytes(b"original audio")
    captured = _settled_fingerprint(str(regular))
    assert set(captured) == {"mode", "size", "mtime_ns", "sha256"}
    link = tmp_path / "link.flac"
    link.symlink_to(regular)
    assert set(artifact_fingerprint(str(link))) == {"mode", "size", "mtime_ns", "target"}


def test_symlink_target_is_the_proof_when_size_and_mtime_match(tmp_path):
    if os.utime not in os.supports_follow_symlinks:
        pytest.skip("platform has no lutimes to restore a symlink's mtime")
    from core.playlists.managed_artifacts import (
        artifact_fingerprint, entry_is_owned, load_manifest)

    first_source = _audio(tmp_path / "Music" / "aaaa.flac")
    other_source = _audio(tmp_path / "Music" / "bbbb.flac", b"other audio!")
    root = str(tmp_path / "Playlists")
    built = rebuild_playlist_folder(root, "Mix", [first_source])
    link = Path(built.playlist_dir) / "aaaa.flac"
    record = load_manifest(root, built.playlist_dir)["entries"]["aaaa.flac"]
    before = artifact_fingerprint(str(link))
    original = os.lstat(link)
    link.unlink()
    # Same target-string length, so size matches too; only the target differs.
    link.symlink_to(os.path.relpath(other_source, start=link.parent))
    os.utime(link, ns=(original.st_atime_ns, original.st_mtime_ns), follow_symlinks=False)
    after = artifact_fingerprint(str(link))
    assert {k: v for k, v in after.items() if k != "target"} == {
        k: v for k, v in before.items() if k != "target"}
    assert after["target"] != before["target"]
    assert not entry_is_owned(str(link), record)


def test_legacy_symlink_record_repairs_after_source_move(tmp_path):
    from core.playlists.materialize import update_materialized_paths

    source = _audio(tmp_path / "Music" / "old.flac")
    root = tmp_path / "Playlists"
    built = rebuild_playlist_folder(str(root), "Mix", [source])
    _make_legacy_fingerprint(root, "Mix", "old.flac")
    moved = tmp_path / "Music" / "renamed.flac"
    Path(source).rename(moved)
    assert update_materialized_paths(str(root), {source: str(moved)}) == 1
    assert (Path(built.playlist_dir) / "old.flac").resolve() == moved
    assert rebuild_playlist_folder(str(root), "Mix", []).removed_stale == 1


def test_remount_prune_first_keeps_edited_copy(tmp_path, monkeypatch):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original_stat = copied.stat()
    copied.write_bytes(b"modified audio")
    os.utime(copied, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    _remount_shift(monkeypatch)
    # The prune rebuild is the FIRST rebuild after the edit, so its gate
    # evaluates the still-present record.
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 0
    assert copied.read_bytes() == b"modified audio"


def test_fingerprint_matches_rejects_malformed_inputs(tmp_path):
    import stat as stat_module

    from core.playlists.managed_artifacts import artifact_fingerprint, fingerprint_matches

    regular = tmp_path / "song.flac"
    regular.write_bytes(b"original audio")
    current = _settled_fingerprint(str(regular))
    assert fingerprint_matches(current, dict(current))
    assert not fingerprint_matches(None, current)
    assert not fingerprint_matches(current, None)
    assert not fingerprint_matches(current, "not a dict")
    assert not fingerprint_matches(current, {**current, "sha256": None})
    missing = dict(current)
    del missing["size"]
    assert not fingerprint_matches(current, missing)
    assert not fingerprint_matches(missing, current)
    directory = {**current, "mode": stat_module.S_IFDIR | 0o755}
    assert not fingerprint_matches(directory, dict(directory))
    assert not fingerprint_matches({**current, "mode": True}, {**current, "mode": 1})
    floated = {key: (float(value) if isinstance(value, int) else value)
               for key, value in current.items()}
    assert not fingerprint_matches(current, floated)
    # Per-key isolation on the stored side: each numeric key float-typed in
    # turn, and a bool that is numerically equal to the genuine value.
    assert not fingerprint_matches(current, {**current, "size": float(current["size"])})
    assert fingerprint_matches(current, {**current, "mtime_ns": float(current["mtime_ns"])})  # mtime is never compared
    synthetic = {"mode": current["mode"], "size": 1, "mtime_ns": 5, "sha256": "x"}
    assert not fingerprint_matches(synthetic, {**synthetic, "size": True})
    # Out-of-range stored modes must yield False, never raise: a corrupted
    # manifest can carry any JSON int, and stat.S_IFMT would OverflowError.
    assert not fingerprint_matches(current, {**current, "mode": -0o100644})
    assert not fingerprint_matches(current, {**current, "mode": 2**70 + 0o100644})
    assert not fingerprint_matches({**current, "mode": -5}, current)
    from core.playlists.managed_artifacts import entry_is_owned
    assert not entry_is_owned(str(regular), {"fingerprint": {**current, "mode": -5}})
    assert not entry_is_owned(str(regular), {"fingerprint": None})
    link = tmp_path / "link.flac"
    link.symlink_to(regular)
    link_current = artifact_fingerprint(str(link))
    assert fingerprint_matches(link_current, dict(link_current))
    assert not fingerprint_matches(link_current, {**link_current, "target": None})


def test_legacy_symlink_record_prunes_via_rebuild(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = tmp_path / "Playlists"
    built = rebuild_playlist_folder(str(root), "Mix", [source])
    _make_legacy_fingerprint(root, "Mix", "song.flac")
    pruned = rebuild_playlist_folder(str(root), "Mix", [])
    assert pruned.removed_stale == 1
    assert not os.path.lexists(Path(built.playlist_dir) / "song.flac")


def test_fingerprint_matches_ignores_permission_bits():
    import stat as stat_module

    from core.playlists.managed_artifacts import fingerprint_matches

    current = {"mode": stat_module.S_IFREG | 0o644, "size": 3, "mtime_ns": 5, "sha256": "x"}
    assert fingerprint_matches(current, {**current, "mode": stat_module.S_IFREG | 0o600})
    link = {"mode": stat_module.S_IFLNK | 0o777, "size": 3, "mtime_ns": 5, "target": "t"}
    assert fingerprint_matches(link, {**link, "mode": stat_module.S_IFLNK | 0o755})


def test_fingerprint_matches_rejects_type_change():
    import stat as stat_module

    from core.playlists.managed_artifacts import fingerprint_matches

    regular = {"mode": stat_module.S_IFREG | 0o644, "size": 3, "mtime_ns": 5, "sha256": "x"}
    link = {"mode": stat_module.S_IFLNK | 0o777, "size": 3, "mtime_ns": 5, "target": "t"}
    assert not fingerprint_matches(link, regular)
    assert not fingerprint_matches(regular, link)
    # A crafted record carrying BOTH proof keys gains nothing either way:
    # the type check runs before the proof key is picked from current's type.
    both = {"size": 3, "mtime_ns": 5, "sha256": "x", "target": "t"}
    assert not fingerprint_matches(link, {**both, "mode": stat_module.S_IFREG | 0o644})
    assert not fingerprint_matches(regular, {**both, "mode": stat_module.S_IFLNK | 0o777})


def test_legacy_record_with_different_permission_bits_matches(tmp_path):
    import stat as stat_module

    from core.playlists.managed_artifacts import fingerprint_matches

    regular = tmp_path / "song.flac"
    regular.write_bytes(b"original audio")
    current = _settled_fingerprint(str(regular))
    other_perm = 0o600 if (current["mode"] & 0o7777) != 0o600 else 0o644
    legacy = {**current, "device": 424242, "inode": 989898, "ctime_ns": 111,
              "mode": stat_module.S_IFREG | other_perm}
    assert fingerprint_matches(current, legacy)


def test_chmod_after_materialize_still_prunes(tmp_path):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    before_perm = copied.stat().st_mode & 0o7777
    os.chmod(copied, 0o600 if before_perm != 0o600 else 0o644)
    assert (copied.stat().st_mode & 0o7777) != before_perm
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not copied.exists()


def test_identical_replacement_is_treated_as_owned(tmp_path):
    # Pins the documented tradeoff: a byte-identical original moved or
    # copied onto our copy (mv, cp, cp -p, rsync -a) is indistinguishable
    # from our copy, so it is managed - and pruned - as one.
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original_stat = copied.stat()
    replacement = tmp_path / "replacement.flac"
    replacement.write_bytes(b"original audio")
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    os.replace(replacement, copied)
    assert copied.stat().st_ino != original_stat.st_ino
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not copied.exists()

    # A plain cp (identical bytes, fresh timestamps) counts as ours too:
    # mtime is not part of ownership.
    second = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(second.playlist_dir) / "song.flac"
    plain = tmp_path / "plain-copy.flac"
    shutil.copyfile(source, plain)
    os.replace(plain, copied)
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not copied.exists()


def test_remount_owned_copy_can_be_replaced_after_source_change(tmp_path, monkeypatch):
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    _remount_shift(monkeypatch)
    Path(source).write_bytes(b"new source bytes, longer than before")
    summary = rebuild_playlist_folder(root, "Mix", [source], "copy")
    assert summary.failed == 0
    assert summary.copied == 1
    assert (Path(first.playlist_dir) / "song.flac").read_bytes() == b"new source bytes, longer than before"


def test_remount_repairs_owned_link_after_source_move(tmp_path, monkeypatch):
    from core.playlists.materialize import update_materialized_paths

    source = _audio(tmp_path / "Music" / "old.flac")
    root = str(tmp_path / "Playlists")
    built = rebuild_playlist_folder(root, "Mix", [source])
    _remount_shift(monkeypatch)
    moved = tmp_path / "Music" / "renamed.flac"
    Path(source).rename(moved)
    assert update_materialized_paths(root, {source: str(moved)}) == 1
    assert (Path(built.playlist_dir) / "old.flac").resolve() == moved


@pytest.mark.parametrize("shift_ns", [-57, 1_000_000_000, 3_600_000_000_000])
def test_mtime_shift_keeps_ownership(tmp_path, shift_ns):
    # mtime is not part of ownership: a shift (a CIFS server's rounded time
    # replacing the client's cached exact one, a FAT tz= remount) must not
    # break the entry - it stays ours, rebuilds cleanly, and still prunes.
    from core.playlists.managed_artifacts import load_manifest

    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original = copied.stat()
    os.utime(copied, ns=(original.st_atime_ns, original.st_mtime_ns + shift_ns))
    summary = rebuild_playlist_folder(root, "Mix", [source], "copy")
    assert summary.failed == 0
    assert "song.flac" in load_manifest(root, first.playlist_dir)["entries"]
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not copied.exists()


def test_fingerprint_matches_ignores_mtime(tmp_path):
    from core.playlists.managed_artifacts import fingerprint_matches

    regular = tmp_path / "song.flac"
    regular.write_bytes(b"original audio")
    current = _settled_fingerprint(str(regular))
    assert fingerprint_matches(current, {**current, "mtime_ns": current["mtime_ns"] + 12345})
    missing = dict(current)
    del missing["mtime_ns"]
    assert fingerprint_matches(current, missing)
    assert fingerprint_matches(current, {**current, "mtime_ns": "not an int"})


def test_symlink_recreated_to_same_target_is_owned(tmp_path):
    # Pins the tradeoff: a symlink's whole content is its target, so a link
    # recreated to the same target (new mtime, new inode) counts as ours.
    from core.playlists.managed_artifacts import entry_is_owned, load_manifest

    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    built = rebuild_playlist_folder(root, "Mix", [source])
    link = Path(built.playlist_dir) / "song.flac"
    record = load_manifest(root, built.playlist_dir)["entries"]["song.flac"]
    target = os.readlink(link)
    link.unlink()
    link.symlink_to(target)
    assert entry_is_owned(str(link), record)
    pruned = rebuild_playlist_folder(root, "Mix", [])
    assert pruned.removed_stale == 1
    assert not os.path.lexists(link)


def test_hardlinked_replacement_is_treated_as_owned(tmp_path):
    # A hardlink of the library original at the destination is byte-identical
    # to our copy, so it counts as ours and prunes - only the extra name
    # goes, the library file itself is untouched.
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    hardlink = tmp_path / "hardlink.flac"
    os.link(source, hardlink)
    os.replace(hardlink, copied)
    assert copied.stat().st_ino == Path(source).stat().st_ino
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not copied.exists()
    assert Path(source).read_bytes() == b"original audio"


def test_remount_and_mtime_shift_together_keep_ownership(tmp_path, monkeypatch):
    from core.playlists.managed_artifacts import load_manifest

    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    original = copied.stat()
    os.utime(copied, ns=(original.st_atime_ns, original.st_mtime_ns + 3_600_000_000_000))
    _remount_shift(monkeypatch)
    summary = rebuild_playlist_folder(root, "Mix", [source], "copy")
    assert summary.failed == 0
    assert "song.flac" in load_manifest(root, first.playlist_dir)["entries"]
    pruned = rebuild_playlist_folder(root, "Mix", [], "copy")
    assert pruned.removed_stale == 1
    assert not copied.exists()


def test_symlink_size_is_not_part_of_ownership(tmp_path):
    # A link's size is just its target's length and some drivers report it
    # differently; only the target itself is compared.
    from core.playlists.managed_artifacts import artifact_fingerprint, fingerprint_matches

    target = tmp_path / "song.flac"
    target.write_bytes(b"original audio")
    link = tmp_path / "link.flac"
    link.symlink_to(target)
    current = artifact_fingerprint(str(link))
    assert fingerprint_matches(current, {**current, "size": current["size"] + 999})


def test_identical_replacement_is_replaced_when_source_changes(tmp_path):
    # The replace half of the pinned tradeoff: a byte-identical file placed
    # on our copy counts as ours, so a later source change replaces it
    # through the ownership gate like any managed copy.
    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    plain = tmp_path / "plain-copy.flac"
    shutil.copyfile(source, plain)
    os.replace(plain, copied)
    Path(source).write_bytes(b"new source bytes, longer than before")
    summary = rebuild_playlist_folder(root, "Mix", [source], "copy")
    assert summary.failed == 0
    assert summary.copied == 1
    assert copied.read_bytes() == b"new source bytes, longer than before"


def test_post_bug_dropped_record_is_not_recovered_but_file_is_preserved(tmp_path):
    # Pins the documented limitation: an install already hit by #2137 has
    # manifests whose records were dropped by the old comparator. This fix
    # does not re-adopt those entries (the manifest is marked
    # legacy_adopted and holds no record for the file) - the stale copy
    # stays unmanaged and is preserved, never overwritten or pruned.
    from core.playlists.managed_artifacts import save_manifest

    source = _audio(tmp_path / "Music" / "song.flac")
    root = str(tmp_path / "Playlists")
    first = rebuild_playlist_folder(root, "Mix", [source], "copy")
    copied = Path(first.playlist_dir) / "song.flac"
    save_manifest(root, first.playlist_dir,
                  {"version": 1, "folder": "Mix", "entries": {}, "legacy_adopted": True})
    Path(source).write_bytes(b"new source bytes, longer than before")
    summary = rebuild_playlist_folder(root, "Mix", [source], "copy")
    assert summary.failed == 1
    assert copied.read_bytes() == b"original audio"
