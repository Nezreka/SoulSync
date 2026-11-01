"""Expose the native Library-v2 catalogue to the reorganize pipeline."""

from __future__ import annotations

from contextlib import closing
from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("library2.reorganize_bridge")


class ReorganizeBridgeError(ValueError):
    """User-facing reorganize-bridge failure with an HTTP-ish status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def resolve_legacy_album_id(conn: Any, lib2_album_id: int) -> Any:
    """Compatibility name: validate and return the native catalogue ID."""
    row = conn.execute(
        "SELECT id FROM lib2_albums WHERE id=?", (int(lib2_album_id),)
    ).fetchone()
    if row is None:
        raise ReorganizeBridgeError(f"Album {lib2_album_id} not found", status=404)
    return int(row["id"])


def resolve_legacy_artist_id(conn: Any, lib2_artist_id: int) -> Any:
    """Compatibility name: validate and return the native catalogue ID."""
    row = conn.execute(
        "SELECT id FROM lib2_artists WHERE id=?", (int(lib2_artist_id),)
    ).fetchone()
    if row is None:
        raise ReorganizeBridgeError(f"Artist {lib2_artist_id} not found", status=404)
    return int(row["id"])


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


def catalogue_preview_fn(
    *, album_id: Any, db: Any, transfer_dir: str,
    resolve_file_path_fn: Any, build_final_path_fn: Any,
) -> Dict[str, Any]:
    """``preview_fn`` adapter over the catalogue planner.

    Carries the signature the rename executor calls with. A path needs no
    metadata source, and the runner already plans per library root, with
    ``transfer_dir`` being it. Keeping the shape means the executor keeps
    acting on exactly what the preview showed, which is the property that made
    the preview trustworthy in the first place (#875).
    """
    from core.library2.reorganize_plan import plan_album_reorganize

    with closing(db._get_connection()) as conn:
        return plan_album_reorganize(
            conn, album_id,
            build_final_path_fn=build_final_path_fn,
            transfer_dir=transfer_dir,
            resolve_file_path_fn=resolve_file_path_fn,
        )


def preview_album_reorganize(db: Any, config_manager: Any, lib2_album_id: int) -> Dict[str, Any]:
    """Preview the reorganize plan for one lib2 album (docs §50)."""
    from core.imports.paths import build_final_path_for_track

    result = catalogue_preview_fn(
        album_id=lib2_album_id,
        db=db,
        transfer_dir=_transfer_dir(config_manager),
        resolve_file_path_fn=_resolve_file_path_fn(config_manager),
        build_final_path_fn=build_final_path_for_track,
    )
    if result.get("status") == "no_album":
        raise ReorganizeBridgeError("Album not found", status=404)
    if result.get("status") == "no_tracks":
        raise ReorganizeBridgeError("No tracks found for this album", status=404)
    return result


def enqueue_album_reorganize(db: Any, lib2_album_id: int) -> Dict[str, Any]:
    """Enqueue one lib2 album for reorganize (docs §50)."""
    from core.reorganize_queue import get_queue

    meta = db.get_album_display_meta(lib2_album_id)
    if meta is None:
        raise ReorganizeBridgeError("Album not found", status=404)

    return get_queue().enqueue(
        album_id=str(lib2_album_id),
        album_title=meta["album_title"],
        artist_id=meta["artist_id"],
        artist_name=meta["artist_name"],
    )


def enqueue_artist_reorganize_all(db: Any, lib2_artist_id: int) -> Dict[str, Any]:
    """Enqueue every album of one lib2 artist for reorganize (docs §50)."""
    from core.reorganize_queue import get_queue

    with closing(db._get_connection()) as conn:
        artist = conn.execute("SELECT id FROM lib2_artists WHERE id=?", (int(lib2_artist_id),)).fetchone()
        if artist is None:
            raise ReorganizeBridgeError("Artist not found", status=404)
        from core.library2.artist_aliases import resolve_alias_group

        group = resolve_alias_group(conn, lib2_artist_id)

    albums_by_id = {}
    for artist_id in group:
        for album in db.get_artist_albums_for_reorganize(artist_id):
            albums_by_id[str(album["album_id"])] = album
    albums = list(albums_by_id.values())
    if not albums:
        raise ReorganizeBridgeError("No albums found for this artist", status=404)

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
    "catalogue_preview_fn",
    "preview_album_reorganize",
    "enqueue_album_reorganize",
    "enqueue_artist_reorganize_all",
]
