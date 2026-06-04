"""Library Re-tag Job — rewrite audio tags from a fresh metadata-source pull.

Re-does ONLY the tagging part of post-processing (tags + cover art), IN PLACE:
no file moves, no renames, no re-matching, no library reorganization. Only
albums that are matched to a metadata source are eligible — the album's stored
source id is used to pull fresh data to write.

Dry-run by design: scan creates detailed, per-track findings (old -> new for
every field that would change); nothing touches a file until you apply a
finding. The apply handler lives in repair_worker (_fix_library_retag).
"""

import os

from core.library.retag_planner import (
    MODE_FILL_MISSING,
    MODE_OVERWRITE,
    match_source_tracks,
    plan_track,
)
from core.metadata.album_tracks import get_album_for_source, get_album_tracks_for_source
from core.metadata_service import get_primary_source, get_source_priority
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.library_retag")

# (source, albums-table column) in resolution-preference order is decided at
# runtime from the configured source priority; this maps source -> column.
_ALBUM_SOURCE_COLUMNS = {
    'spotify': 'spotify_album_id',
    'itunes': 'itunes_album_id',
    'deezer': 'deezer_id',
    'musicbrainz': 'musicbrainz_release_id',
}


def _read_current_tags(file_path):
    try:
        from core.soulsync_client import _read_tags
        return _read_tags(file_path) or {}
    except Exception as exc:
        logger.debug("read tags failed for %s: %s", file_path, exc)
        return {}


def apply_track_plans(track_plans, cover_action=None, cover_url=None) -> dict:
    """Write each plan's tags in place (+ optionally embed/refresh cover art),
    reusing tag_writer.write_tags_to_file. ``file_path`` on each plan must be a
    real, reachable path (caller resolves Docker paths). Shared by the dry-run=
    False auto-apply and the repair_worker fix handler. Never raises.
    """
    import os as _os
    result = {'written': 0, 'failed': 0, 'skipped': 0, 'cover_written': False}
    embed_cover = bool(cover_action and cover_url)
    cover_data = None
    if embed_cover:
        try:
            from core.tag_writer import download_cover_art
            cover_data = download_cover_art(cover_url)
        except Exception as e:
            logger.debug("retag cover download failed: %s", e)
    embed_cover = embed_cover and cover_data is not None

    from core.tag_writer import write_tags_to_file
    last_dir = None
    for tp in track_plans or []:
        fp = tp.get('file_path')
        db_data = tp.get('db_data') or {}
        if not fp or not _os.path.isfile(fp) or (not db_data and not embed_cover):
            result['skipped'] += 1
            continue
        try:
            res = write_tags_to_file(fp, db_data, embed_cover=embed_cover, cover_data=cover_data)
            if res.get('success'):
                result['written'] += 1
                last_dir = _os.path.dirname(fp)
            else:
                result['failed'] += 1
        except Exception as e:
            logger.warning("retag write failed for %s: %s", fp, e)
            result['failed'] += 1

    if cover_action and cover_data and last_dir:
        try:
            cover_path = _os.path.join(last_dir, 'cover.jpg')
            if cover_action == 'replace' or not _os.path.exists(cover_path):
                with open(cover_path, 'wb') as fh:
                    fh.write(cover_data[0])
                result['cover_written'] = True
        except Exception as e:
            logger.debug("retag cover.jpg write failed: %s", e)
    return result


def _add_source_ids(db_data, source, album_source_id, source_track):
    """Stamp the album/track source IDs onto the write payload so the canonical
    writer embeds them too (Spotify / iTunes / MusicBrainz)."""
    album_key = {'spotify': 'spotify_album_id', 'itunes': 'itunes_album_id',
                 'musicbrainz': 'musicbrainz_release_id'}.get(source)
    track_key = {'spotify': 'spotify_track_id', 'itunes': 'itunes_track_id',
                 'musicbrainz': 'musicbrainz_recording_id'}.get(source)
    if album_key and album_source_id:
        db_data[album_key] = album_source_id
    if track_key:
        tid = None
        for k in ('id', 'track_id', 'source_track_id'):
            v = source_track.get(k) if isinstance(source_track, dict) else getattr(source_track, k, None)
            if v:
                tid = v
                break
        if tid:
            db_data[track_key] = tid


def _track_list(result):
    """Normalize a get_album_tracks result into a plain list of track items."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ('tracks', 'items', 'data'):
            v = result.get(key)
            if isinstance(v, list):
                return v
    v = getattr(result, 'tracks', None)
    return v if isinstance(v, list) else []


@register_job
class LibraryRetagJob(RepairJob):
    job_id = 'library_retag'
    display_name = 'Library Re-tag'
    description = 'Rewrites tags + cover art from a fresh metadata-source pull, in place'
    help_text = (
        'Re-tags albums in your library using a fresh pull from the metadata source '
        'they are matched to — writing the tags (and optionally cover art) directly '
        'into the files. It only does the tagging step: files are NOT moved, renamed, '
        're-matched, or reorganized.\n\n'
        'Only albums matched to a metadata source (Spotify / iTunes / Deezer / '
        'MusicBrainz album id) are eligible, since the source is where the fresh data '
        'comes from. Each finding lists every tag that would change (old -> new) per '
        'track so you can review before applying — nothing is written until you do.\n\n'
        'Settings:\n'
        '- Dry run (default ON): only create findings to review; nothing is written. '
        'Turn it off to auto-apply on scan.\n'
        '- Mode: "overwrite" rewrites every field the source provides; "fill_missing" '
        'only fills blank tags (keeps your existing values).\n'
        '- Cover art: replace / fill-missing / skip.\n'
        '- Source: which matched source to pull from (default: your source priority).'
    )
    icon = 'repair-icon-retag'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'dry_run': True,
        'mode': MODE_OVERWRITE,
        'cover_art': 'replace',
        'source': '',
    }
    setting_options = {
        'mode': [MODE_OVERWRITE, MODE_FILL_MISSING],
        'cover_art': ['replace', 'fill_missing', 'skip'],
        'source': ['', 'spotify', 'itunes', 'deezer', 'musicbrainz'],
    }
    auto_fix = True

    def _get_settings(self, context: JobContext) -> dict:
        merged = dict(self.default_settings)
        if context.config_manager:
            cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {}) or {}
            merged.update(cfg)
        return merged

    def _source_order(self, settings) -> list:
        override = (settings.get('source') or '').strip()
        if override in _ALBUM_SOURCE_COLUMNS:
            return [override]
        return [s for s in get_source_priority(get_primary_source()) if s in _ALBUM_SOURCE_COLUMNS]

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        mode = settings.get('mode', MODE_OVERWRITE)
        cover_mode = settings.get('cover_art', 'replace')
        dry_run = settings.get('dry_run', True)
        source_order = self._source_order(settings)
        if not source_order:
            logger.warning("Library re-tag: no usable metadata sources configured")
            return result

        # Albums that carry at least one usable source id.
        cols = ', '.join(f'al.{c}' for c in _ALBUM_SOURCE_COLUMNS.values())
        try:
            with context.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT al.id, al.title, ar.name, {cols}
                    FROM albums al
                    LEFT JOIN artists ar ON ar.id = al.artist_id
                    WHERE al.title IS NOT NULL AND al.title != ''
                """)
                albums = cursor.fetchall()
        except Exception as e:
            logger.error("Library re-tag: album query failed: %s", e, exc_info=True)
            result.errors += 1
            return result

        total = len(albums)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} albums for tag drift...', total=total)

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 5 == 0 and context.wait_if_paused():
                return result
            result.scanned += 1
            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

            album_id, album_title, artist_name = row[0], row[1], row[2]
            source_ids = {src: row[3 + idx] for idx, src in enumerate(_ALBUM_SOURCE_COLUMNS)}

            source = next((s for s in source_order if source_ids.get(s)), None)
            if not source:
                continue  # not matched to a usable source — skip
            album_source_id = str(source_ids[source])

            try:
                self._scan_album(context, result, album_id, album_title, artist_name,
                                 source, album_source_id, mode, cover_mode, dry_run)
            except Exception as e:
                logger.debug("Library re-tag: album %s failed: %s", album_id, e)
                result.errors += 1

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("Library re-tag scan: %d albums checked, %d findings",
                    result.scanned, result.findings_created)
        return result

    def _scan_album(self, context, result, album_id, album_title, artist_name,
                    source, album_source_id, mode, cover_mode, dry_run=True):
        # Local tracks for this album.
        with context.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, track_number, disc_number, file_path
                FROM tracks
                WHERE album_id = ? AND file_path IS NOT NULL AND file_path != ''
                ORDER BY disc_number, track_number
            """, (album_id,))
            library_tracks = [
                {'id': r[0], 'title': r[1], 'track_number': r[2],
                 'disc_number': r[3], 'file_path': r[4]}
                for r in cur.fetchall()
            ]
        if not library_tracks:
            return

        album_meta = get_album_for_source(source, album_source_id)
        source_tracks = _track_list(get_album_tracks_for_source(source, album_source_id))
        if not album_meta or not source_tracks:
            logger.debug("Library re-tag: no source data for album %s (%s)", album_id, source)
            return

        cover_url = None
        for k in ('image_url', 'album_image_url', 'cover_url', 'thumb_url'):
            v = album_meta.get(k) if isinstance(album_meta, dict) else getattr(album_meta, k, None)
            if v:
                cover_url = v
                break

        # Cover action (album-level), independent of tag changes. Decided first
        # so cover-only albums (tags fine, art missing) still include their
        # tracks for the apply to embed art into.
        cover_action = self._cover_action(cover_mode, cover_url, library_tracks)

        pairs = match_source_tracks(source_tracks, library_tracks)
        track_plans = []
        unmatched = []
        for lib, src in pairs:
            if src is None:
                unmatched.append(lib['title'] or os.path.basename(lib['file_path']))
                continue
            if not os.path.isfile(lib['file_path']):
                continue  # not reachable at the stored path — skip (apply resolves paths)
            current = _read_current_tags(lib['file_path'])
            plan = plan_track(current, src, album_meta, mode=mode)
            # Include a track when its tags change, OR when there's a cover action
            # to apply to it (db_data may be empty — apply embeds art either way).
            if plan['changes'] or cover_action:
                db_data = plan['db_data']
                _add_source_ids(db_data, source, album_source_id, src)
                track_plans.append({
                    'file_path': lib['file_path'],
                    'track_id': lib['id'],
                    'title': lib['title'],
                    'changes': plan['changes'],
                    'db_data': db_data,
                })

        tag_change_tracks = sum(1 for tp in track_plans if tp['changes'])
        if not tag_change_tracks and not cover_action:
            result.skipped += 1
            return

        # Not dry-run: apply the tags in place now (the track paths were already
        # isfile-checked above) and count it as an auto-fix — no finding.
        if not dry_run:
            res = apply_track_plans(track_plans, cover_action, cover_url)
            if res['written'] or res['cover_written']:
                result.auto_fixed += 1
            else:
                result.errors += 1
            return

        total_changes = sum(len(tp['changes']) for tp in track_plans)
        summary_bits = []
        if tag_change_tracks:
            summary_bits.append(f"{tag_change_tracks} track(s), {total_changes} tag change(s)")
        if cover_action:
            summary_bits.append(f"cover art ({cover_action})")
        desc = (f'Album "{album_title}" by {artist_name or "Unknown"} would be re-tagged from '
                f'{source} ({", ".join(summary_bits)}).')
        if unmatched:
            desc += f' {len(unmatched)} track(s) could not be matched to the source and are left untouched.'

        if context.create_finding:
            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type='library_retag',
                severity='info',
                entity_type='album',
                entity_id=str(album_id),
                file_path=None,
                title=f'Re-tag: {album_title or "Unknown"} ({tag_change_tracks} track(s))',
                description=desc,
                details={
                    'album_id': album_id,
                    'album_title': album_title,
                    'artist': artist_name,
                    'source': source,
                    'album_source_id': album_source_id,
                    'mode': mode,
                    'cover_mode': cover_mode,
                    'cover_url': cover_url,
                    'cover_action': cover_action,
                    'tracks': track_plans,        # each carries its db_data for a deterministic apply
                    'unmatched': unmatched,
                },
            )
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1

    @staticmethod
    def _cover_action(cover_mode, cover_url, library_tracks):
        """Return 'replace' / 'fill' / None for the album's cover under the mode."""
        if cover_mode == 'skip' or not cover_url:
            return None
        if cover_mode == 'replace':
            return 'replace'
        # fill_missing — only if the album has no art on disk
        try:
            from core.metadata.art_apply import album_has_art_on_disk
            rep = library_tracks[0]['file_path'] if library_tracks else ''
            return None if album_has_art_on_disk(rep) else 'fill'
        except Exception:
            return None

    def estimate_scope(self, context: JobContext) -> int:
        try:
            with context.db._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM albums WHERE title IS NOT NULL AND title != ''")
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0
