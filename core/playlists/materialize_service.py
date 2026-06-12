"""Build a playlist's materialized folder from a FINISHED organize-by-playlist
download batch.

The batch already carries the real on-disk location of every resolved track:

  - **owned** tracks → ``analysis_results[*]['matched_file_path']`` (the library
    matcher's result, captured during analysis), and
  - **downloaded** tracks → ``download_tasks[tid]['final_file_path']`` (where the
    import landed).

So this is pure stitching + filesystem work: **no re-matching, no source-ID
lookup, no mirrored-playlist resolution**. It works for any organize-by-playlist
download, mirrored or not, and for the all-owned case (where nothing downloads).

Each stored path is run through ``resolve_library_file_path`` — the same resolver
playback uses — so a DB path that's host-formatted (Docker) maps to the real file
the container can see; a freshly-downloaded path already exists and passes
through unchanged.
"""

from __future__ import annotations

from typing import Any, List, Optional

from core.imports.paths import docker_resolve_path
from core.playlists.materialize import (
    RebuildSummary,
    normalize_mode,
    rebuild_playlist_folder,
)


def collect_batch_real_paths(batch: dict, download_tasks: dict, *, config_manager) -> List[str]:
    """Real on-disk paths of every track in a finished batch that resolved —
    owned (from analysis) + downloaded (from completed tasks) — resolved to this
    process's filesystem and de-duplicated, in playlist order then download order."""
    from core.library.path_resolver import resolve_library_file_path

    out: List[str] = []
    seen = set()

    def _add(stored_path: Any) -> None:
        if not stored_path:
            return
        real = resolve_library_file_path(str(stored_path), config_manager=config_manager)
        if real and real not in seen:
            seen.add(real)
            out.append(real)

    for res in (batch.get("analysis_results") or []):
        if res.get("found"):
            _add(res.get("matched_file_path"))

    for tid in (batch.get("queue") or []):
        task = (download_tasks or {}).get(tid) or {}
        if task.get("status") == "completed":
            _add(task.get("final_file_path"))

    return out


def materialize_playlist_from_batch(batch: dict, download_tasks: dict, config_manager) -> Optional[RebuildSummary]:
    """(Re)build the playlist folder for a finished organize-by-playlist batch.

    Returns the :class:`RebuildSummary`, or ``None`` when the batch isn't an
    organize-by-playlist batch. Reads ``playlists.materialize_path`` /
    ``playlists.materialize_mode`` from config."""
    if not batch or not batch.get("playlist_folder_mode"):
        return None
    name = batch.get("playlist_name") or "Unknown Playlist"
    real_paths = collect_batch_real_paths(batch, download_tasks, config_manager=config_manager)
    root = docker_resolve_path(config_manager.get("playlists.materialize_path", "./Playlists"))
    mode = normalize_mode(config_manager.get("playlists.materialize_mode", "symlink"))
    return rebuild_playlist_folder(root, name, real_paths, mode)


__all__ = ["collect_batch_real_paths", "materialize_playlist_from_batch"]
