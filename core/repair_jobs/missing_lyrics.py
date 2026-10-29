"""Missing Lyrics maintenance job (Sokhi) — the lyrics sibling of the Cover
Art Filler.

Scans the library for tracks that have no ``.lrc`` sidecar, asks LRClib
whether lyrics actually exist for them (so instrumentals/interludes that
genuinely have no lyrics are never flagged — Option A), and creates a finding
for each fixable track. Applying a finding fetches + writes the ``.lrc`` and
embeds the lyrics, reusing the same LyricsClient the import pipeline uses.

Mirrors MissingCoverArtJob's "only surface actionable findings" design.
"""

from __future__ import annotations

import hashlib
import json
import math
import os

from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from core.repair_jobs.empty_lookup_memory import EmptyLookupMemory
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.missing_lyrics")

# Consecutive "unknown" LRCLib answers (429/503/timeout) after which the
# scan stops early instead of mis-recording the rest of the library (#1879).
MAX_CONSECUTIVE_UNKNOWN = 5


def _single_str(row) -> bool:
    """True for a one-column row carrying a string — the shape the
    pending-key queries return. Anything else (a test double answering
    every query with wider rows) is not a key."""
    try:
        return len(row) == 1 and isinstance(row[0], str)
    except Exception:
        return False


def _single_key(row) -> bool:
    """Like _single_str, but an integer is also a key: an entity_id
    stored without TEXT affinity comes back as an int, and its key is
    str(value) — matching how findings store entity_id as text."""
    try:
        return len(row) == 1 and isinstance(row[0], (str, int)) and not isinstance(row[0], bool)
    except Exception:
        return False


def _has_lrc_sidecar(file_path: str) -> bool:
    """True if a .lrc (or .txt lyrics) sidecar already sits next to the file."""
    if not file_path:
        return False
    base = os.path.splitext(file_path)[0]
    return os.path.exists(base + '.lrc') or os.path.exists(base + '.txt')


@register_job
class MissingLyricsJob(RepairJob):
    job_id = 'missing_lyrics'
    display_name = 'Lyrics Filler'
    description = 'Finds tracks with no .lrc lyrics and fetches synced lyrics from LRClib'
    help_text = (
        'Scans your library for tracks that have no .lrc lyrics file next to them. '
        'For each one it asks LRClib whether lyrics actually exist — tracks with no '
        'lyrics available (instrumentals, interludes) are skipped, so only fixable '
        'tracks are surfaced.\n\n'
        'When lyrics are found, a finding is created so you can review and apply it. '
        'Applying writes a synced .lrc sidecar (or plain text if no synced version '
        'exists) and embeds the lyrics in the file — the same way the import pipeline '
        'and the Library Re-tag tool do.\n\n'
        'Tracks that already have a pending Missing Lyrics finding are not '
        'asked about again until that finding is dealt with — its saved '
        'track details (title, artist, album, file path, duration) are '
        'kept up to date without asking LRClib again.\n\n'
        'Settings:\n'
        '- Remember Empty Lookups: When on, a track LRClib definitively has no '
        'lyrics for is not asked about again until it changes (including '
        'being moved to a different file) or the recheck time passes. A '
        'lookup that could not be completed (rate limit, timeout) is never '
        'remembered. Fewer repeated requests to LRClib.\n'
        '- Recheck After Days: How long an empty answer is trusted (default 7)\n\n'
        'Requires LRClib to be enabled (Settings > Metadata Enhancement).'
    )
    icon = 'repair-icon-lyrics'
    default_enabled = False
    default_interval_hours = 48
    default_settings = {
        'remember_empty_lookups': False,
        'recheck_after_days': 7,
    }
    auto_fix = False

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        if isinstance(cfg, dict):
            merged.update(cfg)
        # A stored non-bool (e.g. the string "false" from a hand-edited
        # config) must not read as on — the toggle is bool or default.
        if not isinstance(merged.get('remember_empty_lookups'), bool):
            merged['remember_empty_lookups'] = self.default_settings['remember_empty_lookups']
        return merged

    def _load_pending_keys(self, db):
        """({entity_id}, {file_path}) of this job's PENDING findings,
        loaded once per scan (#1902).

        Mirrors the worker's dedup key in _create_finding: the entity
        arm requires entity_type='track', the file_path arm does not.
        One deliberate difference: the worker's path arm also matches
        an empty-string path, these queries exclude '' — an empty path
        identifies nothing and must never suppress a probe.
        Loaded as two flat sets instead of a per-track correlated
        EXISTS — the OR in one subquery defeats the findings indexes,
        so a big library with a long findings history would scan every
        missing_lyrics finding for every track. Any failure (a very
        old schema with no repair_findings table, a missing column, a
        locked DB) disables the pending skip for this run, logged as a
        warning — a safe degradation, never a silent one. Rows that
        are not single-column strings (a test double answering every
        query with track rows) are ignored, never mistaken for keys.
        """
        conn = None
        try:
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT entity_id FROM repair_findings
                WHERE job_id = ? AND finding_type = 'missing_lyrics'
                  AND status = 'pending' AND entity_type = 'track'
                  AND entity_id IS NOT NULL
            """, (self.job_id,))
            entity_ids = {str(r[0]) for r in cursor.fetchall()
                          if _single_key(r)}
            cursor.execute("""
                SELECT file_path FROM repair_findings
                WHERE job_id = ? AND finding_type = 'missing_lyrics'
                  AND status = 'pending'
                  AND file_path IS NOT NULL AND file_path != ''
            """, (self.job_id,))
            file_paths = {r[0] for r in cursor.fetchall()
                          if _single_str(r)}
            return entity_ids, file_paths
        except Exception as e:
            logger.warning(
                "[Lyrics Filler] pending-finding check unavailable (%s) — "
                "scanning without it; tracks with a pending finding "
                "will be re-probed this run", e)
            return set(), set()
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.debug("[Lyrics Filler] error closing pending-key connection: %s", e)

    def _finding_args(self, track_id, title, artist_name, album_title, file_path, duration_s) -> dict:
        """The create_finding kwargs for this track — one definition,
        shared by the new-finding call and the pending refresh so the
        two can never drift apart."""
        return {
            'job_id': self.job_id,
            'finding_type': 'missing_lyrics',
            'severity': 'info',
            'entity_type': 'track',
            'entity_id': str(track_id),
            'file_path': file_path,
            'title': f'Missing lyrics: {title or "Unknown"}',
            'description': f'"{title}" by {artist_name or "Unknown"} has no .lrc — lyrics found on LRClib.',
            'details': {
                'track_id': track_id,
                'track_title': title,
                'artist': artist_name,
                'album_title': album_title,
                'file_path': file_path,
                'duration': duration_s,
            },
        }

    def _refresh_pending_findings(self, context, payloads) -> None:
        """Refresh PENDING findings' saved copies of their tracks, in
        one connection and one transaction for the whole scan.

        Deliberately NOT context.create_finding: that path INSERTS a
        new finding when no pending row matches any more, so a finding
        cleared or resolved after this scan loaded its pending keys
        would be re-filed as "lyrics found on LRClib" without any
        LRClib check, uncounted and with a spurious event. An UPDATE
        limited to status='pending' can only touch rows that are
        still pending — zero rows is a fine outcome.

        Per track, ONE row is refreshed: an entity match is preferred
        over a file-path match (the worker's own dedup simply takes
        the newest matching row; preferring the entity match is this
        job's deterministic choice, since that row IS this track's
        finding). Updating EVERY row the OR key touches would merge
        two different tracks' findings into duplicates of one track.
        The refresh also writes the entity_type / entity_id /
        file_path COLUMNS, which the worker's own refresh leaves
        stale (the apply reads details first, but listings and later
        dedup read the columns). A row whose saved copy already
        matches is left untouched, so updated_at only moves when
        something actually changed.
        """
        if not payloads:
            return
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            for args in payloads:
                entity_id = args['entity_id']
                file_path = args['file_path']
                cursor.execute("""
                    SELECT id, severity, title, description, details_json,
                           entity_type, entity_id, file_path
                    FROM repair_findings
                    WHERE job_id = ? AND finding_type = 'missing_lyrics'
                      AND status = 'pending'
                      AND ((entity_type = 'track' AND entity_id = ?)
                           OR (file_path IS NOT NULL AND file_path != '' AND file_path = ?))
                    ORDER BY CASE WHEN entity_type = 'track' AND entity_id = ? THEN 0 ELSE 1 END,
                             id DESC
                    LIMIT 1
                """, (self.job_id, entity_id, file_path, entity_id))
                row = cursor.fetchone()
                if not row:
                    continue        # cleared/resolved since the keys loaded
                details_json = json.dumps(args['details'])
                current = (row[1], row[2], row[3], row[4], row[5], row[6], row[7])
                wanted = (args['severity'], args['title'], args['description'],
                          details_json, 'track', entity_id, file_path)
                if current == wanted:
                    continue        # already current — no write, no updated_at bump
                cursor.execute("""
                    UPDATE repair_findings
                    SET severity = ?, title = ?, description = ?, details_json = ?,
                        entity_type = 'track', entity_id = ?, file_path = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                """, (*wanted[:4], entity_id, file_path, row[0]))
            conn.commit()
        except Exception as e:
            logger.warning("[Lyrics Filler] refresh of pending findings failed (%s) — "
                           "their saved track copies may be stale this run", e)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.debug("[Lyrics Filler] error closing pending-refresh connection: %s", e)

    @staticmethod
    def _lookup_signature(title, artist_name, album_title, duration_s, file_path=None) -> str:
        """A short fingerprint of everything that decides what the LRClib
        lookup asks, plus the file it asks about, so a changed or
        moved/re-imported track is asked about again."""
        payload = json.dumps(
            [title, artist_name, album_title, duration_s, file_path], default=str)
        return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        # Respect the same LRClib master toggle the import pipeline uses.
        if context.config_manager and context.config_manager.get(
                'metadata_enhancement.lrclib_enabled', True) is False:
            logger.info("[Lyrics Filler] LRClib disabled in settings — skipping scan")
            return result

        try:
            from core.lyrics_client import lyrics_client
        except Exception as e:
            logger.warning("[Lyrics Filler] lyrics client unavailable: %s", e)
            return result
        if not getattr(lyrics_client, 'api', None):
            logger.info("[Lyrics Filler] LRClib API not available — skipping scan")
            return result

        settings = self._get_settings(context)

        rows = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, t.file_path, t.duration
                FROM tracks t
                LEFT JOIN albums al ON al.id = t.album_id
                LEFT JOIN artists ar ON ar.id = t.artist_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND t.title IS NOT NULL AND t.title != ''
            """)
            rows = cursor.fetchall()
        except Exception as e:
            logger.error("[Lyrics Filler] Error reading tracks: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        # Tracks with a PENDING missing_lyrics finding (#1902) are not
        # probed again: the finding is already waiting for review, so a
        # probe spends an LRClib request on a track whose outcome is
        # already sitting in the inbox. Only 'pending' suppresses — a
        # dismissed/resolved finding follows the worker's normal
        # recurrence rules if the track is probed again. Rows stay
        # unfiltered, so total/scanned, the scope estimate and the
        # empty-lookup prune keep-set all describe the whole library,
        # and the skip is counted instead of invisible.
        pending_entity_ids, pending_file_paths = self._load_pending_keys(context.db)

        # The stored file_path may not exist as-is in this process's filesystem view (docker mounts,
        # a Plex/SoulSync path mismatch). The .lrc check below MUST run against the resolved on-disk
        # path — exactly like the apply (_fix_missing_lyrics) and the Cover Art sibling already do.
        # Checking the raw DB path made every path-mapped user's tracks read as ".lrc missing" even
        # though the sidecar was right there (issue #955).
        download_folder = (context.config_manager.get('soulseek.download_path', '')
                           if context.config_manager else '') or None

        total = len(rows)
        # Tri-state probing (#1879): an LRCLib lookup can come back
        # "unknown" (429/503/timeout) — that is NOT a definitive negative
        # and must never be counted as a skip. A run of consecutive
        # unknowns means LRCLib is pushing back; stop the scan early
        # (stopped_early) instead of burning the whole library against a
        # rate limit and recording thousands of false negatives.
        unknown_count = 0
        consecutive_unknown = 0
        last_unknown_reason = ''
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking lyrics for {total} tracks...', total=total)

        # Opt-in empty-answer memory (#1902), mirroring the Cover Art
        # Filler: a track LRClib definitively has no lyrics for (its
        # 'absent' verdict — a 404 / empty search, never an 'unknown')
        # is not asked about again until the question changes or the
        # recheck window passes.
        memory = None
        # Validated whether or not the memory is on, so the value can
        # never sit raw in a log line or a later reader: a bool is not
        # a day count (float(True) == 1.0), and inf/nan/negative or
        # unparseable values fall back to the 7-day default.
        recheck_days = settings.get('recheck_after_days', 7)
        if isinstance(recheck_days, bool):
            recheck_days = 7.0
        else:
            try:
                recheck_days = float(recheck_days)
                if not math.isfinite(recheck_days) or recheck_days < 0:
                    recheck_days = 7.0
            except (TypeError, ValueError):
                recheck_days = 7.0
        if settings.get('remember_empty_lookups'):
            memory = EmptyLookupMemory.open(context.db, self.job_id, recheck_days)
        remembered_empty = 0
        pending_skipped = 0
        pending_refreshes = []
        completed_scan = False
        # Remembered answers not yet committed by the periodic commit.
        # Counted in ANSWERS, not row index: rows that skip without
        # probing (sidecar, pending, remembered, unknown) must not be
        # able to starve the periodic commit by landing on the index
        # multiples.
        remembered_since_commit = 0

        try:
            for i, row in enumerate(rows):
                if context.check_stop():
                    return result
                if i % 10 == 0 and context.wait_if_paused():
                    return result

                track_id, title, artist_name, album_title, file_path, duration = row[:6]
                result.scanned += 1

                # Resolve the stored path to the real file before checking for the sidecar (see note
                # above). Fall back to the raw path when the resolver returns nothing — the common docker
                # case where the container path already equals the stored path.
                resolved_path = resolve_library_file_path(
                    file_path,
                    transfer_folder=getattr(context, 'transfer_folder', None),
                    download_folder=download_folder,
                    config_manager=context.config_manager,
                ) or file_path

                # Already has a sidecar on disk → nothing to do.
                if _has_lrc_sidecar(resolved_path):
                    result.skipped += 1
                    continue

                # Option A: only flag tracks LRClib actually has lyrics for. An
                # instrumental returns nothing here and is silently skipped (never
                # re-flagged on future scans).
                # tracks.duration is stored in MILLISECONDS (schema) — LRClib's
                # exact-match wants SECONDS, so convert (else exact-match never
                # hits and it silently falls back to a fuzzier search).
                try:
                    duration_s = int(int(duration) / 1000) if duration else None
                    if duration_s is not None and duration_s <= 0:
                        duration_s = None
                except (TypeError, ValueError):
                    duration_s = None
                # Already has a pending finding (#1902): do not spend an
                # LRClib request on it — but DO keep the finding's saved
                # copy of the track current. The apply reads title,
                # artist, album, file_path and duration from that copy,
                # so letting it go stale (a corrected title, a
                # reorganised file) would make a later fix use the old
                # values. The refresh is a bounded UPDATE (see
                # _refresh_pending_findings), not a create_finding
                # call, so it can never file a new finding; the updates
                # are collected here and written in one batched pass
                # when the scan ends. Counted as scanned + skipped so
                # run totals still describe the whole library.
                if str(track_id) in pending_entity_ids or (file_path and file_path in pending_file_paths):
                    pending_refreshes.append(self._finding_args(
                        track_id, title, artist_name, album_title,
                        file_path, duration_s))
                    pending_skipped += 1
                    result.skipped += 1
                    if context.update_progress and (i + 1) % 10 == 0:
                        context.update_progress(i + 1, total)
                    continue
                # Remembered empty answer (#1902): this exact question
                # already came back definitively empty inside the recheck
                # window — asking again would only repeat the answer.
                # A remembered skip is not a probe, so it neither counts
                # as unknown nor touches the consecutive-unknown streak.
                lookup_key = str(track_id)
                lookup_signature = (self._lookup_signature(
                    title, artist_name, album_title, duration_s, file_path) if memory else '')
                if memory and memory.recently_empty(lookup_key, lookup_signature):
                    remembered_empty += 1
                    result.skipped += 1
                    if context.update_progress and (i + 1) % 10 == 0:
                        context.update_progress(i + 1, total)
                    continue
                # Tri-state probe (#1879): prefer the detailed check when the
                # client offers it so the unknown reason is available for the
                # log / stopped_early text; fall back to has_remote_lyrics
                # (True = found, False = absent, None = unknown).
                probe_status = 'absent'
                probe_reason = ''
                probe_kind = ''
                try:
                    check = getattr(lyrics_client, 'check_remote_lyrics', None)
                    if callable(check):
                        outcome = check(title, artist_name or '', album_title, duration_s)
                        if hasattr(outcome, 'status'):
                            probe_status = outcome.status
                            probe_reason = getattr(outcome, 'reason', '') or ''
                            probe_kind = getattr(outcome, 'error_kind', '') or ''
                            if probe_status not in ('found', 'absent', 'unknown'):
                                # An unrecognized status string is a contract
                                # we don't understand — never record it as a
                                # definitive negative.
                                probe_reason = probe_reason or f'unrecognized probe status: {probe_status!r}'
                                probe_status = 'unknown'
                        else:
                            # A check that returns a bare bool/None instead of
                            # a result object: map it like has_remote_lyrics
                            # rather than guessing 'absent' for a truthy hit.
                            probe_status = ('found' if outcome else
                                            'unknown' if outcome is None else 'absent')
                    else:
                        available = lyrics_client.has_remote_lyrics(
                            title, artist_name or '', album_title, duration_s)
                        probe_status = 'found' if available else ('unknown' if available is None else 'absent')
                except Exception as e:
                    # A raising probe is an unknown, not a negative (#1879).
                    # Warning, not debug: if the probe is raising for a
                    # programming reason (not LRCLib), that must be visible —
                    # and the consecutive-unknown abort below bounds this to
                    # a handful of lines before the scan stops. The full
                    # exception goes to the LOG; the user-facing reason (it
                    # can land in stopped_early) names only the error type.
                    logger.warning("[Lyrics Filler] availability check failed for '%s': %s", title, e)
                    probe_status = 'unknown'
                    probe_reason = f'LRCLib lookup failed ({type(e).__name__})'

                if probe_status == 'unknown':
                    unknown_count += 1
                    # Only TRANSIENT unknowns (429/5xx/timeout/network — or an
                    # unclassified one, e.g. a raising probe) count toward the
                    # early stop. A permanent query error (e.g. a 400 for one
                    # album's bad metadata) says something about that track's
                    # query, not about LRCLib's health: counting it would stop
                    # a whole-library scan with a false "LRCLib stopped
                    # answering" diagnosis over a cluster of bad rows. It does
                    # RESET the streak, though — LRCLib answered, and
                    # "consecutive" in the stop message should be literally
                    # true (same rule as the bulk-fix breaker).
                    if probe_kind == 'permanent':
                        consecutive_unknown = 0
                    else:
                        consecutive_unknown += 1
                        last_unknown_reason = probe_reason
                    logger.warning(
                        "[Lyrics Filler] LRCLib availability unknown for '%s' (%s) — not counted as missing lyrics",
                        title, probe_reason or 'lookup failed')
                    if consecutive_unknown >= MAX_CONSECUTIVE_UNKNOWN:
                        result.stopped_early = (
                            f'LRCLib stopped answering reliably after {consecutive_unknown} '
                            f'consecutive failed lookups ({last_unknown_reason or "unknown error"}). '
                            f'Scan stopped early so the remaining tracks are not recorded as '
                            f'having no lyrics; retry later.')
                        logger.warning("[Lyrics Filler] %s", result.stopped_early)
                        if context.report_progress:
                            context.report_progress(
                                scanned=i + 1, total=total,
                                log_line=result.stopped_early,
                                log_type='warning')
                        if context.update_progress:
                            context.update_progress(i + 1, total)
                        logger.info("[Lyrics Filler] %d tracks checked, %d with lyrics found, "
                                    "%d skipped, %d unknown (LRCLib unavailable)",
                                    result.scanned, result.findings_created, result.skipped,
                                    unknown_count)
                        return result
                    if context.update_progress and (i + 1) % 10 == 0:
                        context.update_progress(i + 1, total)
                    continue

                # A definitive answer (found OR absent) breaks the unknown
                # run. The streak counts consecutive PROBES: sidecar,
                # pending-finding and remembered-empty skips are not
                # probes — in particular a remembered answer says nothing
                # about whether LRClib is healthy right now, so it
                # deliberately does not break the streak.
                consecutive_unknown = 0

                if probe_status != 'found':
                    # Only a definitive 'absent' is remembered (#1902) —
                    # 'unknown' already continued above and is never
                    # passed here, so a rate limit or timeout can never
                    # be recorded as "no lyrics".
                    if memory:
                        memory.remember(lookup_key, lookup_signature)
                        # Commit HERE, on the path that creates entries:
                        # absent tracks continue, so a commit placed at
                        # the loop bottom (the found path) would almost
                        # never run and a killed scan would lose every
                        # remembered answer of the run. Every 25
                        # remembered answers, counted — not every 25
                        # rows (see remembered_since_commit).
                        remembered_since_commit += 1
                        if remembered_since_commit >= 25:
                            memory.commit()
                            remembered_since_commit = 0
                    result.skipped += 1
                    if context.update_progress and (i + 1) % 10 == 0:
                        context.update_progress(i + 1, total)
                    continue

                if memory:
                    # Not counted in remembered_since_commit: forget()
                    # is a no-op when the track was never remembered,
                    # and the counter's job is bounding how many
                    # remembered answers a hard kill could lose.
                    memory.forget(lookup_key)

                if context.report_progress:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        log_line=f'Found lyrics: {title} — {artist_name or "Unknown"}',
                        log_type='success')

                if context.create_finding:
                    try:
                        inserted = context.create_finding(**self._finding_args(
                            track_id, title, artist_name, album_title,
                            file_path, duration_s))
                        if inserted:
                            result.findings_created += 1
                        else:
                            result.findings_skipped_dedup += 1
                    except Exception as e:
                        logger.debug("[Lyrics Filler] create finding failed for track %s: %s", track_id, e)
                        result.errors += 1

                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)

            completed_scan = True
        finally:
            # Batched pending-finding refresh — one connection, one
            # transaction, on every exit path (a stopped scan still
            # refreshes the pending tracks it did reach).
            self._refresh_pending_findings(context, pending_refreshes)
            if memory:
                if completed_scan:
                    # Guarded on purpose: a prune failure must never
                    # stop close() from committing this run's entries,
                    # and must never replace the scan's own result.
                    try:
                        memory.prune(str(row[0]) for row in rows)
                    except Exception as e:
                        logger.debug("[Lyrics Filler] empty-lookup prune failed: %s", e)
                memory.close()
            # In the finally so early exits (stop, pause, unknown
            # abort) log their skip counts too, not just full scans.
            if pending_skipped:
                logger.info("[Lyrics Filler] %d tracks not probed — a missing_lyrics "
                            "finding is already pending for them", pending_skipped)
            if memory and remembered_empty:
                logger.info("[Lyrics Filler] %d tracks not asked about again (empty LRClib "
                            "answer within the last %g days)", remembered_empty, recheck_days)

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("[Lyrics Filler] %d tracks checked, %d with lyrics found, %d skipped, "
                    "%d unknown (LRCLib unavailable)",
                    result.scanned, result.findings_created, result.skipped, unknown_count)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM tracks
                WHERE file_path IS NOT NULL AND file_path != ''
                  AND title IS NOT NULL AND title != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
