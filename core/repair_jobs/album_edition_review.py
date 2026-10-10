"""Findings-only bulk edition review over native owned file subjects."""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob


@register_job
class AlbumEditionReviewJob(RepairJob):
    job_id = 'album_edition_review'
    display_name = 'Album Edition Review'
    description = 'Suggests the release edition that best matches your owned tracks.'
    help_text = (
        'Compares track count, durations and titles using the existing canonical '
        'release scorer. Only tracks with active imported files count as owned; '
        'extra formats do not inflate the track count. Uses exact provider release '
        'IDs and available edition-bound snapshots. Partial candidates are not '
        'treated as complete releases.\n\n'
        'Always creates Findings, including when old dry_run=false settings were '
        'saved. Applying a reviewed suggestion uses the normal manual edition pin. '
        'Existing manual pins and hand-tagged albums are protected. Opt-in because '
        'uncached candidates require provider requests.'
    )
    icon = 'repair-icon-tracknumber'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {'min_score': 0.5, 'source_selection': 'active_preferred'}
    setting_options = {'source_selection': ['active_preferred', 'active_only', 'best_fit']}
    auto_fix = False
    supports_artist_scope = True

    def _subjects(self, context):
        from core.library2.maintenance_subjects import active_file_subjects
        rows = active_file_subjects(context.db, context.config_manager)
        name = (context.scope or {}).get('artist_name')
        return [r for r in rows if not name or str(r.get('artist_name') or '').casefold() == str(name).casefold()]

    def estimate_scope(self, context):
        return len({row['album_id'] for row in self._subjects(context)})

    def scan(self, context: JobContext) -> JobResult:
        from core.library2.edition_review import propose_album_edition
        result = JobResult()
        settings = dict(self.default_settings)
        if context.config_manager:
            raw = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
            if isinstance(raw, dict):
                settings.update(raw)
        subjects = self._subjects(context)
        album_ids = sorted({row['album_id'] for row in subjects})
        for album_id in album_ids:
            if context.check_stop() or context.wait_if_paused():
                break
            try:
                proposal = propose_album_edition(
                    context.db, context.config_manager, album_id,
                    mode=settings.get('source_selection', 'active_preferred'),
                    min_score=settings.get('min_score', 0.5), file_subjects=subjects)
                if proposal and context.create_finding:
                    created = context.create_finding(
                        job_id=self.job_id, finding_type='canonical_version', severity='info',
                        entity_type='album', entity_id=f'lib2:{album_id}',
                        file_path=next((s.get('path') for s in subjects if s['album_id'] == album_id), None),
                        title=f"Review edition: {proposal['artist_name']} — {proposal['album_title']}",
                        description=(f"{proposal['source']} release {proposal['album_id']}: "
                                     f"{proposal['score']:.0%} fit, {proposal['file_track_count']} owned tracks "
                                     f"against {proposal['release_track_count']} release tracks. "
                                     'Apply to confirm this edition; no automatic pin is made.'),
                        details=proposal)
                    if created:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                else:
                    result.skipped += 1
            except Exception:
                result.errors += 1
            result.scanned += 1
            if context.update_progress:
                context.update_progress(result.scanned, len(album_ids))
        return result
