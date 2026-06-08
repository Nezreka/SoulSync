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

from core.library.path_resolver import resolve_library_file_path
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


def _run_full_enrich(file_path, full_meta) -> bool:
    """'full' depth: run the same multi-source enrichment a fresh download gets
    (MusicBrainz/Deezer/AudioDB/Tidal/… via embed_source_ids), ADDITIVELY — it
    adds rich frames without clearing existing tags. Slow + API-heavy per track.
    """
    if not full_meta:
        return False
    try:
        from core.metadata.common import get_mutagen_symbols
        from core.metadata.source import embed_source_ids
        symbols = get_mutagen_symbols()
        if not symbols:
            return False
        audio = symbols.File(file_path)
        if audio is None:
            return False
        if getattr(audio, 'tags', None) is None and hasattr(audio, 'add_tags'):
            audio.add_tags()
        embed_source_ids(audio, full_meta, context=None, runtime=None)
        audio.save()
        return True
    except Exception as e:
        logger.warning("full enrich failed for %s: %s", file_path, e)
        return False


def apply_track_plans(track_plans, cover_action=None, cover_url=None, full=False,
                      lyrics_action=False) -> dict:
    """Write each plan's tags in place (+ optionally embed/refresh cover art,
    + optionally fetch/refresh .lrc lyrics), reusing tag_writer.write_tags_to_file.
    ``file_path`` on each plan must be a real, reachable path (caller resolves
    Docker paths). Shared by the dry-run=False auto-apply and the repair_worker
    fix handler. Never raises.

    ``lyrics_action`` (Sokhi): when True, after a track's tags are written, fetch
    + write its .lrc and embed the lyrics — the same LyricsClient the import
    pipeline uses (fetch if missing, re-embed if a sidecar already exists)."""
    import os as _os
    result = {'written': 0, 'failed': 0, 'skipped': 0, 'cover_written': False, 'lyrics_written': 0}
    embed_cover = bool(cover_action and cover_url)
    cover_data = None
    if embed_cover:
        try:
            from core.tag_writer import download_cover_art
            cover_data = download_cover_art(cover_url)
        except Exception as e:
            logger.debug("retag cover download failed: %s", e)
    embed_cover = embed_cover and cover_data is not None

    _lyrics_client = None
    if lyrics_action:
        try:
            from core.lyrics_client import lyrics_client as _lyrics_client
        except Exception as e:
            logger.debug("retag lyrics client unavailable: %s", e)
            _lyrics_client = None

    from core.tag_writer import write_tags_to_file
    last_dir = None
    for tp in track_plans or []:
        fp = tp.get('file_path')
        db_data = tp.get('db_data') or {}
        if not fp or not _os.path.isfile(fp) or (not db_data and not embed_cover and not _lyrics_client):
            result['skipped'] += 1
            continue
        try:
            res = write_tags_to_file(fp, db_data, embed_cover=embed_cover, cover_data=cover_data)
            if res.get('success'):
                result['written'] += 1
                last_dir = _os.path.dirname(fp)
                if full and tp.get('full_meta'):
                    _run_full_enrich(fp, tp['full_meta'])
            else:
                result['failed'] += 1
        except Exception as e:
            logger.warning("retag write failed for %s: %s", fp, e)
            result['failed'] += 1

        # Lyrics: fetch/refresh the .lrc for this track (independent of tag write
        # success — a track with no tag changes may still be missing lyrics).
        # Query metadata comes from the plan's READ-only lyrics_meta (never
        # db_data, so nothing here can leak into a tag write). Falls back to
        # db_data for plans that predate lyrics_meta.
        if _lyrics_client:
            lm = tp.get('lyrics_meta') or {}
            title = lm.get('title') or db_data.get('title') or ''
            artist = lm.get('artist') or db_data.get('artist') or ''
            if title:
                try:
                    dur = lm.get('duration') or db_data.get('duration')
                    wrote = _lyrics_client.create_lrc_file(
                        fp, title, artist,
                        album_name=lm.get('album') or db_data.get('album'),
                        duration_seconds=int(dur) if dur else None,
                    )
                    if wrote:
                        result['lyrics_written'] += 1
                except Exception as e:
                    logger.debug("retag lyrics fetch failed for %s: %s", fp, e)

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


_FULL_META_ID_KEYS = (
    'spotify_album_id', 'spotify_track_id',
    'itunes_album_id', 'itunes_track_id',
    'musicbrainz_release_id', 'musicbrainz_recording_id',
    'deezer_id',
)


def _build_full_meta(db_data, src, album_title, artist_name, lib_title):
    """Metadata dict for the 'full' depth enrichment cascade. Carries the matched
    source's ids so embed_source_ids resolves the right entity instead of guessing
    by name."""
    src_title = None
    for k in ('name', 'title', 'track_name'):
        v = src.get(k) if isinstance(src, dict) else getattr(src, k, None)
        if v:
            src_title = v
            break
    meta = {
        'title': src_title or lib_title,
        'album': album_title,
        'album_artist': artist_name,
        'artist': artist_name,
    }
    meta.update({k: db_data[k] for k in _FULL_META_ID_KEYS if db_data.get(k)})
    return meta


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
        '- Depth: "light" writes the core tags + the matched source\'s ids (fast, '
        'additive). "full" also runs the same multi-source enrichment a fresh '
        'download gets (MusicBrainz / Deezer / AudioDB / Tidal / etc. — BPM, ISRC, '
        'lyrics, moods, …); much richer but slower and API-heavy on a big library.\n'
        '- Dry run (default ON): only create findings to review; nothing is written. '
        'Turn it off to auto-apply on scan.\n'
        '- Mode: "overwrite" rewrites every field the source provides; "fill_missing" '
        'only fills blank tags (keeps your existing values).\n'
        '- Cover art: replace / fill-missing / skip. "replace" force-refreshes '
        'art on every matched album (use this after changing your cover-art '
        'sources to re-pull fresh covers). When you have configured cover-art '
        'sources (Settings > metadata enhancement art order), the art is pulled '
        'from those; otherwise it falls back to the matched source\'s album image.\n'
        '- Source: which matched source to pull from (default: your source priority).'
    )
    icon = 'repair-icon-retag'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'dry_run': True,
        'depth': 'light',
        'mode': MODE_OVERWRITE,
        'cover_art': 'replace',
        'lyrics': 'skip',
        'source': 'auto',
    }
    setting_options = {
        'depth': ['light', 'full'],
        'mode': [MODE_OVERWRITE, MODE_FILL_MISSING],
        'cover_art': ['replace', 'fill_missing', 'skip'],
        'lyrics': ['fetch', 'skip'],
        'source': ['auto', 'spotify', 'itunes', 'deezer', 'musicbrainz'],
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
        lyrics_action = (settings.get('lyrics', 'skip') or 'skip').lower() == 'fetch'
        dry_run = settings.get('dry_run', True)
        depth = settings.get('depth', 'light')
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
                                 source, album_source_id, mode, cover_mode, dry_run, depth,
                                 lyrics_action=lyrics_action)
            except Exception as e:
                logger.debug("Library re-tag: album %s failed: %s", album_id, e)
                result.errors += 1

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("Library re-tag scan: %d albums checked, %d findings",
                    result.scanned, result.findings_created)
        return result

    def _scan_album(self, context, result, album_id, album_title, artist_name,
                    source, album_source_id, mode, cover_mode, dry_run=True, depth='light',
                    lyrics_action=False):
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

        # Honor the user's configured cover-art sources (the same
        # `metadata_enhancement.album_art_order` the post-process embed uses), so
        # changing those sources and re-tagging pulls fresh art FROM them rather
        # than always using the matched metadata source's album image. Non-
        # breaking: select_preferred_art_url returns None when no order is
        # configured, so we keep the source image. Skipped when not embedding art.
        if cover_mode != 'skip':
            try:
                from core.metadata.art_lookup import select_preferred_art_url
                order = (context.config_manager.get('metadata_enhancement.album_art_order')
                         if context.config_manager else None)
                preferred = select_preferred_art_url(artist_name, album_title, album_meta, order)
                if preferred:
                    cover_url = preferred
            except Exception as e:
                logger.debug("preferred cover-art lookup failed for album %s: %s", album_id, e)

        # Cover action (album-level), independent of tag changes. Decided first
        # so cover-only albums (tags fine, art missing) still include their
        # tracks for the apply to embed art into.
        cover_action = self._cover_action(cover_mode, cover_url, library_tracks)

        pairs = match_source_tracks(source_tracks, library_tracks)
        download_folder = (context.config_manager.get('soulseek.download_path', '')
                           if context.config_manager else None)
        track_plans = []
        unmatched = []
        unreachable = 0
        for lib, src in pairs:
            # Resolve container/host path mismatches the same way the apply
            # handler does. The old bare os.path.isfile() on the raw DB path
            # failed for EVERY track on path-mapped setups (Docker mounts), so
            # cover-mode scans produced "(0 track(s))" findings that the apply
            # then rejected with "No tracks to re-tag in finding".
            rp = resolve_library_file_path(
                lib['file_path'],
                transfer_folder=getattr(context, 'transfer_folder', None),
                download_folder=download_folder,
                config_manager=context.config_manager,
            )
            if not rp:
                unreachable += 1
                continue  # genuinely unreachable from this process
            if src is None:
                unmatched.append(lib['title'] or os.path.basename(lib['file_path']))
                # No source match means no re-tag — but album cover art and/or
                # lyrics still apply to the file, so those modes include an
                # art/lyrics-only plan (empty db_data → apply writes NO tags).
                if cover_action or lyrics_action:
                    plan_row = {
                        'file_path': rp,
                        'track_id': lib['id'],
                        'title': lib['title'],
                        'changes': [],
                        'db_data': {},   # never write tags for an unmatched track
                    }
                    if lyrics_action:
                        # READ-only metadata for the lyrics query — kept OUT of
                        # db_data so it can never be written as tags.
                        plan_row['lyrics_meta'] = {
                            'title': lib.get('title'), 'artist': artist_name,
                            'album': album_title}
                    track_plans.append(plan_row)
                continue
            current = _read_current_tags(rp)
            plan = plan_track(current, src, album_meta, mode=mode)
            # Include a track when its tags change, OR there's a cover action,
            # OR lyrics are being fetched (db_data may be empty — apply still
            # embeds art / writes the .lrc).
            if plan['changes'] or cover_action or lyrics_action:
                db_data = plan['db_data']
                _add_source_ids(db_data, source, album_source_id, src)
                tp = {
                    'file_path': rp,
                    'track_id': lib['id'],
                    'title': lib['title'],
                    'changes': plan['changes'],
                    'db_data': db_data,
                }
                if lyrics_action:
                    # READ-only lyrics query metadata (never written as tags).
                    tp['lyrics_meta'] = {
                        'title': lib.get('title'), 'artist': artist_name,
                        'album': album_title}
                if depth == 'full':
                    tp['full_meta'] = _build_full_meta(
                        db_data, src, album_title, artist_name, lib['title'])
                track_plans.append(tp)

        tag_change_tracks = sum(1 for tp in track_plans if tp['changes'])
        if (not tag_change_tracks and not cover_action and not lyrics_action) or not track_plans:
            # Nothing actionable. The second clause covers cover-action albums
            # where no track is reachable/included — creating a finding there
            # gives an unappliable "(0 track(s))" entry.
            if cover_action and not track_plans:
                logger.debug(
                    "Library re-tag: album %s skipped — cover action but no usable tracks "
                    "(%d unreachable, %d unmatched)", album_id, unreachable, len(unmatched))
            result.skipped += 1
            return

        # Not dry-run: apply the tags in place now (the track paths were already
        # isfile-checked above) and count it as an auto-fix — no finding.
        if not dry_run:
            res = apply_track_plans(track_plans, cover_action, cover_url, full=(depth == 'full'),
                                    lyrics_action=lyrics_action)
            if res['written'] or res['cover_written'] or res.get('lyrics_written'):
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
        if depth == 'full':
            summary_bits.append("full multi-source enrichment")
        desc = (f'Album "{album_title}" by {artist_name or "Unknown"} would be re-tagged from '
                f'{source} ({", ".join(summary_bits)}).')
        if unmatched:
            desc += (f' {len(unmatched)} track(s) could not be matched to the source — '
                     f'tags left untouched{" (cover art still applied)" if cover_action else ""}.')
        if unreachable:
            desc += f' {unreachable} track(s) not reachable on disk and skipped.'

        # Cover-only findings say so instead of the puzzling "(0 track(s))".
        title_what = (f'{tag_change_tracks} track(s)' if tag_change_tracks
                      else f'cover art, {len(track_plans)} track(s)')

        if context.create_finding:
            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type='library_retag',
                severity='info',
                entity_type='album',
                entity_id=str(album_id),
                file_path=None,
                title=f'Re-tag: {album_title or "Unknown"} ({title_what})',
                description=desc,
                details={
                    'album_id': album_id,
                    'album_title': album_title,
                    'artist': artist_name,
                    'source': source,
                    'album_source_id': album_source_id,
                    'depth': depth,
                    'mode': mode,
                    'cover_mode': cover_mode,
                    'cover_url': cover_url,
                    'cover_action': cover_action,
                    'lyrics_action': lyrics_action,
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
