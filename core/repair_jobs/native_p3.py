"""P3-native implementations for mature maintenance job identities.

The public job IDs and UX remain stable while their catalogue boundary is
Library v2 only.  Subclassing preserves the proven file/fingerprint helpers;
every method that previously queried or projected a legacy catalogue row is
overridden here.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from core.library2.maintenance_subjects import (
    active_album_subjects,
    active_file_subjects,
    count_active_files,
    subject_details,
)
from core.repair_jobs import register_job
from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from core.repair_jobs.album_tag_consistency import AlbumTagConsistencyJob
from core.repair_jobs.base import JobContext, JobResult, get_scope_artist
from core.repair_jobs.live_commentary_cleaner import (
    LiveCommentaryCleanerJob,
    _detect_content_type,
    _format_type,
)
from core.repair_jobs.metadata_gap_filler import MetadataGapFillerJob, _extract_isrc
from core.repair_jobs.missing_cover_art import MissingCoverArtJob
from core.repair_jobs.track_number_repair import (
    TrackNumberRepairJob,
    _check_single_track,
    _fix_track_number_tag,
    _rename_to_basename,
)
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.native_p3")


@register_job
class NativeTrackNumberRepairJob(TrackNumberRepairJob):
    """Compare file tags/names with the native canonical album track rows."""

    def scan(self, context: JobContext) -> JobResult:
        from core.library2.paths import resolve_lib2_path

        result = JobResult()
        settings = self._get_settings(context)
        similarity = float(settings.get("title_similarity", 0.80))
        dry_run = bool(settings.get("dry_run", True))
        scope_artist = get_scope_artist(context)
        scope_key = scope_artist.casefold() if scope_artist else None
        subjects = [
            row for row in active_file_subjects(context.db, context.config_manager)
            if not scope_key or str(row.get("artist_name") or "").casefold() == scope_key
        ]
        by_album: Dict[int, list[Dict[str, Any]]] = {}
        for subject in subjects:
            by_album.setdefault(int(subject["album_id"]), []).append(subject)
        total = len(subjects)

        for album_subjects in by_album.values():
            api_tracks = [
                {
                    "name": row.get("title") or "",
                    "track_number": row.get("track_number"),
                    "disc_number": row.get("disc_number") or 1,
                    "lib2_track_id": int(row["track_id"]),
                }
                for row in album_subjects
                if row.get("track_number") is not None
            ]
            if not api_tracks:
                result.skipped += len(album_subjects)
                continue
            for subject in album_subjects:
                if context.check_stop() or context.wait_if_paused():
                    return result
                result.scanned += 1
                raw_path = str(subject.get("path") or "")
                resolved = raw_path if os.path.isfile(raw_path) else resolve_lib2_path(
                    raw_path, config_manager=context.config_manager,
                )
                if not resolved or not os.path.isfile(resolved):
                    result.skipped += 1
                    continue
                try:
                    finding = _check_single_track(
                        resolved, os.path.basename(resolved), api_tracks, similarity,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Track-number inspection failed for %s: %s", raw_path, exc)
                    result.errors += 1
                    continue
                if not finding:
                    result.skipped += 1
                    continue
                details = dict(finding["details"])
                details.update(subject_details(subject))
                details.update({
                    "track_id": f"lib2:{subject['track_id']}",
                    "file_id": int(subject["file_id"]),
                    "title": subject.get("title"),
                    "artist": subject.get("artist_name"),
                    "album": subject.get("album_title"),
                    "album_thumb_url": subject.get("album_image"),
                    "artist_thumb_url": subject.get("artist_image"),
                })
                if dry_run:
                    if context.create_finding:
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type="track_number_mismatch",
                            severity="warning",
                            entity_type="track",
                            entity_id=f"lib2:{subject['track_id']}",
                            file_path=raw_path,
                            title=f"Track number mismatch: {subject.get('title') or 'Unknown'}",
                            description=finding["description"],
                            details=details,
                        )
                        if inserted:
                            result.findings_created += 1
                        else:
                            result.findings_skipped_dedup += 1
                    continue

                try:
                    if not details.get("tag_ok", False):
                        _fix_track_number_tag(
                            resolved,
                            int(details["correct_track_num"]),
                            int(details.get("total_tracks") or 0),
                        )
                    new_path = None
                    new_filename = details.get("new_filename")
                    if new_filename:
                        new_path = _rename_to_basename(
                            resolved,
                            os.path.basename(resolved),
                            os.path.splitext(str(new_filename))[0],
                        )
                    if new_path:
                        conn = context.db._get_connection()
                        try:
                            conn.execute(
                                "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                                "WHERE id=?",
                                (new_path, int(subject["file_id"])),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    result.auto_fixed += 1
                    if context.report_change:
                        context.report_change(
                            finding_type="track_number_mismatch",
                            action="fixed_track_number",
                            entity_type="track",
                            entity_id=f"lib2:{subject['track_id']}",
                            file_path=new_path or raw_path,
                            details=details,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Native track-number repair failed for %s: %s", raw_path, exc)
                    result.errors += 1
                if context.update_progress:
                    context.update_progress(result.scanned, total)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        return count_active_files(context.db, context.config_manager)


@register_job
class NativeAcoustIDScannerJob(AcoustIDScannerJob):
    """AcoustID scanner over primary Library-v2 files."""

    def _load_db_tracks(self, context: JobContext) -> dict:
        tracks: Dict[str, Dict[str, Any]] = {}
        try:
            for subject in active_file_subjects(context.db, context.config_manager):
                key = f"lib2:{subject['track_id']}"
                current = tracks.get(key)
                if current is not None and not subject.get("is_primary"):
                    continue
                tracks[key] = {
                    "title": subject.get("title") or "",
                    "artist": subject.get("artist_name") or "",
                    "file_path": str(subject["path"]),
                    "track_number": subject.get("track_number"),
                    "album_title": subject.get("album_title") or "",
                    "album_thumb_url": subject.get("album_image"),
                    "artist_thumb_url": subject.get("artist_image"),
                    "track_artist": subject.get("artist_name") or "",
                    "album_artist": subject.get("artist_name") or "",
                    "duration_ms": subject.get("duration") or 0,
                    "lib2_file_id": int(subject["file_id"]),
                    "lib2_subject": subject,
                }
        except Exception as exc:  # noqa: BLE001
            logger.error("Native AcoustID subject enumeration failed: %s", exc)
        return tracks

    def _persist_status(
        self, context, track_id, fpath, db_path, status, write_tag, expected=None,
    ):
        if not status:
            return
        if write_tag:
            try:
                from core.tag_writer import write_verification_status

                write_verification_status(fpath, status)
            except Exception as exc:  # noqa: BLE001
                logger.debug("verification tag write failed for %s: %s", fpath, exc)
        file_id = int((expected or {}).get("lib2_file_id") or 0)
        if not file_id:
            return
        conn = context.db._get_connection()
        try:
            conn.execute(
                "UPDATE lib2_track_files SET verification_status=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, file_id),
            )
            conn.commit()
        finally:
            conn.close()
        if context.report_change:
            context.report_change(
                finding_type="acoustid_verification",
                action="verification_status_updated",
                entity_type="track",
                entity_id=track_id,
                file_path=db_path or fpath,
                details={
                    **subject_details((expected or {}).get("lib2_subject") or {}),
                    "verification_status": status,
                },
            )

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len({
                int(subject["track_id"])
                for subject in active_file_subjects(context.db, context.config_manager)
                if subject.get("is_primary")
            })
        except Exception:
            return 0


@register_job
class NativeAlbumTagConsistencyJob(AlbumTagConsistencyJob):
    """Album tag consistency over native album/file groups only."""

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        check_album = settings.get("check_album_name", True)
        check_artist = settings.get("check_album_artist", True)
        check_mbid = settings.get("check_mb_release_id", True)
        if any((check_album, check_artist, check_mbid)):
            self._scan_native_albums(
                context, result, check_album, check_artist, check_mbid,
            )
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            counts: Dict[int, int] = {}
            for subject in active_file_subjects(context.db, context.config_manager):
                counts[int(subject["album_id"])] = counts.get(int(subject["album_id"]), 0) + 1
            return sum(1 for count in counts.values() if count >= 2)
        except Exception:
            return 0


@register_job
class NativeMetadataGapFillerJob(MetadataGapFillerJob):
    """Provider-qualified ISRC/recording-ID completion for native tracks."""

    def scan(self, context: JobContext) -> JobResult:
        from core.metadata_service import (
            get_client_for_source,
            get_primary_source,
            get_source_priority,
        )

        result = JobResult()
        settings = self._get_settings(context)
        fill_isrc = settings.get("fill_isrc", True)
        fill_mb_id = settings.get("fill_musicbrainz_id", True)
        if not (fill_isrc or fill_mb_id):
            return result
        source_priority = list(get_source_priority(get_primary_source()))
        scope_artist = get_scope_artist(context)
        scope_key = scope_artist.casefold() if scope_artist else None

        subjects: Dict[int, Dict[str, Any]] = {}
        for subject in active_file_subjects(context.db, context.config_manager):
            track_id = int(subject["track_id"])
            if track_id in subjects and not subject.get("is_primary"):
                continue
            if scope_key and str(subject.get("artist_name") or "").casefold() != scope_key:
                continue
            missing_isrc = fill_isrc and not str(subject.get("isrc") or "").strip()
            missing_mbid = fill_mb_id and not str(
                (subject.get("track_source_ids") or {}).get("musicbrainz") or ""
            ).strip()
            if missing_isrc or missing_mbid:
                subjects[track_id] = subject
        work = list(subjects.values())[:500]
        total = len(work)

        for index, subject in enumerate(work):
            if context.check_stop() or (index % 20 == 0 and context.wait_if_paused()):
                return result
            result.scanned += 1
            source_ids = dict(subject.get("track_source_ids") or {})
            order = list(source_priority)
            order.extend(sorted(set(source_ids) - set(order)))
            found: Dict[str, Any] = {}
            resolved_source = None
            resolved_track_id = None

            if fill_isrc and not subject.get("isrc"):
                for source in order:
                    provider_id = source_ids.get(source)
                    if not provider_id:
                        continue
                    try:
                        client = get_client_for_source(source)
                        getter = getattr(client, "get_track_details", None) if client else None
                        if not callable(getter):
                            continue
                        try:
                            payload = getter(provider_id, allow_fallback=False)
                        except TypeError:
                            payload = getter(provider_id)
                        value = _extract_isrc(payload)
                        if value:
                            found["isrc"] = value
                            resolved_source = source
                            resolved_track_id = provider_id
                            break
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("%s ISRC lookup failed: %s", source, exc)

            if fill_mb_id and not source_ids.get("musicbrainz") and context.mb_client:
                try:
                    rows = context.mb_client.search_recording(
                        subject.get("title"),
                        artist_name=subject.get("artist_name"),
                        limit=1,
                    )
                    if rows and rows[0].get("id"):
                        found["musicbrainz_recording_id"] = rows[0]["id"]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("MusicBrainz recording lookup failed: %s", exc)

            if not found:
                result.skipped += 1
                continue
            details = {
                "track_id": f"lib2:{subject['track_id']}",
                "title": subject.get("title"),
                "artist": subject.get("artist_name"),
                "album": subject.get("album_title"),
                "track_ids": source_ids,
                "resolved_source": resolved_source,
                "resolved_track_id": resolved_track_id,
                "found_fields": found,
                "album_thumb_url": subject.get("album_image"),
                "artist_thumb_url": subject.get("artist_image"),
            }
            details.update(subject_details(subject))
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="metadata_gap",
                    severity="info",
                    entity_type="track",
                    entity_id=f"lib2:{subject['track_id']}",
                    file_path=subject.get("path"),
                    title=f"Missing metadata: {subject.get('title') or 'Unknown'}",
                    description=(
                        f'Found {", ".join(found)} for "{subject.get("title")}" '
                        f'by {subject.get("artist_name") or "Unknown"}.'
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

    def estimate_scope(self, context: JobContext) -> int:
        try:
            track_ids = set()
            for subject in active_file_subjects(context.db, context.config_manager):
                ids = subject.get("track_source_ids") or {}
                if not subject.get("isrc") or not ids.get("musicbrainz"):
                    track_ids.add(int(subject["track_id"]))
            return min(len(track_ids), 500)
        except Exception:
            return 0


@register_job
class NativeMissingCoverArtJob(MissingCoverArtJob):
    """Artwork review using effective native metadata and all provider IDs."""

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

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(active_album_subjects(context.db, context.config_manager))
        except Exception:
            return 0


@register_job
class NativeLiveCommentaryCleanerJob(LiveCommentaryCleanerJob):
    """Review heuristic over native track metadata and files."""

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        enabled_types = {
            content_type
            for content_type, key in (
                ("live", "flag_live"),
                ("commentary", "flag_commentary"),
                ("interview", "flag_interviews"),
                ("spoken_word", "flag_spoken_word"),
            )
            if settings.get(key, True)
        }
        if not enabled_types:
            return result
        scan_album_titles = settings.get("scan_album_titles", True)
        subjects = active_file_subjects(context.db, context.config_manager)
        for subject in subjects:
            if context.check_stop():
                return result
            result.scanned += 1
            content_type = _detect_content_type(subject.get("title"), "")
            album_matched = False
            if not content_type and scan_album_titles:
                content_type = _detect_content_type("", subject.get("album_title"))
                album_matched = bool(content_type)
            if not content_type or content_type not in enabled_types:
                continue
            album_id = int(subject["album_id"])
            type_label = _format_type(content_type)
            details = {
                "track": {
                    "id": f"lib2:{subject['track_id']}",
                    "title": subject.get("title"),
                    "artist": subject.get("artist_name") or "",
                    "album": subject.get("album_title") or "",
                    "album_id": f"lib2:{album_id}",
                    "album_type": subject.get("album_type") or "",
                    "file_path": subject.get("path"),
                    "bitrate": subject.get("bitrate"),
                    "duration": subject.get("duration"),
                    "track_number": subject.get("track_number"),
                },
                "content_type": content_type,
                "type_label": type_label,
                "album_matched": album_matched,
                "album_thumb_url": subject.get("album_image"),
                "artist_thumb_url": subject.get("artist_image"),
            }
            details.update(subject_details(subject))
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="unwanted_content",
                    severity="info",
                    entity_type="track",
                    entity_id=f"lib2:{subject['track_id']}",
                    file_path=subject.get("path"),
                    title=(
                        f'{type_label}: {subject.get("title")} by '
                        f'{subject.get("artist_name") or "Unknown"}'
                    ),
                    description=(
                        f'{type_label} content detected in '
                        f'{"album" if album_matched else "track"} metadata.'
                    ),
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        return result

    def estimate_scope(self, context: JobContext) -> int:
        return count_active_files(context.db, context.config_manager)


__all__ = [
    "NativeAcoustIDScannerJob",
    "NativeAlbumTagConsistencyJob",
    "NativeLiveCommentaryCleanerJob",
    "NativeMetadataGapFillerJob",
    "NativeMissingCoverArtJob",
    "NativeTrackNumberRepairJob",
]
