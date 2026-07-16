"""Bridges lib2 albums/artists onto the existing (legacy-schema) reorganize
pipeline for docs §50 (Interactive Reorganize).

``core.library_reorganize``/``core.reorganize_queue`` are hard-coded against
the legacy ``albums``/``artists``/``tracks`` tables (see
``load_album_and_tracks``) — reimplementing that whole pipeline (staging,
copy, post-process/re-tag, quality-gate, sidecar handling) against lib2 tables
would be a second, parallel implementation to keep in sync, exactly what the
project's reuse-first philosophy (docs §4.5) argues against. Every lib2
album/artist that came from the legacy import keeps a ``legacy_album_id`` /
``legacy_artist_id`` back-reference (NULL only for rows added purely via
"Update Discography", which never had a legacy row to begin with) — so this
module resolves that back-reference and delegates to the exact same
planner/queue the legacy Enhanced View's reorganize modal uses. This mirrors
the same pattern already used for lib2 Enrich (docs §44,
``api/library_v2.py``'s ``lib2_enrich``).

Physically moving a file and rewriting its stored path is safe to bridge this
way ONLY because ``core/reorganize_runner.py::_update_track_path`` now also
syncs ``lib2_track_files.path`` via ``legacy_track_id`` after every move (see
the reorganize-runner fix landed alongside this module) — without that fix,
bridging here would silently desync the lib2 side on every reorganize.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.reorganize_bridge")


class ReorganizeBridgeError(ValueError):
    """User-facing reorganize-bridge failure with an HTTP-ish status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


NO_LEGACY_ALBUM_MSG = (
    "This album has no legacy library record to reorganize "
    "(it was added via Update Discography)."
)
NO_LEGACY_ARTIST_MSG = (
    "This artist has no legacy library record to reorganize "
    "(it was added via Update Discography)."
)


def resolve_legacy_album_id(conn: Any, lib2_album_id: int) -> int:
    row = conn.execute(
        "SELECT legacy_album_id FROM lib2_albums WHERE id=?", (int(lib2_album_id),)
    ).fetchone()
    if row is None:
        raise ReorganizeBridgeError(f"Album {lib2_album_id} not found", status=404)
    legacy_id = row["legacy_album_id"]
    if legacy_id is None:
        raise ReorganizeBridgeError(NO_LEGACY_ALBUM_MSG, status=409)
    return int(legacy_id)


def resolve_legacy_artist_id(conn: Any, lib2_artist_id: int) -> int:
    row = conn.execute(
        "SELECT legacy_artist_id FROM lib2_artists WHERE id=?", (int(lib2_artist_id),)
    ).fetchone()
    if row is None:
        raise ReorganizeBridgeError(f"Artist {lib2_artist_id} not found", status=404)
    legacy_id = row["legacy_artist_id"]
    if legacy_id is None:
        raise ReorganizeBridgeError(NO_LEGACY_ARTIST_MSG, status=409)
    return int(legacy_id)


def _transfer_dir(config_manager: Any) -> str:
    from core.imports.paths import docker_resolve_path
    return docker_resolve_path(
        config_manager.get("soulseek.transfer_path", "./Transfer") if config_manager else "./Transfer"
    )


def _resolve_file_path_fn(config_manager: Any):
    from core.library.path_resolver import resolve_library_file_path
    transfer_dir = _transfer_dir(config_manager)
    download_dir = (
        config_manager.get("soulseek.download_path", "./downloads") if config_manager else "./downloads"
    )

    def _resolve(file_path):
        return resolve_library_file_path(
            file_path,
            transfer_folder=transfer_dir,
            download_folder=download_dir,
            config_manager=config_manager,
        )

    return _resolve


def album_reorganize_sources(db: Any, lib2_album_id: int) -> List[Dict[str, str]]:
    """Sources this album's stored provider IDs support, for the per-album
    source picker (mirrors legacy ``GET .../album/<id>/reorganize/sources``)."""
    from core.library_reorganize import available_sources_for_album, load_album_and_tracks

    conn = db._get_connection()
    try:
        legacy_album_id = resolve_legacy_album_id(conn, lib2_album_id)
    finally:
        conn.close()
    album_data, _tracks = load_album_and_tracks(db, legacy_album_id)
    if album_data is None:
        raise ReorganizeBridgeError("Album not found", status=404)
    return available_sources_for_album(album_data)


def global_reorganize_sources() -> List[Dict[str, str]]:
    """Sources authed/configured on this instance, for the artist-level
    "Reorganize All" picker (no per-album ID coverage check)."""
    from core.library_reorganize import authed_sources
    return authed_sources()


def preview_album_reorganize(
    db: Any, config_manager: Any, lib2_album_id: int,
    *, source: Optional[str] = None, mode: str = "api",
) -> Dict[str, Any]:
    """Preview the reorganize plan for one lib2 album (docs §50)."""
    from core.imports.paths import build_final_path_for_track
    from core.library_reorganize import preview_album_reorganize as _preview

    conn = db._get_connection()
    try:
        legacy_album_id = resolve_legacy_album_id(conn, lib2_album_id)
    finally:
        conn.close()

    metadata_source = mode if mode in ("api", "tags") else "api"
    result = _preview(
        album_id=str(legacy_album_id),
        db=db,
        transfer_dir=_transfer_dir(config_manager),
        resolve_file_path_fn=_resolve_file_path_fn(config_manager),
        build_final_path_fn=build_final_path_for_track,
        primary_source=source or None,
        strict_source=bool(source),
        metadata_source=metadata_source,
    )
    if result.get("status") == "no_album":
        raise ReorganizeBridgeError("Album not found", status=404)
    if result.get("status") == "no_tracks":
        raise ReorganizeBridgeError("No tracks found for this album", status=404)
    return result


def enqueue_album_reorganize(
    db: Any, lib2_album_id: int,
    *, source: Optional[str] = None, mode: str = "api", rename_only: bool = False,
) -> Dict[str, Any]:
    """Enqueue one lib2 album for reorganize (docs §50)."""
    from core.reorganize_queue import get_queue

    conn = db._get_connection()
    try:
        legacy_album_id = resolve_legacy_album_id(conn, lib2_album_id)
    finally:
        conn.close()

    metadata_source = mode if mode in ("api", "tags") else "api"
    meta = db.get_album_display_meta(legacy_album_id)
    if meta is None:
        raise ReorganizeBridgeError("Album not found", status=404)

    return get_queue().enqueue(
        album_id=str(legacy_album_id),
        album_title=meta["album_title"],
        artist_id=meta["artist_id"],
        artist_name=meta["artist_name"],
        source=source or None,
        metadata_source=metadata_source,
        rename_only=bool(rename_only),
    )


def enqueue_artist_reorganize_all(
    db: Any, lib2_artist_id: int,
    *, source: Optional[str] = None, mode: str = "api",
) -> Dict[str, Any]:
    """Enqueue every album of one lib2 artist for reorganize (docs §50)."""
    from core.reorganize_queue import get_queue

    conn = db._get_connection()
    try:
        legacy_artist_id = resolve_legacy_artist_id(conn, lib2_artist_id)
    finally:
        conn.close()

    metadata_source = mode if mode in ("api", "tags") else "api"
    albums = db.get_artist_albums_for_reorganize(legacy_artist_id)
    if not albums:
        raise ReorganizeBridgeError("No albums found for this artist", status=404)

    for album in albums:
        album["source"] = source or None
        album["metadata_source"] = metadata_source
    result = get_queue().enqueue_many(albums)
    return {
        "enqueued": result["enqueued"],
        "already_queued": result["already_queued"],
        "total_albums": result["total"],
    }


__all__ = [
    "ReorganizeBridgeError",
    "resolve_legacy_album_id",
    "resolve_legacy_artist_id",
    "album_reorganize_sources",
    "global_reorganize_sources",
    "preview_album_reorganize",
    "enqueue_album_reorganize",
    "enqueue_artist_reorganize_all",
]
