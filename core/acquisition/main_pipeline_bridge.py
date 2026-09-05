"""Dispatch edition-matched bundle files through SoulSync's main pipeline.

This is deliberately an adapter, not an importer.  It supplies persistent
Acquisition/Library context to the existing post-processing implementation,
which remains responsible for every quality, integrity, AcoustID, quarantine,
tagging, path and retry decision.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from core.acquisition.candidates import redact_sensitive_text
from core.runtime_state import download_tasks, tasks_lock
from utils.logging_config import get_logger


logger = get_logger("acquisition.main_pipeline_bridge")
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class BridgeDispatchResult:
    import_id: str
    dispatched: Tuple[str, ...] = ()
    waiting: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()


def _safe_source_path(root: str, relative_path: str) -> Path:
    base = Path(str(root or "")).resolve()
    relative = str(relative_path or "").replace("\\", "/").lstrip("/")
    source = (base / Path(*relative.split("/"))).resolve()
    try:
        source.relative_to(base)
    except ValueError as exc:
        raise ValueError("matched bundle file escapes the resolved download root") from exc
    if not source.is_file():
        raise ValueError(f"matched bundle file is not readable: {relative}")
    return source


def _pipeline_context(
    conn: Any,
    record: Any,
    match: Mapping[str, Any],
    *,
    source: str,
) -> Dict[str, Any]:
    track_id = int(match.get("track_id") or 0)
    if track_id <= 0:
        raise ValueError("matched bundle entry has no Library-v2 track id")
    row = conn.execute(
        """SELECT t.id AS track_id, t.title, t.track_number, t.disc_number,
                  t.duration, t.spotify_id AS track_spotify_id,
                  al.id AS album_id, al.title AS album_title,
                  al.album_type, al.release_date,
                  al.spotify_id AS album_spotify_id,
                  ar.id AS artist_id, ar.name AS artist_name,
                  req.quality_profile_id, req.trigger
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id=t.album_id
             JOIN lib2_artists ar ON ar.id=al.primary_artist_id
             JOIN acquisition_requests req ON req.id=?
            WHERE t.id=?""",
        (record.request_id, track_id),
    ).fetchone()
    if row is None:
        raise ValueError("matched Library-v2 track or acquisition request disappeared")
    data = dict(row)
    relative = str(match.get("relative_path") or "").replace("\\", "/")
    lib2_entity = {
        "track_id": track_id,
        "album_id": int(data["album_id"]),
        "quality_profile_id": int(data["quality_profile_id"]),
    }
    track_info = {
        "id": data.get("track_spotify_id") or f"lib2-track:{track_id}",
        "provider": "spotify" if data.get("track_spotify_id") else "library_v2",
        "name": data["title"],
        "title": data["title"],
        "artists": [{"name": data["artist_name"]}],
        "album": {
            "id": data.get("album_spotify_id") or f"lib2-album:{data['album_id']}",
            "name": data["album_title"],
            "album_type": data.get("album_type") or "album",
            "release_date": data.get("release_date") or "",
        },
        "track_number": match.get("track_number") or data.get("track_number"),
        "disc_number": match.get("disc_number") or data.get("disc_number") or 1,
        "duration_ms": data.get("duration") or 0,
        "quality_profile_id": data["quality_profile_id"],
        "lib2_entity": lib2_entity,
        "_acquisition_import_id": record.id,
        "_acquisition_relative_path": relative,
        "_acquisition_track_id": track_id,
    }
    artist_context = {
        "id": f"lib2-artist:{data['artist_id']}",
        "name": data["artist_name"],
        "genres": [],
    }
    album_context = {
        **track_info["album"],
        "artists": [{"name": data["artist_name"]}],
    }
    context = {
        "track_info": track_info,
        "lib2_entity": lib2_entity,
        "spotify_artist": artist_context,
        "spotify_album": album_context,
        "original_search_result": {
            "username": source,
            "filename": relative,
            "title": data["title"],
            "artist": data["artist_name"],
            "spotify_clean_title": data["title"],
            "spotify_clean_album": data["album_title"],
            "spotify_clean_artist": data["artist_name"],
            "track_number": track_info["track_number"],
            "disc_number": track_info["disc_number"],
        },
        "username": source,
        "is_album_download": True,
        "has_clean_spotify_data": True,
        "staging_source": True,
        "_acquisition_import_id": record.id,
        "_acquisition_relative_path": relative,
        "_acquisition_track_id": track_id,
        "_acquisition_manual_pick": data.get("trigger") == "manual",
    }
    from core.imports.upgrade_intent import attach_upgrade_intent, issue_upgrade_intent
    attach_upgrade_intent(
        context,
        issue_upgrade_intent(track_id, origin=f"acquisition:{data.get('trigger') or 'unknown'}"),
    )
    return context


PROVENANCE_SUFFIX = ".soulsync-acquisition.json"


def _provenance_path(destination: Path) -> Path:
    return destination.with_name(destination.name + PROVENANCE_SUFFIX)


def _read_provenance(destination: Path) -> Dict[str, Any]:
    marker = _provenance_path(destination)
    try:
        with marker.open("r", encoding="utf-8") as stream:
            import json

            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_provenance(destination: Path, *, import_id: str, track_id: int) -> None:
    import json

    marker = _provenance_path(destination)
    try:
        with marker.open("w", encoding="utf-8") as stream:
            json.dump({"import_id": str(import_id), "track_id": int(track_id)}, stream)
    except OSError as exc:  # noqa: BLE001 - a missing marker only costs a retry
        logger.warning("Could not record acquisition working-copy provenance: %s",
                       redact_sensitive_text(exc))


def _discard_working_copy(destination: Path) -> None:
    for path in (destination, _provenance_path(destination)):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:  # noqa: BLE001
            raise ValueError(
                "stale acquisition working copy could not be replaced") from exc


def _stage_working_copy(
    source: Path,
    *,
    transfer_dir: str,
    import_id: str,
    track_id: int,
    copier: Optional[Callable[[Path, Path], bool]] = None,
) -> str:
    """Copy one bundle file to this import's deterministic working path.

    FI-03: the pipeline TAGS this copy in place (and may downsample it), so
    after a crash between tagging and the move it no longer matches the
    untouched download original. The reuse check compared size + full SHA-256
    against that original and nothing else, so every resumed dispatch raised
    "existing acquisition working copy has different content" — forever, while
    the complete original sat right there. The import stayed open and no retry
    could ever get past this function.

    A copy we made carries a provenance marker naming the import and track it
    belongs to. Ours may therefore be discarded and re-staged from the
    original; a file we did NOT write still collides loudly, because that is a
    real conflict rather than our own half-finished work.
    """
    from core.imports.paths import sanitize_filename
    if copier is None:
        from core.download_plugins.album_bundle import atomic_copy_to_staging
        copier = atomic_copy_to_staging
    destination_root = Path(transfer_dir)
    destination_root.mkdir(parents=True, exist_ok=True)
    prefix = sanitize_filename(str(import_id)).replace(" ", "_")
    destination = destination_root / f"{prefix}_{track_id}_{source.name}"
    if destination.is_file():
        unchanged = (
            destination.stat().st_size == source.stat().st_size
            and _content_hash(destination) == _content_hash(source)
        )
        if unchanged:
            _write_provenance(destination, import_id=import_id, track_id=track_id)
            return str(destination)
        provenance = _read_provenance(destination)
        ours = (
            str(provenance.get("import_id") or "") == str(import_id)
            and int(provenance.get("track_id") or 0) == int(track_id)
        )
        if not ours:
            raise ValueError("existing acquisition working copy has different content")
        logger.info(
            "Replacing this import's own processed working copy for track %s "
            "from the untouched download original", track_id)
        _discard_working_copy(destination)
    if not copier(source, destination):
        raise ValueError("main-pipeline working copy could not be staged")
    _write_provenance(destination, import_id=import_id, track_id=track_id)
    return str(destination)


def _pipeline_published(
    context: Mapping[str, Any], connection_factory: Callable[[], Any],
) -> bool:
    """Whether the shared pipeline actually published this file.

    FI-01: ``_final_processed_path`` is written during path *planning* — before
    the upgrade decision, before the move, before catalogue registration. Using
    it as the success signal meant an import that was afterwards quarantined
    (rejected upgrade) or failed to move was still journalled as permanently
    completed: import row, acquisition request and retry state all ``completed``
    and the file dropped from ``result.quarantined``, with nothing at the
    destination. ``advance_import`` prefers that persisted completion over the
    bridge's own ``waiting``, so no later import run or retry walk ever picked
    it up again.

    ``_pipeline_import_succeeded`` is the pipeline's own success contract — the
    same flag auto-import checks — and it is set last, after the registration
    gate. The catalogue lookup is the independent proof that the published file
    is really owned by Library v2 rather than only reported as moved; it runs
    through the caller's own connection, which in production is the same
    database ``advance_import`` was handed.
    """
    if not context.get("_pipeline_import_succeeded"):
        return False
    if context.get("_upgrade_rejected") or context.get("_quarantine_entry_id"):
        return False
    final_path = context.get("_final_processed_path") or context.get("_final_path")
    if not final_path:
        return False
    conn = None
    try:
        conn = connection_factory()
        row = conn.execute(
            "SELECT id FROM lib2_track_files WHERE path=?"
            " AND COALESCE(file_state,'active')='active' LIMIT 1",
            (str(final_path),),
        ).fetchone()
        return row is not None
    except Exception as exc:  # noqa: BLE001 - never let the check invent success
        logger.warning(
            "Could not confirm Library v2 registration before completing an "
            "acquisition import: %s", redact_sensitive_text(exc))
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as close_err:  # noqa: BLE001
                logger.debug("registration probe close failed: %s", close_err)


def _content_hash(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.digest()


def dispatch_import_to_main_pipeline(
    connection_factory: Callable[[], Any],
    import_id: str,
    *,
    config_get: Optional[Callable[..., Any]] = None,
    processor: Optional[Callable[..., Any]] = None,
    runtime: Optional[Any] = None,
    copier: Optional[Callable[[Path, Path], bool]] = None,
) -> BridgeDispatchResult:
    """Dispatch every unprocessed matched file through the shared pipeline."""
    if config_get is None:
        from core.settings import config_manager
        config_get = config_manager.get
    if runtime is None:
        from core.imports.pipeline import build_import_pipeline_runtime
        runtime = build_import_pipeline_runtime()
    if processor is None:
        from core.imports.pipeline import post_process_matched_download_with_verification
        processor = post_process_matched_download_with_verification

    conn = connection_factory()
    try:
        from core.acquisition.grabs import get_grab
        from core.acquisition.imports import get_import
        record = get_import(conn, import_id)
        if record is None:
            raise KeyError(f"acquisition import not found: {import_id}")
        if record.status != "importing":
            return BridgeDispatchResult(record.id, waiting=(record.status,))
        grab = get_grab(conn, record.download_id) or {}
        source = str(grab.get("source") or "staging")
        processed = {
            (str(item.get("relative_path") or ""), int(item.get("track_id") or 0))
            for item in record.result.get("processed", [])
            if isinstance(item, Mapping)
        }
        work = []
        for match in record.matches:
            key = (str(match.get("relative_path") or ""), int(match.get("track_id") or 0))
            if key in processed:
                continue
            work.append((dict(match), _pipeline_context(conn, record, match, source=source)))
        resolved_path = record.resolved_path
    finally:
        conn.close()

    from core.imports.paths import docker_resolve_path
    transfer_dir = docker_resolve_path(
        str(config_get("soulseek.transfer_path", "./Transfer") or "./Transfer"))
    dispatched = []
    waiting = []
    errors = []
    for match, context in work:
        relative = str(match.get("relative_path") or "")
        task_id = f"acq-{import_id}-{int(match.get('track_id') or 0)}"
        context_key = f"acquisition_{import_id}_{int(match.get('track_id') or 0)}"
        try:
            source_path = _safe_source_path(str(resolved_path or ""), relative)
            staged_path = _stage_working_copy(
                source_path,
                transfer_dir=transfer_dir,
                import_id=import_id,
                track_id=int(match.get("track_id") or 0),
                copier=copier,
            )
            task = {
                "id": task_id,
                "status": "post_processing",
                "track_info": dict(context["track_info"]),
                "username": str(context["username"]),
                "filename": staged_path,
                "used_sources": set(),
                "_user_manual_pick": bool(context["_acquisition_manual_pick"]),
            }
            # ACQ-02: the sealed upgrade intent lives on the context, but the
            # candidate/staging consumers read it off the task. Without it a
            # quarantine retry loses the track lock, the profile snapshot and
            # the comparison against the existing primary file.
            from core.imports.upgrade_intent import carry_upgrade_intent
            carry_upgrade_intent(context, task)
            with tasks_lock:
                download_tasks[task_id] = task
            processor(
                context_key,
                context,
                staged_path,
                task_id,
                None,
                runtime,
            )
            from core.acquisition.pipeline_callback import notify_pipeline_import_success
            if _pipeline_published(context, connection_factory):
                notify_pipeline_import_success(
                    context, connection_factory=connection_factory)
            with tasks_lock:
                status = str(download_tasks.get(task_id, {}).get("status") or "unknown")
                if status == "completed":
                    download_tasks.pop(task_id, None)
            if status == "completed":
                dispatched.append(relative)
            else:
                waiting.append(relative)
        except Exception as exc:  # noqa: BLE001 - one file must not hide others
            safe_error = redact_sensitive_text(exc)
            logger.warning(
                "Main-pipeline dispatch failed for %s/%s: %s",
                import_id,
                relative,
                safe_error,
            )
            errors.append(f"{relative}: {safe_error}")
    return BridgeDispatchResult(
        import_id=str(import_id),
        dispatched=tuple(dispatched),
        waiting=tuple(waiting),
        errors=tuple(errors),
    )


__all__ = [
    "BridgeDispatchResult",
    "dispatch_import_to_main_pipeline",
]
