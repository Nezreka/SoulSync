"""Expired Download Cleaner (Boulder) — retention-based cleanup of
origin-tracked downloads.

Watchlist- and playlist-sourced downloads (recorded by the Download Origins
provenance) get a per-origin retention window. Past it, a download is proposed
for deletion UNLESS it's still in an actively-mirrored playlist / watched
artist, or you've played it more than once. By default it creates findings to
review; flip ``auto_delete`` to true for hands-off cleanup.

The expiry decision is the pure core in core.library.expired_cleanup; this job
gathers the facts (play_count via DB, active-mirror/watch protection) and
deletes via the shared helper the Download Origins delete also conceptually
uses (resolve path → remove file → drop track row → drop history row).
"""

from __future__ import annotations

import os

from core.library.expired_cleanup import RETENTION_OPTIONS, select_expired
from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.expired_download_cleaner")


def delete_origin_download(db, entry, config_manager) -> dict:
    """Delete one origin-tracked download: the file on disk (resolved through
    the shared resolver), its library track row, and the history entry. A file
    that refuses deletion keeps its history row and reports the error. Returns
    {removed, file_deleted, error}."""
    raw_path = entry.get('file_path') or ''
    file_deleted = False
    error = None
    if raw_path:
        resolved = resolve_library_file_path(raw_path, config_manager=config_manager)
        if resolved and os.path.isfile(resolved):
            try:
                os.remove(resolved)
                file_deleted = True
            except OSError as e:
                error = str(e)
        # File gone or deleted → clean up the library track row either way.
        if error is None:
            try:
                db.delete_track_by_file_path(raw_path)
            except Exception as e:
                logger.debug("expired cleanup: track row delete failed: %s", e)
    removed = 0
    if error is None:
        removed = db.delete_library_history_rows([entry['id']])
    return {'removed': removed, 'file_deleted': file_deleted, 'error': error}


@register_job
class ExpiredDownloadCleanerJob(RepairJob):
    job_id = 'expired_download_cleaner'
    display_name = 'Expired Download Cleaner'
    description = 'Deletes watchlist/playlist downloads past a retention window (keeps active + played ones)'
    help_text = (
        'Cleans up downloads that came in via the watchlist or playlist sync '
        '(tracked by Download Origins) once they pass a retention window you set '
        'per origin.\n\n'
        'A download is only ever proposed for deletion when ALL are true: it is '
        'older than its origin\'s retention, it is NOT still in a playlist you '
        'actively mirror (or an artist you still watch), and you have played it '
        'fewer than the keep-threshold (default: played more than once is kept). '
        'It only touches downloads recorded from the Download Origins feature '
        'forward — never your pre-existing or manually-added library.\n\n'
        'Dry run is ON by default: it only creates findings for you to review '
        'and delete — nothing is deleted automatically. Turn Dry run OFF for '
        'hands-off auto-cleanup.\n\n'
        'Settings:\n'
        '- Watchlist retention / Playlist retention: off, or a window\n'
        '- Keep if played at least: play count that protects a track (default 2)\n'
        '- Dry run: ON = findings only (default); OFF = delete automatically'
    )
    icon = 'repair-icon-cleanup'
    default_enabled = False
    default_interval_hours = 24
    default_settings = {
        'watchlist_retention': 'off',
        'playlist_retention': 'off',
        'keep_if_played_at_least': 2,
        'dry_run': True,
    }
    setting_options = {
        'watchlist_retention': RETENTION_OPTIONS,
        'playlist_retention': RETENTION_OPTIONS,
        'dry_run': [True, False],
    }
    auto_fix = False

    def _get_settings(self, context: JobContext) -> dict:
        merged = dict(self.default_settings)
        if context.config_manager:
            cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {}) or {}
            merged.update(cfg)
        return merged

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        wl = (settings.get('watchlist_retention') or 'off')
        pl = (settings.get('playlist_retention') or 'off')
        if wl == 'off' and pl == 'off':
            return result  # nothing configured — no-op
        try:
            min_plays = int(settings.get('keep_if_played_at_least', 2))
        except (TypeError, ValueError):
            min_plays = 2
        dry_run = bool(settings.get('dry_run', True))

        candidates = context.db.get_origin_cleanup_candidates()
        if not candidates:
            return result

        # Build the "protected" set: still-mirrored playlists + still-watched
        # artists (by name — what origin_context stores). Case-folded.
        mirrored_names, watched_names = set(), set()
        try:
            for p in (context.db.get_mirrored_playlists() or []):
                n = (p.get('name') if isinstance(p, dict) else None) or ''
                if n:
                    mirrored_names.add(n.strip().casefold())
        except Exception as e:
            logger.debug("expired cleanup: mirrored-playlist lookup failed: %s", e)
        try:
            for a in (context.db.get_watchlist_artists() or []):
                n = getattr(a, 'artist_name', None) or ''
                if n:
                    watched_names.add(n.strip().casefold())
        except Exception as e:
            logger.debug("expired cleanup: watchlist lookup failed: %s", e)

        for c in candidates:
            ctx = (c.get('origin_context') or '').strip().casefold()
            origin = (c.get('origin') or '').strip().lower()
            c['protected'] = bool(
                (origin == 'playlist' and ctx and ctx in mirrored_names) or
                (origin == 'watchlist' and ctx and ctx in watched_names))

        expired = select_expired(candidates, watchlist_retention=wl,
                                 playlist_retention=pl, min_plays=min_plays)
        result.scanned = len(candidates)
        if context.update_progress:
            context.update_progress(0, len(expired))

        for i, entry in enumerate(expired):
            if context.check_stop():
                return result
            if not dry_run:
                try:
                    res = delete_origin_download(context.db, entry, context.config_manager)
                    if res.get('removed') or res.get('file_deleted'):
                        result.auto_fixed += 1
                except Exception as e:
                    logger.error("expired auto-delete failed for %s: %s", entry.get('title'), e)
                    result.errors += 1
            elif context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='expired_download',
                        severity='info',
                        entity_type='track',
                        entity_id=str(entry.get('id')),
                        file_path=entry.get('file_path'),
                        title=f'Expired: {entry.get("title") or "Unknown"}',
                        description=(f'"{entry.get("title")}" by {entry.get("artist_name") or "Unknown"} '
                                     f'— via {entry.get("origin")} ({entry.get("origin_context") or "?"}), '
                                     f'past retention, not active, not replayed.'),
                        details={
                            'history_id': entry.get('id'),
                            'file_path': entry.get('file_path'),
                            'title': entry.get('title'),
                            'artist': entry.get('artist_name'),
                            'origin': entry.get('origin'),
                            'origin_context': entry.get('origin_context'),
                        })
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("expired finding create failed: %s", e)
                    result.errors += 1
            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, len(expired))

        logger.info("[Expired Cleaner] %d candidates, %d expired (%s)",
                    len(candidates), len(expired),
                    "findings created (dry run)" if dry_run else "auto-deleted")
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(context.db.get_origin_cleanup_candidates())
        except Exception:
            return 0
