"""Playlist materialize SERVICE — builds the folder from a finished batch's own
payload (owned matched_file_path + downloaded final_file_path). Locks down: it
stitches owned + downloaded, ignores not-found/not-completed, de-dupes, gates on
the organize flag, and never depends on source IDs or a mirrored playlist."""

from __future__ import annotations

from pathlib import Path

from core.playlists.materialize_service import (
    collect_batch_real_paths,
    materialize_playlist_from_batch,
    rebuild_mirrored_playlist_if_organized,
    rebuild_organized_playlists_from_db,
    reconcile_batch_playlists,
)


class _Track:
    """Mimics database.DatabaseTrack — what check_track_exists returns."""
    def __init__(self, file_path):
        self.file_path = file_path


class _RebuildDB:
    """One organized playlist (Mix) + one not (Off); check_track_exists matches
    by NAME via `owned` (track_name -> file_path), so no source IDs involved."""
    def __init__(self, owned):
        self.owned = owned

    def get_mirrored_playlists(self, profile_id=1):
        return [
            {"id": 1, "name": "Mix", "source_playlist_id": "PL1", "organize_by_playlist": True},
            {"id": 2, "name": "Off", "source_playlist_id": "PL2", "organize_by_playlist": False},
        ]

    def get_mirrored_playlist_tracks(self, pid):
        if pid == 1:
            return [{"track_name": "A", "artist_name": "x"},
                    {"track_name": "Gone", "artist_name": "y"}]   # not owned
        return [{"track_name": "B", "artist_name": "z"}]

    def get_mirrored_playlist(self, playlist_id):
        for pl in self.get_mirrored_playlists():
            if pl["id"] == playlist_id:
                return pl
        return None

    def resolve_mirrored_playlist(self, ref, profile_id=1, *, default_source="spotify"):
        for pl in self.get_mirrored_playlists():
            if str(ref) in (pl["source_playlist_id"], str(pl["id"]), pl["name"]):
                return pl
        return None

    def check_track_exists(self, title, artist, confidence_threshold=0.8,
                           server_source=None, album=None, candidate_tracks=None):
        fp = self.owned.get(title)
        return (_Track(fp), 1.0) if fp else (None, 0.0)


class _Cfg:
    def __init__(self, root, mode="symlink"):
        self._d = {"playlists.materialize_path": root, "playlists.materialize_mode": mode}

    def get(self, key, default=None):
        return self._d.get(key, default)


def _mk(tmp_path: Path, *names) -> list[str]:
    paths = []
    for n in names:
        f = tmp_path / "Music" / n
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"audio")
        paths.append(str(f))
    return paths


def test_collect_stitches_owned_and_downloaded(tmp_path: Path):
    owned, downloaded = _mk(tmp_path, "Owned.mp3"), _mk(tmp_path, "Fresh.mp3")
    batch = {
        "analysis_results": [
            {"found": True, "matched_file_path": owned[0]},
            {"found": False, "matched_file_path": None},      # not owned → skip
        ],
        "queue": ["t1", "t2"],
    }
    tasks = {
        "t1": {"status": "completed", "final_file_path": downloaded[0]},
        "t2": {"status": "failed", "final_file_path": None},  # not completed → skip
    }
    cfg = _Cfg(str(tmp_path / "Playlists"))
    paths = collect_batch_real_paths(batch, tasks, config_manager=cfg)
    assert paths == [owned[0], downloaded[0]]                 # owned first, then downloaded


def test_collect_dedupes(tmp_path: Path):
    owned = _mk(tmp_path, "Same.mp3")
    batch = {
        "analysis_results": [{"found": True, "matched_file_path": owned[0]}],
        "queue": ["t1"],
    }
    tasks = {"t1": {"status": "completed", "final_file_path": owned[0]}}  # same file
    cfg = _Cfg(str(tmp_path / "Playlists"))
    assert collect_batch_real_paths(batch, tasks, config_manager=cfg) == [owned[0]]


def test_materialize_from_batch_all_owned(tmp_path: Path):
    """The all-owned case (no downloads) — folder built entirely from analysis."""
    owned = _mk(tmp_path, "A.mp3", "B.mp3")
    batch = {
        "playlist_folder_mode": True,
        "playlist_name": "Smack That",
        "analysis_results": [
            {"found": True, "matched_file_path": owned[0]},
            {"found": True, "matched_file_path": owned[1]},
        ],
        "queue": [],
    }
    cfg = _Cfg(str(tmp_path / "Playlists"))
    summary = materialize_playlist_from_batch(batch, {}, cfg)
    assert summary is not None and summary.linked == 2
    pdir = Path(summary.playlist_dir)
    assert pdir == tmp_path / "Playlists" / "Smack That"
    assert (pdir / "A.mp3").resolve() == Path(owned[0]).resolve()
    assert (pdir / "B.mp3").resolve() == Path(owned[1]).resolve()


def test_materialize_from_batch_owned_plus_downloaded(tmp_path: Path):
    owned, downloaded = _mk(tmp_path, "Owned.mp3"), _mk(tmp_path, "Fresh.mp3")
    batch = {
        "playlist_folder_mode": True,
        "playlist_name": "Mix",
        "analysis_results": [{"found": True, "matched_file_path": owned[0]}],
        "queue": ["t1"],
    }
    tasks = {"t1": {"status": "completed", "final_file_path": downloaded[0]}}
    cfg = _Cfg(str(tmp_path / "Playlists"), mode="copy")
    summary = materialize_playlist_from_batch(batch, tasks, cfg)
    assert summary.copied == 2
    pdir = Path(summary.playlist_dir)
    assert (pdir / "Owned.mp3").is_file() and (pdir / "Fresh.mp3").is_file()


def test_materialize_skipped_when_not_organize(tmp_path: Path):
    batch = {"playlist_folder_mode": False, "playlist_name": "X", "analysis_results": [], "queue": []}
    assert materialize_playlist_from_batch(batch, {}, _Cfg(str(tmp_path / "Playlists"))) is None
    assert not (tmp_path / "Playlists").exists()


def test_reconcile_organize_batch_rebuilds_from_library(tmp_path: Path):
    """An organize batch → its playlist is rebuilt from the LIBRARY (membership ×
    check_track_exists), not from fragile per-task fields. Owned members link,
    non-owned drop out."""
    a = _mk(tmp_path, "A.mp3")[0]                 # Mix membership = A, Gone; only A owned
    db = _RebuildDB({"A": a})
    batch = {"playlist_folder_mode": True, "playlist_name": "Mix",
             "source_playlist_ref": "PL1", "batch_source": "spotify", "queue": []}
    cfg = _Cfg(str(tmp_path / "Playlists"))
    results = reconcile_batch_playlists(db, batch, {}, cfg)
    assert len(results) == 1
    name, s = results[0]
    assert name == "Mix" and s.linked == 1        # A owned; Gone not in library → skipped
    assert (tmp_path / "Playlists" / "Mix" / "A.mp3").exists()


def test_reconcile_wishlist_track_rebuilds_its_playlist(tmp_path: Path):
    """The wishlist gap: a wishlist batch (not organize) completes a track whose
    provenance points to an organize playlist → that playlist gets rebuilt from the
    library, regardless of which import path set which task field."""
    a = _mk(tmp_path, "A.mp3")[0]
    db = _RebuildDB({"A": a})
    batch = {"playlist_folder_mode": False, "playlist_name": "wishlist", "queue": ["w1"]}
    tasks = {"w1": {"status": "completed",
                    "track_info": {"source_info": {"playlist_id": "PL1", "source": "spotify"}}}}
    cfg = _Cfg(str(tmp_path / "Playlists"))
    results = reconcile_batch_playlists(db, batch, tasks, cfg)
    assert len(results) == 1 and results[0][0] == "Mix"
    assert (tmp_path / "Playlists" / "Mix" / "A.mp3").exists()   # Mix rebuilt from the library


def test_reconcile_skips_non_organized_provenance(tmp_path: Path):
    """A completed track pointing to a NON-organize playlist (Off / PL2) is ignored."""
    db = _RebuildDB({"B": _mk(tmp_path, "B.mp3")[0]})
    batch = {"playlist_folder_mode": False, "playlist_name": "wishlist", "queue": ["w1"]}
    tasks = {"w1": {"status": "completed",
                    "track_info": {"source_info": {"playlist_id": "PL2", "source": "spotify"}}}}
    assert reconcile_batch_playlists(db, batch, tasks, _Cfg(str(tmp_path / "Playlists"))) == []
    assert not (tmp_path / "Playlists" / "Off").exists()


def test_reconcile_noop_for_plain_batch(tmp_path: Path):
    """A normal (non-organize, no provenance) batch → nothing happens."""
    db = _RebuildDB({})
    batch = {"playlist_folder_mode": False, "playlist_name": "album", "queue": ["a1"]}
    tasks = {"a1": {"status": "completed", "track_info": {}}}     # no source_info
    assert reconcile_batch_playlists(db, batch, tasks, _Cfg(str(tmp_path / "Playlists"))) == []
    assert not (tmp_path / "Playlists").exists()


def test_mirror_cleanup_prunes_removed_track(tmp_path: Path):
    """Mirror-update hook: after a track LEAVES the playlist, its symlink is pruned."""
    a = tmp_path / "Music" / "A.mp3"
    gone = tmp_path / "Music" / "Gone.mp3"
    for f in (a, gone):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"audio")

    class _DB:
        def get_mirrored_playlist(self, pid):
            return {"id": 1, "name": "Mix", "organize_by_playlist": True} if pid == 1 else None

        def get_mirrored_playlist_tracks(self, pid):
            return [{"track_name": "A", "artist_name": "x"}]   # 'Gone' was removed upstream

        def check_track_exists(self, title, artist, confidence_threshold=0.8,
                               server_source=None, album=None, candidate_tracks=None):
            fp = {"A": str(a), "Gone": str(gone)}.get(title)
            return (_Track(fp), 1.0) if fp else (None, 0.0)

    from core.playlists.materialize import rebuild_playlist_folder
    rebuild_playlist_folder(str(tmp_path / "Playlists"), "Mix", [str(a), str(gone)], "symlink")
    assert (tmp_path / "Playlists" / "Mix" / "Gone.mp3").exists()   # both present before

    summary = rebuild_mirrored_playlist_if_organized(_DB(), _Cfg(str(tmp_path / "Playlists")), 1, profile_id=1)
    assert summary is not None
    pdir = tmp_path / "Playlists" / "Mix"
    assert (pdir / "A.mp3").exists()
    assert not (pdir / "Gone.mp3").exists()                        # pruned on mirror update


def test_mirror_cleanup_skips_non_organized(tmp_path: Path):
    db = _RebuildDB({})
    cfg = _Cfg(str(tmp_path / "Playlists"))
    assert rebuild_mirrored_playlist_if_organized(db, cfg, 2, profile_id=1) is None     # Off (organize=0)
    assert rebuild_mirrored_playlist_if_organized(db, cfg, 999, profile_id=1) is None   # unknown
    assert rebuild_mirrored_playlist_if_organized(db, cfg, None, profile_id=1) is None


def test_rebuild_from_db_only_organized_and_owned(tmp_path: Path):
    """The manual button: rebuild every organized playlist by re-matching with
    check_track_exists (name), linking only owned tracks."""
    f = tmp_path / "Music" / "A.mp3"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"audio")
    db = _RebuildDB({"A": str(f)})                  # 'Gone' + the 'Off' playlist's 'B' not owned
    cfg = _Cfg(str(tmp_path / "Playlists"))
    results = rebuild_organized_playlists_from_db(db, cfg, profile_id=1)
    assert len(results) == 1                         # only Mix (organize on)
    name, s = results[0]
    assert name == "Mix" and s.linked == 1           # only A owned; Gone skipped
    assert (tmp_path / "Playlists" / "Mix" / "A.mp3").exists()
    assert not (tmp_path / "Playlists" / "Off").exists()
