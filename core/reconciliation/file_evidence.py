"""Audio and local-file evidence summaries used during reconciliation."""
from __future__ import annotations

import hashlib
import json
import os
import re

from core.library.duplicate_rules import (
    is_lossy_companion_file,
    is_lossy_companion_pair,
)
from core.reconciliation.plan import (
    _title_score,
    file_version_markers,
)
from utils.logging_config import get_logger

logger = get_logger('reconciliation.file_evidence')


def _release_identity_target(files, album, source, release_id):
    """Unify inconsistent file identity or honor an explicitly pinned edition.

    An automatically selected catalogue-equivalent pressing is not evidence
    that a coherent set of embedded release IDs needs rewriting.
    """
    if source != 'musicbrainz':
        return ''
    identities = {str((file.get('tags') or {}).get('musicbrainz_release_id') or '').strip()
                  for file in files} - {''}
    # A missing tag is not evidence against the coherent existing pressing.
    # Fill it only when that identity agrees with the selected release, or the
    # user has explicitly pinned the target edition.
    return release_id if (len(identities) > 1 or identities == {release_id}
                          or (album or {}).get('canonical_locked')) else ''



def _same_recording_copies(files: list[dict], indexes: list[int]) -> bool:
    """Require shared strong audio identity and compatible lengths."""
    if len(indexes) < 2:
        return False
    version_sets = {file_version_markers(files[index]) for index in indexes}
    if len(version_sets) != 1:
        return False
    identity_titles = []
    for index in indexes:
        file = files[index]
        row = file.get('db') or {}
        title = ((file.get('tags') or {}).get('title') or row.get('title') or
                 os.path.splitext(os.path.basename(file.get('path') or ''))[0])
        disambiguation = str(row.get('recording_disambiguation') or '').strip()
        identity_titles.append(
            f'{title} ({disambiguation})' if disambiguation else str(title)
        )
    if any(_title_score(left, right) < 0.83
           for offset, left in enumerate(identity_titles)
           for right in identity_titles[offset + 1:]):
        return False
    if any(version_sets) or any(
            (files[index].get('db') or {}).get('recording_disambiguation')
            for index in indexes):
        literal_titles = {
            re.sub(r'\s+', ' ', re.sub(r'[^\w\s]+', ' ', title.casefold())).strip()
            for title in identity_titles
        }
        if len(literal_titles) != 1:
            # Category parity (both say "remix" or "live") is insufficient:
            # named remixes and performances from different years remain
            # separate recordings even when a bad external ID links them.
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



def _mark_lossy_companions(files, extensions):
    """Protect configured companions only when the audio also agrees."""
    if not extensions:
        return
    for index, file in enumerate(files):
        if not is_lossy_companion_file(file['path'], extensions):
            continue
        for other_index, other in enumerate(files):
            if (is_lossy_companion_pair(file['path'], other['path'], extensions)
                    and _same_recording_copies(files, [index, other_index])):
                file['companion_of'] = other['path']
                break



def _duplicate_copy(file: dict) -> dict:
    row = file.get('db') or {}
    path = file.get('path') or ''
    quality = {'format': os.path.splitext(path)[1].lstrip('.').upper()}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        info = getattr(audio, 'info', None)
        if info:
            quality.update({
                'sample_rate': getattr(info, 'sample_rate', None),
                'bits_per_sample': getattr(info, 'bits_per_sample', None),
                'channels': getattr(info, 'channels', None),
                'bitrate': getattr(info, 'bitrate', None),
            })
    except Exception as exc:
        logger.debug('Could not read duplicate audio quality for %s: %s', path, exc)
    return {
        'track_id': row.get('id'),
        'title': row.get('title') or file.get('tags', {}).get('title') or '',
        'artist': row.get('artist_name') or file.get('tags', {}).get('artist') or '',
        'album': row.get('album_title') or file.get('tags', {}).get('album') or '',
        'file_path': path,
        'fingerprint_key': file.get('acoustid', {}).get('fingerprint_key'),
        'size_bytes': file.get('size_bytes'),
        'mtime_ns': file.get('mtime_ns'),
        'bitrate': quality.get('bitrate') or row.get('bitrate') or 0,
        'duration': (file.get('tags') or {}).get('duration_ms') or row.get('duration') or 0,
        'quality': quality,
        'track_number': row.get('track_number'),
        'recording_disambiguation': row.get('recording_disambiguation') or '',
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
            'file_path': file['path'],
            'track_id': (file.get('db') or {}).get('id'),
            'duration_ms': file['tags'].get('duration_ms') or 0,
            'tag_title': file['tags'].get('title') or '',
            'audio_status': file['acoustid'].get('status'),
            'audio_title': top.get('title') or '',
            'audio_artist': top.get('artist') or '',
            'audio_score': top.get('score'),
        })
    return checked



def _evidence_signature(files: list[dict], extra: object = None) -> str:
    evidence = [(file['path'], file['acoustid'].get('fingerprint_key'),
                 file['acoustid'].get('status')) for file in files]
    raw = json.dumps([evidence, extra], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()

