"""Resolve Canonical Album Versions — backfill job (#765 Stage 2 trigger).

Pins each album's canonical release (best-fit to its files) so the Library
Reorganizer (Stage 3) and Track Number Repair (Stage 4) resolve the SAME
release and stop contradicting each other. The resolution logic lives in the
tested core.metadata.canonical_resolver; this job is the opt-in, rate-limited,
progress-reported bulk runner.

Opt-in (``default_enabled = False``) because resolving compares an album's
candidate releases across sources, which costs metadata-source API calls — done
once per album, then stored. Albums that already have a canonical are skipped.
"""

import os
from typing import Optional

from core.metadata.canonical_resolver import resolve_and_store_canonical_for_album
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.canonical_version")


def _pct(v) -> str:
    return f"{round(v * 100)}%" if isinstance(v, (int, float)) else "n/a"


def _describe_pin(resolved: dict) -> str:
    """Human-readable, judge-able explanation of WHY this release was chosen."""
    artist = resolved.get('artist_name') or ''
    album = resolved.get('album_title') or ''
    head = f"{artist} — {album}".strip(" —") or resolved.get('album_id', '')
    year = resolved.get('year')
    if year:
        head += f" ({year})"
    lines = [
        f"{head}" if head else "",
        f"Pin {resolved['source']} release {resolved['album_id']} "
        f"(confidence {_pct(resolved.get('score'))}).",
        f"Fit to your library: {resolved.get('file_track_count', '?')} files vs "
        f"{resolved.get('release_track_count', '?')} tracks on this release — "
        f"track count {_pct(resolved.get('count_fit'))}, "
        f"durations {_pct(resolved.get('duration_fit'))}, "
        f"titles {_pct(resolved.get('title_fit'))}.",
    ]

    # What the album is currently linked to vs what we'd pin.
    linked = resolved.get('linked_sources') or {}
    if linked:
        linked_str = ", ".join(f"{s}={i}" for s, i in linked.items())
        lines.append(f"Currently linked: {linked_str} → pinning {resolved['source']}.")

    others = [c for c in resolved.get('candidates', []) if c.get('source') != resolved.get('source')]
    if others:
        comp = ", ".join(
            f"{c['source']} {_pct(c['score'])} ({c['track_count']} tk)" for c in others
        )
        lines.append(f"Beat: {comp}.")
    elif len(resolved.get('candidates', [])) == 1:
        lines.append("Only this source had a release linked for this album.")

    # Track listing of the pinned release (so you can eyeball the actual songs).
    titles = resolved.get('release_track_titles') or []
    if titles:
        shown = "; ".join(f"{i+1}. {t}" for i, t in enumerate(titles[:25]))
        more = f" (+{len(titles) - 25} more)" if len(titles) > 25 else ""
        lines.append(f"Release tracks: {shown}{more}")

    return "\n".join(l for l in lines if l)


@register_job
class CanonicalVersionResolveJob(RepairJob):
    job_id = 'canonical_version_resolve'
    display_name = 'Resolve Canonical Album Versions'
    description = (
        'Pins the best-fit release per album (by track count + durations) so '
        'reorganize and track-number repair agree (dry run by default)'
    )
    help_text = (
        'For each album, compares the releases its linked metadata sources point '
        'at and pins the one that best matches the files you actually have '
        '(track count + durations + titles). The Library Reorganizer and Track '
        'Number Repair then both use that pinned release, so they stop '
        'contradicting each other (e.g. standard vs deluxe, or Spotify vs '
        'MusicBrainz track numbering).\n\n'
        'In dry run mode (default) it reports what it would pin without saving. '
        'Disable dry run to store the pins. Albums already pinned are skipped.\n\n'
        'Opt-in: resolving costs metadata-source API calls (once per album).'
    )
    icon = 'repair-icon-tracknumber'
    default_enabled = False
    default_interval_hours = 168  # weekly, but disabled by default
    default_settings = {
        'dry_run': True,
        'min_score': 0.5,
        # Which source's release to pin: 'active_preferred' (default — use your
        # active metadata source when it fits, else best-fit fallback),
        # 'active_only' (only ever the active source), or 'best_fit' (whichever
        # source matches the files best, regardless of which it is).
        'source_selection': 'active_preferred',
    }
    # Render source_selection as a dropdown (not a text box) in the settings UI.
    setting_options = {
        'source_selection': ['active_preferred', 'active_only', 'best_fit'],
    }
    auto_fix = True

    def _get_settings(self, context: JobContext) -> dict:
        merged = dict(self.default_settings)
        if context.config_manager:
            merged.update(context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {}) or {})
        return merged

    def _load_album_ids(self, db, active_server: Optional[str]) -> list:
        conn = None
        try:
            conn = db._get_connection()
            cursor = conn.cursor()
            if active_server:
                cursor.execute(
                    "SELECT al.id, al.title FROM albums al WHERE al.server_source = ? ORDER BY al.id",
                    (active_server,),
                )
            else:
                cursor.execute("SELECT al.id, al.title FROM albums al ORDER BY al.id")
            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error loading albums for canonical resolve: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        dry_run = settings.get('dry_run', True)
        min_score = settings.get('min_score', 0.5)
        mode = settings.get('source_selection', 'active_preferred')

        active_server = None
        if context.config_manager:
            try:
                active_server = context.config_manager.get_active_media_server()
            except Exception as e:
                logger.warning("Couldn't read active media server: %s", e)

        albums = self._load_album_ids(context.db, active_server)
        total = len(albums)
        if context.report_progress:
            mode = 'DRY RUN' if dry_run else 'LIVE'
            context.report_progress(
                phase=f'Resolving canonical versions for {total} albums ({mode})...',
                total=total, scanned=0, log_type='info',
            )

        for i, (album_id, album_title) in enumerate(albums):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            # Skip albums already pinned — one-time cost per album.
            try:
                if context.db.get_album_canonical(album_id):
                    result.skipped += 1
                    result.scanned += 1
                    continue
            except Exception:  # noqa: S110 — best-effort skip check; on read error just resolve it
                pass

            try:
                resolved = resolve_and_store_canonical_for_album(
                    context.db, album_id, min_score=min_score, store=not dry_run, mode=mode,
                )
            except Exception as e:
                logger.warning("Canonical resolve failed for album %s ('%s'): %s",
                               album_id, album_title, e)
                result.errors += 1
                result.scanned += 1
                continue

            result.scanned += 1
            if resolved:
                if dry_run and context.create_finding:
                    artist = resolved.get('artist_name') or ''
                    label = f"{artist} — {album_title}" if artist else (album_title or str(album_id))
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='canonical_version',
                        severity='info',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=f'Pin {resolved["source"]} as canonical: {label}',
                        description=_describe_pin(resolved),
                        details={'album_id': str(album_id), **resolved},
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                elif not dry_run:
                    result.auto_fixed += 1

            if context.report_progress and (i + 1) % 25 == 0:
                context.report_progress(scanned=i + 1, total=total,
                                        phase=f'Resolving ({i+1}/{total})...')

        return result

    def estimate_scope(self, context: JobContext) -> int:
        active_server = None
        if context.config_manager:
            try:
                active_server = context.config_manager.get_active_media_server()
            except Exception:  # noqa: S110 — best-effort; fall back to no server filter
                pass
        return len(self._load_album_ids(context.db, active_server))
