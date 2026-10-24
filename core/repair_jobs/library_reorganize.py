"""Library Reorganize Job — moves files to match the current file organization template.

Pre-rewrite this job had its own tag-reading + transfer-folder-walk +
template-application implementation that worked off file tags. The
classification heuristic ``is_album = (group_size > 1)`` (where
``group_size`` was the count of tracks for the same album currently
sitting in the transfer folder being scanned) misclassified album
tracks as singles whenever:

- only one track of an album lived in the transfer folder (the rest
  already moved to the library, or not yet downloaded), or
- album tags varied slightly across tracks (e.g. "Buds" vs
  "Buds (Bonus)"), splitting them into separate single-element groups.

Result: those tracks got routed through the SINGLE template and ended
up at e.g. ``Surf Curse/Surf Curse - Christine F/Surf Curse - Christine F.flac``
instead of the album destination.

GitHub issue #500 (@bafoed). Fix: delegate to the per-album planner
(``core.library_reorganize.preview_album_reorganize`` /
``reorganize_album``) the per-album reorganize modal already uses. The
planner is DB-driven — it knows the album has multiple tracks
regardless of how many currently sit in the transfer folder, so the
album-vs-single classification is structurally correct.

Apply mode delegates to ``core.reorganize_queue`` so the actual file
move + post-processing + DB update + sidecar handling all flow
through the same code path the per-album modal uses. No second move
implementation to keep in sync.

Safety design:
- Dry run mode is ON by default. Disabled by user explicitly.
- Job is disabled by default — never auto-runs unless user enables.
- Only DB-known tracks are considered. Files in transfer with no DB
  entry are handled by the separate ``orphan_file_detector`` job.
- Albums with no matching metadata source ID are skipped with a
  clear "needs enrichment first" finding rather than guessed at.
"""

import os
from typing import Optional

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.library_reorganize")


@register_job
class LibraryReorganizeJob(RepairJob):
    job_id = 'library_reorganize'
    display_name = 'Library Reorganize'
    description = 'Moves files to match the current file organization template (dry run by default)'
    help_text = (
        'Iterates every album in your library and computes the expected file path for each '
        'track using the same per-album planner the artist-detail "Reorganize" modal uses. '
        'Any track whose current path doesn\'t match the expected path gets flagged in dry-run '
        'mode or queued for a move in live mode.\n\n'
        'In live mode, moves are dispatched to the same reorganize queue the per-album modal '
        'uses — file move + post-processing + DB update + sidecar handling all flow through '
        'one code path.\n\n'
        'Albums with no matching metadata source ID are skipped — run enrichment first to '
        'populate at least one of spotify_album_id / itunes_album_id / deezer_id.\n\n'
        'Files in the transfer folder that aren\'t tracked in the database are handled by '
        'the separate Orphan File Detector job.\n\n'
        'Sidecars (.lrc, .jpg, .nfo, cover.jpg, etc) are handled by the underlying '
        'reorganize queue: per-track sidecars are deleted at the source and album-level '
        'cover art is re-downloaded fresh at the destination via the same post-processing '
        'pipeline downloads use.\n\n'
        'Once an album sits where the plan says, it is remembered as placed: later scans '
        'keep the release it was placed with and skip it until you change your naming '
        'settings or pin a different version, so new SoulSync naming rules or a source '
        'hiccup don\'t move it again.\n\n'
        'A scan that gets stopped (or a restart) picks up at the album it stopped on '
        'next time, then goes back to the top once it reaches the end. Changing dry run, '
        'your naming settings or the active server starts it over.\n\n'
        'Settings:\n'
        '- Dry Run: When enabled, only reports what would change without moving files\n'
        '- Recheck Placed Albums: Re-plan albums already placed, e.g. to pick up '
        'naming changes from a SoulSync update'
    )
    icon = 'repair-icon-reorganize'
    default_enabled = False
    default_interval_hours = 168  # Weekly — but disabled by default so won't auto-run
    default_settings = {
        'dry_run': True,
        'recheck_placed_albums': False,
    }
    auto_fix = True
    writes_library_files = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        cm = context.config_manager

        if not cm:
            logger.error("No config manager available")
            return result

        if not cm.get('file_organization.enabled', True):
            logger.info("File organization is disabled — skipping reorganize")
            if context.report_progress:
                context.report_progress(
                    phase='Skipped — file organization disabled',
                    log_line='File organization is disabled in settings',
                    log_type='skip',
                )
            return result

        dry_run = self._get_setting(context, 'dry_run', True)
        recheck_placed = bool(self._get_setting(context, 'recheck_placed_albums', False))

        # Imports kept inside scan() so the module can be imported in
        # contexts that don't have web_server's Flask app booted.
        from core.imports.paths import build_final_path_for_track
        from core.library.path_resolver import resolve_library_file_path
        from core.library_reorganize import (
            album_placement_settled,
            mark_album_placed,
            naming_fingerprint,
            preview_album_reorganize,
        )

        transfer_dir = context.transfer_folder
        download_folder = cm.get('soulseek.download_path', '') if cm else ''

        def _resolve(file_path):
            return resolve_library_file_path(
                file_path,
                transfer_folder=transfer_dir,
                download_folder=download_folder,
                config_manager=cm,
            )

        # Scope to the active media server only — the artist-detail
        # reorganize modal does the same. Multi-server users (Plex +
        # Jellyfin etc) shouldn't have this job touch the inactive
        # server's files (different paths, likely shouldn't move).
        active_server = None
        try:
            active_server = cm.get_active_media_server()
        except Exception as exc:
            logger.warning("Couldn't read active media server: %s", exc)

        album_rows = self._load_albums(context.db, active_server=active_server)
        total = len(album_rows)

        if total == 0:
            logger.info(
                "No albums in DB to reorganize (active server: %s)", active_server,
            )
            if context.report_progress:
                context.report_progress(
                    phase='No albums to scan',
                    log_line=f'No albums for server "{active_server}" in database',
                    log_type='info',
                )
            return result

        fingerprint = naming_fingerprint()

        # #1716: a stopped scan used to start over at the first album. resume
        # right after the last album a run finished, as long as it ran under
        # the same mode, naming and server (anything else is a different scan).
        scan_key = self._scan_key(dry_run, recheck_placed, fingerprint, active_server)
        start = self._resume_index(context, album_rows, scan_key)
        if start:
            logger.info(
                "Reorganize: resuming at album %d of %d ('%s')",
                start + 1, total, album_rows[start]['title'] or 'Unknown Album',
            )

        if context.report_progress:
            mode_label = 'DRY RUN' if dry_run else 'LIVE'
            resume_note = f', resuming at album {start + 1}' if start else ''
            context.report_progress(
                phase=f'Scanning {total} albums ({mode_label})...',
                log_line=f'Mode: {mode_label} — Iterating {total} albums{resume_note}',
                log_type='info',
                scanned=start, total=total,
            )

        items_to_enqueue = []
        settled = 0
        last_done = None

        def save_progress():
            # live mode queues before the checkpoint moves, so a restart never
            # skips an album whose move was only held in memory.
            self._flush_enqueue(items_to_enqueue, result)
            if last_done is not None:
                self._save_checkpoint(context, {'album_id': last_done, 'scan': scan_key})

        for i in range(start, total):
            album_row = album_rows[i]
            if i > start:
                last_done = album_rows[i - 1]['id']
                if (i - start) % 25 == 0:
                    save_progress()
            if context.check_stop():
                save_progress()
                return result
            if i % 20 == 0 and context.wait_if_paused():
                save_progress()
                return result

            album_id = album_row['id']
            album_title = album_row['title'] or 'Unknown Album'

            # #1701: placed under today's naming settings and the same release, so
            # there is nothing to re-plan. re-planning is what kept re-flagging
            # albums every time a pin or a soulsync update changed the answer.
            if not recheck_placed and album_placement_settled(album_row, fingerprint):
                settled += 1
                result.skipped += 1
                continue

            # Default to the API planner (authoritative metadata via source IDs).
            # But media-server libraries usually have NO source IDs, so that path
            # dead-ends at 'no_source_id' and the job only ever reports "needs
            # enrichment" without moving anything (#862). Reorganizing to match
            # the template only needs the metadata already on the files — so when
            # the API planner can't resolve a source, fall back to TAG mode, which
            # reads each file's embedded title/artist/album/year (the year is what
            # the user's "($year) $album" template needs). Only genuinely tag-poor
            # albums then fall through to a finding.
            reorg_metadata_source = 'api'
            try:
                preview = preview_album_reorganize(
                    album_id=str(album_id),
                    db=context.db,
                    transfer_dir=transfer_dir,
                    resolve_file_path_fn=_resolve,
                    build_final_path_fn=build_final_path_for_track,
                )
                if preview.get('status') == 'no_source_id':
                    tags_preview = preview_album_reorganize(
                        album_id=str(album_id),
                        db=context.db,
                        transfer_dir=transfer_dir,
                        resolve_file_path_fn=_resolve,
                        build_final_path_fn=build_final_path_for_track,
                        metadata_source='tags',
                    )
                    if tags_preview.get('status') == 'planned':
                        preview = tags_preview
                        reorg_metadata_source = 'tags'
            except Exception as exc:
                logger.warning(
                    "Reorganize preview failed for album %s ('%s'): %s",
                    album_id, album_title, exc,
                )
                result.errors += 1
                continue

            tracks = preview.get('tracks', [])
            result.scanned += len(tracks)

            status = preview.get('status', '')

            if status == 'source_unavailable':
                # the release it was placed with didn't answer. skip rather than
                # plan from another source and flag the whole album (#1701).
                logger.info(
                    "Reorganize: skipped '%s', its placed release didn't answer",
                    album_title,
                )
                result.skipped += 1
                continue

            if status in ('no_album', 'no_tracks'):
                # Album was deleted between the SELECT and the preview,
                # or has no tracks. Nothing to do.
                result.skipped += 1
                continue

            if status == 'no_source_id':
                # Reached only when BOTH the API planner (no source ID) AND the
                # tag-mode fallback (files missing essential title/artist/album
                # tags, or not on disk) failed — so no destination can be computed.
                # One album-level finding rather than N per-track ones (UI clutter).
                result.skipped += len(tracks) or 1
                if dry_run and context.create_finding and tracks:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='album_needs_enrichment',
                        severity='info',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=f'Cannot place: {album_title}',
                        description=(
                            f"Album '{album_title}' by {preview.get('artist', '?')} "
                            "couldn't be reorganized: it has no metadata source ID "
                            "AND its files are missing essential tags (title / artist "
                            "/ album) or aren't on disk. Re-tag the files or run "
                            "'Fix Unknown Artists', then run this job again."
                        ),
                        details={'album_id': str(album_id), 'reason': 'no_source_id'},
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                continue

            # Successful plan — count mismatched tracks
            mismatched = [
                t for t in tracks
                if t.get('matched')
                and t.get('new_path')
                and not t.get('unchanged')
                and t.get('file_exists')
            ]

            if not mismatched:
                # already where the plan says: remember it as placed so the next
                # scan keeps this release. needs a file actually seen on disk, an
                # unreachable library has no mismatches either.
                if (status == 'planned' and reorg_metadata_source == 'api'
                        and any(t.get('file_exists') for t in tracks)):
                    mark_album_placed(
                        context.db, album_id, preview.get('source'),
                        preview.get('source_album_id'), fingerprint,
                    )
                if context.report_progress and (i + 1) % 25 == 0:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        phase=f'Scanning ({i+1}/{total})...',
                    )
                continue

            if dry_run:
                # One finding per track that would move.
                for t in mismatched:
                    try:
                        if context.create_finding:
                            inserted = context.create_finding(
                                job_id=self.job_id,
                                finding_type='path_mismatch',
                                severity='info',
                                entity_type='track',
                                entity_id=str(t.get('track_id') or ''),
                                file_path=t.get('current_path') or '',
                                title=f"Would move: {os.path.basename(t.get('current_path') or '') or t.get('title', '')}",
                                description=(
                                    f"From: {t.get('current_path') or '?'}\n"
                                    f"To: {t.get('new_path') or '?'}"
                                ),
                                details={
                                    'from': t.get('current_path') or '',
                                    'to': t.get('new_path') or '',
                                    # Authoritative absolute paths (the from/to above are
                                    # display-trimmed) — the fix handler moves THESE, so it
                                    # works for libraries not rooted under transfer_path (#978).
                                    'from_abs': t.get('current_path_abs') or '',
                                    'to_abs': t.get('new_path_abs') or '',
                                    'album_id': str(album_id),
                                    'album_title': album_title,
                                    'source': preview.get('source'),
                                    'track_id': t.get('track_id'),
                                },
                            )
                            if inserted:
                                result.findings_created += 1
                            else:
                                result.findings_skipped_dedup += 1
                    except Exception as e:
                        logger.debug(
                            "Error creating path_mismatch finding for track %s: %s",
                            t.get('track_id'), e,
                        )
                        result.errors += 1
            else:
                # Apply mode: enqueue the album for the live reorganize
                # queue worker. The queue handles file move + post-process
                # + DB update + sidecar via the same code path the per-
                # album modal uses — no second move implementation.
                items_to_enqueue.append({
                    'album_id': str(album_id),
                    'album_title': album_title,
                    'artist_id': str(album_row.get('artist_id') or ''),
                    'artist_name': preview.get('artist') or album_row.get('artist_name') or 'Unknown Artist',
                    'source': preview.get('source'),
                    # Carry the mode the preview actually used so the live move
                    # matches it — otherwise the queue runner defaults to 'api'
                    # and a tag-mode-only album would fail at apply time (#862).
                    'metadata_source': reorg_metadata_source,
                })

            if context.update_progress and (i + 1) % 25 == 0:
                context.update_progress(i + 1, total)
            if context.report_progress and (i + 1) % 25 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'{"Dry run" if dry_run else "Queueing"} ({i+1}/{total})...',
                )

        self._flush_enqueue(items_to_enqueue, result)
        # the pass reached the end, so the next run starts at the top
        self._save_checkpoint(context, None)

        if context.update_progress:
            context.update_progress(total, total)

        mode_text = 'Dry run' if dry_run else 'Enqueue'
        summary = (
            f"{mode_text} complete: {result.scanned} tracks scanned across {total} albums, "
            f"{result.auto_fixed} albums queued, {result.findings_created} findings, "
            f"{result.skipped} skipped ({settled} already placed), {result.errors} errors"
        )
        logger.info(summary)
        if context.report_progress:
            context.report_progress(
                phase='Complete',
                log_line=summary,
                log_type='success',
                scanned=total, total=total,
            )

        return result

    @staticmethod
    def _flush_enqueue(items: list, result: JobResult) -> None:
        """Hand the collected albums to the reorganize queue (live mode only)
        and empty the list. Queue's tally shape: {'enqueued', 'already_queued',
        'total'}."""
        if not items:
            return
        batch = list(items)
        items.clear()
        try:
            from core.reorganize_queue import get_queue
            queue_summary = get_queue().enqueue_many(batch)
            enqueued_count = queue_summary.get('enqueued', 0)
            already_queued = queue_summary.get('already_queued', 0)
            result.auto_fixed += enqueued_count
            # Dedupe-skipped albums are tracked separately so the
            # repair-job summary doesn't double-count.
            result.skipped += already_queued
            logger.info(
                "Reorganize: enqueued %d albums (%d already in queue)",
                enqueued_count, already_queued,
            )
        except Exception as exc:
            logger.error("Failed to enqueue reorganize items: %s", exc)
            result.errors += 1

    # -- resume after a stop (#1716) ------------------------------------
    # same store the acoustid scanner and discography backfill use: a value
    # under this job's own config key. cleared when a pass reaches the end.

    @staticmethod
    def _scan_key(dry_run, recheck_placed, fingerprint, active_server) -> str:
        return f"{'dry' if dry_run else 'live'}|{int(bool(recheck_placed))}|{fingerprint}|{active_server or ''}"

    def _resume_index(self, context: JobContext, album_rows: list, scan_key: str) -> int:
        """Index of the first album this run should plan: right after the
        checkpoint album, or 0 when there's no usable checkpoint."""
        if not context.config_manager:
            return 0
        try:
            saved = context.config_manager.get(
                f'repair.jobs.{self.job_id}.scan_checkpoint', None)
        except Exception as e:  # noqa: BLE001 (a broken store means start at the top, not crash)
            logger.warning("Could not read reorganize checkpoint (starting at the top): %s", e)
            return 0
        if not isinstance(saved, dict):
            return 0
        if saved.get('scan') != scan_key:
            # say what changed, so a scan that "reset itself" can be traced
            old = str(saved.get('scan') or '').split('|')
            new = scan_key.split('|')
            changed = [name for n, name in enumerate(('dry run', 'recheck placed', 'naming settings', 'server'))
                       if (old[n] if n < len(old) else None) != new[n]]
            logger.info("Reorganize: starting at the top, %s changed since the last scan",
                        ', '.join(changed) or 'the scan')
            return 0
        album_id = saved.get('album_id')
        if album_id is None:
            return 0
        for idx, row in enumerate(album_rows):
            if str(row['id']) == str(album_id):
                return idx + 1 if idx + 1 < len(album_rows) else 0
        # the album is gone: deleted, or the media server gave it a new id (a
        # server update, or a rescan after a move). going back to the top
        # threw away the whole pass (#1857), so carry on from the first album
        # that sorts after it, the one this run would have reached next.
        gone = self._id_sort_key(album_id)
        for idx, row in enumerate(album_rows):
            if self._id_sort_key(row['id']) > gone:
                logger.info("Reorganize checkpoint album %s is gone, resuming at album %d",
                            album_id, idx + 1)
                return idx
        return 0

    @staticmethod
    def _id_sort_key(value):
        # the order sqlite's ORDER BY al.id gives: integers before text
        if isinstance(value, int) and not isinstance(value, bool):
            return (0, value, '')
        return (1, 0, str(value))

    def _save_checkpoint(self, context: JobContext, value) -> None:
        if not context.config_manager or not hasattr(context.config_manager, 'set'):
            return
        try:
            context.config_manager.set(f'repair.jobs.{self.job_id}.scan_checkpoint', value)
        except Exception as e:  # noqa: BLE001 (losing the checkpoint must not fail the scan)
            logger.warning("Could not save reorganize checkpoint: %s", e)

    def estimate_scope(self, context: JobContext) -> int:
        """Estimate is the active-server album count — matches what
        scan() iterates over."""
        active_server = None
        if context.config_manager:
            try:
                active_server = context.config_manager.get_active_media_server()
            except Exception as e:
                logger.debug("active media server lookup: %s", e)
        try:
            conn = context.db._get_connection()
            try:
                cursor = conn.cursor()
                if active_server:
                    cursor.execute(
                        "SELECT COUNT(*) FROM albums WHERE server_source = ?",
                        (active_server,),
                    )
                else:
                    cursor.execute("SELECT COUNT(*) FROM albums")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    def _load_albums(self, db, active_server: Optional[str] = None) -> list:
        """Load minimal album metadata (id, title, artist_id, artist_name)
        for albums on the active media server.

        SoulSync's DB stores rows for every configured server (Plex +
        Jellyfin + Navidrome + SoulSync standalone) distinguished by
        the ``server_source`` column. The artist-detail reorganize
        modal only sees the active server's library; this job matches
        that scope so users don't accidentally try to reorganize the
        inactive server's files (which live at different paths and
        likely shouldn't move).

        ``active_server=None`` falls back to "no filter" — used by
        legacy callers / tests that don't have a config_manager.
        """
        conn = None
        try:
            conn = db._get_connection()
            cursor = conn.cursor()
            if active_server:
                cursor.execute("""
                    SELECT al.*, ar.name AS artist_name
                    FROM albums al
                    LEFT JOIN artists ar ON ar.id = al.artist_id
                    WHERE al.server_source = ?
                    ORDER BY al.id
                """, (active_server,))
            else:
                cursor.execute("""
                    SELECT al.*, ar.name AS artist_name
                    FROM albums al
                    LEFT JOIN artists ar ON ar.id = al.artist_id
                    ORDER BY al.id
                """)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to load album list for reorganize: %s", e)
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                    pass

    def _get_setting(self, context: JobContext, key: str, default):
        """Read a job-specific setting from config."""
        if context.config_manager:
            return context.config_manager.get(
                f'repair.jobs.{self.job_id}.settings.{key}', default,
            )
        return default
