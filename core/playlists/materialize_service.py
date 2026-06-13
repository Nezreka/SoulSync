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


def reconcile_batch_playlists(db, batch: dict, download_tasks: dict, config_manager, *, profile_id: int = 1):
    """One post-batch step: rebuild every organize-by-playlist playlist this batch
    TOUCHED, from CURRENT library ownership.

    Touched playlists = the batch's own organize playlist + any playlist a completed
    track belongs to (via the per-track ``source_info`` provenance — covers a
    wishlist batch fulfilling a track that belongs to an organize playlist). Each is
    rebuilt via ``_rebuild_one_from_db`` (``check_track_exists`` over its membership),
    so it's robust to HOW a track imported — modal worker, slskd monitor, or the
    verification worker, which don't all set the same task fields. It simply asks the
    library what's owned, and prunes tracks that have left the playlist.

    Returns ``[(playlist_name, RebuildSummary)]``. Callers wrap non-fatally."""
    import json as _json

    # Collect the (ref, source) of every organize playlist this batch touched.
    wanted, seen_ref = [], set()

    def _want(ref, source):
        ref = str(ref or "").strip()
        if not ref:
            return
        key = (ref, source or "spotify")
        if key not in seen_ref:
            seen_ref.add(key)
            wanted.append(key)

    if batch.get("playlist_folder_mode"):
        _want(batch.get("source_playlist_ref") or batch.get("playlist_id"),
              batch.get("batch_source") or "spotify")

    for tid in (batch.get("queue") or []):
        task = (download_tasks or {}).get(tid) or {}
        if task.get("status") != "completed":
            continue
        si = (task.get("track_info") or {}).get("source_info") or {}
        if isinstance(si, str):
            try:
                si = _json.loads(si)
            except Exception:
                si = {}
        if isinstance(si, dict) and si.get("playlist_id"):
            _want(si["playlist_id"], si.get("source") or "spotify")

    results, seen_id = [], set()
    for ref, source in wanted:
        try:
            pl = db.resolve_mirrored_playlist(ref, profile_id, default_source=source)
        except Exception:
            pl = None
        if not pl or not pl.get("organize_by_playlist") or pl.get("id") in seen_id:
            continue
        seen_id.add(pl.get("id"))
        try:
            results.append(_rebuild_one_from_db(db, config_manager, pl))
        except Exception:
            continue
    return results


def _rebuild_one_from_db(db, config_manager, playlist: dict):
    """Rebuild ONE playlist's folder from its CURRENT membership × ownership —
    re-matching each member via the app's own ``check_track_exists`` (by name, not
    source IDs), resolving to disk, and rebuilding WITH prune. Because it's driven
    by current membership, a track that has LEFT the playlist drops out of the set
    and its symlink is pruned. Returns ``(playlist_name, RebuildSummary)``."""
    from core.library.path_resolver import resolve_library_file_path

    root = docker_resolve_path(config_manager.get("playlists.materialize_path", "./Playlists"))
    mode = normalize_mode(config_manager.get("playlists.materialize_mode", "symlink"))
    real_paths: List[str] = []
    seen = set()
    for t in (db.get_mirrored_playlist_tracks(playlist["id"]) or []):
        title = (t.get("track_name") or "").strip()
        artist = (t.get("artist_name") or "").strip()
        if not title:
            continue
        try:
            db_track, conf = db.check_track_exists(title, artist, confidence_threshold=0.7)
        except Exception:
            continue
        if db_track is None or conf < 0.7:
            continue
        real = resolve_library_file_path(getattr(db_track, "file_path", None), config_manager=config_manager)
        if real and real not in seen:
            seen.add(real)
            real_paths.append(real)
    name = playlist.get("name") or "Unnamed Playlist"
    return name, rebuild_playlist_folder(root, name, real_paths, mode)   # prune_stale=True


def rebuild_organized_playlists_from_db(db, config_manager, *, profile_id: int = 1):
    """Rebuild EVERY "organize by playlist" folder from current library ownership —
    for the manual "Rebuild" button. Self-heals after a library reorganize moves
    files or membership changes. Returns a list of ``(playlist_name, summary)``."""
    return [
        _rebuild_one_from_db(db, config_manager, pl)
        for pl in (db.get_mirrored_playlists(profile_id) or [])
        if pl.get("organize_by_playlist")
    ]


def rebuild_mirrored_playlist_if_organized(db, config_manager, playlist_id, *, profile_id: int = 1):
    """Mirror-update hook: after a playlist's membership is re-synced, rebuild its
    folder (with prune) IF it's organize-by-playlist — so a track that just LEFT
    the playlist has its symlink cleaned up the instant membership changes (the
    mirror image of the post-download reconcile that handles additions). Returns
    the summary, or ``None`` when the playlist isn't organized / can't be found."""
    if playlist_id is None:
        return None
    pl = db.get_mirrored_playlist(playlist_id)
    if not pl or not pl.get("organize_by_playlist"):
        return None
    _name, summary = _rebuild_one_from_db(db, config_manager, pl)
    return summary


__all__ = [
    "collect_batch_real_paths",
    "materialize_playlist_from_batch",
    "reconcile_batch_playlists",
    "rebuild_organized_playlists_from_db",
    "rebuild_mirrored_playlist_if_organized",
]
