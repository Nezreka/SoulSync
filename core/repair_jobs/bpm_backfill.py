"""BPM Backfill Job — finds tracks missing BPM and fills it.

Issue #1476: lib2_tracks.bpm is only written at download time. Tracks already in
the library never get one. This job backfills BPM for existing tracks:

1. The file's own BPM tag — free, and what the file already says (#1939)
2. Beatport, by the track's isrc — the label's own BPM, the reference for
   electronic music (#1939)
3. Deezer API (via deezer_id) — fast, no local CPU cost
4. Local librosa analysis fallback — no API calls, works offline

A BPM the file already carries is taken straight into SoulSync: nothing about
the file changes, it only stops being invisible. Every other source is
findings-first like metadata_gap_filler: reported for review, not auto-written.
The existing _fix_metadata_gap handler applies the bpm field.
"""

import os

from core.metadata_service import get_client_for_source
from core.repair_jobs import register_job
from core.library2.maintenance_subjects import active_file_subjects, subject_details
from core.library2.paths import resolve_lib2_path
from core.repair_jobs.base import (
    JobContext, JobResult, RepairJob, drop_hand_tagged, scoped_file_subjects,
)
from core.repair_jobs.metadata_gap_filler import _gap_cursor
from utils.logging_config import get_logger

logger = get_logger("repair_job.bpm_backfill")


@register_job
class BpmBackfillJob(RepairJob):
    job_id = 'bpm_backfill'
    display_name = 'BPM Backfill'
    description = 'Finds tracks missing BPM and fills it from the file, Beatport, Deezer or local audio analysis'
    help_text = (
        'Searches for tracks in your library that are missing BPM (tempo) values. '
        'BPM is only written when a track is downloaded, so older library tracks never get one.\n\n'
        'For each track missing BPM, the job tries:\n'
        '1. The file\'s own BPM tag — free, taken into SoulSync straight away\n'
        '2. Beatport (by the track\'s ISRC) — the label\'s own BPM, best for electronic music\n'
        '3. Deezer API (via the track\'s Deezer ID) — fast, accurate\n'
        '4. Local audio analysis (librosa) — no API calls, works on any file\n\n'
        'A BPM found in the file is saved in SoulSync right away, since the file already has it. '
        'Anything else is reported as a finding for your review. Nothing changes until you '
        'apply a finding: then the BPM is saved in SoulSync and written into the file\'s '
        'tags (unless BPM tags are turned off for that source in the tag settings).\n\n'
        'Settings:\n'
        '- Use file tags: Take the BPM a file already carries\n'
        '- Use Beatport: Look up BPM on Beatport by ISRC (searches only tracks that look '
        'electronic, unless Beatport is set to search every track)\n'
        '- Use Deezer: Look up BPM via the Deezer API\n'
        '- Use local analysis: Fall back to analyzing the audio file locally'
    )
    icon = 'repair-icon-metadata'  # Reuse existing icon; no bpm-specific CSS class exists
    default_enabled = False
    default_interval_hours = 168  # Weekly — backfill is a slow, one-time-ish task
    default_settings = {
        'use_file_tags': True,
        'use_beatport': True,
        'use_deezer': True,
        'use_local_analysis': True,
    }
    auto_fix = False
    supports_file_scope = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        use_file_tags = settings.get('use_file_tags', True)
        use_beatport = settings.get('use_beatport', True)
        use_deezer = settings.get('use_deezer', True)
        use_local = settings.get('use_local_analysis', True)

        if not (use_file_tags or use_beatport or use_deezer or use_local):
            logger.info("BPM backfill: every source disabled, nothing to do")
            return result

        cfg = context.config_manager
        beatport_on = use_beatport and (not cfg or cfg.get('beatport.embed_tags', True) is not False)
        beatport_search_all = bool(cfg and cfg.get('beatport.search_all_tracks', False) is True)

        # Probe backends upfront so we can warn if neither is available.
        deezer_client = None
        if use_deezer:
            try:
                deezer_client = get_client_for_source('deezer')
            except Exception as e:
                logger.debug("Could not get Deezer client: %s", e)

        local_available = False
        if use_local:
            try:
                from core.sample.analyze import analyze_track  # noqa: F401
                local_available = True
            except ImportError:
                logger.warning("BPM backfill: librosa not available, local analysis disabled")

        if not (use_file_tags or beatport_on or deezer_client or local_available):
            logger.warning(
                "BPM backfill: no usable backend (Deezer client unavailable, "
                "librosa not installed). Nothing to do."
            )
            return result

        # Tracks missing BPM, one subject per track (its primary file when it
        # has several). A batch per run, continuing where the last one stopped:
        # a track no source can answer would otherwise hold its place in the
        # first batch on every run and the rest of the library never come up.
        try:
            missing = _missing_bpm_subjects(context)
        except Exception as e:
            logger.error("Error fetching tracks missing BPM: %s", e, exc_info=True)
            result.errors += 1
            return result
        cursor = _gap_cursor(context, _BPM_CURSOR_KEY)
        tracks = [s for s in missing if int(s['track_id']) > cursor][:_BPM_BATCH]
        if not tracks:
            tracks = missing[:_BPM_BATCH]

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Found %d tracks missing BPM", total)
        unreachable = 0

        if context.report_progress:
            context.report_progress(phase=f'Finding BPM for {total} tracks...', total=total)

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            track_id = row['track_id']
            title, artist_name, album_title = row.get('title'), row.get('artist_name'), row.get('album_title')
            file_path = row.get('path')
            deezer_id = row.get('deezer_id')

            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Finding BPM {i + 1} / {total}',
                    log_line=f'Checking: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            bpm_value = None
            bpm_source = None
            local_path = None
            if file_path:
                local_path = file_path if os.path.exists(file_path) else resolve_lib2_path(
                    file_path, config_manager=context.config_manager)

            # 1. the file's own tag: what it already says, taken straight in
            if use_file_tags and local_path:
                adopted = _adopt_file_tag_bpm(context, row, local_path)
                if adopted is not None:
                    result.auto_fixed += 1
                    if context.report_progress:
                        context.report_progress(
                            log_line=f'Took BPM {adopted} from the file for {title or "Unknown"}',
                            log_type='success')
                    continue

            # 2. beatport, for the exact recording (by isrc)
            if beatport_on and row.get('isrc'):
                bpm_value = _beatport_bpm(row, beatport_search_all)
                if bpm_value is not None:
                    bpm_source = 'beatport'

            # 3. Deezer (same lookup as the download path in
            # core/metadata/source.py:563 — top-level 'bpm' key)
            if bpm_value is None and deezer_client and deezer_id:
                try:
                    track_data = deezer_client.get_track_details(deezer_id)
                    if track_data:
                        bpm_val = track_data.get('bpm')
                        if bpm_val and float(bpm_val) > 0:
                            bpm_value = round(float(bpm_val), 1)
                            bpm_source = 'deezer'
                        else:
                            logger.info(
                                "Deezer has no BPM for track %s (deezer_id=%s)",
                                track_id, deezer_id,
                            )
                    else:
                        logger.warning(
                            "Deezer returned no data for track %s (deezer_id=%s)",
                            track_id, deezer_id,
                        )
                except Exception as e:
                    logger.warning("Deezer BPM lookup failed for track %s: %s", track_id, e)
            elif bpm_value is None and not deezer_client:
                logger.debug("Deezer client unavailable, skipping Deezer BPM lookup for track %s", track_id)
            elif bpm_value is None and not deezer_id:
                logger.debug("Track %s has no deezer_id, skipping Deezer BPM lookup", track_id)

            # 4. Fall back to local analysis
            if bpm_value is None and local_available and file_path:
                if not local_path:
                    # upstream 1db9c12bf: counted and reported below, never silent
                    unreachable += 1
                else:
                    try:
                        from core.sample.isolated import analyze_track_isolated
                        analysis = analyze_track_isolated(local_path)
                        bpm_val = analysis.get('bpm')
                        if bpm_val and float(bpm_val) > 0:
                            bpm_value = round(float(bpm_val), 1)
                            bpm_source = 'local'
                        else:
                            # Analysis ran but couldn't detect a confident BPM
                            # (common on classical/ambient). Distinct from a
                            # crash — the file was readable, just no beat.
                            logger.info(
                                "Local BPM analysis found no confident BPM for track %s",
                                track_id,
                            )
                    except Exception as e:
                        result.errors += 1
                        logger.warning("Local BPM analysis failed for track %s: %s", track_id, e)
                        if context.report_progress:
                            context.report_progress(
                                log_line=f'Could not analyze {title or "Unknown"}: {e}',
                                log_type='error'
                            )

            # Create finding for user review
            if bpm_value:
                if context.report_progress:
                    context.report_progress(
                        log_line=f'Found BPM {bpm_value} ({bpm_source}) for {title or "Unknown"}',
                        log_type='success'
                    )
                if context.create_finding:
                    try:
                        details = {
                            'track_id': f'lib2:{track_id}',
                            'title': title,
                            'artist': artist_name,
                            'album': album_title,
                            'bpm': bpm_value,
                            'bpm_source': bpm_source,
                            'found_fields': {'bpm': bpm_value},
                            'album_thumb_url': row.get('album_image') or None,
                            'artist_thumb_url': row.get('artist_image') or None,
                            'artist_id': row.get('artist_id'),
                        }
                        details.update(subject_details(row))
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type='bpm_backfill',
                            severity='info',
                            entity_type='track',
                            entity_id=f'lib2:{track_id}',
                            file_path=file_path,
                            title=f'Missing BPM: {title or "Unknown"}',
                            description=(
                                f'Track "{title}" by {artist_name or "Unknown"} is missing BPM. '
                                f'Found {bpm_value} BPM via {bpm_source}.'
                            ),
                            details=details,
                        )
                        if inserted:
                            result.findings_created += 1
                        else:
                            result.findings_skipped_dedup += 1
                    except Exception as e:
                        logger.debug("Error creating BPM finding for track %s: %s", track_id, e)
                        result.errors += 1
            else:
                result.skipped += 1

            # Rate limit API calls; local analysis is CPU-bound so no sleep needed
            if bpm_source == 'deezer':
                if context.sleep_or_stop(0.5):
                    return result

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        if tracks:
            _gap_cursor(context, _BPM_CURSOR_KEY, int(tracks[-1]['track_id']))

        if unreachable:
            logger.warning("BPM backfill: %d track files couldn't be found on disk", unreachable)
            if context.report_progress:
                context.report_progress(
                    log_line=f"{unreachable} track files couldn't be found from here, so they weren't analyzed. "
                             "Check that SoulSync can see your music folder.",
                    log_type='error'
                )

        logger.info("BPM backfill scan: %d tracks checked, %d BPM found, %d skipped",
                    result.scanned, result.findings_created, result.skipped)
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
            return len(_missing_bpm_subjects(context))
        except Exception:
            return 0


_BPM_BATCH = 500
_BPM_CURSOR_KEY = 'repair.bpm_backfill.cursor'


def _adopt_file_tag_bpm(context: JobContext, row: dict, local_path: str):
    """the bpm the file carries, saved into SoulSync, or None (#1939)"""
    try:
        from core.tag_writer import read_file_tags
        tags = read_file_tags(local_path) or {}
    except Exception as e:  # noqa: BLE001 - an unreadable file just goes on to the next source
        logger.debug("BPM backfill: could not read tags of %s: %s", local_path, e)
        return None
    from core.library2.tag_cache import plausible_bpm
    bpm = plausible_bpm(tags.get('bpm'))
    if bpm is None:
        return None
    bpm = round(bpm, 1)
    from contextlib import closing
    with closing(context.db._get_connection()) as conn:
        cur = conn.execute(
            "UPDATE lib2_tracks SET bpm=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND (bpm IS NULL OR bpm=0)", (bpm, int(row['track_id'])))
        conn.commit()
    if not cur.rowcount:
        return None
    if context.report_change:
        context.report_change(finding_type='bpm_backfill', action='took_file_bpm',
                              entity_type='track', entity_id=f"lib2:{row['track_id']}",
                              file_path=row.get('path'), details={'bpm': bpm, 'bpm_source': 'file'})
    return bpm


def _beatport_bpm(row: dict, search_all: bool):
    """beatport's bpm for this exact recording, or None. a beatport search
    (rate limited) only for a track that looks electronic, like the tagger"""
    try:
        import json
        from core.beatport.enrich import lookup, looks_electronic
        genres = []
        for key in ('album_genres', 'artist_genres'):
            raw = row.get(key)
            try:
                genres += list(json.loads(raw)) if isinstance(raw, str) else list(raw or [])
            except (TypeError, ValueError):
                genres.append(raw)
        found = lookup(row.get('isrc'), row.get('artist_name') or '', row.get('title') or '',
                       allow_search=search_all or looks_electronic(genres))
    except Exception as e:  # noqa: BLE001 - the next source still runs
        logger.debug("Beatport BPM lookup failed for track %s: %s", row.get('track_id'), e)
        return None
    from core.library2.tag_cache import plausible_bpm
    bpm = plausible_bpm((found or {}).get('bpm'))
    return round(bpm, 1) if bpm is not None else None


def _missing_bpm_subjects(context: JobContext) -> list:
    """Library-v2 tracks without a BPM, by track id, hand-tagged files left out."""
    by_track: dict = {}
    for subject in drop_hand_tagged(context, scoped_file_subjects(
            context, active_file_subjects(context.db, context.config_manager))):
        if not str(subject.get('title') or '').strip():
            continue
        try:
            if subject.get('bpm') and float(subject['bpm']) > 0:
                continue
        except (TypeError, ValueError):
            pass
        track_id = int(subject['track_id'])
        if track_id in by_track and not subject.get('is_primary'):
            continue
        by_track[track_id] = subject
    return [by_track[k] for k in sorted(by_track)]
