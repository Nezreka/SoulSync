"""Artist NFO Backfill Job — writes artist.nfo files for existing folders (#1449).

Companion to the automatic import-time ``artist.nfo`` writer
(``core/library/artist_nfo.py``, gated by ``library.write_artist_nfo``).
This job backfills the nfo for every artist in the database that has a
MusicBrainz artist ID but no ``artist.nfo`` on disk yet — the "repair job
or button to backfill existing folders" from the feature request.

Running the job explicitly is itself the opt-in for the backfill, so it
does not require ``library.write_artist_nfo`` to be on.

An existing ``artist.nfo`` carrying a *different* MBID is never replaced —
the user may have corrected it by hand (same guarantee as the import path).
"""

import os

from core.library.artist_nfo import ensure_artist_nfo_for_track
from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.artist_nfo_backfill")


@register_job
class ArtistNfoBackfillJob(RepairJob):
    job_id = 'artist_nfo_backfill'
    display_name = 'Artist NFO Backfill'
    description = 'Writes artist.nfo files with MusicBrainz artist IDs for Jellyfin/Kodi/Emby'
    help_text = (
        'Backfills a Kodi-format artist.nfo into each artist folder of artists that have '
        'a MusicBrainz artist ID in the database but no artist.nfo on disk yet. The nfo '
        'contains the artist name (matching the ALBUMARTIST tag exactly) and the '
        'MusicBrainz artist ID, so Jellyfin/Kodi/Emby identifies the artist correctly '
        'instead of guessing by name.\n\n'
        'Only artist folders are touched, only when no artist.nfo exists, and never '
        'when the folder already has one with a different MBID (hand-corrected files '
        'are respected). Run this once after enabling "Write artist.nfo" in Settings, '
        'or any time you want existing folders to get the file — future imports write '
        'it automatically when the setting is on.'
    )
    icon = 'repair-icon-backfill'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {}
    auto_fix = True
    # Writes NEW sidecar files into library folders (never moves/rewrites
    # media) — still a real library-file write, so flag it.
    writes_library_files = True

    def estimate_scope(self, context: JobContext) -> int:
        rows = self._candidates(context)
        return len(rows)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        rows = self._candidates(context)
        total = len(rows)
        if context.update_progress:
            context.update_progress(0, total)

        for i, (artist_id, artist_name, mbid, file_path) in enumerate(rows):
            if context.check_stop():
                logger.debug("Artist NFO backfill stopped by user request")
                return result
            if context.wait_if_paused():
                return result
            if context.update_progress:
                context.update_progress(i + 1, total)
            result.scanned += 1

            try:
                resolved = resolve_library_file_path(
                    file_path,
                    transfer_folder=context.transfer_folder,
                    config_manager=context.config_manager,
                )
                if not resolved or not os.path.isfile(resolved):
                    result.skipped += 1
                    logger.debug(
                        "Artist NFO backfill: no resolvable file for artist %s (%s)",
                        artist_name, artist_id)
                    continue

                # force=True: explicit job run is the opt-in for the backfill
                # (independent of the automatic-import setting).
                # fallback_mbid=db_mbid: files that predate MusicBrainz tag
                # embedding carry no MBID in their tags — the DB row's
                # musicbrainz_id (already queried) covers them, but only
                # when the DB artist name matches the file's ALBUMARTIST.
                # <name> still comes from the file's own ALBUMARTIST tag.
                ok, detail = ensure_artist_nfo_for_track(
                    resolved, context.config_manager, force=True,
                    fallback_mbid=mbid, fallback_name=artist_name)
                if ok:
                    result.auto_fixed += 1
                    logger.info("Artist NFO backfill: wrote %s", detail)
                elif detail == "artist.nfo already exists":
                    # The normal steady state (incl. hand-fixed different
                    # MBID) — not worth a log line per artist.
                    pass
                else:
                    logger.debug(
                        "Artist NFO backfill skipped %s: %s", artist_name, detail)
            except Exception as exc:  # noqa: BLE001 — one artist must not sink the job
                result.errors += 1
                logger.warning(
                    "Artist NFO backfill error for artist %s: %s", artist_name, exc)

        return result

    def _candidates(self, context: JobContext):
        """(artist_id, name, musicbrainz_id, one file_path) for artists that
        have an MBID. One representative track each — the nfo writer reads the
        actual tags from the file, so the name always matches the folder."""
        if context.db is None:
            return []
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.id, a.name, a.musicbrainz_id, MIN(t.file_path)
                FROM artists a
                JOIN tracks t ON t.artist_id = a.id
                WHERE a.musicbrainz_id IS NOT NULL
                  AND TRIM(a.musicbrainz_id) != ''
                  AND t.file_path IS NOT NULL
                  AND TRIM(t.file_path) != ''
                GROUP BY a.id, a.name, a.musicbrainz_id
            """)
            return cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 — old schema etc.
            logger.debug("Artist NFO backfill candidates unavailable: %s", exc)
            return []
