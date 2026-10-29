"""Unknown Artist recovery on Library v2 (feature-parity A03).

Upstream's ``unknown_artist_fixer`` identified every track filed under an
"Unknown Artist" placeholder on its own -- embedded tags first, then the
track's provider ids, then a title search -- and re-filed it. Library v2 had
retired it in favour of per-artist enrichment, which can only give the whole
placeholder row ONE identity: a mixed bag of tracks by different artists kept
its placeholder name and its shared credits.

This job keeps upstream's identity, settings and evidence order, and re-files
per track on the catalogue: the file is moved onto the right artist, album and
track rows (created when the library does not have them yet), the placeholder
track it left is removed, and the file's tags can be corrected. Moving the file
on disk is Library Reorganize's job, which plans from the catalogue.

Dry Run (default) reports a finding per track to review; with Dry Run off a
scheduled run applies the fixes itself.
"""

from __future__ import annotations

import os
from contextlib import closing
from typing import Any, Dict, List, Optional

from core.library2.maintenance_subjects import active_file_subjects, subject_details
from core.metadata_service import get_client_for_source, get_primary_source, get_source_priority
from core.repair_jobs import register_job
from core.repair_jobs.base import (
    JobContext, JobResult, RepairJob, drop_hand_tagged, scoped_file_subjects,
)
from utils.logging_config import get_logger

logger = get_logger("repair_job.unknown_artist_recovery")

# Every server writes its own placeholder: Jellyfin "Unknown Artist", Navidrome
# and Plex "[Unknown Artist]", some tag sets "Unknown Artists" or "Unknown".
_UNKNOWN_NAMES = {'unknown artist', 'unknown artists', 'unknown', ''}
_BRACKETS = '[](){}<>'
_TRACK_ID_SOURCES = ('spotify', 'deezer', 'itunes')
_TITLE_SEARCH_SOURCES = {'spotify', 'deezer', 'itunes', 'hydrabase'}
_BATCH = 500


def fold_artist_name(name) -> str:
    """lower, trimmed, surrounding brackets dropped: "[Unknown Artist]" -> "unknown artist"."""
    value = str(name or '').strip().lower()
    while value and value[0] in _BRACKETS:
        value = value[1:].strip()
    while value and value[-1] in _BRACKETS:
        value = value[:-1].strip()
    return value


def is_unknown_artist_name(name, extra=()) -> bool:
    folded = fold_artist_name(name)
    return folded in _UNKNOWN_NAMES or folded in {fold_artist_name(x) for x in extra}


def _value(payload, key, default=None):
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _corrected_from_payload(payload, fallback_title: str, source: str,
                            confidence: float, extra=()) -> Optional[Dict[str, Any]]:
    if not payload:
        return None
    artist = _value(payload, 'primary_artist', '') or ''
    artists = _value(payload, 'artists', []) or []
    if not artist and isinstance(artists, list) and artists:
        first = artists[0]
        artist = first.get('name', '') if isinstance(first, dict) else str(first)
    artist = str(artist or '').strip()
    if not artist or is_unknown_artist_name(artist, extra):
        return None
    album = _value(payload, 'album', {}) or {}
    if isinstance(album, dict):
        album_name = album.get('name') or album.get('title') or ''
        year = str(album.get('release_date') or '')[:4]
    else:
        album_name, year = str(album), ''
    return {
        'artist': artist,
        'album': album_name,
        'title': _value(payload, 'name', fallback_title) or fallback_title,
        'track_number': _value(payload, 'track_number'),
        'disc_number': _value(payload, 'disc_number', 1) or 1,
        'year': year,
        'source': source,
        'confidence': confidence,
    }


def resolve_track_identity(subject: Dict[str, Any], resolved_path: Optional[str],
                           extra=(), sleep_or_stop=None) -> Optional[Dict[str, Any]]:
    """Upstream's evidence order: the file's own tags, the track's provider
    ids, then a title search. None when nothing names a real artist."""
    title = str(subject.get('title') or '')
    if resolved_path:
        try:
            from core.tag_writer import read_file_tags
            tags = read_file_tags(resolved_path)
            tag_artist = tags.get('artist') or tags.get('album_artist')
            if tag_artist and not is_unknown_artist_name(tag_artist, extra):
                return {
                    'artist': str(tag_artist).strip(),
                    'album': str(tags.get('album') or '').strip() or subject.get('album_title') or '',
                    'title': str(tags.get('title') or '').strip() or title,
                    'track_number': tags.get('track_number') or subject.get('track_number'),
                    'disc_number': tags.get('disc_number') or 1,
                    'year': str(tags.get('year') or '').strip(),
                    'source': 'file_tags',
                    'confidence': 1.0,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("tag read failed for %s: %s", resolved_path, exc)

    priority = list(get_source_priority(get_primary_source()))
    source_ids = subject.get('track_source_ids') or {}
    for source in [s for s in priority if s in _TRACK_ID_SOURCES]:
        provider_id = source_ids.get(source)
        client = get_client_for_source(source) if provider_id else None
        if not client or not hasattr(client, 'get_track_details'):
            continue
        try:
            corrected = _corrected_from_payload(
                client.get_track_details(str(provider_id)), title,
                f"{source}_track_id_lookup", 0.95, extra)
            if corrected:
                return corrected
        except Exception as exc:  # noqa: BLE001
            logger.debug("track id lookup failed for %s %s: %s", source, provider_id, exc)

    if not title:
        return None
    from difflib import SequenceMatcher
    album_lower = str(subject.get('album_title') or '').lower()
    for source in [s for s in priority if s in _TITLE_SEARCH_SOURCES]:
        client = get_client_for_source(source)
        if not client or not hasattr(client, 'search_tracks'):
            continue
        try:
            best, best_score = None, 0.0
            for candidate in client.search_tracks(title, limit=5) or []:
                name = str(_value(candidate, 'name', '') or '')
                if not name:
                    continue
                score = SequenceMatcher(None, title.lower(), name.lower()).ratio()
                cand_album = _value(candidate, 'album', '') or ''
                if isinstance(cand_album, dict):
                    cand_album = cand_album.get('name') or cand_album.get('title') or ''
                if album_lower and cand_album:
                    score = score * 0.7 + SequenceMatcher(
                        None, album_lower, str(cand_album).lower()).ratio() * 0.3
                if score > best_score:
                    best, best_score = candidate, score
            if best is None or best_score < 0.7:
                continue
            details = None
            if hasattr(client, 'get_track_details') and _value(best, 'id'):
                try:
                    details = client.get_track_details(str(_value(best, 'id')))
                except Exception:  # noqa: BLE001
                    details = None
            corrected = _corrected_from_payload(
                details or best, title, f"{source}_title_search", round(best_score, 3), extra)
            if corrected:
                return corrected
        except Exception as exc:  # noqa: BLE001
            logger.debug("title search failed for %r via %s: %s", title, source, exc)
        if sleep_or_stop and sleep_or_stop(0.2):
            return None
    return None


def apply_unknown_artist_fix(db: Any, track_id: int, details: Dict[str, Any], *,
                             fix_tags: bool = True, config_manager: Any = None) -> Dict[str, Any]:
    """Re-file one placeholder track's files onto the identified artist,
    album and track. Returns a fix result."""
    from core.library2.autolink import (
        find_or_create_album, find_or_create_artist, find_or_create_track,
    )
    from core.library2.completeness import _delete_track_row

    artist_name = str(details.get('corrected_artist') or '').strip()
    title = str(details.get('corrected_title') or '').strip()
    if not artist_name or not title:
        return {'success': False, 'error': 'The finding names no artist or title'}
    album_title = str(details.get('corrected_album') or '').strip() or title
    track_number = details.get('corrected_track_number')
    with closing(db._get_connection()) as conn:
        try:
            paths = [r[0] for r in conn.execute(
                "SELECT path FROM lib2_track_files WHERE track_id=?"
                " AND COALESCE(file_state,'active')<>'deleted'", (int(track_id),))]
            if not paths:
                return {'success': False, 'error': 'The track has no file any more'}
            old_album = conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?",
                                     (int(track_id),)).fetchone()
            artist_id = find_or_create_artist(conn, artist_name)
            album_id = find_or_create_album(
                conn, artist_id, album_title,
                album_type='single' if album_title == title else 'album')
            new_track_id = find_or_create_track(
                conn, album_id, artist_id, title,
                track_number=int(track_number) if str(track_number or '').isdigit() else None,
                disc_number=details.get('corrected_disc_number') or 1)
            if int(new_track_id) != int(track_id):
                conn.execute(
                    "UPDATE lib2_track_files SET track_id=?, updated_at=CURRENT_TIMESTAMP"
                    " WHERE track_id=?", (int(new_track_id), int(track_id)))
                _delete_track_row(conn, int(track_id))
                if old_album and not conn.execute(
                        "SELECT 1 FROM lib2_tracks WHERE album_id=? LIMIT 1",
                        (old_album[0],)).fetchone():
                    conn.execute("DELETE FROM lib2_albums WHERE id=?", (old_album[0],))
            conn.execute(
                "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)"
                " VALUES(?, ?, 'primary', 0)", (int(new_track_id), int(artist_id)))
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            return {'success': False, 'error': f'Re-filing failed: {exc}'}

    if fix_tags:
        from core.library2.paths import resolve_lib2_path
        from core.tag_writer import write_tags_to_file
        for path in paths:
            resolved = resolve_lib2_path(path, config_manager=config_manager)
            if not resolved or not os.path.isfile(resolved):
                continue
            try:
                write_tags_to_file(resolved, {
                    'title': title,
                    'artist_name': artist_name,
                    'album_title': album_title,
                    'year': details.get('corrected_year') or '',
                    'track_number': track_number,
                    'disc_number': details.get('corrected_disc_number') or 1,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unknown Artist tag write failed for %s: %s", resolved, exc)
    return {
        'success': True,
        'action': 'identified_artist',
        'message': f'Filed under {artist_name} — {album_title}',
        'library_v2_rehomed_track_id': int(new_track_id),
        'library_v2_recompute_wanted': True,
    }


@register_job
class UnknownArtistRecoveryJob(RepairJob):
    job_id = 'unknown_artist_fixer'
    display_name = 'Fix Unknown Artists'
    description = 'Identifies tracks filed under "Unknown Artist", one track at a time'
    help_text = (
        'Scans your library for tracks filed under "Unknown Artist" — a common result of '
        'files that arrived without readable tags.\n\n'
        'Each track is identified on its own, so a placeholder holding songs by several '
        'artists is split correctly:\n'
        '1. The file\'s own embedded tags\n'
        '2. The track\'s id on your metadata sources\n'
        '3. A title search as a last resort\n\n'
        'Fixing a finding files the track under the right artist and album (creating them '
        'when needed) and can correct the file\'s tags. Run Library Reorganize afterwards to '
        'move the files into the matching folders.\n\n'
        'What counts as unknown: "Unknown Artist", "Unknown Artists", "Unknown", an empty '
        'name, and the bracketed forms servers write ("[Unknown Artist]"). Add your own '
        'spellings under Unknown names.\n\n'
        'Settings:\n'
        '- Dry Run: report findings to review (default); off applies the fixes on each run\n'
        '- Fix file tags: write the identified artist, album and title into the file\n'
        '- Unknown names: extra placeholder names, comma-separated'
    )
    icon = 'repair-icon-artist'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'dry_run': True,
        'fix_tags': True,
        'unknown_names': '',
    }
    auto_fix = True
    writes_library_files = True
    supports_file_scope = True

    def _settings(self, context: JobContext) -> dict:
        merged = dict(self.default_settings)
        if context.config_manager:
            merged.update(context.config_manager.get(
                f'repair.jobs.{self.job_id}.settings', {}) or {})
        return merged

    @staticmethod
    def _extra_names(settings: dict) -> List[str]:
        raw = settings.get('unknown_names') or ''
        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw if str(x).strip()]
        return [part.strip() for part in str(raw).split(',') if part.strip()]

    def _subjects(self, context: JobContext, extra) -> List[Dict[str, Any]]:
        by_track: Dict[int, Dict[str, Any]] = {}
        for subject in drop_hand_tagged(context, scoped_file_subjects(
                context, active_file_subjects(context.db, context.config_manager))):
            if not is_unknown_artist_name(subject.get('artist_name'), extra):
                continue
            track_id = int(subject['track_id'])
            if track_id in by_track and not subject.get('is_primary'):
                continue
            by_track[track_id] = subject
        return [by_track[k] for k in sorted(by_track)][:_BATCH]

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(self._subjects(context, self._extra_names(self._settings(context))))
        except Exception:  # noqa: BLE001
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._settings(context)
        extra = self._extra_names(settings)
        dry_run = bool(settings.get('dry_run', True))
        subjects = self._subjects(context, extra)
        total = len(subjects)
        if context.update_progress:
            context.update_progress(0, total)
        from core.library2.paths import resolve_lib2_path
        for index, subject in enumerate(subjects):
            if context.check_stop() or (index % 20 == 0 and context.wait_if_paused()):
                return result
            result.scanned += 1
            path = subject.get('path')
            resolved = path if path and os.path.isfile(path) else resolve_lib2_path(
                path, config_manager=context.config_manager)
            corrected = resolve_track_identity(subject, resolved, extra, context.sleep_or_stop)
            if not corrected:
                result.skipped += 1
                continue
            details = {
                **subject_details(subject),
                'track_id': f"lib2:{subject['track_id']}",
                'current_artist': subject.get('artist_name'),
                'current_album': subject.get('album_title'),
                'corrected_artist': corrected['artist'],
                'corrected_album': corrected.get('album') or '',
                'corrected_title': corrected.get('title') or subject.get('title'),
                'corrected_track_number': corrected.get('track_number'),
                'corrected_disc_number': corrected.get('disc_number') or 1,
                'corrected_year': corrected.get('year') or '',
                'source': corrected.get('source'),
                'confidence': corrected.get('confidence'),
                'file_path': path,
                'album_thumb_url': subject.get('album_image'),
            }
            if dry_run:
                if context.create_finding:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='unknown_artist',
                        severity='warning',
                        entity_type='track',
                        entity_id=f"lib2:{subject['track_id']}",
                        file_path=path,
                        title=f"{corrected['artist']} - {details['corrected_title']}",
                        description=(
                            f"Artist: {subject.get('artist_name') or 'Unknown Artist'} → "
                            f"{corrected['artist']}\nAlbum: {subject.get('album_title') or '?'}"
                            f" → {details['corrected_album'] or details['corrected_title']}"
                            f"\nEvidence: {corrected.get('source')}"),
                        details=details,
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
            else:
                outcome = apply_unknown_artist_fix(
                    context.db, int(subject['track_id']), details,
                    fix_tags=bool(settings.get('fix_tags', True)),
                    config_manager=context.config_manager)
                if outcome.get('success'):
                    result.auto_fixed += 1
                else:
                    result.errors += 1
            if context.update_progress and (index + 1) % 10 == 0:
                context.update_progress(index + 1, total)
        if context.update_progress:
            context.update_progress(total, total)
        return result


__all__ = [
    "UnknownArtistRecoveryJob", "apply_unknown_artist_fix", "fold_artist_name",
    "is_unknown_artist_name", "resolve_track_identity",
]
