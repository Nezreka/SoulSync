"""Folder-first audio identity audit feeding the album review queue.

Opt-in and read-only: the scan records evidence-backed proposals. Fix handlers
recheck the file before any tag, path, or wishlist write. No album is declared
complete from folder layout alone; missing slots need explicit intent.
"""

from __future__ import annotations

import os
import re
import hashlib
import json
from collections import Counter, defaultdict
from functools import partial
from typing import Any

from core.auto_import_worker import _read_file_tags
from core.imports.staging import AUDIO_EXTENSIONS
from core.library.path_resolver import resolve_library_file_path
from core.library_reorganize import _safe_filename
from core.library.reconciliation_evidence import ReconciliationEvidence, folder_audio_path_digest
from core.library.reconciliation_plan import reconcile_folder, _title_score
from core.library.retag_planner import plan_track
from core.matching.audio_verification import fingerprint_is_ambiguous, normalize
from core.metadata.album_tracks import get_album_for_source, get_album_tracks_for_source
from core.repair_jobs import register_job
from core.repair_jobs.base import RepairJob, JobContext, JobResult, all_library_roots, walk_library
from core.repair_review_identity import missing_slot_key
from utils.logging_config import get_logger

logger = get_logger('repair_job.library_reconciliation')

_SOURCES = {
    'musicbrainz_release_id': 'musicbrainz',
    'spotify_album_id': 'spotify',
    'itunes_album_id': 'itunes',
    'deezer_id': 'deezer',
}
_DISC_FOLDER = re.compile(r'^(?:cd|disc|disk|volume|vol)\s*[-_. ]*\d+\b', re.I)
_NUMBERED_FILENAME = re.compile(r'^(\d{1,4})(\s*[-_.]\s*|\s+)(.+)$')


def _suggest_filename(path: str, slot: dict) -> str:
    """Keep the file's numbering convention while correcting a clear slot."""
    if not str(slot.get('title') or '').strip():
        return ''
    stem, extension = os.path.splitext(os.path.basename(path))
    match = _NUMBERED_FILENAME.match(stem)
    if match:
        digits, separator, old_title = match.groups()
        try:
            track = int(slot['track_number'])
            disc = int(slot.get('disc_number') or 1)
        except (TypeError, ValueError, KeyError):
            track, disc = 0, 0
        if (len(digits) == 4 and 0 < int(digits[:2]) <= 9 and
                0 < int(digits[2:]) < 100 and 0 < track < 100 and 0 < disc <= 9):
            number = f'{disc:02d}{track:02d}'
        elif len(digits) <= 2 and track > 0:
            number = str(track).zfill(len(digits))
        else:
            number = digits
        title = (old_title if _title_score(old_title, slot['title']) >= 0.83
                 else _safe_filename(slot['title']))
        proposed = f'{number}{separator}{title}{extension}'
    else:
        title = (stem if _title_score(stem, slot['title']) >= 0.83
                 else _safe_filename(slot['title']))
        proposed = f'{title}{extension}'
    return os.path.join(os.path.dirname(path), proposed) if proposed != os.path.basename(path) else ''


def _group_disc_folders(folders: dict[str, list[str]], tracks: dict,
                        roots: list[str]) -> dict[str, list[str]]:
    """Treat conventional disc subfolders as one release-level folder audit."""
    groups = {folder: list(paths) for folder, paths in folders.items()}
    by_album: dict[object, list[str]] = defaultdict(list)
    for folder, paths in folders.items():
        ids = {tracks[os.path.normpath(path)]['album_id'] for path in paths
               if os.path.normpath(path) in tracks and
               tracks[os.path.normpath(path)].get('album_id') is not None}
        if len(ids) == 1:
            by_album[next(iter(ids))].append(folder)
    normalized_roots = {os.path.normpath(root) for root in roots}
    for album_folders in by_album.values():
        if len(album_folders) < 2:
            continue
        parent = os.path.commonpath(album_folders)
        if (parent in normalized_roots or
                (parent in groups and parent not in album_folders) or not all(
            folder == parent or
            (os.path.dirname(folder) == parent and _DISC_FOLDER.match(os.path.basename(folder)))
            for folder in album_folders
        )):
            continue
        merged = []
        for folder in album_folders:
            merged.extend(groups.pop(folder))
        groups[parent] = merged
    return groups


def _retire_stale_findings(db, observed: set[tuple[str, str, str]]) -> None:
    """Close proposals absent from a completed library scan."""
    conn = db._get_connection()
    try:
        rows = conn.execute('''
            SELECT id, finding_type, entity_type, entity_id FROM repair_findings
            WHERE job_id = 'library_reconciliation' AND status = 'pending'
        ''').fetchall()
        stale = [row[0] for row in rows if tuple(str(part) for part in row[1:4])
                 not in observed]
        if stale:
            conn.executemany('''
                UPDATE repair_findings SET status = 'resolved', user_action = 'obsolete',
                    resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', [(finding_id,) for finding_id in stale])
            conn.commit()
    finally:
        conn.close()


def _preserve_folder_findings(db, observed: set[tuple[str, str, str]], folder: str) -> None:
    """Keep existing decisions when transient evidence left one folder unscanned."""
    conn = db._get_connection()
    try:
        rows = conn.execute('''
            SELECT finding_type, entity_type, entity_id
            FROM repair_findings
            WHERE job_id = 'library_reconciliation' AND status = 'pending'
              AND json_valid(details_json)
              AND json_extract(details_json, '$.album_folder') = ?
        ''', (folder,)).fetchall()
        observed.update((row[0], row[1], str(row[2])) for row in rows)
    finally:
        conn.close()


def _track_items(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        items = data.get('items') or data.get('tracks') or []
        if isinstance(items, dict):
            items = items.get('items') or []
        return [row for row in items if isinstance(row, dict)]
    return []


def _normalize_slot(item: dict, source: str) -> dict:
    def positive_int(value, default=None):
        try:
            number = int(value)
            return number if number > 0 else default
        except (TypeError, ValueError):
            return default

    artists = item.get('artists') or []
    artist = next((a.get('name') for a in artists if isinstance(a, dict) and a.get('name')), '')
    if not artist and artists and isinstance(artists[0], str):
        artist = artists[0]
    return {
        'title': item.get('name') or item.get('title') or '',
        'artist': artist or '',
        'track_number': positive_int(item.get('track_number')),
        'disc_number': positive_int(item.get('disc_number'), 1),
        'duration_ms': positive_int(item.get('duration_ms'), 0),
        'source_track_id': item.get('id') or '',
        'recording_mbid': item.get('id') if source == 'musicbrainz' else '',
    }


def _score_release_candidates(files: list[dict], candidates: list[tuple[str, str]],
                              release_cache: dict) -> list[tuple]:
    scored = []
    for source, release_id in candidates:
        key = (source, release_id)
        if key not in release_cache:
            raw = get_album_tracks_for_source(source, release_id)
            release_cache[key] = [_normalize_slot(item, source)
                                  for item in _track_items(raw)]
        slots = release_cache[key]
        if not slots:
            continue
        plan = reconcile_folder(files, slots)
        clear = sum(pair['confidence'] == 'clear' for pair in plan['pairs'])
        matched = len(plan['pairs'])
        scored.append((clear, matched, -len(plan['extra_files']),
                       source, release_id, slots, plan))
    return scored


def _candidate_evidence(files: list[dict], scored: tuple) -> dict:
    """Keep the actual file-to-slot comparison behind a release decision."""
    clear, matched, _extras, source, release_id, slots, plan = scored
    pairs = []
    for pair in plan['pairs']:
        file = files[pair['file_index']]
        slot = slots[pair['slot_index']]
        recordings = file['acoustid'].get('recordings') or []
        slot_mbid = str(slot.get('recording_mbid') or '')

        def support_rank(recording, slot_mbid=slot_mbid, slot=slot):
            score = float(recording.get('score') or 0)
            eligible = score >= 0.80
            mbid_match = bool(slot_mbid and recording.get('mbid') == slot_mbid)
            title_match = _title_score(str(recording.get('title') or ''),
                                       str(slot.get('title') or '')) >= 0.88
            artist = str(recording.get('artist') or '')
            slot_artist = str(slot.get('artist') or '')
            artist_match = (not artist or not slot_artist or
                            _title_score(artist, slot_artist) >= 0.75)
            return (eligible and mbid_match, eligible and title_match and artist_match, score)

        top = max(recordings, key=support_rank, default={})

        def identity(recording):
            # AcoustID often links one fingerprint to several MusicBrainz
            # recording rows for the same named performance. Different MBIDs
            # are useful internally, but repeating "Song by Artist" ten times
            # is not useful review evidence. Keep version qualifiers while
            # folding punctuation/case variants.
            def part(value):
                folded = re.sub(r'[^\w\s]+', ' ', str(value or '').casefold())
                return re.sub(r'\s+', ' ', folded).strip()
            return part(recording.get('title')), part(recording.get('artist'))

        top_identity = identity(top)
        grouped = {}
        for recording in recordings:
            key = identity(recording)
            if not any(key) or key == top_identity:
                continue
            current = grouped.get(key)
            if current is None or support_rank(recording) > support_rank(current):
                grouped[key] = recording
        alternatives = [
            {'mbid': recording.get('mbid') or '',
             'title': recording.get('title') or '',
             'artist': recording.get('artist') or '',
             'score': recording.get('score'),
             'recording_count': sum(identity(other) == key for other in recordings)}
            for key, recording in grouped.items()
        ]
        alternatives.sort(key=support_rank, reverse=True)
        pairs.append({
            'file': os.path.basename(file['path']),
            'tag_title': file['tags'].get('title') or '',
            'file_duration_ms': file['tags'].get('duration_ms') or file.get('duration_ms') or 0,
            'audio_title': top.get('title') or '',
            'audio_artist': top.get('artist') or '',
            'audio_score': top.get('score'),
            'audio_recording_count': sum(identity(recording) == top_identity
                                         for recording in recordings),
            'audio_alternatives': alternatives,
            'slot_title': slot['title'],
            'slot_number': slot.get('track_number'),
            'disc_number': slot.get('disc_number'),
            'slot_duration_ms': slot.get('duration_ms') or 0,
            'duration_delta_ms': pair.get('duration_delta_ms'),
            'confidence': pair['confidence'],
            'reasons': pair['reasons'],
            'ambiguity_reasons': pair.get('ambiguity_reasons') or [],
            'playlists': list(file.get('playlists') or []),
        })
    return {
        'source': source, 'release_id': release_id,
        'matched': matched, 'clear': clear, 'total_slots': len(slots),
        'pairs': pairs,
        'missing': [slots[index]['title'] for index in plan['missing_slots']],
        'extra': [os.path.basename(files[index]['path'])
                  for index in plan['extra_files']],
    }


def _candidate_shape(scored: tuple) -> tuple:
    """The release facts reconciliation can distinguish, excluding edition ID."""
    _clear, _matched, _extras, _source, _release_id, slots, plan = scored

    def slot_shape(slot: dict) -> tuple:
        track_number = slot.get('track_number')
        return (slot.get('disc_number') or 1, track_number is None,
                track_number or 0, normalize(slot.get('title') or ''))

    pairs = tuple(sorted(
        (pair['file_index'], *slot_shape(slots[pair['slot_index']]))
        for pair in plan['pairs']))
    missing = tuple(sorted(
        slot_shape(slots[index])
        for index in plan['missing_slots']))
    return len(slots), pairs, missing, tuple(sorted(plan['extra_files']))


def _same_recording_copies(files: list[dict], indexes: list[int]) -> bool:
    """Require shared strong audio identity and compatible lengths."""
    if len(indexes) < 2:
        return False
    evidence = [files[index].get('acoustid') or {} for index in indexes]
    fingerprint_keys = [item.get('fingerprint_key') for item in evidence]
    if all(fingerprint_keys) and len(set(fingerprint_keys)) == 1:
        return True
    strong_ids = [
        {str(row.get('mbid')) for row in item.get('recordings') or []
         if row.get('mbid') and float(row.get('score') or 0) >= 0.90}
        for item in evidence]
    if not strong_ids or not set.intersection(*strong_ids):
        return False
    durations = [int((files[index].get('tags') or {}).get('duration_ms') or 0)
                 for index in indexes]
    durations = [duration for duration in durations if duration]
    return len(durations) < 2 or max(durations) - min(durations) <= 3000


def _duplicate_copy(file: dict) -> dict:
    row = file.get('db') or {}
    return {
        'track_id': row.get('id'),
        'title': row.get('title') or file.get('tags', {}).get('title') or '',
        'artist': row.get('artist_name') or file.get('tags', {}).get('artist') or '',
        'album': row.get('album_title') or file.get('tags', {}).get('album') or '',
        'file_path': file.get('path') or '',
        'fingerprint_key': file.get('acoustid', {}).get('fingerprint_key'),
        'size_bytes': file.get('size_bytes'),
        'mtime_ns': file.get('mtime_ns'),
        'bitrate': row.get('bitrate') or 0,
        'duration': row.get('duration') or 0,
        'track_number': row.get('track_number'),
        'playlists': list(file.get('playlists') or []),
    }


def _checked_file_evidence(files: list[dict]) -> list[dict]:
    checked = []
    for file in files:
        recordings = file['acoustid'].get('recordings') or []
        top = max(recordings, key=lambda row: float(row.get('score') or 0),
                  default={})
        checked.append({
            'file': os.path.basename(file['path']),
            'tag_title': file['tags'].get('title') or '',
            'audio_status': file['acoustid'].get('status'),
            'audio_title': top.get('title') or '',
            'audio_artist': top.get('artist') or '',
            'audio_score': top.get('score'),
        })
    return checked


def _emit_finding(context: JobContext, observed: set[tuple[str, str, str]], **finding):
    observed.add((finding['finding_type'], finding['entity_type'],
                  str(finding['entity_id'])))
    return bool(context.create_finding and context.create_finding(**finding))


def _evidence_signature(files: list[dict], extra: object = None) -> str:
    evidence = [(file['path'], file['acoustid'].get('fingerprint_key'),
                 file['acoustid'].get('status')) for file in files]
    raw = json.dumps([evidence, extra], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


@register_job
class LibraryReconciliationJob(RepairJob):
    job_id = 'library_reconciliation'
    display_name = 'Reconcile Library Albums'
    description = 'Compare album folders, audio fingerprints, tags, and exact release tracklists'
    help_text = (
        'Opt-in, read-only scan. Fingerprints audio and compares each folder with '
        'its linked or inferred release. Findings enter the Review Queue with '
        'the file, AcoustID candidates, expected slot and recommended action. '
        'Missing release slots are never requested without complete-album intent.'
    )
    icon = 'repair-icon-acoustid'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {'min_acoustid_score': 0.80}
    auto_fix = False

    def _load_rows(self, context: JobContext, roots: list[str]) -> tuple[dict, dict]:
        """One DB read for path→track and album metadata, with old-schema guards."""
        conn = context.db._get_connection()
        try:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(albums)')}
            track_columns = {row[1] for row in conn.execute('PRAGMA table_info(tracks)')}
            source_cols = [col for col in _SOURCES if col in columns]
            extras = ', '.join(f'al.{col}' for col in source_cols)
            if extras:
                extras = ', ' + extras
            canonical = (', al.canonical_source, al.canonical_album_id'
                         if {'canonical_source', 'canonical_album_id'} <= columns else '')
            if canonical and 'canonical_locked' in columns:
                canonical += ', al.canonical_locked'
            owner = ', al.owner_profile_id' if 'owner_profile_id' in columns else ''
            locked = ', t.metadata_locked' if 'metadata_locked' in track_columns else ''
            size = ', t.file_size' if 'file_size' in track_columns else ''
            bitrate = ', t.bitrate' if 'bitrate' in track_columns else ''
            artist = ', ar.name AS artist_name' if 'artists' in {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            } else ''
            artist_join = 'LEFT JOIN artists ar ON ar.id = al.artist_id' if artist else ''
            cursor = conn.execute(f'''
                SELECT t.id, t.file_path, t.title, t.album_id, t.track_number,
                       t.disc_number, t.duration, t.musicbrainz_recording_id,
                       al.title AS album_title{artist}{canonical}{owner}{locked}{size}{bitrate}{extras}
                FROM tracks t LEFT JOIN albums al ON al.id = t.album_id
                {artist_join}
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
            ''')
            names = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            tracks = {}
            albums = {}
            for row in rows:
                data = dict(zip(names, row, strict=True))
                path = data.get('file_path')
                if path:
                    # The same library is commonly mounted at both /music and
                    # /app/Music/library. A stored /music path may exist, but
                    # the scanner can be walking the other mount. Index scan
                    # aliases only when they resolve to the same physical file;
                    # matching suffixes in separate profile roots are not enough.
                    direct_exists = os.path.isfile(path)
                    candidates = []
                    for root in roots:
                        physical = resolve_library_file_path(path, library_root=root)
                        if not physical or physical in candidates:
                            continue
                        candidates.append(physical)
                    physical_paths = [path] if direct_exists else []
                    if direct_exists:
                        for physical in candidates:
                            try:
                                if (physical not in physical_paths and
                                        os.path.samefile(path, physical)):
                                    physical_paths.append(physical)
                            except OSError:
                                continue
                    elif len(candidates) == 1:
                        physical_paths = candidates
                    elif candidates:
                        # A stale root prefix can resolve to the same relative
                        # path in two independent profile libraries. Only keep
                        # multiple aliases when the OS proves they are one file.
                        try:
                            if all(os.path.samefile(candidates[0], candidate)
                                   for candidate in candidates[1:]):
                                physical_paths = candidates
                        except OSError:
                            pass
                    if not physical_paths and not candidates:
                        physical = resolve_library_file_path(
                            path, transfer_folder=context.transfer_folder,
                            config_manager=context.config_manager)
                        if physical:
                            physical_paths.append(physical)
                    for physical in physical_paths:
                        stored_parts = os.path.normpath(path).replace('\\', '/').split('/')[-2:]
                        resolved_parts = os.path.normpath(physical).replace('\\', '/').split('/')[-2:]
                        if [part.casefold() for part in stored_parts] != [
                                part.casefold() for part in resolved_parts]:
                            continue
                        elif data.get('file_size'):
                            try:
                                if os.path.getsize(physical) != data['file_size']:
                                    continue
                            except OSError:
                                continue
                        tracks[os.path.normpath(physical)] = data
                if data.get('album_id') is not None:
                    albums[data['album_id']] = data
            return tracks, albums
        finally:
            conn.close()

    def _release_candidates(self, files: list[dict], album: dict | None,
                            context: JobContext, *, include_audio: bool = False) -> list[tuple[str, str]]:
        candidates = []
        if album:
            if album.get('canonical_source') and album.get('canonical_album_id'):
                candidates.append((album['canonical_source'], str(album['canonical_album_id'])))
            for column, source in _SOURCES.items():
                if album.get(column):
                    candidates.append((source, str(album[column])))
        if include_audio and context.mb_client:
            # Only a few top fingerprint IDs are needed to seed release
            # discovery. Cap the fan-out; a single recording can belong to
            # hundreds of compilations and is not a release verdict.
            release_votes: Counter[str] = Counter()
            seen = set()
            for file in files:
                recordings = (file.get('acoustid') or {}).get('recordings') or []
                if fingerprint_is_ambiguous(recordings):
                    continue
                top = max(recordings, key=lambda row: float(row.get('score') or 0),
                          default={})
                mbid = top.get('mbid')
                if not mbid or mbid in seen or float(top.get('score') or 0) < 0.8:
                    continue
                if len(seen) >= 3:
                    break
                seen.add(mbid)
                try:
                    data = context.mb_client.get_recording(mbid, includes=['releases']) or {}
                except Exception as exc:
                    logger.debug('Could not expand release candidate %s: %s', mbid, exc)
                    continue
                for release in data.get('releases') or []:
                    if release.get('id'):
                        release_votes[release['id']] += 1
                if len(seen) >= 3:
                    break
            candidates.extend(('musicbrainz', release_id)
                              for release_id, _ in release_votes.most_common(5))
        return list(dict.fromkeys(candidates))[:6]

    def _intents(self, context: JobContext) -> dict[str, tuple[str, str, str]]:
        conn = context.db._get_connection()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_album_intent (
                    album_id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    source TEXT NOT NULL, release_id TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            return {str(row[0]): (row[1], row[2], row[3]) for row in conn.execute(
                'SELECT album_id, scope, source, release_id FROM reconcile_album_intent')}
        finally:
            conn.close()

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        client = context.acoustid_client
        if client is None:
            result.stopped_early = 'AcoustID client is unavailable'
            return result
        available, reason = client.is_available()
        if not available:
            result.stopped_early = reason or 'AcoustID unavailable'
            return result
        roots = all_library_roots(context)
        if not roots or any(not os.path.isdir(root) for root in roots):
            result.stopped_early = 'A configured library root is unavailable'
            return result
        tracks, albums = self._load_rows(context, roots)
        intents = self._intents(context)
        playlist_membership = {}
        if context.playlist_membership:
            try:
                playlist_membership = context.playlist_membership() or {}
            except Exception as exc:
                logger.debug('Could not read playlist membership: %s', exc)
        folders: dict[str, list[str]] = defaultdict(list)
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, filenames in walk_library(root):
                for name in filenames:
                    if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                        folders[dirpath].append(os.path.join(dirpath, name))
        folders = _group_disc_folders(folders, tracks, roots)
        observed: set[tuple[str, str, str]] = set()
        completed = True
        album_folder_counts: Counter[object] = Counter()
        for paths in folders.values():
            album_folder_counts.update({tracks[os.path.normpath(path)]['album_id']
                for path in paths if os.path.normpath(path) in tracks and
                tracks[os.path.normpath(path)].get('album_id') is not None})
        release_cache = {}
        release_meta_cache = {}
        cache = ReconciliationEvidence(context.db, client)
        lookup_error_reported = False
        try:
            for folder_index, (folder, paths) in enumerate(sorted(folders.items())):
                if context.check_stop() or context.wait_if_paused():
                    completed = False
                    break
                emit = partial(_emit_finding, context, observed)

                files = []
                cancelled = False
                evidence_error = ''
                for path in sorted(paths):
                    if context.check_stop():
                        cancelled = True
                        break
                    db_row = tracks.get(os.path.normpath(path))
                    try:
                        stat = os.stat(path)
                    except OSError:
                        result.skipped += 1
                        completed = False
                        continue
                    audio = cache.identify(path)
                    if audio.get('status') == 'error':
                        if not lookup_error_reported:
                            result.errors += 1
                            lookup_error_reported = True
                        evidence_error = audio.get('error') or 'AcoustID lookup failed'
                        break
                    playlists = (list(playlist_membership.get(str(db_row['id']), []))
                                 if db_row else [])
                    files.append({'path': path, 'tags': _read_file_tags(path),
                                  'acoustid': audio, 'db': db_row,
                                  'playlists': playlists,
                                  'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
                    result.scanned += 1
                # The evidence cache uses its own SQLite connection. Release
                # its write lock before create_finding opens another one.
                cache.checkpoint()
                if cancelled:
                    completed = False
                    break
                if evidence_error:
                    _preserve_folder_findings(context.db, observed, folder)
                    logger.warning('Skipping reconciliation for %s: %s', folder, evidence_error)
                    if context.update_progress:
                        context.update_progress(folder_index + 1, len(folders))
                    continue
                if not files:
                    continue
                album_counts = Counter(f['db']['album_id'] for f in files
                                       if f.get('db') and f['db'].get('album_id') is not None)
                if len(album_counts) > 1:
                    if emit(
                        job_id=self.job_id, finding_type='reconciliation_unresolved',
                        severity='warning', entity_type='folder', entity_id=folder,
                        file_path=None, title=f'Mixed albums: {os.path.basename(folder)}',
                        description='This folder contains tracks assigned to more than one library album.',
                        details={'album_folder': folder, 'album_title': os.path.basename(folder),
                                 'album_ids': list(album_counts), 'file_count': len(files),
                                 'evidence_signature': _evidence_signature(files, list(album_counts))}):
                        result.findings_created += 1
                    continue
                album_id = album_counts.most_common(1)[0][0] if album_counts else None
                album = albums.get(album_id)
                if album_id is not None and album_folder_counts[album_id] > 1:
                    if emit(
                        job_id=self.job_id, finding_type='reconciliation_unresolved',
                        severity='info', entity_type='folder', entity_id=folder,
                        file_path=None, title=f'Split album: {os.path.basename(folder)}',
                        description='This album spans separate folders that cannot be safely combined.',
                        details={'album_id': album_id, 'album_folder': folder,
                                 'profile_id': album.get('owner_profile_id') if album else 1,
                                 'album_title': os.path.basename(folder), 'file_count': len(files),
                                 'evidence_signature': _evidence_signature(files, album_id)}):
                        result.findings_created += 1
                    continue
                candidates = self._release_candidates(files, album, context)
                scored = _score_release_candidates(files, candidates, release_cache)
                best_initial = max(scored, key=lambda c: c[:3]) if scored else None
                if (not best_initial or best_initial[6]['extra_files']
                        or best_initial[6]['missing_slots']):
                    additional = [candidate for candidate in self._release_candidates(
                        files, album, context, include_audio=True)
                        if candidate not in candidates]
                    scored.extend(_score_release_candidates(files, additional, release_cache))
                if not scored:
                    if emit(
                        job_id=self.job_id, finding_type='reconciliation_unresolved',
                        severity='info', entity_type='album' if album_id else 'folder',
                        entity_id=str(album_id) if album_id else folder, file_path=None,
                        title=f'Identify release: {os.path.basename(folder)}',
                        description='No usable exact release tracklist was found for these files.',
                        details={'album_id': album_id, 'album_title':
                                 album.get('album_title') if album else os.path.basename(folder),
                                 'profile_id': album.get('owner_profile_id') if album else 1,
                                 'artist_name': album.get('artist_name') if album else '',
                                 'album_folder': folder, 'file_count': len(files),
                                 'acoustid_statuses': [f['acoustid'].get('status') for f in files],
                                 'checked_files': _checked_file_evidence(files),
                                 'attempted_releases': [
                                     {'source': source, 'release_id': release_id}
                                     for source, release_id in candidates],
                                 'evidence_signature': _evidence_signature(files, candidates)}):
                        result.findings_created += 1
                    cache.checkpoint()
                    continue
                scored.sort(key=lambda c: c[:3], reverse=True)
                # Album/source IDs are direct evidence of intended edition.
                # Prefer the first stored candidate when it fits as well as
                # the best audio-discovered candidate; sorting by opaque UUID
                # must never manufacture a human decision.
                top_score = scored[0][:3]
                linked_row = next((row for candidate in candidates for row in scored
                                   if row[:3] == top_score and
                                   (row[3], row[4]) == candidate), None)
                if linked_row:
                    scored.remove(linked_row)
                    scored.insert(0, linked_row)
                pinned_row = None
                if album and album.get('canonical_locked'):
                    pinned_row = next((row for row in scored
                                       if (row[3], row[4]) ==
                                       (album.get('canonical_source'),
                                        str(album.get('canonical_album_id') or ''))), None)
                    if pinned_row and pinned_row[:3] == scored[0][:3]:
                        scored.remove(pinned_row)
                        scored.insert(0, pinned_row)
                clear, matched, _extras, source, release_id, slots, plan = scored[0]
                locked_conflict = bool(album and album.get('canonical_locked') and
                                       (not pinned_row or pinned_row[:3] != scored[0][:3]))
                tied_rows = [row for row in scored if row[:3] == scored[0][:3]]
                equivalent_tie = (len(tied_rows) > 1 and
                                  all(_candidate_shape(row) == _candidate_shape(tied_rows[0])
                                      for row in tied_rows[1:]))
                tied = (len(tied_rows) > 1 and not linked_row and not equivalent_tie)
                if pinned_row and not locked_conflict:
                    tied = False
                pinned = bool(album and album.get('canonical_source') == source
                              and str(album.get('canonical_album_id') or '') == release_id)
                if locked_conflict or tied or (matched == 0 and not pinned):
                    issue_type = ('reconciliation_release' if album_id is not None and
                                  (tied or locked_conflict) else 'reconciliation_unresolved')
                    if locked_conflict:
                        description = ('The locked release does not fit as well as another candidate.'
                                       if pinned_row else
                                       'The locked release tracklist could not be loaded.')
                    elif tied:
                        description = 'Multiple releases fit equally well.'
                    else:
                        description = 'No release track could be assigned confidently.'
                    if emit(
                        job_id=self.job_id, finding_type=issue_type,
                        severity='warning' if tied or locked_conflict else 'info',
                        entity_type='album' if album_id is not None else 'folder',
                        entity_id=str(album_id) if album_id is not None else folder,
                        file_path=None, title=f'Choose release: {os.path.basename(folder)}',
                        description=description,
                        details={'album_id': album_id, 'album_title':
                                 album.get('album_title') if album else os.path.basename(folder),
                                 'profile_id': album.get('owner_profile_id') if album else 1,
                                 'artist_name': album.get('artist_name') if album else '',
                                 'album_folder': folder,
                                 'locked_release': {'source': album.get('canonical_source'),
                                                    'release_id': str(album.get('canonical_album_id') or '')}
                                 if locked_conflict else None,
                                 'evidence_signature': _evidence_signature(
                                     files, [(row[3], row[4], row[0], row[1])
                                             for row in scored]),
                                 'candidates': [_candidate_evidence(files, row)
                                                for row in scored[:6]]}):
                        result.findings_created += 1
                    cache.checkpoint()
                    continue
                release_key = (source, release_id)
                if release_key not in release_meta_cache:
                    release_meta_cache[release_key] = get_album_for_source(source, release_id) or {}
                release_meta = release_meta_cache[release_key]
                # MusicBrainz album IDs can be release-group IDs. Its album
                # adapter picks a concrete representative release and exposes
                # that ID here; a missing-track request must target that exact
                # edition even while the stored canonical pin remains a group.
                exact_release_id = (release_meta.get('musicbrainz_release_id')
                                    if source == 'musicbrainz' else None) or release_id
                release_title = (release_meta.get('name') or release_meta.get('title') or
                                 (album.get('album_title') if album else '') or
                                 os.path.basename(folder))
                release_artists = release_meta.get('artists') or []
                release_artist = next((artist.get('name') for artist in release_artists
                                       if isinstance(artist, dict) and artist.get('name')), '')
                common = {
                    'album_id': album_id, 'album_title': release_title,
                    'library_album_title': album.get('album_title') if album else '',
                    'artist_name': release_artist or (album.get('artist_name') if album else ''),
                    'profile_id': album.get('owner_profile_id') if album else 1,
                    'album_folder': folder, 'source': source, 'release_id': release_id,
                    'exact_release_id': exact_release_id,
                    'release_track_count': len(slots), 'local_file_count': len(files),
                    'folder_audio_path_digest': folder_audio_path_digest(folder),
                    'evidence_signature': _evidence_signature(files, (source, release_id)),
                    'playlist_tracks': [
                        {'file': os.path.basename(file['path']),
                         'title': file['tags'].get('title') or '',
                         'playlists': list(file.get('playlists') or [])}
                        for file in files if file.get('playlists')],
                }
                raw_duplicate_groups = []
                for extra_index, rivals in plan.get('competing_files', {}).items():
                    indexes = set([extra_index, *rivals])
                    if (_same_recording_copies(files, indexes) and
                            all((files[index].get('db') or {}).get('id')
                                for index in indexes)):
                        overlapping = [group for group in raw_duplicate_groups
                                       if group & indexes]
                        for group in overlapping:
                            indexes.update(group)
                            raw_duplicate_groups.remove(group)
                        raw_duplicate_groups.append(indexes)
                duplicate_groups = {}
                duplicate_indexes = set()
                extra_indexes = set(plan['extra_files'])
                for indexes in raw_duplicate_groups:
                    if not _same_recording_copies(files, list(indexes)):
                        continue
                    leaders = sorted(indexes & extra_indexes)
                    if not leaders:
                        continue
                    duplicate_groups[leaders[0]] = sorted(indexes)
                    duplicate_indexes.update(indexes)
                album_title_proposed = False
                for pair in plan['pairs']:
                    file = files[pair['file_index']]
                    slot = slots[pair['slot_index']]
                    tags = file['tags']
                    if pair['confidence'] != 'clear':
                        if (pair['file_index'] in duplicate_indexes and
                                pair.get('ambiguity_reasons') == [
                                    'another local file fits this release track']):
                            # The duplicate decision below owns both copies;
                            # emitting a second "confirm this match" card makes
                            # one problem look like two unrelated problems.
                            continue
                        if emit(
                            job_id=self.job_id, finding_type='reconciliation_unresolved',
                            severity='info', entity_type='track' if file.get('db') else 'file',
                            entity_id=str(file['db']['id']) if file.get('db') else file['path'],
                            file_path=file['path'], title=f'Confirm: {slot["title"]}',
                            description='This file fits a release slot, but the evidence is not decisive.',
                            details={**common, 'current_tags': tags, 'acoustid': file['acoustid'],
                                     'fingerprint_key': file['acoustid'].get('fingerprint_key'),
                                     'expected_title': slot['title'], 'match_reasons': pair['reasons'],
                                     'confidence': pair['confidence'],
                                     'playlists': list(file.get('playlists') or [])}):
                            result.findings_created += 1
                        continue
                    target = {'name': slot['title'], 'track_number': slot['track_number'],
                              'disc_number': slot['disc_number'],
                              'artists': [{'name': slot['artist']}]
                              if slot['artist'] else []}
                    meta = {'name': common['album_title'], 'artists':
                            [{'name': common['artist_name']}] if common['artist_name'] else [],
                            'total_tracks': len(slots)}
                    tag_plan = plan_track(tags, target, meta)
                    # Minor title spelling/version annotations are often valid;
                    # only act on a substantive title difference or hard slot
                    # fields. Album/artist edits require an explicit source.
                    changed = tag_plan['changes']
                    if _title_score(tags.get('title') or '', slot['title']) >= 0.83:
                        changed.pop('title', None)
                        tag_plan['db_data'].pop('title', None)
                    confirmed_mbid = (slot['recording_mbid'] if source == 'musicbrainz' and
                                      any(recording.get('mbid') == slot['recording_mbid'] and
                                          float(recording.get('score') or 0) >= 0.90
                                          for recording in
                                          file['acoustid'].get('recordings') or []) else '')
                    if confirmed_mbid and tags.get('mbid') != confirmed_mbid:
                        changed['musicbrainz_recording_id'] = {
                            'old': tags.get('mbid') or '', 'new': confirmed_mbid}
                        tag_plan['db_data']['musicbrainz_recording_id'] = confirmed_mbid
                    db_updates = {}
                    row = file.get('db') or {}
                    if (row and _title_score(row.get('title') or '', slot['title']) < 0.83):
                        db_updates['title'] = slot['title']
                        changed['library_title'] = {
                            'old': row.get('title') or '', 'new': slot['title']}
                    for field in ('track_number', 'disc_number'):
                        if row and slot.get(field) is not None and row.get(field) != slot[field]:
                            db_updates[field] = slot[field]
                            changed[f'library_{field}'] = {
                                'old': str(row.get(field) or ''), 'new': str(slot[field])}
                    if (row and confirmed_mbid and
                            row.get('musicbrainz_recording_id') != confirmed_mbid):
                        db_updates['musicbrainz_recording_id'] = confirmed_mbid
                        changed['library_recording_mbid'] = {
                            'old': row.get('musicbrainz_recording_id') or '',
                            'new': confirmed_mbid}
                    target_path = _suggest_filename(file['path'], slot) if file.get('db') else ''
                    if target_path:
                        changed['filename'] = {'old': os.path.basename(file['path']),
                                               'new': os.path.basename(target_path)}
                    db_album_title = ''
                    if (album and file.get('db') and not album_title_proposed and
                            album.get('album_title') != release_title):
                        db_album_title = release_title
                        changed['library_album_title'] = {
                            'old': album.get('album_title') or '', 'new': release_title}
                    if not changed or not file.get('db'):
                        continue
                    if file['db'].get('metadata_locked'):
                        if emit(
                            job_id=self.job_id, finding_type='reconciliation_unresolved',
                            severity='info', entity_type='track',
                            entity_id=str(file['db']['id']), file_path=file['path'],
                            title=f'Locked metadata: {slot["title"]}',
                            description='The file matches this release slot, but its metadata is locked.',
                            details={**common, 'current_tags': tags, 'acoustid': file['acoustid'],
                                     'changes': changed, 'expected_title': slot['title']}):
                            result.findings_created += 1
                        continue
                    if db_album_title:
                        album_title_proposed = True
                    details = {**common, 'track_id': file['db']['id'],
                               'expected_title': slot['title'],
                               'expected_artist': slot['artist'],
                               'expected_duration_ms': slot['duration_ms'],
                               'recording_mbid': slot['recording_mbid'],
                               'current_title': tags.get('title'),
                               'current_album': tags.get('album'),
                               'current_tags': tags, 'acoustid': file['acoustid'],
                               'current_library_metadata': {
                                   key: row.get(key) for key in
                                   ('title', 'track_number', 'disc_number',
                                    'musicbrainz_recording_id')},
                               'fingerprint_key': file['acoustid'].get('fingerprint_key'),
                               'size_bytes': file['size_bytes'], 'mtime_ns': file['mtime_ns'],
                               'match_reasons': pair['reasons'],
                               'confidence': pair['confidence'],
                               'changes': changed, 'db_data': tag_plan['db_data'],
                               'db_track_updates': db_updates,
                               'db_album_title': db_album_title,
                               'target_path': target_path}
                    if emit(
                        job_id=self.job_id, finding_type='reconciliation_metadata',
                        severity='warning', entity_type='track',
                        entity_id=str(file['db']['id']), file_path=file['path'],
                        title=f'Retag {slot["title"]}',
                        description='Audio and release slot support these metadata corrections.',
                        details=details):
                        result.findings_created += 1

                for file_index in plan['extra_files']:
                    if file_index in duplicate_indexes and file_index not in duplicate_groups:
                        continue
                    file = files[file_index]
                    audio = file['acoustid']
                    recordings = audio.get('recordings') or []
                    best = max(recordings, key=lambda r: r.get('score') or 0) if recordings else {}
                    competing_paths = [files[index]['path'] for index in
                                       plan.get('competing_files', {}).get(file_index, [])]
                    same_audio_paths = [files[pair['file_index']]['path'] for pair in plan['pairs']
                                        if audio.get('fingerprint_key') and
                                        files[pair['file_index']]['acoustid'].get('fingerprint_key') ==
                                        audio['fingerprint_key']]
                    competing_paths = list(dict.fromkeys(competing_paths + same_audio_paths))
                    duplicate_indexes = duplicate_groups.get(file_index) or []
                    duplicate_copies = [_duplicate_copy(files[index])
                                        for index in duplicate_indexes]
                    identified = (audio.get('status') == 'ok' and
                                  float(best.get('score') or 0) >= 0.80 and
                                  not fingerprint_is_ambiguous(recordings) and
                                  not competing_paths)
                    details = {**common, 'current_title': file['tags'].get('title'),
                               'current_tags': file['tags'], 'acoustid': audio,
                               'fingerprint_key': audio.get('fingerprint_key'),
                               'size_bytes': file['size_bytes'], 'mtime_ns': file['mtime_ns'],
                               'track_id': file['db']['id'] if file.get('db') else None,
                               'competing_copies': competing_paths,
                               'duplicate_copies': duplicate_copies,
                               'resolution_kind': ('duplicate_copies'
                                                   if duplicate_copies else ''),
                               'tracks': duplicate_copies,
                               'playlists': list(file.get('playlists') or []),
                               'acoustid_title': best.get('title'),
                               'acoustid_artist': best.get('artist'),
                               'acoustid_score': best.get('score')}
                    if emit(
                        job_id=self.job_id,
                        finding_type=('reconciliation_duplicate' if duplicate_copies else
                                      'reconciliation_extra' if identified else
                                      'reconciliation_unresolved'),
                        severity='warning' if identified or duplicate_copies else 'info',
                        entity_type='track' if file.get('db') else 'file',
                        entity_id=str(file['db']['id']) if file.get('db') else file['path'],
                        file_path=file['path'],
                        title=((f'Duplicate copies: {best.get("title") or file["tags"].get("title")}'
                               if duplicate_copies else
                               f'Unassigned audio: {file["tags"].get("title") or os.path.basename(file["path"])}')),
                        description=('These files identify as the same release track; choose a keeper.'
                                     if duplicate_copies else
                                     'Another file also fits this release slot; choose a keeper.'
                                     if competing_paths else
                                     'Audio does not fit a slot on the selected release.'
                                     if identified else 'Audio identity could not be established.'),
                        details=details):
                        result.findings_created += 1

                missing = [slots[i] for i in plan['missing_slots']]
                if missing and album_id is not None:
                    saved_intent = intents.get(str(album_id))
                    scope = (saved_intent[0] if saved_intent and
                             saved_intent[1:] == (source, release_id) else '')
                    if not scope and len(files) == 1 and files[0].get('playlists'):
                        # A lone album track that is actively referenced by a
                        # playlist is positive partial-acquisition evidence.
                        # Treat absent release slots as intentionally unowned;
                        # do not persist a guess or request the other tracks.
                        scope = 'partial'
                    if not scope:
                        if emit(
                            job_id=self.job_id, finding_type='reconciliation_intent',
                            severity='info', entity_type='album', entity_id=str(album_id),
                            file_path=None, title=f'Is {common["album_title"]} meant to be complete?',
                            description=f'{len(missing)} release track(s) are not in this folder.',
                            details={**common, 'missing_tracks': missing}):
                            result.findings_created += 1
                    elif scope == 'complete':
                        for slot in missing:
                            key = missing_slot_key(album_id, source, release_id, slot)
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_missing',
                                severity='info', entity_type='album_slot', entity_id=key,
                                file_path=None, title=f'Missing: {slot["title"]}',
                                description='This track is absent from the intended complete release.',
                                details={**common, 'missing_tracks': [{
                                    'name': slot['title'], 'track_number': slot['track_number'],
                                    'disc_number': slot['disc_number'],
                                    'source_track_id': slot['source_track_id'],
                                    'source': source, 'duration_ms': slot['duration_ms'],
                                }] }):
                                result.findings_created += 1
                cache.checkpoint()
                if context.update_progress:
                    context.update_progress(folder_index + 1, len(folders))
            if completed and not context.check_stop() and context.create_finding:
                cache.checkpoint()
                _retire_stale_findings(context.db, observed)
        finally:
            logger.info('Library reconciliation: %d files, %d findings, %d fingerprints, '
                        '%d fingerprint hits, %d lookups, %d lookup hits, %d releases',
                        result.scanned, result.findings_created,
                        cache.metrics['fingerprints'], cache.metrics['fingerprint_hits'],
                        cache.metrics['lookups'], cache.metrics['lookup_hits'],
                        len(release_cache))
            cache.close()
        if context.report_progress:
            context.report_progress(log_line=f'Reconciliation cache: {cache.metrics}')
        return result
