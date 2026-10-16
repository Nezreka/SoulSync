"""Missing Cover Art Filler Job — finds albums without artwork and locates art from APIs."""

import hashlib
import json
from contextlib import nullcontext

from core.library2.maintenance_subjects import active_album_subjects
from core.library2.maintenance_subjects import subject_details
from core.lookup_failures import LookupFailures, watch_lookup_failures
from core.repair_jobs import register_job
from core.repair_jobs.empty_lookup_memory import EmptyLookupMemory
from core.repair_jobs.base import JobContext, JobResult, RepairJob, drop_hand_tagged
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
        '- Prefer Source: Optional source to try first; otherwise the primary metadata source is used\n'
        '- Remember Empty Lookups: When on, an album no source had art for is not searched '
        'again until it changes, your art sources change, or the recheck time passes. '
        'Fewer repeated requests to the metadata services\n'
        '- Recheck After Days: How long an empty answer is trusted (default 7)'
    )
    icon = 'repair-icon-coverart'
    default_enabled = True
    default_interval_hours = 48
    default_settings = {
        'remember_empty_lookups': False,
        'recheck_after_days': 7,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
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
        # hand-tagged: the user typed this release, a name search would give a
        # bootleg the studio cover
        albums = drop_hand_tagged(
            context, active_album_subjects(context.db, context.config_manager), path_key="rep_path")
        total = len(albums)
        # Opt-in (#1708): an album no source had art for is not searched again
        # until what is asked changes or the recheck time passes.
        memory = None
        if settings.get('remember_empty_lookups'):
            try:
                recheck_days = max(0.0, float(settings.get('recheck_after_days', 7)))
            except (TypeError, ValueError):
                recheck_days = 7.0
            memory = EmptyLookupMemory.open(context.db, self.job_id, recheck_days)
        # The provider clients turn a failed request into an empty result, so
        # the requests are watched: a throttled lookup is not an empty answer.
        watching = watch_lookup_failures() if memory else nullcontext(LookupFailures())
        try:
            with watching as health:
                finished = self._scan_albums(context, result, albums, source_order, memory, health)
            if memory and finished:
                # only a scan that saw every album may forget the missing ones
                memory.prune(str(subject['album_id']) for subject in albums)
        finally:
            if memory:
                memory.close()
        if context.update_progress:
            context.update_progress(total, total)
        return result

    def _scan_albums(self, context, result, albums, source_order, memory, health):
        import os

        from core.library2.paths import resolve_lib2_path
        from core.library2.provider_adapters import fetch_artwork_url
        from core.library2.tag_cache import observe_file_tags
        from core.library2.validation import artwork_checks

        for index, subject in enumerate(albums):
            if context.check_stop() or (index % 10 == 0 and context.wait_if_paused()):
                return False
            if memory and index % 25 == 0:
                memory.commit()
            result.scanned += 1
            raw_path = str(subject.get("rep_path") or "")
            resolved = raw_path if os.path.isfile(raw_path) else resolve_lib2_path(
                raw_path, config_manager=context.config_manager,
            )
            tags = observe_file_tags(context.db, subject['file_id'], resolved, context.config_manager, self.job_id) if resolved else {}
            if tags.get('error'):
                result.errors += 1
                continue
            embedded, sidecar = bool(tags.get('has_cover_art')), bool(tags.get('cover_sidecar'))
            checks = artwork_checks(str(subject.get('album_image') or '').strip(),
                                    tags, context.config_manager or {})
            db_missing, embed_missing, sidecar_missing = (checks[key] == 'missing' for key in ('artwork_database', 'cover', 'artwork_sidecar'))
            if not (db_missing or embed_missing or sidecar_missing):
                result.skipped += 1
                continue

            key = str(subject['album_id'])
            signature = self._lookup_signature(subject, source_order)
            if memory and memory.recently_empty(key, signature):
                provider_result = None
            else:
                failures_before = health.count
                provider_result = fetch_artwork_url(
                    "album",
                    artist_name=subject.get("artist_name") or "",
                    album_title=subject.get("title") or "",
                    source_ids=subject.get("album_source_ids") or {},
                    source_order=source_order,
                )
                if memory and provider_result:
                    memory.forget(key)
                elif memory and health.count == failures_before and not self._spotify_rate_limited(context):
                    memory.remember(key, signature)
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
                "library_owner_id": subject.get("owner_profile_id"),
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
                "artwork_checks": checks,
                "embed_missing": embed_missing,
                "sidecar_missing": sidecar_missing,
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
                    title=f"Artwork storage gaps: {subject.get('title') or 'Unknown'}",
                    description=(
                        f'Artwork for "{subject.get("title")}" by '
                        f'{subject.get("artist_name") or "Unknown"} can be repaired '
                        f'from {details["artwork_source"]}. Missing: '
                        + ', '.join(name for name, missing in (('database image', db_missing), ('embedded image', embed_missing), ('cover file', sidecar_missing)) if missing)
                    ),
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        return True

    @staticmethod
    def _lookup_signature(subject, source_order) -> str:
        """A short fingerprint of everything that decides what the lookup asks."""
        ids = subject.get("album_source_ids") or {}
        payload = json.dumps(
            [subject.get("title"), subject.get("artist_name"),
             sorted((k, str(v)) for k, v in ids.items() if v), list(source_order or [])],
            default=str)
        return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]

    @staticmethod
    def _spotify_rate_limited(context) -> bool:
        check = getattr(context, 'is_spotify_rate_limited', None)
        try:
            return bool(check()) if callable(check) else False
        except Exception:  # noqa: BLE001 - an unreadable flag means "do not trust this answer"
            return True

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
