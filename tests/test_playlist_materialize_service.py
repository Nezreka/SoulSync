"""Playlist materialize SERVICE — builds the folder from a finished batch's own
payload (owned matched_file_path + downloaded final_file_path). Locks down: it
stitches owned + downloaded, ignores not-found/not-completed, de-dupes, gates on
the organize flag, and never depends on source IDs or a mirrored playlist."""

from __future__ import annotations

from pathlib import Path

from core.playlists.materialize_service import (
    collect_batch_real_paths,
    materialize_playlist_from_batch,
    rebuild_organized_playlists_from_db,
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
            {"id": 1, "name": "Mix", "organize_by_playlist": True},
            {"id": 2, "name": "Off", "organize_by_playlist": False},
        ]

    def get_mirrored_playlist_tracks(self, pid):
        if pid == 1:
            return [{"track_name": "A", "artist_name": "x"},
                    {"track_name": "Gone", "artist_name": "y"}]   # not owned
        return [{"track_name": "B", "artist_name": "z"}]

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
