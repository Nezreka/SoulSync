"""Atomic edition-aware import of a matched bundle (audit §13.4 steps 9-11).

Three strictly separated phases keep the §17.5 crash points harmless:

1. **Plan** (short read transaction): load the importing row, its request
   catalog context and grab source, and compute deterministic destination
   paths under the transfer directory. Nothing is written.
2. **Stage** (filesystem I/O, no transaction): copy every matched file to
   its destination via temp-name + atomic rename. Copies are idempotent —
   a destination that already exists with the source's size is accepted,
   so a crash between staging and the final transaction only causes a
   re-verify on the next cycle, never duplicate files.
3. **Complete** (one short transaction): upsert ``lib2_track_files`` rows,
   graduate provider-only albums to the library, mark the import completed
   with its file journal in ``result_json`` and complete the owning
   request. The download client's originals are never deleted here.

Transient problems (missing source, unreadable/unwritable target) defer
the attempt — the import stays ``importing`` with a visible error and
attempt counter instead of failing or looping silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from core.acquisition.candidates import redact_sensitive_text
from core.acquisition.imports import (
    AcquisitionImport,
    get_import,
    record_import_completed,
    record_import_deferred,
    record_import_failure,
)
from utils.logging_config import get_logger


logger = get_logger("acquisition.bundle_import")


OUTCOME_COMPLETED = "completed"
OUTCOME_DEFERRED = "deferred"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"


@dataclass(frozen=True)
class PlannedImportFile:
    """One matched file with its computed library destination."""

    relative_path: str
    source_path: str
    destination_path: str
    track_id: int
    size_bytes: Optional[int]

    def journal_entry(self) -> Dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "destination": self.destination_path,
            "track_id": self.track_id,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ImportRunOutcome:
    status: str
    import_id: str
    detail: Optional[str] = None
    imported_files: int = 0


def _clean_component(value: Any, fallback: str) -> str:
    from core.imports.paths import sanitize_filename
    text = str(value or "").strip()
    return sanitize_filename(text) if text else fallback


def plan_import_files(
    record: AcquisitionImport,
    *,
    artist: Any,
    release_title: Any,
    edition: Any,
    transfer_dir: str,
) -> Tuple[PlannedImportFile, ...]:
    """Compute deterministic destinations for every persisted match.

    Layout: ``<transfer>/<Artist>/<Artist> - <Album>[ (Edition)]/
    [Disc N/]NN - Title.ext``. Pure function of the persisted state so a
    restarted process re-plans the exact same paths.
    """
    if not record.matches:
        raise ValueError("acquisition import has no persisted track matches")
    resolved_root = str(record.resolved_path or "").strip()
    if not resolved_root:
        raise ValueError("acquisition import has no resolved bundle path")

    sizes: Dict[str, Optional[int]] = {}
    for item in record.inventory:
        raw_size = item.get("size_bytes")
        sizes[str(item.get("relative_path") or "")] = (
            int(raw_size) if isinstance(raw_size, (int, float)) and raw_size
            else None)

    artist_dir = _clean_component(artist, "Unknown Artist")
    album_label = _clean_component(release_title, "Unknown Album")
    edition_label = _clean_component(edition, "")
    album_dir = f"{artist_dir} - {album_label}"
    if edition_label:
        album_dir = f"{album_dir} ({edition_label})"

    discs = {
        int(match.get("disc_number") or 1)
        for match in record.matches
    }
    multi_disc = len(discs) > 1

    planned: list[PlannedImportFile] = []
    taken: set[str] = set()
    for match in record.matches:
        relative_path = str(match.get("relative_path") or "").strip()
        if not relative_path:
            raise ValueError("persisted match is missing its file path")
        raw_track_id = match.get("track_id")
        if raw_track_id is None:
            raise ValueError(
                "persisted match is missing its lib2 track link")
        disc_number = int(match.get("disc_number") or 1)
        track_number = match.get("track_number")
        title = _clean_component(
            match.get("expected_title"),
            relative_path.rsplit("/", 1)[-1].rsplit(".", 1)[0],
        )
        extension = ""
        name = relative_path.rsplit("/", 1)[-1]
        if "." in name:
            extension = "." + name.rsplit(".", 1)[-1]
        filename = (
            f"{int(track_number):02d} - {title}{extension}"
            if track_number else f"{title}{extension}"
        )
        parts = [transfer_dir, artist_dir, album_dir]
        if multi_disc:
            parts.append(f"Disc {disc_number}")
        destination = str(Path(*parts, filename))
        if destination in taken:
            stem, ext = (
                destination.rsplit(".", 1)
                if "." in filename else (destination, ""))
            counter = 2
            while f"{stem} ({counter}){'.' + ext if ext else ''}" in taken:
                counter += 1
            destination = f"{stem} ({counter}){'.' + ext if ext else ''}"
        taken.add(destination)
        planned.append(PlannedImportFile(
            relative_path=relative_path,
            source_path=str(Path(resolved_root, *relative_path.split("/"))),
            destination_path=destination,
            track_id=int(raw_track_id),
            size_bytes=sizes.get(relative_path),
        ))
    return tuple(planned)


def stage_planned_files(
    planned: Sequence[PlannedImportFile],
    *,
    copier: Optional[Callable[[Path, Path], bool]] = None,
) -> Optional[str]:
    """Copy every planned file into the library. Idempotent, I/O only.

    Returns None on success or a human-readable error for a deferred
    retry. Already-present destinations with the source's size count as
    done (crash-recovery re-run).
    """
    if copier is None:
        from core.download_plugins.album_bundle import atomic_copy_to_staging
        copier = atomic_copy_to_staging
    for item in planned:
        source = Path(item.source_path)
        destination = Path(item.destination_path)
        try:
            if not source.is_file():
                return f"Bundle file disappeared before import: {item.relative_path}"
            source_size = source.stat().st_size
            if destination.is_file():
                if destination.stat().st_size == source_size:
                    continue
                return (
                    "Import destination already exists with different "
                    f"content: {destination}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not copier(source, destination):
                return f"Copy failed for {item.relative_path}"
        except OSError as exc:
            return f"Import file operation failed: {exc}"
    return None


def _probe_quality(path: str, prober: Optional[Callable[[str], Any]]) -> Dict[str, Any]:
    facts: Dict[str, Any] = {
        "format": None, "bitrate": None, "sample_rate": None, "bit_depth": None,
    }
    name = path.rsplit(".", 1)
    if len(name) == 2:
        facts["format"] = name[1].lower()
    if prober is None:
        from core.imports.file_ops import probe_audio_quality
        prober = probe_audio_quality
    try:
        quality = prober(path)
    except Exception as exc:  # noqa: BLE001 - probe is best effort
        logger.debug("Quality probe failed for %s: %s", path, exc)
        quality = None
    if quality is not None:
        facts["format"] = getattr(quality, "format", None) or facts["format"]
        facts["bitrate"] = getattr(quality, "bitrate", None)
        facts["sample_rate"] = getattr(quality, "sample_rate", None)
        facts["bit_depth"] = getattr(quality, "bit_depth", None)
    return facts


def complete_import_transaction(
    conn: Any,
    record: AcquisitionImport,
    planned: Sequence[PlannedImportFile],
    quality_facts: Mapping[str, Mapping[str, Any]],
    *,
    source: str,
) -> AcquisitionImport:
    """Upsert file rows and complete import + request in one transaction."""
    from core.library2.status import quality_tier

    imported = []
    album_ids: set[int] = set()
    for item in planned:
        facts = dict(quality_facts.get(item.relative_path) or {})
        fmt = facts.get("format")
        bitrate = facts.get("bitrate")
        bit_depth = facts.get("bit_depth")
        tier = quality_tier(fmt, bitrate, bit_depth) if fmt or bitrate else None
        try:
            size = Path(item.destination_path).stat().st_size
        except OSError:
            size = item.size_bytes
        existing = conn.execute(
            "SELECT id FROM lib2_track_files WHERE track_id=? AND path=?",
            (item.track_id, item.destination_path),
        ).fetchone()
        if existing is not None:
            file_id = existing[0]
            conn.execute(
                """UPDATE lib2_track_files
                      SET size=COALESCE(?, size),
                          bitrate=COALESCE(?, bitrate),
                          sample_rate=COALESCE(?, sample_rate),
                          bit_depth=COALESCE(?, bit_depth),
                          format=COALESCE(?, format),
                          quality_tier=COALESCE(?, quality_tier),
                          file_state='active',
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (size, bitrate, facts.get("sample_rate"), bit_depth,
                 fmt, tier, file_id),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO lib2_track_files(
                       track_id, path, original_path, size, bitrate,
                       sample_rate, bit_depth, format, quality_tier,
                       source, import_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?, 'imported')""",
                (item.track_id, item.destination_path, item.source_path,
                 size, bitrate, facts.get("sample_rate"), bit_depth,
                 fmt, tier, source),
            )
            file_id = cursor.lastrowid
        row = conn.execute(
            "SELECT album_id FROM lib2_tracks WHERE id=?",
            (item.track_id,),
        ).fetchone()
        if row is not None and row[0] is not None:
            album_ids.add(int(row[0]))
        imported.append({**item.journal_entry(), "file_id": file_id})

    for album_id in sorted(album_ids):
        # An album that just gained real files must be visible as library
        # content, exactly like the autolink path graduates it.
        conn.execute(
            "UPDATE lib2_albums SET origin='library', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND origin='discography'", (album_id,))

    return record_import_completed(
        conn,
        record.id,
        result={"imported": imported, "source": source},
    )


def _transfer_directory(config_get: Optional[Callable[..., Any]]) -> str:
    from core.imports.paths import docker_resolve_path
    if config_get is None:
        from config.settings import config_manager
        config_get = config_manager.get
    return docker_resolve_path(
        str(config_get("soulseek.transfer_path", "./Transfer") or "./Transfer"))


def execute_ready_import(
    connection_factory: Callable[[], Any],
    import_id: str,
    *,
    config_get: Optional[Callable[..., Any]] = None,
    copier: Optional[Callable[[Path, Path], bool]] = None,
    prober: Optional[Callable[[str], Any]] = None,
) -> ImportRunOutcome:
    """Drive one ``importing`` row through plan → stage → complete."""
    conn = connection_factory()
    try:
        record = get_import(conn, import_id)
        if record is None or record.status != "importing":
            return ImportRunOutcome(
                status=OUTCOME_SKIPPED,
                import_id=str(import_id),
                detail=None if record is None else record.status,
            )
        from core.acquisition.grabs import get_grab
        from core.acquisition.catalog import resolve_catalog_context
        from core.acquisition.requests import get_request
        request = get_request(conn, record.request_id)
        if request is None:
            raise ValueError("acquisition request no longer exists")
        catalog = resolve_catalog_context(conn, request)
        grab = get_grab(conn, record.download_id) or {}
        source = str(grab.get("source") or "usenet")
        planned = plan_import_files(
            record,
            artist=catalog.artist,
            release_title=catalog.release_title,
            edition=catalog.edition,
            transfer_dir=_transfer_directory(config_get),
        )
    except (ValueError, KeyError) as exc:
        conn.rollback()
        failed = _fail_import(
            connection_factory, import_id, error=str(exc))
        return ImportRunOutcome(
            status=OUTCOME_FAILED if failed else OUTCOME_SKIPPED,
            import_id=str(import_id),
            detail=redact_sensitive_text(exc),
        )
    finally:
        conn.close()

    stage_error = stage_planned_files(planned, copier=copier)
    if stage_error is not None:
        _defer_import(connection_factory, import_id, error=stage_error)
        return ImportRunOutcome(
            status=OUTCOME_DEFERRED,
            import_id=str(import_id),
            detail=redact_sensitive_text(stage_error),
        )

    quality_facts = {
        item.relative_path: _probe_quality(item.destination_path, prober)
        for item in planned
    }

    conn = connection_factory()
    try:
        record = get_import(conn, import_id)
        if record is None or record.status != "importing":
            return ImportRunOutcome(
                status=OUTCOME_SKIPPED,
                import_id=str(import_id),
                detail=None if record is None else record.status,
            )
        completed = complete_import_transaction(
            conn, record, planned, quality_facts, source=source)
        conn.commit()
        logger.info(
            "Acquisition import %s completed with %d file(s)",
            completed.id, len(planned))
        return ImportRunOutcome(
            status=OUTCOME_COMPLETED,
            import_id=completed.id,
            imported_files=len(planned),
        )
    except Exception as exc:  # noqa: BLE001 - keep the row open and visible
        conn.rollback()
        safe_error = redact_sensitive_text(exc)
        _defer_import(
            connection_factory, import_id,
            error=f"Import transaction failed: {safe_error}")
        return ImportRunOutcome(
            status=OUTCOME_DEFERRED,
            import_id=str(import_id),
            detail=safe_error,
        )
    finally:
        conn.close()


def _defer_import(
    connection_factory: Callable[[], Any], import_id: str, *, error: str,
) -> None:
    conn = connection_factory()
    try:
        record_import_deferred(conn, import_id, error=error)
        conn.commit()
    except Exception:  # noqa: BLE001 - deferred bookkeeping is best effort
        conn.rollback()
        logger.warning(
            "Could not persist deferred import attempt for %s", import_id)
    finally:
        conn.close()


def _fail_import(
    connection_factory: Callable[[], Any], import_id: str, *, error: str,
) -> bool:
    conn = connection_factory()
    try:
        record_import_failure(
            conn, import_id, error=error, failure_kind="runtime")
        conn.commit()
        return True
    except Exception:  # noqa: BLE001 - the row may have moved on concurrently
        conn.rollback()
        logger.warning(
            "Could not persist import failure for %s", import_id)
        return False
    finally:
        conn.close()


__all__ = [
    "OUTCOME_COMPLETED",
    "OUTCOME_DEFERRED",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "ImportRunOutcome",
    "PlannedImportFile",
    "complete_import_transaction",
    "execute_ready_import",
    "plan_import_files",
    "stage_planned_files",
]
