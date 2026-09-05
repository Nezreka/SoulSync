"""Missing Cover Art Filler Job — finds albums without artwork and locates art from APIs."""

from core.library2.maintenance_subjects import active_album_subjects
from core.library2.maintenance_subjects import subject_details
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.cover_art")


@register_job
class MissingCoverArtJob(RepairJob):
    job_id = 'missing_cover_art'
    display_name = 'Cover Art Filler'
    description = 'Finds albums missing artwork and locates art from metadata sources'
    help_text = (
        'Scans your library for albums that have no cover art stored in the database. '
        'For each missing cover, it searches for matching artwork using the album name '
        'and artist. If you have configured cover-art sources (Settings > metadata '
        'enhancement art order), those are used first; otherwise it falls back to '
        'Prefer Source (if set) or the primary metadata source.\n\n'
        'When artwork is found, a finding is created with the image URL so you can review '
        'and apply it. The job does not download or embed artwork automatically.\n\n'
        'Settings:\n'
        '- Prefer Source: Optional source to try first; otherwise the primary metadata source is used'
    )
    icon = 'repair-icon-coverart'
    default_enabled = True
    default_interval_hours = 48
    default_settings = {}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        import os

        from core.library2.paths import resolve_lib2_path
        from core.library2.provider_adapters import fetch_artwork_url
        from core.metadata.art_apply import (
            file_has_embedded_art,
            folder_has_cover_sidecar,
        )

        result = JobResult()
        settings = self._get_settings(context)
        configured_order = (
            context.config_manager.get("metadata_enhancement.album_art_order")
            if context.config_manager else None
        )
        source_order = tuple(configured_order or ()) or None
        prefer_source = str(settings.get("prefer_source") or "").strip().lower()
        if prefer_source:
            remaining = tuple(source for source in (source_order or ()) if source != prefer_source)
            source_order = (prefer_source, *remaining)
        sidecar_enabled = bool(
            context.config_manager.get("metadata_enhancement.cover_art_download", True)
            if context.config_manager else True
        )
        albums = active_album_subjects(context.db, context.config_manager)
        total = len(albums)
        for index, subject in enumerate(albums):
            if context.check_stop() or (index % 10 == 0 and context.wait_if_paused()):
                return result
            result.scanned += 1
            raw_path = str(subject.get("rep_path") or "")
            resolved = raw_path if os.path.isfile(raw_path) else resolve_lib2_path(
                raw_path, config_manager=context.config_manager,
            )
            embedded = bool(resolved and file_has_embedded_art(resolved))
            sidecar = bool(
                resolved and folder_has_cover_sidecar(os.path.dirname(resolved))
            )
            db_missing = not str(subject.get("album_image") or "").strip()
            embed_missing = bool(resolved and not embedded)
            sidecar_missing = bool(resolved and sidecar_enabled and not sidecar)
            if not (db_missing or embed_missing or sidecar_missing):
                result.skipped += 1
                continue

            provider_result = fetch_artwork_url(
                "album",
                artist_name=subject.get("artist_name") or "",
                album_title=subject.get("title") or "",
                source_ids=subject.get("album_source_ids") or {},
                source_order=source_order,
            )
            sidecar_from_embedded = sidecar_missing and embedded
            if provider_result is None and not sidecar_from_embedded:
                result.skipped += 1
                continue
            artist_result = fetch_artwork_url(
                "artist",
                artist_name=subject.get("artist_name") or "",
                source_ids=subject.get("artist_source_ids") or {},
            )
            details = {
                "album_id": f"lib2:{subject['album_id']}",
                "album_title": subject.get("title"),
                "artist": subject.get("artist_name"),
                "artist_id": subject.get("artist_id"),
                "found_artwork_url": provider_result.url if provider_result else None,
                "artwork_source": provider_result.source if provider_result else "embedded",
                "artwork_source_id": (
                    provider_result.provider_entity_id if provider_result else None
                ),
                "artist_thumb_url": subject.get("artist_image"),
                "found_artist_url": (
                    artist_result.url
                    if artist_result and artist_result.url != subject.get("artist_image")
                    else None
                ),
                "artist_artwork_source": artist_result.source if artist_result else None,
                "album_folder": os.path.dirname(raw_path) if raw_path else None,
                "db_missing": db_missing,
                "embed_missing": embed_missing,
                "sidecar_from_embedded": sidecar_from_embedded,
                "musicbrainz_release_id": (
                    subject.get("album_source_ids") or {}
                ).get("musicbrainz"),
            }
            details.update(subject_details(subject))
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="missing_cover_art",
                    severity="info",
                    entity_type="album",
                    entity_id=f"lib2:{subject['album_id']}",
                    file_path=raw_path or None,
                    title=f"Missing artwork: {subject.get('title') or 'Unknown'}",
                    description=(
                        f'Artwork for "{subject.get("title")}" by '
                        f'{subject.get("artist_name") or "Unknown"} can be repaired '
                        f'from {details["artwork_source"]}.'
                    ),
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        if context.update_progress:
            context.update_progress(total, total)
        return result

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(active_album_subjects(context.db, context.config_manager))
        except Exception:
            return 0

