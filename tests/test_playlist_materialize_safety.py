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
    assert not (tmp_path / "Playlists" / "Favorites").exists()


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

