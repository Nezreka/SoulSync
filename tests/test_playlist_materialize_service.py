"""Playlist materialize SERVICE — builds the folder from a finished batch's own
payload (owned matched_file_path + downloaded final_file_path). Locks down: it
stitches owned + downloaded, ignores not-found/not-completed, de-dupes, gates on
the organize flag, and never depends on source IDs or a mirrored playlist."""

from __future__ import annotations

from pathlib import Path

from core.playlists.materialize_service import (
    collect_batch_real_paths,
    materialize_playlist_from_batch,
)


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
