"""Retention cleanup for origin-tracked watchlist/playlist downloads."""

from __future__ import annotations

import os

from core.library.expired_cleanup import RETENTION_OPTIONS, select_expired
from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.expired_download_cleaner")


def delete_origin_download(db, entry, config_manager) -> dict:
    """Delete the file and its origin/history rows, preserving retryability."""
    raw_path = entry.get("file_path") or ""
    file_deleted = False
    error = None
    if raw_path:
        resolved = resolve_library_file_path(raw_path, config_manager=config_manager)
        if resolved and os.path.isfile(resolved):
            try:
                os.remove(resolved)
                file_deleted = True
            except OSError as exc:
                error = str(exc)
        if error is None:
            try:
                db.delete_track_by_file_path(raw_path)
            except Exception as exc:  # catalogue sync still runs after the fix
                logger.debug("expired cleanup track-row delete failed: %s", exc)
    removed = 0
    if error is None:
        removed = db.delete_library_history_rows([entry["id"]])
    return {"removed": removed, "file_deleted": file_deleted, "error": error}


@register_job
class ExpiredDownloadCleanerJob(RepairJob):
    job_id = "expired_download_cleaner"
    display_name = "Expired Download Cleaner"
    description = "Deletes expired origin-tracked downloads while protecting active and replayed items"
    help_text = (
        "Reviews downloads recorded by Download Origins. Watchlist and playlist "
        "retention are configured independently; active mirrors/watchlist artists "
        "and sufficiently replayed tracks are protected. Dry run is enabled by default."
    )
    icon = "repair-icon-cleanup"
    default_enabled = False
    default_interval_hours = 24
    default_settings = {
        "watchlist_retention": "off",
        "playlist_retention": "off",
        "keep_if_played_at_least": 2,
        "dry_run": True,
    }
    setting_options = {
        "watchlist_retention": RETENTION_OPTIONS,
        "playlist_retention": RETENTION_OPTIONS,
        "dry_run": [True, False],
    }
    auto_fix = True

    def _settings(self, context: JobContext) -> dict:
        settings = dict(self.default_settings)
        if context.config_manager:
            settings.update(context.config_manager.get(
                f"repair.jobs.{self.job_id}.settings", {},
            ) or {})
        return settings

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._settings(context)
        watchlist_retention = settings.get("watchlist_retention") or "off"
        playlist_retention = settings.get("playlist_retention") or "off"
        if watchlist_retention == "off" and playlist_retention == "off":
            return result
        try:
            min_plays = int(settings.get("keep_if_played_at_least", 2))
        except (TypeError, ValueError):
            min_plays = 2
        dry_run = bool(settings.get("dry_run", True))
        candidates = context.db.get_origin_cleanup_candidates() or []

        mirrored_names: set[str] = set()
        watched_names: set[str] = set()
        try:
            mirrored_names = {
                str(row.get("name") or "").strip().casefold()
                for row in (context.db.get_mirrored_playlists() or [])
                if isinstance(row, dict) and str(row.get("name") or "").strip()
            }
        except Exception as exc:
            logger.debug("expired cleanup playlist protection unavailable: %s", exc)
        try:
            watched_names = {
                str(getattr(row, "artist_name", "") or "").strip().casefold()
                for row in (context.db.get_watchlist_artists() or [])
                if str(getattr(row, "artist_name", "") or "").strip()
            }
        except Exception as exc:
            logger.debug("expired cleanup watchlist protection unavailable: %s", exc)

        for candidate in candidates:
            origin = str(candidate.get("origin") or "").strip().lower()
            origin_context = str(candidate.get("origin_context") or "").strip().casefold()
            candidate["protected"] = bool(
                origin_context and (
                    (origin == "playlist" and origin_context in mirrored_names)
                    or (origin == "watchlist" and origin_context in watched_names)
                )
            )
        expired = select_expired(
            candidates,
            watchlist_retention=watchlist_retention,
            playlist_retention=playlist_retention,
            min_plays=min_plays,
        )
        result.scanned = len(candidates)
        for index, entry in enumerate(expired):
            if context.check_stop():
                break
            if dry_run and context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="expired_download",
                    severity="info",
                    entity_type="track",
                    entity_id=str(entry.get("id") or ""),
                    file_path=entry.get("file_path"),
                    title=f'Expired: {entry.get("title") or "Unknown"}',
                    description=(
                        f'{entry.get("title") or "Unknown"} via '
                        f'{entry.get("origin") or "unknown"} is past retention.'
                    ),
                    details={
                        "history_id": entry.get("id"),
                        "file_path": entry.get("file_path"),
                        "origin": entry.get("origin"),
                        "origin_context": entry.get("origin_context"),
                    },
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
            elif not dry_run:
                outcome = delete_origin_download(
                    context.db, entry, context.config_manager,
                )
                if outcome.get("error"):
                    result.errors += 1
                elif outcome.get("removed") or outcome.get("file_deleted"):
                    result.auto_fixed += 1
            if context.update_progress:
                context.update_progress(index + 1, len(expired))
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(context.db.get_origin_cleanup_candidates() or [])
        except Exception:
            return 0
