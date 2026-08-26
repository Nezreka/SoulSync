"""AcoustID Scanner Job — fingerprints library tracks to detect wrong downloads.

Scans the entire library (not just Transfer) by resolving DB file paths to
actual files on disk. Creates actionable findings that can be fixed:
  - 'retag': Update DB metadata to match what the file actually is
  - 'redownload': Add the expected track to wishlist and delete the wrong file
  - 'delete': Remove the wrong file and its DB record
"""

import os
import re
from core.library2.maintenance_subjects import active_file_subjects
from core.library2.maintenance_subjects import subject_details
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, scoped_file_subjects
from utils.logging_config import get_logger
from core.matching.audio_verification import fingerprint_is_ambiguous, Decision
from core.matching.acoustid_candidates import duration_mismatches_strongly

logger = get_logger("repair_job.acoustid")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


def _import_recording_mbids(subject: Dict[str, Any]) -> frozenset:
    """The AcoustID recordings the import-time check judged this file against.

    Written by the download pipeline into ``pipeline_result_json`` (see
    ``core/library2/autolink._pipeline_result_json``). Empty for files that
    predate it or that SoulSync never downloaded — those simply get no
    identity contract and the scan decides on its own, as it always did.
    """
    import json

    raw = subject.get("pipeline_result_json")
    if not raw:
        return frozenset()
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return frozenset()
    if not isinstance(parsed, dict):
        return frozenset()
    values = parsed.get("acoustid_recording_mbids") or []
    if not isinstance(values, (list, tuple, set)):
        return frozenset()
    return frozenset(str(v) for v in values if v)


@register_job
class AcoustIDScannerJob(RepairJob):
    job_id = 'acoustid_scanner'
    display_name = 'AcoustID Scanner'
    description = 'Fingerprints library tracks to detect wrong downloads'
    help_text = (
        'Scans your music library by fingerprinting audio files and comparing '
        'them against the AcoustID database. Detects cases where the wrong song '
        'was downloaded — even if the filename and tags look correct.\n\n'
        'When a mismatch is found, you can:\n'
        '• Retag — update the DB record to match the actual audio content\n'
        '• Redownload — add the correct track to your wishlist and remove the wrong file\n'
        '• Delete — remove the wrong file entirely\n\n'
        'The job processes tracks in batches with checkpointing so it resumes '
        'where it left off across runs. Requires an AcoustID API key (Settings).\n\n'
        'Settings:\n'
        '- Fingerprint Threshold: Minimum AcoustID match confidence (0.0–1.0)\n'
        '- Title Similarity: How closely the identified title must match\n'
        '- Artist Similarity: How closely the identified artist must match\n'
        '- Batch Size: Tracks per scan run (checkpoint saved between batches)'
    )
    icon = 'repair-icon-acoustid'
    default_enabled = True
    default_interval_hours = 24
    default_settings = {
        'fingerprint_threshold': 0.80,
        'title_similarity': 0.70,
        'artist_similarity': 0.60,
        'batch_size': 200,
    }
    auto_fix = False  # User chooses fix action per finding
    supports_file_scope = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        fp_threshold = settings.get('fingerprint_threshold', 0.80)
        title_threshold = settings.get('title_similarity', 0.70)
        artist_threshold = settings.get('artist_similarity', 0.60)
        batch_size = settings.get('batch_size', 200)

        # Get AcoustID client
        acoustid_client = context.acoustid_client
        if not acoustid_client:
            try:
                from core.acoustid_client import AcoustIDClient
                acoustid_client = AcoustIDClient()
            except Exception as e:
                logger.warning("AcoustID client not available: %s", e)
                return result

        # Is the client usable AT ALL? `verify_audio_file` probes this per file
        # and answers SKIP when it is not — which for a scan means stamping
        # every row in the library 'skip' ("checked, no claim") and reporting a
        # clean run, wiping whatever earlier scans had concluded. A disabled
        # integration, a missing key or no chromaprint is a property of the RUN,
        # not a verdict about a file, so it belongs here, once, as a refusal to
        # start.
        probe_available = getattr(acoustid_client, 'is_available', None)
        if callable(probe_available):
            try:
                available, reason = probe_available()
            except Exception as e:  # noqa: BLE001 — a broken probe is not a verdict
                available, reason = False, f'availability check failed: {e}'
            if not available:
                logger.warning("AcoustID scan not started: %s", reason)
                if context.report_progress:
                    context.report_progress(
                        log_line=f'AcoustID unavailable — nothing scanned: {reason}',
                        log_type='error')
                result.errors += 1
                return result

        # Load all library tracks from DB with their file paths
        db_tracks = self._load_db_tracks(context)
        if not db_tracks:
            logger.info("No library tracks with file paths found")
            return result

        # Read checkpoint (last processed track ID) to resume from
        checkpoint_id = None
        if context.config_manager:
            checkpoint_id = context.config_manager.get(
                f'repair.jobs.{self.job_id}.checkpoint_id', None
            )
        if checkpoint_id is not None:
            checkpoint_id = str(checkpoint_id)

        # Build ordered list of (track_id, info) sorted by ID for deterministic order
        track_list = sorted(db_tracks.items(), key=lambda x: str(x[0]))

        # Skip past checkpoint if resuming
        if checkpoint_id is not None:
            original_len = len(track_list)
            track_list = [(tid, info) for tid, info in track_list if str(tid) > checkpoint_id]
            if len(track_list) < original_len:
                logger.info("Resuming AcoustID scan from checkpoint ID %s (%d tracks remaining)",
                            checkpoint_id, len(track_list))

        total = len(track_list)
        if context.report_progress:
            context.report_progress(phase=f'Scanning {total} library tracks...', total=total)
        if context.update_progress:
            context.update_progress(0, total)

        batch_count = 0
        for i, (track_id, track_info) in enumerate(track_list):
            if context.check_stop():
                self._save_checkpoint_id(context, track_id)
                return result
            if i % 10 == 0 and context.wait_if_paused():
                self._save_checkpoint_id(context, track_id)
                return result

            # Resolve the DB path to an actual file on disk
            file_path = track_info.get('file_path', '')
            if track_info.get('lib2_file_id'):
                # Library-v2 files carry v2 paths; the legacy resolver's
                # music_paths/Plex heuristics do not apply to them.
                if os.path.exists(file_path):
                    resolved = file_path
                else:
                    from core.library2.paths import resolve_lib2_path
                    resolved = resolve_lib2_path(
                        file_path, config_manager=context.config_manager)
            else:
                resolved = self._resolve_path(file_path, context)
            if not resolved:
                result.skipped += 1
                continue
            try:
                from core.library2.manual_skips import check_is_skipped
                conn = context.db._get_connection()
                try:
                    skip_acoustid = check_is_skipped(
                        conn,
                        (file_path, resolved),
                        ("acoustid",),
                        profile_id=1,
                    )
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("manual-skip lookup failed for %s: %s", file_path, exc)
                skip_acoustid = False
            if skip_acoustid:
                result.skipped += 1
                if context.report_progress:
                    context.report_progress(
                        log_line=f"Skipped (manual AcoustID override): {os.path.basename(resolved)}",
                        log_type="skip",
                    )
                continue

            result.scanned += 1
            batch_count += 1

            fname = os.path.basename(resolved)
            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Fingerprinting {i + 1} / {total}',
                    log_line=f'Scanning: {fname}',
                    log_type='info'
                )

            try:
                self._scan_file(
                    resolved, track_id, track_info, acoustid_client, context, result,
                    fp_threshold, title_threshold, artist_threshold
                )
            except Exception as e:
                logger.debug("Error scanning %s: %s", fname, e)
                result.errors += 1

            # Rate limit: pause between batches to avoid hammering AcoustID API
            if batch_count >= batch_size:
                batch_count = 0
                self._save_checkpoint_id(context, track_id)
                if context.sleep_or_stop(2):
                    return result

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        # Clear checkpoint on full completion
        self._save_checkpoint_id(context, None)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("AcoustID scan: %d scanned, %d skipped, %d mismatches, %d errors",
                     result.scanned, result.skipped, result.findings_created, result.errors)
        return result

    def _scan_file(self, fpath, track_id, expected, acoustid_client, context, result,
                   fp_threshold, title_threshold, artist_threshold):
        """Fingerprint a single file and check for mismatches."""
        fname = os.path.basename(fpath)

        # ONE verification path. Everything from "is AcoustID usable" through
        # the lookup, the confidence floor, the MusicBrainz enrichment of
        # title-less recordings, the lazy alias resolution and the final
        # decision lives in `AcoustIDVerification.verify_audio_file` — the same
        # call the download makes. The scan used to repeat all five steps
        # around a shared decision core, which is precisely where the two drifted
        # apart: the scan never enriched its recordings, and read its expected
        # values from somewhere else. What stays here is what the scan alone
        # has to answer: which files to look at, and what to do with a verdict.
        #
        # The expected artist is the one input the scan must supply itself, so
        # it is resolved before the call rather than after it.
        expected_artist = self._expected_artist(fpath, expected, fname)

        # A human decision is checked BEFORE fingerprinting — the answer cannot
        # change and the API call would be spent for nothing.
        file_verif_status = None
        try:
            from core.tag_writer import read_file_tags as _rft
            file_verif_status = (_rft(fpath) or {}).get('verification_status')
        except Exception:  # noqa: S110 — verification tag is optional context; None is fine
            pass
        # The tag and the catalogue column are two records of ONE fact, and the
        # tag is the losable one: an import write that did not stick, a copy, a
        # retag. Reading the standing from the tag alone while WRITING to the
        # column is what let a scan demote a verified file to 'unverified' — it
        # saw an untagged file and applied the rule for one. Either record
        # saying so is the file standing verified.
        verif_status = file_verif_status or expected.get('db_verification_status')
        if verif_status == 'human_verified':
            # The user explicitly confirmed this file via the review queue —
            # never second-guess a human decision.
            if context.report_progress:
                context.report_progress(
                    log_line=f'Skipped (human-verified): {fname}', log_type='skip')
            return

        probe: Dict[str, Any] = {}
        try:
            from core.acoustid_verification import (
                AcoustIDVerification, VerificationResult,
            )

            verifier = AcoustIDVerification()
            if acoustid_client is not None:
                verifier.acoustid_client = acoustid_client
            verdict, message = verifier.verify_audio_file(
                fpath, expected['title'], expected_artist, probe,
                min_score=fp_threshold,
            )
        except Exception as e:
            logger.debug("Fingerprint failed for %s: %s", fname, e)
            result.errors += 1
            if context.report_progress:
                context.report_progress(log_line=f'Error: {fname} — {e}', log_type='error')
            return

        if verdict == VerificationResult.ERROR:
            # Broken, not answered. Leave the column alone so the run does not
            # paint an unchecked library as checked.
            if context.report_progress:
                context.report_progress(
                    log_line=f'Error: {fname} — {message}', log_type='error')
            result.errors += 1
            return

        outcome = probe.get('_acoustid_decision')
        recordings = probe.get('_acoustid_recordings') or []
        best_score = probe.get('_acoustid_best_score') or 0.0
        if outcome is None:
            # The verifier never reached a judgement — no match, or a
            # fingerprint too weak to trust. An inconclusive check is still a
            # check, and recording nothing is what left files reading "Not
            # scanned" after a completed run. 'skip' is the schema's word for
            # "checked, no claim"; it never touches the file's verification
            # standing, so nothing gets demoted for being obscure.
            if context.report_progress:
                context.report_progress(log_line=f'No match: {fname}', log_type='skip')
            self._persist_inconclusive(
                context, track_id, fpath, expected, message)
            return
        if not any(r.get('title') for r in recordings):
            self._persist_inconclusive(
                context, track_id, fpath, expected,
                'the matched AcoustID recording has no title to compare against')
            return

        # Fingerprint-collision guard: when the match's length is wildly
        # different from the file, the fingerprint hit is a hash collision (the
        # 17-min mashup → 5-min track case), not a real match — skip BEFORE any
        # title/artist/version analysis so it can't surface as a false finding.
        #
        # Judged across ALL top-scoring recordings, not just `recordings[0]`.
        # One AcoustID result carries many recordings sharing its score, in
        # MusicBrainz order — so [0] is an arbitrary pick among equals (the same
        # root cause as #1132). If [0] happened to be a 12-minute live version
        # linked to the same entry, a perfectly ordinary track was skipped with
        # no verification at all. It is only a collision when NO plausible
        # candidate has a compatible length.
        try:
            file_duration_s = (expected.get('duration_ms') or 0) / 1000.0
        except Exception:
            file_duration_s = 0.0
        _recs = recordings
        _scored = [r for r in _recs if r.get('score') is not None]
        _top = max((r['score'] for r in _scored), default=None)
        _judge = [r for r in _scored if r['score'] >= _top] if _top is not None else _recs
        _durations = [
            (r.get('duration') or r.get('length')) for r in _judge
        ]
        _known = [d for d in _durations if d]
        all_mismatch = bool(_known) and all(
            duration_mismatches_strongly(file_duration_s, d) for d in _known)
        if file_duration_s and all_mismatch:
            if context.report_progress:
                context.report_progress(
                    log_line=(f'Skipped (duration mismatch suggests fingerprint '
                              f'collision): {fname}'),
                    log_type='skip')
            return

        # What the download verified, the scan may not overturn for free.
        # Both paths share this decision core, so a flip can only come from
        # their inputs — and they read the expected title/artist from
        # different places (provider payload vs catalogue row), which for
        # cross-script metadata are different strings for the same thing.
        # Recording MBIDs are not: the same audio fingerprints to the same set
        # every time. So if this file still identifies as the recording it
        # identified when it was verified, the scan has learned nothing new
        # and has no standing to call it a wrong download. A fingerprint that
        # lands on a genuinely different recording IS new information and
        # still fails below.
        # Either record of the verdict will do. The tag is the one the import
        # writes onto the file; the catalogue column is the one that survives a
        # later retag or a copy that dropped the tag. They say the same thing,
        # and requiring both would make the contract depend on which of the two
        # a given file still happens to carry.
        _stands_verified = verif_status == 'verified'
        import_mbids = expected.get('import_recording_mbids') or frozenset()
        current_mbids = {
            str(m) for m in (probe.get('_acoustid_recording_mbids') or []) if m
        } or {
            str(r.get('mbid')) for r in recordings if r.get('mbid')
        }
        same_recording_as_import = bool(import_mbids and (import_mbids & current_mbids))
        if outcome.decision == Decision.FAIL and _stands_verified:
            if same_recording_as_import:
                if context.report_progress:
                    context.report_progress(
                        log_line=(f'OK (same recording as at import): {fname}'),
                        log_type='ok')
                self._persist_inconclusive(
                    context, track_id, fpath, expected,
                    'the fingerprint still identifies the recording this file '
                    'was verified against at import')
                return

        # Persist the scan outcome so it feeds the same review pipeline as
        # import-time verification: PASS backfills 'verified' on untagged or
        # previously-unverified files; SKIP (ambiguous / cross-script / no
        # hard confirmation) marks untagged files 'unverified' so they surface
        # in the Downloads-page review queue. force_imported is never blessed
        # here (normalize() strips version words, so an instrumental can PASS
        # the title check) and 'verified' is never downgraded by a SKIP (the
        # import-time check ran with richer candidate metadata). FAIL keeps
        # the finding flow below.
        # Judged on `verif_status` — the standing across BOTH records — while
        # `new_status` still starts from it so a file whose tag went missing
        # gets it written back rather than replaced with the scan's guess.
        new_status = verif_status
        if outcome.decision == Decision.PASS and verif_status in (None, '', 'unverified'):
            new_status = 'verified'
        elif outcome.decision == Decision.SKIP and not verif_status:
            new_status = 'unverified'
        elif (outcome.decision == Decision.SKIP and verif_status == 'unverified'
                and same_recording_as_import):
            # Healing, for the files an earlier build of this scanner demoted.
            # It read the standing from the file TAG and wrote to the catalogue
            # COLUMN, so a file whose tag had gone missing was recorded
            # 'unverified' however the import had judged it — and nothing since
            # gives it back, because a SKIP is by design not allowed to move the
            # standing. The one thing that settles it is identity: the
            # fingerprint still lands on the recording the import checked this
            # file against, and only files the import let through are here at
            # all. That is the same evidence the FAIL guard above trusts.
            new_status = 'verified'
        # The fingerprint's own verdict is recorded even when the overall
        # verification standing does not move. Those are different questions —
        # "does this file stand verified" vs "what did the fingerprint check
        # conclude" — and the library's Check column renders the second one.
        # Writing only the first is why a fully scanned library still read
        # "Not scanned": the scan agreed with an already-'verified' file, so
        # nothing at all was written about the check it had just performed.
        self._persist_status(
            context, track_id, fpath,
            (expected.get('file_path') or '').strip() or None,
            new_status, write_tag=(bool(new_status) and new_status != file_verif_status),
            expected=expected, acoustid_status=outcome.decision.value,
            # Without this the row keeps whatever reason was written last —
            # which for a downloaded file is the IMPORT's message. The Check
            # column then renders "Skipped" with a tooltip reading "Audio
            # verified: ... artist 100%", two runs disagreeing in one row.
            acoustid_message=outcome.reason)

        if outcome.decision != Decision.FAIL:
            if context.report_progress:
                context.report_progress(
                    log_line=f'OK ({outcome.decision.value}): {fname} — {outcome.reason}',
                    log_type='ok',
                )
            return

        # #1132: the finding asserts "X is actually Y". Y comes from the
        # recording that best resembles the EXPECTED title — but this code only
        # runs when the expected title is already believed wrong, so that
        # ranking is against noise. When the fingerprint's top-scoring
        # recordings name different songs (an AcoustID entry with several linked
        # recordings, all tied on score), there is no defensible Y and the
        # reported one is effectively arbitrary: a file of "You're the
        # Inspiration" got reported as "Saturday in the Park".
        #
        # The DETECTION stands either way — it only asks whether ANY candidate
        # matched, which ties don't affect. What gets withheld is the single
        # "is actually Y" claim, replaced by the candidate list.
        _ambiguous = fingerprint_is_ambiguous(recordings)

        title_sim = outcome.title_sim
        artist_sim = outcome.artist_sim
        matched_title = outcome.matched_title or '?'
        matched_artist = outcome.matched_artist or '?'

        # Distinct candidate labels for the ambiguous copy — drawn from the
        # TIED TOP scores only (the set the ambiguity verdict was made on).
        # Listing every recording would pad the message with lower-scored
        # links that were never really in contention.
        _cand_labels = []
        _cand_detail = []
        for _r in sorted(_judge, key=lambda r: r.get('score') or 0, reverse=True):
            _lbl = f'"{_r.get("title") or "?"}" by {_r.get("artist") or "?"}'
            if _lbl not in _cand_labels:
                _cand_labels.append(_lbl)
                # structured twin of the label, so the fix dialog's pick-one flow
                # never has to parse a display string back apart
                _cand_detail.append({'title': _r.get('title') or '',
                                     'artist': _r.get('artist') or ''})

        # Mismatch (FAIL) — create finding.
        if context.report_progress:
            context.report_progress(
                log_line=(
                    f'Mismatch: {fname} — expected "{expected["title"]}", '
                    f'fingerprint matches {len(_cand_labels)} different recordings'
                    if _ambiguous else
                    f'Mismatch: {fname} — expected "{expected["title"]}", got "{matched_title}"'
                ),
                log_type='error'
            )
        if context.create_finding:
            _is_force = verif_status == 'force_imported'
            severity = 'info' if _is_force else ('warning' if best_score >= 0.90 else 'info')
            if _ambiguous:
                _title = (
                    f'Force-imported (fallback): "{expected["title"]}" does not match this audio'
                    if _is_force else
                    f'Wrong download: "{expected["title"]}" does not match this audio'
                )
            else:
                _title = (
                    f'Force-imported (fallback): "{expected["title"]}" is actually "{matched_title}"'
                    if _is_force else
                    f'Wrong download: "{expected["title"]}" is actually "{matched_title}"'
                )
            finding_details = {
                'expected_title': expected['title'],
                'expected_artist': expected_artist,
                'acoustid_title': matched_title,
                'acoustid_artist': matched_artist,
                'fingerprint_score': round(best_score, 3),
                'title_similarity': round(title_sim, 3),
                'artist_similarity': round(artist_sim, 3),
                'album_thumb_url': expected.get('album_thumb_url'),
                'artist_thumb_url': expected.get('artist_thumb_url'),
                'artist_id': expected.get('artist_id'),
                'album_title': expected.get('album_title', ''),
                'track_number': expected.get('track_number'),
                'force_imported': verif_status == 'force_imported',
                # #1132: True when the fingerprint's top recordings name
                # different songs, so `acoustid_title`/`acoustid_artist`
                # are one arbitrary pick among equals. Anything that would
                # RETAG from this finding must refuse when this is set.
                'ambiguous': _ambiguous,
                'candidates': _cand_labels[:10],
                'candidates_detail': _cand_detail[:10],
            }
            subject = expected.get('lib2_subject')
            if subject:
                from core.library2.maintenance_subjects import subject_details
                finding_details.update(subject_details(subject))
            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type='acoustid_mismatch',
                severity=severity,
                entity_type='track',
                entity_id=str(track_id),
                file_path=fpath,
                title=_title,
                description=(
                    (
                        f'Expected "{expected["title"]}" by {expected_artist}, but the audio '
                        f'fingerprint matches none of them. It maps equally well to '
                        f'{len(_cand_labels)} different recordings, so SoulSync cannot say '
                        f'which one this is: {"; ".join(_cand_labels[:5])}'
                        f' (fingerprint: {best_score:.0%})'
                    ) if _ambiguous else
                    f'Expected "{expected["title"]}" by {expected_artist}, '
                    f'but audio fingerprint matches "{matched_title}" by {matched_artist} '
                    f'(fingerprint: {best_score:.0%}, title match: {title_sim:.0%}, '
                    f'artist match: {artist_sim:.0%})'
                ),
                details=finding_details
            )
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1

    def _expected_artist(self, fpath, expected, fname):
        """Which artist value the scan compares against, in priority order:

        1. the catalogue's per-track artist — manually curated or scanner
           populated, so it wins when it is there;
        2. the file's ARTIST tag — ground truth for what is on disk, and the
           rescue for compilation rows whose per-track artist is NULL because
           they predate that column;
        3. the album artist, for files with neither.

        This is the one input the download gets from its provider payload and
        the scan has to derive, which is why it lives here and not in the
        shared verifier.
        """
        track_artist = (expected.get('track_artist') or '').strip()
        if track_artist:
            return track_artist
        file_artist = None
        try:
            from core.tag_writer import read_file_tags
            file_artist = ((read_file_tags(fpath) or {}).get('artist') or '').strip() or None
        except Exception as e:  # noqa: BLE001
            logger.debug("file-tag artist read failed for %s: %s", fname, e)
        return (file_artist
                or (expected.get('album_artist') or '').strip()
                or expected['artist'])

    def _persist_inconclusive(self, context, track_id, fpath, expected, reason):
        """Record "checked, no claim" — the verdict, plus why, and nothing else."""
        self._persist_status(
            context, track_id, fpath,
            (expected.get('file_path') or '').strip() or None,
            None, write_tag=False, expected=expected,
            acoustid_status='skip', acoustid_message=reason,
        )

    def _persist_status(
        self, context, track_id, fpath, db_path, status, write_tag, expected=None,
        acoustid_status=None, acoustid_message=None,
    ):
        """Record what the scan concluded, in both places it belongs.

        ``status`` is the file's overall verification standing and may be
        unchanged (or absent — a FAIL on an untagged file moves nothing).
        ``acoustid_status`` is the fingerprint verdict itself
        (``pass``/``skip``/``fail``), which the library's Check column reads
        and which a scan always produces. Either one alone is enough reason to
        write; only ``status`` reaches the tag and the history projection,
        because those have no notion of a separate fingerprint verdict.
        """
        if not status and not acoustid_status:
            return
        if status and write_tag:
            try:
                from core.tag_writer import write_verification_status

                write_verification_status(fpath, status)
            except Exception as exc:  # noqa: BLE001
                logger.debug("verification tag write failed for %s: %s", fpath, exc)
        file_id = int((expected or {}).get("lib2_file_id") or 0)
        conn = context.db._get_connection()
        try:
            if file_id:
                assignments, params = [], []
                if status:
                    assignments.append("verification_status=?")
                    params.append(status)
                if acoustid_status:
                    assignments.append("acoustid_status=?")
                    params.append(acoustid_status)
                if acoustid_message:
                    # The badge's tooltip reads this; "Skipped" with no reason
                    # is only marginally better than "Not scanned".
                    assignments.append(
                        "pipeline_result_json=json_set("
                        "COALESCE(NULLIF(pipeline_result_json,''),'{}'),"
                        "'$.acoustid_message', ?)")
                    params.append(acoustid_message)
                conn.execute(
                    f"UPDATE lib2_track_files SET {', '.join(assignments)}, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*params, file_id),
                )
            if status:
                self._project_status_to_history(
                    conn, fpath, db_path, status, expected or {})
            conn.commit()
        finally:
            conn.close()
        if not file_id:
            return
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
                    "acoustid_status": acoustid_status,
                },
            )

    def _project_status_to_history(self, conn, fpath, db_path, status, expected):
        """Carry the verdict into the file's ``library_history`` row (#934).

        The Unverified review queue on the Downloads page is still read from
        ``library_history`` (``get_library_history_unverified``), so without this
        a scan verdict never reaches the user: a newly flagged file never shows
        up for review, and a file the scan just verified stays stuck
        'unverified' — which is #934's symptom exactly. Goes when that queue
        reads lib2 (docs §32.3.1 stage 3).

        The stored path is frozen at import time while the file has since moved,
        so an exact-path match alone misses the row — then the status lands
        nowhere and every scan inserts another duplicate. Match exact path
        first, then filename guarded by title, and heal the row's path so later
        scans match cleanly.
        """
        try:
            from core.downloads.history_match import (
                like_filename_filter, pick_history_row,
            )

            cur = conn.cursor()
            current = fpath or db_path
            basename = os.path.basename(current) if current else ''
            clauses, params = [], []
            for path in {p for p in (fpath, db_path) if p}:
                clauses.append("file_path = ?")
                params.append(path)
            if basename:
                clauses.append("file_path LIKE ? ESCAPE '\\'")
                params.append(like_filename_filter(basename))
            row_id = None
            if clauses:
                cur.execute(
                    "SELECT id, file_path, title, download_source FROM library_history "
                    "WHERE " + " OR ".join(clauses), params)
                row_id = pick_history_row(
                    cur.fetchall(), current_paths=(fpath, db_path),
                    basename=basename, title=expected.get('title') or '')
            if row_id is not None:
                cur.execute(
                    "UPDATE library_history SET verification_status = ?, file_path = ? "
                    "WHERE id = ?", (status, current, row_id))
                # Drop the synthetic scan-created duplicates for this exact file
                # (the #934 leftovers). Exact path → collision-free; never
                # touches a real download row.
                cur.execute(
                    "DELETE FROM library_history WHERE id != ? "
                    "AND download_source = 'acoustid_scan' AND file_path = ?",
                    (row_id, current))
            elif status == 'unverified':
                # A file SoulSync never downloaded has no history row at all.
                # Insert one so EVERY scan-flagged file lands in the review
                # queue, not only past downloads; re-scans then match it by path.
                cur.execute(
                    """INSERT INTO library_history
                       (event_type, title, artist_name, album_name, file_path,
                        thumb_url, download_source, verification_status)
                       VALUES ('download', ?, ?, ?, ?, ?, 'acoustid_scan', ?)""",
                    (expected.get('title') or os.path.basename(fpath or ''),
                     expected.get('artist') or None,
                     expected.get('album_title') or None,
                     db_path or fpath,
                     expected.get('album_thumb_url') or None,
                     status))
        except Exception as exc:  # noqa: BLE001
            # The native status write must never be lost to the projection —
            # a v2-only install may have no library_history table at all.
            logger.debug("history projection failed for %s: %s", fpath, exc)

    def _load_db_tracks(self, context: JobContext) -> dict:
        tracks: Dict[str, Dict[str, Any]] = {}
        try:
            for subject in scoped_file_subjects(context, active_file_subjects(context.db, context.config_manager)):
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
                    "artist_id": subject.get("artist_id"),
                    "track_artist": subject.get("artist_name") or "",
                    "album_artist": subject.get("artist_name") or "",
                    "duration_ms": subject.get("duration") or 0,
                    "lib2_file_id": int(subject["file_id"]),
                    "lib2_subject": subject,
                    "import_recording_mbids": _import_recording_mbids(subject),
                    "db_verification_status": subject.get("verification_status"),
                }
        except Exception as exc:  # noqa: BLE001
            logger.error("Native AcoustID subject enumeration failed: %s", exc)
        return tracks

    def _resolve_path(self, file_path, context):
        """Resolve a DB file path to an actual file on disk."""
        if not file_path:
            return None
        if os.path.exists(file_path):
            return file_path
        # Use the shared library-path resolver — picks up
        # library.music_paths and Plex library locations too.
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(
            file_path,
            transfer_folder=context.transfer_folder,
            config_manager=context.config_manager,
        )

    def _save_checkpoint_id(self, context: JobContext, track_id):
        """Save or clear the scan checkpoint by track ID."""
        if context.config_manager:
            context.config_manager.set(
                f'repair.jobs.{self.job_id}.checkpoint_id', track_id
            )

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len({
                int(subject["track_id"])
                for subject in scoped_file_subjects(context, active_file_subjects(context.db, context.config_manager))
                if subject.get("is_primary")
            })
        except Exception:
            return 0

